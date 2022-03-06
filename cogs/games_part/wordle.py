from __future__ import annotations

import asyncio
import base64
import bisect
import contextlib
import dataclasses
import datetime
import io
import itertools
import json
import random
from enum import Enum
from typing import Generator, Optional, List, Dict, TYPE_CHECKING, Union, Any

import discord
from PIL import ImageDraw, ImageFont
from PIL import Image
from discord import TextStyle
from discord.ext import commands
from discord.ext.commands import Greedy
from discord.ui import Modal, TextInput

from utils import flags as flg
from utils.buttons import BaseView, QueueView
from utils.decorators import in_executor
from utils.greedy_parser import GreedyParser, Separator
from utils.useful import StellaContext, StellaEmbed, plural, aware_utc

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
        font = ImageFont.truetype("fonts/arialbd.ttf", 40)
        x2, y2 = draw.textsize(self.char, font=font)
        x = x1 + ((BOX_SIZE - x2) / 2)
        y = y1 - (MARGIN_BOX / 2) + ((BOX_SIZE - y2) / 2)  # for some reason i need to subtract 2
        draw.text((x, y), self.char, (255, 255, 255), font=font)


class WordleUnavailable(commands.CommandError):
    def __init__(self):
        super().__init__("Lewdle is unavailable.")


class WordleNotEnough(commands.CommandError):
    def __init__(self, word, length):
        super().__init__(f"'{word}' is not {length} of length.")


class WordleNotDictionary(commands.CommandError):
    def __init__(self, word):
        super().__init__(f"Word '{word}' is not in this dictionary")


class WordleGame:
    def __init__(self, ctx: StellaContext, *, dictionaries,
                 name: str = "wordle",
                 player: Optional[discord.Member] = None,
                 answer: Optional[str] = None,
                 word_length: int = 5, tries: int = 6,
                 display_answer: bool = True):

        self.name = name
        self.dictionaries = dictionaries
        self.ctx: StellaContext = ctx
        self.player = player or ctx.author
        self.word_length: int = word_length
        self.display: List[Optional[Letter]] = [[None] * word_length for _ in range(tries)]
        self.max_tries: int = tries
        self.answer: str = answer or random.choice(dictionaries)
        self.user_tries: Optional[int] = None
        self.message: Optional[discord.Message] = None
        self._word_guessed: Optional[asyncio.Future] = None
        self.view: WordleView = None
        self.win: bool = False
        self.finish: bool = False
        self.task: Optional[asyncio.Task] = None
        self._background: Optional[Image] = None
        self._background_draw: Optional[ImageDraw] = None
        self.display_answer: bool = display_answer
        self._previous_url: Optional[str] = None

    def map_letter(self, guess: str) -> Generator[Union[Letter, str], None, None]:
        for char, correct_char in zip(guess, self.answer):
            if char == correct_char:
                yield Letter(char, LetterKind.correct)
            elif char not in self.answer:
                yield Letter(char, LetterKind.incorrect)
            else:
                yield char

    def convert_guess(self, guess: str) -> List[Letter]:
        formed = []
        unformed = list(self.answer)
        for i, char in enumerate(self.map_letter(guess)):
            formed.append(char)
            if isinstance(char, Letter) and char.kind is LetterKind.correct:
                unformed[i] = None

        if not any(unformed):
            return formed

        for i, char in enumerate(formed):
            if not isinstance(char, str):
                continue

            kind = LetterKind.incorrect
            if char in unformed:
                kind = LetterKind.half_correct
                index = unformed.index(char)
                unformed[index] = None

            formed[i] = Letter(char, kind)

        return formed

    def guess_word(self, word: str) -> bool:
        guess = word.strip().upper()
        if len(guess) != self.word_length:  # kinda useless ngl, but hey in case people steal it lol
            raise WordleNotEnough(guess.casefold(), self.word_length)

        if guess not in self.dictionaries:
            raise WordleNotDictionary(guess.casefold())

        self.display[self.user_tries] = self.convert_guess(guess)
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
        name = f"[{self.name}]" if self.name != "wordle" else ""
        embed = StellaEmbed(title=f"Wordle Game{name}")
        amount = self.max_tries - self.user_tries
        embed.description = content or f"You have {amount} {plural('attempt(s)', amount)} left. Press 'Guess' button to guess!"
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
        return await self.ctx.bot.ipc_client.request('upload_file', base64=base, filename="lewdle_board.png")

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
        query = "INSERT INTO wordle_rank " \
                "VALUES($1, $2, $3, $4, 1) " \
                "ON CONFLICT(user_id, tag, word, attempt) " \
                "DO UPDATE SET amount = wordle_rank.amount + 1"

        await self.ctx.bot.pool_pg.execute(query, self.player.id, self.name, self.answer.upper(), self.user_tries)

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
        i = bisect.bisect_left(tries, self.user_tries)
        tried = "first try!" if self.user_tries == 1 else f"`{self.user_tries}` attempts!"
        content = f"{self.player.mention}, {comments[tries[i]]} You guess the word in {tried}"
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
            self.view = WordleView(self)
            self.message = await self.ctx.reply(
                embed=self.create_embed(url=render),
                view=self.view,
                mention_author=False
            )
            return

        await self.message.edit(embed=self.create_embed(url=render))


