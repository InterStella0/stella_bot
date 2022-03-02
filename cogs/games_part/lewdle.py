from __future__ import annotations

import asyncio
import base64
import bisect
import contextlib
import dataclasses
import io
import json
import random
import traceback
from enum import Enum
from typing import Generator, Optional, List, Dict, TYPE_CHECKING

import discord
from PIL import ImageDraw, ImageFont
from PIL import Image
from discord.ext import commands
from discord.ext.commands import Greedy

from addons.modal import Modal, TextInput
from addons.modal.raw import ResponseModal
from utils.buttons import BaseView
from utils.decorators import in_executor
from utils.greedy_parser import GreedyAllowStr
from utils.useful import StellaContext, StellaEmbed, plural, unpack

if TYPE_CHECKING:
    from main import StellaBot

BOX_SIZE = 50
MARGIN_BOX = 5
PADDING = 50
BACKGROUND_COLOR = 18, 18, 19


class LetterKind(Enum):
    correct: discord.Color = discord.Color(0x538d4e)
    half_correct: discord.Color = discord.Color(0xb59f3b)
    incorrect: discord.Color = discord.Color(0x39393c)


def create_block(draw, x, y, color):
    draw.rectangle((x, y, x + BOX_SIZE, y + BOX_SIZE), fill=color, outline=(58, 58, 60))


@dataclasses.dataclass
class Letter:
    char: str
    kind: LetterKind

    def render_letter(self, draw: ImageDraw, x1: int, y1: int):
        create_block(draw, x1, y1, self.kind.value.to_rgb())
        font = ImageFont.truetype("arialbd.ttf", 40)
        x2, y2 = draw.textsize(self.char, font=font)
        x = x1 + ((BOX_SIZE - x2) / 2)
        y = y1 - (MARGIN_BOX / 2) + ((BOX_SIZE - y2) / 2)  # for some reason i need to subtract 2
        draw.text((x, y), self.char, (255, 255, 255), font=font)


class LewdleUnavailable(commands.CommandError):
    def __init__(self):
        super().__init__("Lewdle is unavailable.")


class LewdleNotEnough(commands.CommandError):
    def __init__(self, word, length):
        super().__init__(f"'{word}' is not {length} of length.")


class LewdleNotDictionary(commands.CommandError):
    def __init__(self, word):
        super().__init__(f"Word '{word}' is not in my dicktionary")


