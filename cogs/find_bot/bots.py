from __future__ import annotations

import base64
import contextlib
import datetime
import functools
import io
import itertools
import operator
import random
import textwrap
import time

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Type, TypeVar, Union

import discord

from discord import ui
from discord.ext import commands, menus
from discord.ext.menus import ListPageSource

from .baseclass import FindBotCog
from .converters import pprefix, clean_prefix
from .models import BotGitHubLink, BotRepo, BotAdded, BotOwner
from utils import flags as flg, greedy_parser
from utils.buttons import InteractionPages, PromptView
from utils.decorators import event_check, is_discordpy, pages
from utils.errors import NotInDatabase
from utils.image_manipulation import create_bar, get_majority_color, islight, process_image
from utils.new_converters import BotCommands, BotPrefixes, IsBot
from utils.useful import StellaContext, StellaEmbed, aware_utc, plural, realign

T = TypeVar("T")


@dataclass
class BotPredictPrefixes:
    bot: discord.User
    prefix: str
    raw_data: List[Tuple[str, float]]

    @classmethod
    async def convert(cls, ctx: StellaContext, argument: str) -> BotPredictPrefixes:
        user = await IsBot().convert(ctx, argument)
        query = """
            SELECT pt.letter, pt.position, pt.count, total, (pt.count::FLOAT) / (total::FLOAT) "percentage"
            FROM position_letter pt
            INNER JOIN (
                SELECT position, MAX(count) as count FROM position_letter
                WHERE bot_id=$1
                GROUP BY position
            ) AS m
            ON (m.count=pt.count AND pt.position=m.position)
            INNER JOIN (
                SELECT position, SUM(count) "total"
                FROM position_letter
                WHERE bot_id=$1
                GROUP BY position
            ) AS sums
            ON (sums.position=m.position)
            WHERE bot_id=$1
            ORDER BY pt.position
        """
        if not (data := await ctx.bot.pool_pg.fetch(query, user.id)):
            raise NotInDatabase(user)
        neural_net = ctx.bot.derivative_prefix_neural
        prefix, raw_data = await neural_net.predict(data, return_raw=True)
        instance = cls(user, prefix, raw_data)
        if not instance.prefix:
            raise commands.CommandError(
                f"Seems like I'm unable to determine the prefix confidently. Please continue to use "
                f"`{user}` for more data."
            )
        return instance


class AllPrefixes(ListPageSource):
    """Menu for allprefix command."""
    def __init__(self, data: List[BotPrefixes], count_mode: bool):
        super().__init__(data, per_page=6)
        self.count_mode = count_mode

    async def format_page(self, menu: InteractionPages, entries: List[BotPrefixes]) -> discord.Embed:
        key = "(\u200b|\u200b)"
        offset = menu.current_page * self.per_page
        content = "`{no}. {prefix} {key} {b.count}`" if self.count_mode else "`{no}. {b} {key} {prefix}`"
        contents = [content.format(no=i+1, b=b, key=key, prefix=pprefix(menu.ctx.guild, b.prefix)) for i, b in enumerate(entries, start=offset)]
        embed = StellaEmbed(title="All Prefixes",
                            description="\n".join(realign(contents, key)))
        return menu.generate_page(embed, self._max_pages)