class WordleView(BaseView):
    def __init__(self, game: WordleGame):
        super().__init__(timeout=600)
        self.game = game
        self._prompter = None

    def _get_prompter(self):
        if self._prompter is None:
            self._prompter = WordlePrompt(self)

        return self._prompter

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        author = self.game.player
        if author.id == getattr(interaction.user, "id", None):  # due to discord.Object
            return True

        await interaction.response.send_message(f"Sorry, only {author} can use this.", ephemeral=True)

    @discord.ui.button(label="Guess", style=discord.ButtonStyle.green)
    async def guess_button(self, _: discord.ui.Button, interaction: discord.Interaction):
        prompter = self._get_prompter()
        prompter.update_text()
        await interaction.response.send_modal(prompter)

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


class WordlePrompt(Modal):
    text_input = TextInput(label="Guess a word", default="")
    text_display = TextInput(label="Display", required=False, default="", style=TextStyle.paragraph,
                             placeholder="No need to fill these. This is your display")

    def __init__(self, view: WordleView):
        name = f"[{view.game.name}]" if view.game.name != "wordle" else ""
        super().__init__(title=f"Wordle Game{name}")
        self.view = view
        self.game = view.game
        word_length = self.game.word_length
        self.text_input.min_length = word_length
        self.text_input.max_length = word_length
        self.first = True

    @staticmethod
    def format_word(word):
        formed = []
        indicator = {
            LetterKind.correct: "[{}]",
            LetterKind.half_correct: "[{}]?",
            LetterKind.incorrect: "[{}]X"
        }

        for letter in word:
            if letter is None:
                return None

            char = letter.char.upper()
            value = indicator[letter.kind].format(char)
            formed.append(value)

        return " ".join(formed)

    def update_text(self):
        self.text_input.default = ""
        iterator = map(self.format_word, self.game.display[:self.game.user_tries])
        guesses = "\n".join(f"{i + 1}. {word}" for i, word in enumerate(iterator))
        instruction = "Instruction:\n" \
                      "[Char] = correct\n" \
                      "[Char]? = half correct\n" \
                      "[Char]X = incorrect "
        self.text_display.default = f"{guesses}\n\n{instruction}"

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.view.reset_timeout()
        guess = self.text_input.value
        await self.game.user_answer(interaction, guess)


def tries_convert(arg: str) -> int:
    try:
        value = int(arg)
    except ValueError:
        raise commands.CommandError(f"'{arg}' is not a number.")
    else:
        if 1 <= value <= 10:
            return value
        raise commands.CommandError(f"argument must be between 1 - 10. Not {value}")