class LewdleGame:
    def __init__(self, ctx: StellaContext, *,
                 player: Optional[discord.Member] = None,
                 answer: Optional[str] = None,
                 word_length: int = 5, tries: int = 6,
                 display_answer: bool = True):
        self.cog: LewdleCommandCog = ctx.cog
        self.ctx: StellaContext = ctx
        self.player = player or ctx.author
        self.word_length: int = word_length
        self.display: List[Optional[Letter]] = [[None] * word_length for _ in range(tries)]
        self.max_tries: int = tries
        self.answer: str = answer or random.choice(self.cog.list_guess)
        self.user_tries: Optional[int] = None
        self.message: Optional[discord.Message] = None
        self._word_guessed: Optional[asyncio.Future] = None
        self.view: LewdleView = None
        self.win: bool = False
        self.finish: bool = False
        self.task: Optional[asyncio.Task] = None
        self._background: Optional[Image] = None
        self._background_draw: Optional[ImageDraw] = None
        self.display_answer: bool = display_answer
        self._previous_url: Optional[str] = None

    def map_letter(self, guess: str) -> Generator[Letter, None, None]:
        letters = list(self.answer)
        for char, correct_char in zip(guess, self.answer):
            if char not in letters:
                yield Letter(char, LetterKind.incorrect)
                continue

            if char == correct_char:
                yield Letter(char, LetterKind.correct)
            elif char in letters:
                yield Letter(char, LetterKind.half_correct)

            letters.remove(char)

    def guess_word(self, word: str) -> bool:
        guess = word.strip().upper()
        if len(guess) != self.word_length:  # kinda useless ngl, but hey in case people steal it lol
            raise LewdleNotEnough(guess.casefold(), self.word_length)

        if guess not in self.cog.list_guess:
            raise LewdleNotDictionary(guess.casefold())

        guess_words = [*self.map_letter(guess)]
        self.display[self.user_tries] = guess_words
        return guess == self.answer

    def receive(self) -> asyncio.Future:
        self._word_guessed = self.ctx.bot.loop.create_future()
        return self._word_guessed

    async def game_progress(self) -> Optional[bool]:
        await self.show_display()
        print("Answer:", self.answer)
        while True:
            interaction, answer = await self.receive()
            try:
                return self.guess_word(answer)
            except Exception as e:
                await interaction.followup.send(e, ephemeral=True)

    async def user_answer(self, interaction: discord.Interaction, guess: str):
        if self._word_guessed is not None:
            self._word_guessed.set_result((interaction, guess))

    def create_embed(self, *, content: Optional[str] = None, url: Optional[str] = None):
        embed = StellaEmbed(title="Lewdle Game")
        amount = self.max_tries - self.user_tries
        embed.description = content or f"You have {amount} {plural('attempt(s)', amount)} left"
        url = url or self._previous_url
        if url is not None:
            embed.set_image(url=url)
            self._previous_url = url

        return embed.set_footer(text=f"Player {self.player}", icon_url=self.player.display_avatar)

    def stop(self):
        self.finish = True
        self.view.stop()
        if not self.task.done():
            self.task.cancel()

    async def current_game(self):
        for i in range(self.max_tries):
            self.user_tries = i
            if await self.game_progress():
                await self.won_display()
                await self.insert_win_db()
                self.win = True
                break

        if not self.win:
            await self.lost_display()

    async def start(self):
        self.task = self.ctx.bot.loop.create_task(self.current_game())
        with contextlib.suppress(asyncio.CancelledError):
            await self.task

        if not self.finish:
            self.stop()

    def render_background(self):
        x = (BOX_SIZE + (MARGIN_BOX * 2)) * self.word_length + PADDING * 2
        y = (BOX_SIZE + (MARGIN_BOX * 2)) * self.max_tries + PADDING * 2
        self._background = Image.new("RGB", (x, y), BACKGROUND_COLOR)
        self._background_draw = ImageDraw.Draw(self._background)
        next_box_x = PADDING
        next_box_y = PADDING
        for tries in self.display:
            next_box_y += MARGIN_BOX
            original_x = next_box_x
            for _ in tries:
                next_box_x += MARGIN_BOX
                create_block(self._background_draw, next_box_x, next_box_y, BACKGROUND_COLOR)
                next_box_x += BOX_SIZE + MARGIN_BOX

            next_box_x = original_x
            next_box_y += BOX_SIZE + MARGIN_BOX

        return self._background

    def get_background(self):
        if self._background is None:
            return self.render_background()
        return self._background

    async def render_display(self):
        base = await self._render_display()
        return await self.cog.bot.ipc_client.request('upload_file', base64=base, filename="lewdle_board.png")

    @in_executor()
    def _render_display(self):
        self.__render_display()
        byte = io.BytesIO()
        self._background.save(byte, format="PNG")
        byte.seek(0)
        base = base64.b64encode(byte.read()).decode('utf-8')
        return base

    def __render_display(self):
        background = self.get_background()
        if self.user_tries == 0:
            return

        self._background_draw = ImageDraw.Draw(background)
        row = self.user_tries - 1
        x_axis = PADDING
        y_axis = PADDING + MARGIN_BOX + ((BOX_SIZE + MARGIN_BOX * 2) * row)
        for char in self.display[row]:
            if char is None:
                return  # something went wrong
            x_axis += MARGIN_BOX
            char.render_letter(self._background_draw, x_axis, y_axis)
            x_axis += BOX_SIZE + MARGIN_BOX

    async def forfeit(self):
        message = f"`{self.player}` has forfeited after {self.user_tries} {plural('attempt(s)', self.user_tries)}."
        await self.message.edit(embed=self.create_embed(content=message))
        self.task.cancel()
        self.stop()

    async def insert_win_db(self):
        query = "INSERT INTO lewdle_rank " \
                "VALUES($1, $2, $3, 1) " \
                "ON CONFLICT(user_id, word, attempt) " \
                "DO UPDATE SET amount = lewdle_rank.amount + 1"
        await self.ctx.bot.pool_pg.execute(query, self.player.id, self.answer.upper(), self.user_tries)

    async def won_display(self):
        self.user_tries += 1
        comments = {
            1: "Absolute god.",
            2: "Insane ngl.",
            4: "Impressive.",
            5: "Pretty average.",
            10: "Close call lol."
        }
        tries = [*comments]
        i = bisect.bisect(tries, self.user_tries)
        tried = "first try!" if self.user_tries == 1 else f"`{self.user_tries}` attempts!"
        content = f"{self.player.mention}, {comments[i]} You guess the word in {tried}"
        render = await self.render_display()
        await self.message.edit(embed=self.create_embed(content=content, url=render))

    async def lost_display(self, message: Optional[str] = None):
        content = message
        if message is None:
            content = f"Ran out of tries {self.player.mention}!"
            if self.display_answer:
                content += f" The word was `{self.answer.casefold()}`."

        self.user_tries += 1
        render = await self.render_display()
        await self.message.edit(embed=self.create_embed(content=content, url=render))

    async def show_display(self):
        render = await self.render_display()
        if self.message is None:
            self.view = LewdleView(self)
            self.message = await self.ctx.reply(
                embed=self.create_embed(url=render),
                view=self.view,
                mention_author=False
            )
            return

        await self.message.edit(embed=self.create_embed(url=render))