class BotHandler(FindBotCog):
    @commands.command(aliases=["wp", "whatprefixes"],
                      brief="Shows the bot prefix.",
                      help="Shows what the bot's prefix. This is sometimes inaccurate. Don't rely on it too much. "
                           "This also does not know it's aliases prefixes.")
    @commands.guild_only()
    async def whatprefix(self, ctx: StellaContext, *, member: BotPrefixes):
        show_prefix = functools.partial(clean_prefix, ctx)
        prefix = show_prefix(member.prefix)
        alias = '`, `'.join(map(show_prefix, member.aliases))
        e = discord.Embed()
        e.add_field(name="Current", value=f"`{prefix}`")
        if member.aliases:
            e.add_field(name="Potential Aliases", value=f"`{alias}`")

        with contextlib.suppress(Exception):
            botcmds = await BotCommands.convert(ctx, str(member.bot.id))
            cmds = botcmds.commands[:3]
            highest = len(cmds)
            prefixes = random.sample(member.all_raw_prefixes, k=min(len(member.all_raw_prefixes), highest))
            gen = zip(itertools.cycle(map(show_prefix, [member.prefix, *prefixes])),["<command>", *cmds])
            e.add_field(name="Example Usage", value="\n".join(f"`{prefix}{cmd}`" for prefix, cmd in gen), inline=False)

        e.set_thumbnail(url=member.bot.display_avatar)
        await ctx.embed(title=f"{member}'s Prefix", embed=e)

    @commands.command(aliases=["pu", "shares", "puse"],
                      brief="Shows the amount of bot that uses the same prefix.",
                      help="Shows the number of bot that shares a prefix between bots.")
    @commands.guild_only()
    async def prefixuse(self, ctx: StellaContext, prefix: str):
        instance_bot = await self.get_all_prefix(ctx, prefix)
        prefix = clean_prefix(ctx, prefix)
        desk = plural(f"There (is/are) `{len(instance_bot)}` bot(s) that use `{prefix}` as prefix", len(instance_bot))
        await ctx.embed(description=desk)

    async def get_all_prefix(self, ctx: StellaContext, prefix: str) -> List[discord.Member]:
        """Quick function that gets the amount of bots that has the same prefix in a server."""
        sql = "SELECT * FROM prefixes_list WHERE guild_id=$1 AND prefix=$2"
        data = await self.bot.pool_pg.fetch(sql, ctx.guild.id, prefix)

        def mem(x):
            return ctx.guild.get_member(x)

        bot_list = []
        for each in [mem(x['bot_id']) for x in data if mem(x['bot_id'])]:
            bot = await BotPrefixes.convert(ctx, f"{each.id}")
            if prefix in bot.all_raw_prefixes:
                bot_list.append(bot)

        return bot_list

    @commands.command(aliases=["prefixbots", "pbots"],
                      brief="Shows the name of bot(s) have a given prefix.",
                      help="Shows a list of bot(s) name that have a given prefix.")
    @commands.guild_only()
    async def prefixbot(self, ctx: StellaContext, prefix: str):
        instance_bot = await self.get_all_prefix(ctx, prefix)
        list_bot = "\n".join(f"`{no + 1}. {x}`" for no, x in enumerate(instance_bot)) or "`Not a single bot have it.`"
        prefix = clean_prefix(ctx, prefix)
        desk = f"Bot(s) with `{prefix}` as prefix\n{list_bot}"
        await ctx.embed(description=plural(desk, len(list_bot)))

    @commands.command(aliases=["bot_use", "bu", "botusage", "botuses"],
                      brief="Show's how many command calls for a bot.",
                      help="Show's how many command calls for a given bot. This works by counting how many times "
                           "a message is considered a command for that bot where that bot has responded in less than "
                           "2 seconds.")
    async def botuse(self, ctx: StellaContext, *, bot: BotCommands):
        await ctx.embed(
            title=f"{bot}'s Usage",
            description=plural(f"`{bot.total_usage}` command(s) has been called for **{bot}**.", bot.total_usage)
        )

    @commands.command(aliases=["bot_info", "bi", "botinfos"],
                      brief="Shows the bot information such as bot owner, prefixes, command usage.",
                      help="Shows the bot information such as bot owner, it's prefixes, the amount of command it has "
                           "been called, the reason on why it was added, the time it was requested and the time it "
                           "joined the server.")
    @is_discordpy()
    async def botinfo(self, ctx: StellaContext, *, bot: IsBot):
        embed = await self.format_bot_info(ctx, bot)
        kwargs = dict(embed=embed)
        async with ctx.breaktyping(limit=60):
            if file := await self.create_bar(ctx, bot):
                embed.set_image(url="attachment://picture.png")
                kwargs.update(dict(file=file))

        await ctx.embed(**kwargs)

    async def create_bar(self, ctx: StellaContext, bot: Union[discord.Member, discord.User]) -> Optional[discord.File]:
        query = 'SELECT command, COUNT(command) "usage" ' \
                'FROM commands_list ' \
                'WHERE bot_id=$1 AND guild_id=$2 ' \
                'GROUP BY command ' \
                'ORDER BY usage DESC ' \
                'LIMIT 5'

        data = await self.bot.pool_pg.fetch(query, bot.id, ctx.guild.id)
        if not data:
            return

        data.reverse()
        names = [v["command"] for v in data]
        usages = [v["usage"] for v in data]
        payload = dict(title=f"Top {len(names)} commands for {bot}",
                       xlabel="Usage",
                       ylabel="Commands")

        asset = bot.display_avatar
        avatar_bytes = io.BytesIO(await asset.read())
        color = major = await get_majority_color(avatar_bytes)
        if not islight(*major.to_rgb()) or bot == ctx.me:
            color = discord.Color(ctx.bot.color)

        bar = await create_bar(names, usages, str(color), **payload)
        to_send = await process_image(avatar_bytes, bar)
        return discord.File(to_send, filename="picture.png")

    async def format_bot_info(self, ctx, bot: Union[discord.Member, discord.User]) -> discord.Embed:
        embed = StellaEmbed.default(ctx, title=str(bot))
        bot_id = str(bot.id)
        embed.add_field(name="ID", value=f"`{bot_id}`")

        async def handle_convert(converter: Type[T]) -> Optional[T]:
            with contextlib.suppress(Exception):
                return await converter.convert(ctx, bot_id)

        if val := await handle_convert(BotAdded):
            reason = textwrap.shorten(val.reason, width=1000, placeholder='...')
            embed.add_field(name="Bot Invited By", value=val.author)
            if value := val.requested_at:
                embed.add_field(name="Requested at", value=aware_utc(value, mode='f'))
            embed.add_field(name="Reason", value=reason, inline=False)

        if val := await handle_convert(BotPrefixes):
            allprefixes = ", ".join(map("`{}`".format, [clean_prefix(ctx, v) for v in val.all_raw_prefixes]))
            embed.add_field(name="Bot Prefix", value=allprefixes)

        if val := await handle_convert(BotCommands):
            embed.add_field(name="Command Usage", value=f"{val.total_usage:,}")
            high_command = val.highest_command
            high_amount = len(val.command_usages.get(high_command))
            embed.add_field(name="Top Command", value=f"{high_command}(`{high_amount:,}`)")

        if val := await handle_convert(BotRepo):
            repo = val.repo
            embed.add_field(name="Bot Repository", value=f"[Source]({repo.html_url})")
            with contextlib.suppress(Exception):
                author = await self.bot.git.get_user(repo.owner.login)
                embed.set_author(name=f"Repository by {author.name}", icon_url=author.display_avatar)
            embed.add_field(name="Written in", value=f"{repo.language}")

        embed.set_thumbnail(url=bot.display_avatar)
        if date := getattr(bot, "joined_at", None):
            embed.add_field(name="Joined at", value=f"{aware_utc(date, mode='f')}")

        return embed.add_field(name="Created at", value=f"{aware_utc(bot.created_at, mode='f')}")

    @commands.group(name="bot",
                    help="A group command that are related to all the bot that is stored in my database.")
    @commands.guild_only()
    @is_discordpy()
    async def _bot(self, ctx: StellaContext):
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @_bot.command(cls=greedy_parser.GreedyParser,
                  aliases=["ci", "changeinfos"],
                  brief="Allows you to change your own bot's information in whoadd/whatadd command.",
                  help="Allows you to change your own bot's information  in whoadd/whatadd command, "
                       "only applicable for discord.py server. The user is only allowed to change their own bot, "
                       "which they are able to change 'requested', 'reason' and 'jump url' values.")
    async def changeinfo(self, ctx: StellaContext, bot: greedy_parser.UntilFlag[BotOwner], *, flags: flg.InfoFlag):
        bot = bot.bot
        new_data = {'bot_id': bot.id}
        flags = dict(flags)
        if not any(flags.values()):
            ctx.current_parameter = [*self.changeinfo.params.values()][-1]
            raise commands.CommandError("No value were passed, at least put a flag."
                                        f" Type {ctx.prefix}help {ctx.invoked_with} for more information.")
        if message := flags.pop('message'):
            new_data['reason'] = message.content
            new_data['requested_at'] = message.created_at.replace(tzinfo=None)
            new_data['jump_url'] = message.jump_url

        if len(new_data.get('reason', "")) > 1000:
            raise commands.CommandError("Reason cannot exceed 1000 character, because I'm lazy.")
        for flag, item in flags.items():
            if item:
                new_data.update({flag: item})

        bot_exist = await self.bot.pool_pg.fetchrow("SELECT * FROM confirmed_bots WHERE bot_id=$1", bot.id)
        existing_data = dict(bot_exist)
        new_changes = set()
        for key, before in existing_data.items():
            if (now := new_data.get(key)) and now != before:
                new_changes.add(key)

        if not new_changes:
            raise commands.CommandError("No data changes, wth")

        existing_data.update(new_data)
        query = "UPDATE confirmed_bots SET "
        queries = [f"{k}=${i}" for i, k in enumerate(list(existing_data)[1:], start=2)]
        query += ", ".join(queries)
        query += " WHERE bot_id=$1"
        values = [*existing_data.values()]
        await self.bot.pool_pg.execute(query, *values)
        changes = []
        for topic, value in new_data.items():
            if value and topic in new_changes:
                changes.append((topic, f"**Before:**\n{bot_exist.get(topic)}\n**After**:\n {value}"))
        await ctx.embed(title=f"{bot} Information Changes", fields=changes)

    @_bot.command(help="View raw prefix that is stored on a bot for bot owners")
    async def viewprefix(self, ctx: StellaContext, *, bot: BotOwner):
        query = "SELECT * FROM prefixes_list WHERE bot_id=$1 AND guild_id=$2"
        raw_prefixes = await self.bot.pool_pg.fetch(query, bot.bot.id, ctx.guild.id)

        @pages(per_page=10)
        async def show_result(_, menu: menus.MenuPages, entry: List[Dict[str, str]]) -> discord.Embed:
            to_show = "\n".join(f"{i}. `{x['prefix']}`" for i, x in enumerate(entry, start=menu.current_page * 10 + 1))
            return discord.Embed(title=f"{bot}'s raw prefixes", description=to_show)

        await InteractionPages(show_result(raw_prefixes)).start(ctx)

    @_bot.command(help="Removes prefix that is stored on a specific bot for bot owners")
    async def delprefix(self, ctx: StellaContext, bot: BotOwner, *prefixes: str):
        query = "DELETE FROM prefixes_list WHERE guild_id=$1 AND bot_id=$2 AND prefix=$3"
        unique_prefixes = set(prefixes)
        await self.bot.pool_pg.executemany(query, [(ctx.guild.id, bot.bot.id, x) for x in unique_prefixes])
        await ctx.confirmed()

    @_bot.command(help="Add prefixes into a specific bot for bot owners")
    async def addprefix(self, ctx: StellaContext, bot: BotOwner, *prefixes: str):
        query = "INSERT INTO prefixes_list VALUES ($1, $2, $3, $4, $5)"
        unique_prefixes = set(prefixes)
        guild_id, bot_id = ctx.guild.id, bot.bot.id
        current_prefixes = await self.bot.pool_pg.fetch("SELECT * FROM prefixes_list WHERE guild_id=$1 AND bot_id=$2", guild_id, bot_id)
        max_usage = max([p['usage'] for p in current_prefixes] or [1])
        values = [(guild_id, bot_id, x, max_usage, datetime.datetime.utcnow()) for x in unique_prefixes]
        await self.bot.pool_pg.executemany(query, values)
        await ctx.maybe_reply(f"Successfully inserted `{'` `'.join(unique_prefixes)}`")
        await ctx.confirmed()

    @_bot.command(help="Manual insert of github's owner repository", aliases=["changegithubs", "cgithub"])
    async def changegithub(self, ctx: StellaContext, bot: BotOwner, *, github_link: BotGitHubLink):
        bot_id = bot.bot.id
        sql = "INSERT INTO bot_repo VALUES($1, $2, $3, $4) " \
              "ON CONFLICT (bot_id) DO UPDATE SET owner_repo=$2, bot_name=$3, certainty=$4"

        values = (bot_id, github_link.repo_owner, github_link.repo_name, 100)
        await self.bot.pool_pg.execute(sql, *values)
        await ctx.confirmed()

    @commands.Cog.listener('on_message')
    @event_check(lambda _, m: m.author.bot)
    async def is_bot_triggered(self, message: discord.Message):
        def resolve_message(m: discord.Message) -> Optional[discord.Message]:
            if m.reference:
                caught = m.reference.resolved
                if isinstance(caught, discord.DeletedReferencedMessage) or caught is None:
                    return
                return caught

            return discord.utils.get(reversed(self.bot.cached_messages), author__bot=False, channel__id=m.channel.id)

        if not (triggering := resolve_message(message)):
            return

        no_newline, *_ = triggering.content.partition('\n')
        processed = textwrap.shorten(no_newline, width=30, placeholder="")
        if not processed:
            return

        bot_id = message.author.id
        values = [(bot_id, x.lower(), i, 1) for i, x in enumerate(processed)]
        sql = "INSERT INTO position_letter VALUES($1, $2, $3, $4) " \
              "ON CONFLICT(bot_id, letter, position) DO " \
              "UPDATE SET count = position_letter.count + 1"

        await self.bot.pool_pg.executemany(sql, values)

    @commands.command(aliases=["bpd"], help="Uses neural network to predict a bot's prefix.")
    async def botpredict(self, ctx: StellaContext, *, bot: BotPredictPrefixes):
        content = [f"`{discord.utils.escape_markdown(letter)}`: **{predict * 100:.2f}%**"
                   for letter, predict in bot.raw_data if predict > .4]
        summation = sum([p for _, p in bot.raw_data if p >= .5])
        evaluated = "\n".join(content)
        desc = f'**Prefix: ** "{bot.prefix}"\n' \
               f'**Evaluation: **\n{evaluated}\n' \
               f'**Overall Confidence: ** `{summation / len(bot.prefix) * 100:.2f}%`'
        await ctx.embed(title=f"Predicted Prefix for '{bot.bot}'", description=desc)

    @commands.command(aliases=["ab"], help="Shows the list of all bots in discord.py server and information.")
    @is_discordpy()
    async def allbots(self, ctx: StellaContext):
        command = self.allbots
        bots = [m for m in ctx.guild.members if m.bot]
        bots.sort(key=operator.attrgetter("id"))

        class CacheListPageSource(ListPageSource):
            def __init__(self, *args, formatter):
                super().__init__(*args, per_page=1)
                self.bot_cache = {}
                self.formatter = formatter
                self.current_bot = None
                self.current_embed = None

            async def format_page(self, menu_inter: InteractionPages, entry: discord.Member) -> discord.Embed:
                self.current_bot = entry
                if not (embed := self.bot_cache.get(entry)):
                    embed = await self.formatter(ctx, entry)

                self.current_embed = embed
                return embed

        class InteractionBots(InteractionPages):
            def __init__(self, cog, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.cog = cog
                self.url_store = {}

            class BotPrompter(PromptView):
                def __init__(self, *args, set_bots, timeout, **kwargs):
                    super().__init__(*args, timeout=timeout or 60, delete_after=True,
                                     message_error="I'm still waiting for a bot for you to mention, you can't run "
                                                   "another command.",
                                     **kwargs)
                    self.set_bots = set_bots
                    self.user = None

                def invalid_response(self) -> str:
                    return f"Bot is not in the server."

                async def message_respond(self, message: discord.Message) -> bool:
                    value = message.content
                    try:
                        user = await IsBot().convert(ctx, value)
                        self.user = user
                    except Exception as e:
                        await command.dispatch_error(ctx, e)
                    else:
                        return user.id in self.set_bots

            @ui.button(label="Select Bot")
            async def select_bot(self, interaction: discord.Interaction, _: ui.Button):
                await interaction.response.edit_message(view=None)
                prompt_timeout = 60
                # Ensures the winteractionpages doesn't get remove after timeout
                self.set_timeout(time.monotonic() + self.timeout + prompt_timeout)
                set_bots = set([b.id for b in bots])
                prompt = self.BotPrompter(self.ctx, set_bots=set_bots, timeout=prompt_timeout)
                content = "Mention a bot."
                value = self.current_page
                try:
                    respond = await prompt.send(content, reference=ctx.message.to_reference())
                    if isinstance(respond, discord.Message):  # Handles both timeout and False
                        value = bots.index(prompt.user)
                except Exception as e:
                    await self.ctx.reply(f"Something went wrong. {e}")
                finally:
                    await self.show_checked_page(value)
                    self.reset_timeout()

            @ui.button(label="Generate Bar")
            async def generate_bar(self, interaction: discord.Interaction, _: ui.Button):
                embed = self._source.current_embed
                bot = self._source.current_bot
                if url := self.url_store.get(bot.id):
                    embed.set_image(url=url)
                elif file := await self.cog.create_bar(self.ctx, bot):
                    base = base64.b64encode(file.fp.read()).decode('utf-8')
                    url = await self.cog.bot.ipc_client.request('upload_file', base64=base, filename=file.filename)
                    embed.set_image(url=url)
                    self.url_store.update({bot.id: url})
                else:
                    await interaction.response.send_message("No Command data for this bot.", ephemeral=True)
                    return
                await interaction.response.edit_message(embed=embed)

        menu = InteractionBots(self, CacheListPageSource(bots, formatter=self.format_bot_info), generate_page=True)
        await menu.start(ctx)

    @commands.command(aliases=["pp", "predictprefixes"], help="Shows how likely a prefix is valid for a bot.")
    async def predictprefix(self, ctx: StellaContext, *, bot: discord.Member = commands.param(converter=IsBot)):
        data = await self.bot.pool_pg.fetch("SELECT * FROM prefixes_list WHERE bot_id=$1", bot.id)
        if not data:
            raise commands.CommandError("Looks like i have no data to analyse sry.")

        dataset = [[d['prefix'], d['usage'], d['last_usage'].timestamp()] for d in data]
        array = await self.bot.get_prefixes_dataset(dataset)
        pairs = [(p, float(c)) for p, _, _, c in array]
        pairs.sort(key=lambda x: x[1], reverse=True)
        size = min(len(pairs), 5)
        prefixes = "\n".join([f'`{clean_prefix(ctx, p)}`: **{c:.2f}%**' for p, c in itertools.islice(pairs, size)])
        await ctx.embed(
            title=f"Top {size} {bot}'s prefixes",
            description=prefixes
        )