def word_count_convert(arg: str) -> int:
    try:
        value = int(arg)
    except ValueError:
        raise commands.CommandError(f"'{arg}' is not a number.")
    else:
        if 3 <= value <= 10:
            return value
        raise commands.CommandError(f"argument must be between 3 - 10. Not {value}")


class WordleFlag(commands.FlagConverter):
    tries: Optional[tries_convert] = flg.flag(
        help="The amount of time user can try, it defaults to 6.", default=6
    )
    word_count: Optional[word_count_convert] = flg.flag(
        help="The word count that the user will be guessing, it defaults to 5.", default=5
    )


class WordleTags(commands.Converter[str]):
    def __init__(self, *, existing: bool = True, author=False):
        self.existing = existing
        self.author = author

    async def convert(self, ctx: StellaContext, argument: str) -> str:
        argument = argument.casefold()
        if not 3 < len(argument) < 100:
            raise commands.CommandError("Tag length must be between 3 to 100 characters")

        result = await ctx.bot.pool_pg.fetchrow("SELECT * FROM wordle_tag WHERE tag=$1", argument)
        if not result and self.existing:
            raise commands.CommandError(f"Tag {argument} does not exist.")

        if result:
            if not self.existing:
                raise commands.CommandError(f"Tag {argument} already exist with this name.")

            if self.author and result["user_id"] != ctx.author.id:
                raise commands.CommandError("You do not own this wordle tag.")

        return argument


@dataclasses.dataclass
class WordleTag:
    owner: Union[discord.Member, discord.User, discord.Object]
    name: str
    description: Optional[str]
    amount_words: int
    created_at: datetime.datetime

    @staticmethod
    def resolving_user(ctx: StellaContext, uid: int) -> Union[discord.Member, discord.User, discord.Object]:
        resolve_user = discord.Object(uid)
        if ctx.guild:
            if member := ctx.guild.get_member(resolve_user.id):
                return member

        if user := ctx.bot.get_user(resolve_user.id):
            return user

        return resolve_user

    @classmethod
    async def convert(cls, ctx: StellaContext, argument: str) -> WordleTag:
        argument = argument.casefold()
        query = """
        SELECT t.*, (
            SELECT COUNT(word)
            FROM wordle_word w
            WHERE t.tag = w.tag
        ) "tag_count" FROM wordle_tag t
        WHERE t.tag=$1
        """
        result = await ctx.bot.pool_pg.fetchrow(query, argument)
        if not result:
            raise commands.CommandError(f"Tag {argument} does not exist.")

        owner = cls.resolving_user(ctx, result["user_id"])
        description = result["description"] or "Undocumented"

        return cls(owner, argument, description, result["tag_count"], result["created_at"])


class DuelView(discord.ui.View):
    def __init__(self, url: str):
        super().__init__()
        self.add_item(discord.ui.Button(label="Winner Message", style=discord.ButtonStyle.green, url=url))


class MultiWordle:
    def __init__(self, ctx: StellaContext, *players: discord.Member, dictionaries, tries, word_count):
        self.ctx = ctx
        self.tries = tries
        self.word_count = word_count
        self.dictionaries = dictionaries
        self.players = players
        self.games: Dict[int, WordleGame] = {}
        self.answer = random.choice(self.dictionaries)
        self.loop = ctx.bot.loop
        self._result = ctx.bot.loop.create_future()
        self.amount_finished = 0

    async def check_finish(self):
        self.amount_finished += 1
        if self.amount_finished == len(self.players):
            self._result.set_result(None)

    async def every_player(self, player: discord.Member):
        game = WordleGame(self.ctx, dictionaries=self.dictionaries, player=player, answer=self.answer,
                          display_answer=False,
                          word_length=self.word_count,
                          tries=self.tries)
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