class LewdleView(BaseView):
    def __init__(self, game: LewdleGame):
        super().__init__(timeout=600)
        self.game = game
        self._prompter = None

    def _get_prompter(self):
        if self._prompter is None:
            self._prompter = LewdlePrompt(self)

        return self._prompter

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        author = self.game.player
        if author.id == getattr(interaction.user, "id", None):  # due to discord.Object
            return True

        await interaction.response.send_message(f"Sorry, only {author} can use this.", ephemeral=True)

    @discord.ui.button(label="Guess", style=discord.ButtonStyle.green)
    async def guess_button(self, _: discord.ui.Button, interaction: discord.Interaction):
        prompter = self._get_prompter()
        await prompter.prompt(interaction)

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger)
    async def stop_button(self, _: discord.ui.Button, __: discord.Interaction):
        await self.game.forfeit()

    async def disable_items(self):
        if (message := self.game.message) is None:
            return

        for x in self.children:
            x.disabled = True

        await message.edit(view=self)

    def stop(self):
        if self._prompter is not None:
            self._prompter.stop()

        self.game.ctx.bot.loop.create_task(self.disable_items())


class LewdlePrompt(Modal):
    def __init__(self, view: LewdleView):
        super().__init__(title="Lewdle Game", timeout=None)
        self.view = view
        self.game = view.game
        word_length = self.game.word_length
        self.add_item(TextInput(label="Guess a word", required=True, min_length=word_length, max_length=word_length))

    async def callback(self, modal: ResponseModal, interaction: discord.Interaction) -> None:
        self.view.reset_timeout()
        guess = modal["Guess a word"].value
        await self.game.user_answer(interaction, guess)


def lewdle_check():
    def check_command(ctx):
        if getattr(ctx.cog, "list_guess", None) is None:
            raise LewdleUnavailable()

        return True
    return commands.check(check_command)