class LimitWordleMember(commands.MemberConverter):
    async def convert(self, ctx: StellaContext, argument: str) -> discord.Member:
        member = await super().convert(ctx, argument)
        if member == ctx.author:
            raise commands.CommandError("You cannot mention yourself!")

        if member.bot:
            raise commands.CommandError(f"{member} is a bot. You can't play with a bot.")

        return member

    @classmethod
    async def after_greedy(cls, ctx: StellaContext, converted: List[discord.Member]):
        checked = []
        for member in converted:
            if member in checked:
                raise commands.CommandError(f"Duplicated member {member}.")
            checked.append(member)

        if not 1 <= len(checked) <= 3:
            raise commands.CommandError("Member selected must be between 1 - 3.")

        return checked


class QueueWordle(QueueView):
    def __init__(self, ctx: StellaContext, title: str, *respondents: Union[discord.Member, discord.User],
                 delete_after: bool = False):
        super().__init__(ctx, *respondents, delete_after=delete_after)
        self.embed = StellaEmbed.default(
            ctx,
            title=title
        )

    async def send(self, content: str, **kwargs: Any) -> List[Optional[Union[discord.Member, discord.User]]]:
        self.form_embed()
        return await super().send(content, embed=self.embed, **kwargs)

    def every_respondent(self, i: int, member: Union[discord.Member, discord.User]) -> str:
        value = "<:question:848263403604934729>"
        if member in self.accepted_respondents:
            value = "<:checkmark:753619798021373974>"
        elif member in self.denied_respondents:
            value = "<:crossmark:753620331851284480>"
        return f"{i + 1}. {member} {value}"

    def form_embed(self):
        self.embed.description = "\n".join(itertools.starmap(self.every_respondent, enumerate(self.respondents)))

    async def on_member_respond(self, member: Union[discord.Member, discord.User],
                                interaction: discord.Interaction, response: QueueView.State):
        self.form_embed()
        await self.message.edit(embed=self.embed)


class LewdleCommandCog(commands.Cog):
    def __init__(self, bot: StellaBot):
        self.bot = bot
        self.lewdle_query = "SELECT word FROM wordle_word WHERE tag='lewdle' AND LENGTH(word) = $1"

    @commands.group(invoke_without_command=True, help="A wordle game except it's lewd.")
    async def lewdle(self, ctx: StellaContext, *, flags: WordleFlag):
        records = [r[0] for r in await self.bot.pool_pg.fetch(self.lewdle_query, flags.word_count)]
        if not records:
            raise commands.CommandError(f"Looks like we dont have words for lewdle in {flags.word_count} word count. Try 5.")

        game = WordleGame(ctx, name="Lewdle", dictionaries=records, tries=flags.tries)
        await game.start()

    @lewdle.command(help="Duel lewdle game with your friends! Who ever guess the word first wins!")
    async def duel(self, ctx: StellaContext, member: discord.Member):
        value = await ctx.confirmation(
            f"{member.mention}, `{ctx.author}` has invited you to a lewdle duel. Do you accept?",
            to_respond=member
        )
        if not value:
            raise commands.CommandError(f"Looks like `{member}` declined. Sorry {ctx.author.mention}.")

        records = [r[0] for r in await self.bot.pool_pg.fetch(self.lewdle_query, 5)]
        games = MultiWordle(ctx, ctx.author, member, dictionaries=records, word_count=5, tries=6)
        await games.start()

    @commands.group(invoke_without_command=True,
                    brief="A customizable wordle game!",
                    help="Play wordle with tag to specificy which dictionary to use, by default it uses the wordle "
                         "dictionary.")
    async def wordle(self, ctx: StellaContext, tag: Optional[WordleTags] = "wordle", *, flags: WordleFlag):
        query = "SELECT word FROM wordle_word WHERE tag=$1 AND LENGTH(word)=$2"
        results = [r[0] for r in await self.bot.pool_pg.fetch(query, tag, flags.word_count)]

        if not results:
            raise commands.CommandError(f"Looks like `{tag}` does not have a dictionary for {flags.word_count} word count.")

        games = WordleGame(ctx, name=tag, dictionaries=results, tries=flags.tries, word_length=flags.word_count)
        await games.start()

    @wordle.command(name="create",
                    brief="Create your own tag for a custom wordle game.",
                    help="Create a wordle tag which will contain your dictionary that you can used in `wordle <tag>`"
                         "command.")
    async def wordle_create(self, ctx: StellaContext, tag: WordleTags(existing=False), *, description: str):
        query = "INSERT INTO wordle_tag VALUES($1, $2, 0, now() at time zone 'utc', $3)"
        await self.bot.pool_pg.execute(query, tag, ctx.author.id, description)
        await ctx.confirmed()

    @wordle.command(name="info",
                    brief="Shows information about the wordle tag.",
                    help="Shows information about the wordle tag in detail.")
    async def wordle_info(self, ctx: StellaContext, tag: WordleTag):
        desc = (
            "**Owner:** `{0.owner}`\n"
            "**Name:** `{0.name}`\n"
            "**Description:** {0.description}\n"
            "**Dictionary Size:** `{0.amount_words:,}` words\n"
            f"**Created At:** {aware_utc(tag.created_at, mode='f')}"
        )
        embed = discord.Embed().set_thumbnail(url=tag.owner.display_avatar)
        await ctx.embed(title="Wordle Tag Info", description=desc.format(tag), embed=embed)

    @wordle.command(name="insert",
                    brief="Add a new word into your tag dictionary.",
                    help="Add a new word into your tag dictionary. This can take a json file which should contain an "
                         "array of strings to automatically inserted into the database. You can submit up to 1k words.")
    async def wordle_insert(self, ctx: StellaContext, tag: WordleTags(author=True), words: Greedy[str.upper]):
        if ctx.message.attachments:
            attachment: discord.Attachment = ctx.message.attachments[0]
            words_attachment = json.load(io.BytesIO(await attachment.read()))
            words.extend([word.upper() for word in words_attachment])

        conflict_sql = "SELECT word FROM wordle_word WHERE tag=$1"
        list_guess = [r[0] for r in await self.bot.pool_pg.fetch(conflict_sql, tag)]
        sql = "INSERT INTO wordle_word VALUES($1, $2) ON CONFLICT DO NOTHING"
        to_insert = [[tag, word] for word in words if word not in list_guess]
        if to_insert:
            await self.bot.pool_pg.executemany(sql, to_insert)
            value = f"{len(to_insert)} was inserted"
        else:
            value = "No value was inserted."
        await ctx.maybe_reply(value)

    @wordle.command(name="duel",
                    brief="Duel a wordle game with your friends!",
                    help="Duel a wordle game with your friends! Who ever guess the word first wins!"
                         "Note: Members argument must be separated by ','",
                    cls=GreedyParser
                    )
    async def wordle_duel(self, ctx: StellaContext, members: Separator[LimitWordleMember],
                          tag: Optional[WordleTags] = "wordle", *, flags: WordleFlag):

        query = "SELECT word FROM wordle_word WHERE tag=$1 AND LENGTH(word)=$2"
        records = [r[0] for r in await self.bot.pool_pg.fetch(query, tag, flags.word_count)]
        if not records:
            raise commands.CommandError(f"Looks like `{tag}` does not have a dictionary for {flags.word_count} word count.")

        name = "" if tag is None or tag == "wordle" else f"[{tag}]"
        mentions = ", ".join(map(discord.Member.mention.fget, members[:-1]))
        user_mention = members[-1].mention
        if mentions:
            user_mention = f"{mentions} and {user_mention}"

        queue = QueueWordle(ctx, f"Wordle{name} Invitation", *members)
        content = f"{user_mention}, `{ctx.author}` has invited you to a wordle duel! "\
                  f"Please respond with Confirm to participate."
        players = await queue.send(content)
        if not players:
            raise commands.CommandError(f"Looks like everyone declined. Sorry `{ctx.author}`.")

        games = MultiWordle(ctx, ctx.author, *players, dictionaries=records, tries=flags.tries, word_count=flags.word_count)
        await games.start()