def tries_convert(arg):
    try:
        value = int(arg)
    except ValueError:
        raise commands.CommandError(f"'{arg}' is not a number.")
    else:
        if 1 <= value <= 10:
            return value
        raise commands.CommandError(f"argument must be between 1 - 10. Not {value}")


class DuelView(discord.ui.View):
    def __init__(self, url: str):
        super().__init__()
        self.add_item(discord.ui.Button(label="Winner Message", style=discord.ButtonStyle.green, url=url))


class MultiLewdle:
    def __init__(self, ctx: StellaContext, *players: discord.Member):
        self.ctx = ctx
        self.cog: LewdleCommandCog = ctx.cog
        if not isinstance(ctx.cog, LewdleCommandCog):
            raise Exception(f"Invalid Cog class. Expecting '{LewdleCommandCog}' not '{type(ctx.cog)}'")

        self.players = players
        self.games: Dict[int, LewdleGame] = {}
        self.answer = random.choice(self.cog.list_guess)
        self.loop = ctx.bot.loop
        self._result = ctx.bot.loop.create_future()
        self.amount_finished = 0

    async def check_finish(self):
        self.amount_finished += 1
        if self.amount_finished == len(self.players):
            self._result.set_result(None)

    async def every_player(self, player: discord.Member):
        game = LewdleGame(self.ctx, player=player, answer=self.answer, display_answer=False)
        self.games[player.id] = game
        await game.start()
        if game.win and not self._result.done():
            self._result.set_result(player)
            return

        await self.check_finish()

    async def start(self):
        for player in self.players:
            self.loop.create_task(self.every_player(player))

        winner = await self._result
        if winner is None:
            players = " and ".join(f"`{player}`" for player in self.players)
            await self.ctx.reply(f"Looks like nobody won! Thank you for playing. {players}")
            return

        winner_game = self.games[winner.id]
        await self.ctx.send(f"The winner is {winner} with {winner_game.user_tries + 1} tries!")
        for game in self.games.values():
            if game.finish:
                continue

            self.loop.create_task(game.lost_display(f"{winner} won! The word was {self.answer}"))


class LewdleCommandCog(commands.Cog):
    def __init__(self, bot: StellaBot):
        self.bot = bot
        self.list_guess = []
        bot.loop.create_task(self.fill_list_guess())

    async def fill_list_guess(self):
        query = "SELECT * FROM lewdle_word"
        records = await self.bot.pool_pg.fetch(query)
        self.list_guess = [record[0] for record in records]

    @commands.group(invoke_without_command=True)
    @lewdle_check()
    async def lewdle(self, ctx: StellaContext, tries: tries_convert = 6):
        game = LewdleGame(ctx, tries=tries)
        await game.start()

    @lewdle.command()
    @lewdle_check()
    async def duel(self, ctx: StellaContext, member: discord.Member):
        value = await ctx.confirmation(
            f"{member.mention}, `{ctx.author}` has invited you to a lewdle duel. Do you accept?",
            to_respond=member
        )
        if not value:
            await ctx.maybe_reply(f"Looks like `{member}` declined. Sorry {ctx.author.mention}.")
            return

        games = MultiLewdle(ctx, ctx.author, member)
        await games.start()

    @commands.command()
    @commands.is_owner()
    async def lewdle_insert(self, ctx: StellaContext, words: GreedyAllowStr[str.upper]):
        if ctx.message.attachments:
            attachment: discord.Attachment = ctx.message.attachments[0]
            words_attachment = json.load(io.BytesIO(await attachment.read()))
            words.extend([word.upper() for word in words_attachment])

        sql = "INSERT INTO lewdle_word VALUES($1) ON CONFLICT DO NOTHING"
        to_insert = [[word] for word in words if word not in self.list_guess]
        if to_insert:
            await self.bot.pool_pg.executemany(sql, to_insert)
            self.list_guess.extend([*unpack(to_insert)])
            value = f"{len(to_insert)} was inserted"
        else:
            value = "No value was inserted."
        await ctx.maybe_reply(value)
