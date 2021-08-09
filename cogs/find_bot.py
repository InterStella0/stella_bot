from __future__ import annotations
import discord
import datetime
import re
import asyncio
import itertools
import ctypes
import contextlib
import functools
import collections
import textwrap
import operator
from dataclasses import dataclass
from discord.ext import commands
from discord.ext.commands import UserNotFound
from discord.ext import menus
from discord.ext.menus import ListPageSource
from aiogithub.objects import Repo
from fuzzywuzzy import fuzz
from utils import flags as flg
from utils.new_converters import BotPrefixes, IsBot, BotCommands
from utils.buttons import InteractionPages
from utils.useful import try_call, BaseEmbed, compile_array, search_prefixes, default_date, plural, realign, search_commands, StellaContext, aware_utc
from utils.errors import NotInDatabase, BotNotFound
from utils.decorators import is_discordpy, event_check, wait_ready, pages, listen_for_guilds
from utils import greedy_parser
from typing import Any, Optional, Union, List, Tuple, Callable, Dict, Coroutine, TYPE_CHECKING


if TYPE_CHECKING:
    from main import StellaBot

ReactRespond = collections.namedtuple("ReactRespond", "created_at author reference")
DISCORD_PY = 336642139381301249


@dataclass
class BotRepo:
    bot: discord.User = None
    repo: Repo = None

    @classmethod
    async def from_db(cls, stellabot, bot, data):
        repo = await stellabot.git.get_repo(data["owner_repo"], data["bot_name"])
        return cls(bot=bot, repo=repo)

    @classmethod
    async def convert(cls, ctx: StellaContext, argument: str) -> BotRepo:
        if user := await IsBot().convert(ctx, argument):
            data = await ctx.bot.pool_pg.fetchrow("SELECT * FROM bot_repo WHERE bot_id=$1", user.id)
            if data:
                return await cls.from_db(ctx.bot, user, data)
        raise NotInDatabase(user)

    def __str__(self) -> str:
        return str(self.bot)


@dataclass
class BotGitHubLink:
    repo_owner: str = None
    repo_name: str = None

    @classmethod
    async def convert(cls, ctx: StellaContext, argument: str) -> BotGitHubLink:
        find = ctx.bot.get_cog("Bots")
        regex = find.re_github
        if found := regex.search(argument):
            repo_owner = found['repo_owner']
            repo_bot = found['repo_name']
            content = f"**Owner repository:** `{repo_owner}`\n**Bot repository:** `{repo_bot}`\n\n **Is this correct?**"
            if not await ctx.confirmation(content, delete_after=True):
                raise commands.CommandNotFound()
            return cls(repo_owner=repo_owner, repo_name=repo_bot)
        raise commands.CommandError("Unable to resolve repository owner and repository bot")


@dataclass
class BotAdded:
    """BotAdded information for discord.py that is used in whoadd and whatadd command."""
    author: discord.Member = None
    bot: discord.Member = None
    reason: str = None
    requested_at: datetime.datetime = None
    jump_url: str = None
    joined_at: datetime.datetime = None

    @classmethod
    def from_json(cls, bot: Optional[Union[discord.Member, discord.User]] = None, *, bot_id: Optional[int] = None,
                  **data: Union[discord.Member, datetime.datetime, str]) -> BotAdded:
        """factory method on data from a dictionary like object into BotAdded."""
        author = data.pop("author_id", None)
        join = data.pop("joined_at", None)
        bot = bot or bot_id
        if isinstance(bot, discord.Member):
            join = bot.joined_at
            author = bot.guild.get_member(author) or author
        if join is not None:
            join = join.replace(tzinfo=None)
        return cls(author=author, bot=bot, joined_at=join, **data)

    @classmethod
    async def convert(cls, ctx: StellaContext, argument: str) -> BotAdded:
        """Invokes when the BotAdded is use as a typehint."""
        with contextlib.suppress(commands.BadArgument):
            if user := await IsBot().convert(ctx, argument):
                for attribute in ("pending", "confirmed")[isinstance(user, discord.Member):]:
                    attribute += "_bots"
                    if user.id in getattr(ctx.bot, attribute):
                        data = await ctx.bot.pool_pg.fetchrow(f"SELECT * FROM {attribute} WHERE bot_id = $1", user.id)
                        return cls.from_json(user, **data)
                raise NotInDatabase(user)
        raise BotNotFound(argument)

    def __str__(self) -> str:
        return str(self.bot or "")


class BotOwner(BotAdded):
    """Raises an error if the bot does not belong to the context author"""
    @classmethod
    async def convert(cls, ctx: StellaContext, argument: str) -> BotOwner:
        botdata = await super().convert(ctx, argument)
        if botdata.author != ctx.author:
            raise commands.CommandError(f"Sorry you can only change your own bot's information. This bot belongs to {botdata.author}.")
        
        if not ctx.guild.get_member(botdata.bot.id):
            raise commands.CommandError("This bot must be in the server.")
        return botdata


class BotPending(BotAdded):
    @classmethod
    async def convert(cls, ctx: StellaContext, argument: str) -> BotPending:
        botdata = await super().convert(ctx, argument)
        if isinstance(botdata.bot, discord.Member):
            suggest = ctx.bot.get_command_signature(ctx, "botinfo")
            raise commands.CommandError(f"Sorry `{botdata.bot}` is already in the server. Use `{suggest}` instead.")

        return botdata


class BotPendingFlag(commands.FlagConverter):
    reverse: Optional[bool] = flg.flag(aliases=["reverses"],
                                       help="Reverse the list order, this is False by default.", default=False)
    bot: Optional[BotPending] = flg.flag(aliases=['user'],
                                         help="Allow execution of repl, defaults to True, unless a non owner.")


def pprefix(bot_guild: Union[StellaBot, discord.Guild], prefix: str) -> str:
    if content := re.search("<@(!?)(?P<id>[0-9]*)>", prefix):
        method = getattr(bot_guild, ("get_user", "get_member")[isinstance(bot_guild, discord.Guild)])
        if user := method(int(content["id"])):
            return f"@{user.display_name} "
    return prefix


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
        embed = BaseEmbed(title="All Prefixes",
                          description="\n".join(realign(contents, key)))
        return menu.generate_page(embed, self._max_pages)


@pages(per_page=10)
async def all_bot_count(self, menu: InteractionPages, entries: List[BotCommands]) -> discord.Embed:
    """Menu for botrank command."""
    key = "(\u200b|\u200b)"
    offset = menu.current_page * self.per_page
    content = "`{no}. {b} {key} {b.total_usage}`"
    contents = [content.format(no=i+1, b=b, key=key) for i, b in enumerate(entries, start=offset)]
    return BaseEmbed(title="Bot Command Rank",
                     description="\n".join(realign(contents, key)))


@pages(per_page=6)
async def bot_added_list(self, menu: InteractionPages, entries: List[BotAdded]) -> discord.Embed:
    """Menu for recentbotadd command."""
    offset = menu.current_page * self.per_page
    contents = ((f"{b.author}", f'**{b}** `{discord.utils.format_dt(b.joined_at, "R")}`')
                for i, b in enumerate(entries, start=offset))
    return BaseEmbed(title="Bots added today", fields=contents)


@pages()
async def bot_pending_list(self, menu: InteractionPages, entry: Dict[str, Union[datetime.datetime, int, str]]) -> discord.Embed:
    stellabot = menu.ctx.bot
    bot_id = entry["bot_id"]
    if not (bot := menu.cached_bots.get(bot_id)):
        bot = stellabot.get_user(bot_id) or await stellabot.fetch_user(bot_id)
        menu.cached_bots.update({bot_id: bot})
    fields = (("Requested by", stellabot.get_user(entry["author_id"]) or "idk really"),
              ("Reason", textwrap.shorten(entry["reason"], width=1000, placeholder="...")),
              ("Created at", aware_utc(bot.created_at)),
              ("Requested at", aware_utc(entry["requested_at"])),
              ("Message", f"[jump]({entry['jump_url']})"))
    embed = BaseEmbed(title=f"{bot}(`{bot.id}`)", fields=fields)
    embed.set_thumbnail(url=bot.avatar.url)
    return embed


deco_event = Callable[[Callable], Callable]


def is_user() -> deco_event:
    """Event check for returning true if it's a bot."""
    return event_check(lambda _, m: not m.author.bot)


def prefix_cache_ready() -> deco_event:
    """Event check for command_count"""
    def predicate(self, message: discord.Message) -> bool:
        return self.compiled_prefixes and self.compiled_commands and not message.author.bot
    return event_check(predicate)


def dpy_bot() -> deco_event:
    """Event check for dpy_bots"""
    return event_check(lambda _, member: member.bot and member.guild.id == DISCORD_PY)


class FindBot(commands.Cog, name="Bots"):
    """Most bot related commands"""
    def __init__(self, bot: StellaBot):
        self.bot = bot
        valid_prefix = ("!", "?", "？", "<@(!?)80528701850124288> ")
        re_command = "(\{}|\{}|\{}|({}))addbot".format(*valid_prefix)
        re_bot = "[\s|\n]+(?P<id>[0-9]{17,19})[\s|\n]"
        re_reason = "+(?P<reason>.[\s\S\r]+)"
        self.re_addbot = re_command + re_bot + re_reason
        self.re_github = re.compile(r'https?://(?:www\.)?github.com/(?P<repo_owner>[^/ \'"]+)/(?P<repo_name>[^/ \'">]+)?')
        self.cached_bots = {}
        self.compiled_prefixes = None
        self.compiled_commands = None
        self.all_bot_prefixes = {}
        self.all_bot_commands = {}
        bot.loop.create_task(self.loading_all_prefixes())

    async def loading_all_prefixes(self) -> None:
        """Loads all unique prefix when it loads and set compiled_pref for C code."""
        await self.bot.wait_until_ready()
        prefix_data = await self.bot.pool_pg.fetch("SELECT DISTINCT bot_id, prefix FROM prefixes_list")
        commands_data = await self.bot.pool_pg.fetch("SELECT DISTINCT bot_id, command FROM commands_list")
        for prefix, command in itertools.zip_longest(prefix_data, commands_data):
            if prefix:
                prefixes = self.all_bot_prefixes.setdefault(prefix["bot_id"], set())
                prefixes.add(prefix["prefix"])
            if command:
                commands = self.all_bot_commands.setdefault(command["bot_id"], set())
                commands.add(command["command"])
        self.update_compile()

    def update_compile(self) -> None:
        temp = [*{prefix for prefixes in self.all_bot_prefixes.values() for prefix in prefixes}]
        cmds = [*{command for commands in self.all_bot_commands.values() for command in commands}]
        self.compiled_prefixes = compile_array(sorted(temp))
        self.compiled_commands = compile_array(sorted(x[::-1] for x in cmds))

    @commands.Cog.listener("on_member_join")
    @wait_ready()
    @dpy_bot()
    async def join_bot_tracker(self, member: discord.Member):
        """Tracks when a bot joins in discord.py where it logs all the BotAdded information."""
        if member.id in self.bot.pending_bots:
            data = await self.bot.pool_pg.fetchrow("SELECT * FROM pending_bots WHERE bot_id = $1", member.id)
            await self.update_confirm(BotAdded.from_json(member, **data))
            await self.bot.pool_pg.execute("DELETE FROM pending_bots WHERE bot_id = $1", member.id)
        else:
            await self.update_confirm(BotAdded.from_json(member, joined_at=member.joined_at.replace(tzinfo=None)))

    async def listen_for_bots_at(self, message: discord.Message, message_check: Callable[[discord.Message], bool]) -> \
            Tuple[Dict[int, Union[discord.Message, ReactRespond]], Dict[int, Union[discord.Message, ReactRespond]]]:
        """Listens for bots responding and terminating when a user respond"""
        bots = {}
        after_user = {}
        time_to_listen = message.created_at + datetime.timedelta(seconds=5)
        flip = 0

        def reaction_add_check(reaction: discord.Reaction, user: discord.User) -> bool:
            return reaction.message == message

        stuff_here = locals()
        with contextlib.suppress(asyncio.TimeoutError):
            while time_to_listen > (time_rn := discord.utils.utcnow()):
                time_left = (time_to_listen - time_rn).total_seconds()
                done, didnt = await asyncio.wait(
                    [self.bot.wait_for(event, check=stuff_here[f"{event}_check"], timeout=time_left) 
                     for event in ("reaction_add", "message")]
                    )
                for coro in done:
                    coro.exception()

                responded = done.pop().result()
                if isinstance(responded, tuple):
                    responded = ReactRespond(datetime.datetime.utcnow(), responded[1], None)

                for coro in didnt:
                    coro.cancel()

                if any(responded.author.id in respondance for respondance in (bots, after_user)):
                    continue
                flip |= not responded.author.bot
                if not responded.author.bot:
                    continue

                if not flip:
                    bots.update({responded.author.id: responded})
                elif getattr(responded.reference, "cached_message", None) == message:
                    after_user.update({responded.author.id: responded})

        return bots, after_user

    @commands.Cog.listener("on_member_remove")
    @wait_ready()
    @dpy_bot()
    async def remove_bot_tracker(self, member: discord.Member):
        """Since there is no reason to store these bots after they left, best to delete them"""
        if member.id in self.bot.confirmed_bots:
            await self.bot.pool_pg.execute("DELETE FROM confirmed_bots WHERE bot_id=$1", member.id)
            self.bot.confirmed_bots.remove(member.id)

    async def update_prefix_bot(self, message: discord.Message, func: Callable[[discord.Message], bool],
                                prefix: str, command: str) -> None:
        """Updates the prefix of a bot, or multiple bot where it waits for the bot to respond. It updates in the database."""
        def setting(inner):
            def check(msg):
                return msg.channel == message.channel and not msg.author.bot or inner(msg)
            return check

        message_sent, after = await self.listen_for_bots_at(message, setting(func))
        if not message_sent and not after:
            return

        message_sent.update(after)
        bots_responded = list(message_sent)
        # Possibility of duplication removal
        exist_query = "SELECT * FROM prefixes_list WHERE guild_id=$1 AND bot_id=ANY($2::BIGINT[])"
        existing = await self.bot.pool_pg.fetch(exist_query, message.guild.id, bots_responded)
        for x in existing:
            if prefix.startswith(x["prefix"]) and x["bot_id"] in bots_responded:
                message_sent.pop(x["bot_id"], None)

        if not message_sent:
            return

        prefixes = [(message.guild.id, x, prefix, 1, m.created_at.replace(tzinfo=None)) for x, m in message_sent.items()]
        commands = [(message.guild.id, x, command, m.created_at.replace(tzinfo=None)) for x, m in message_sent.items()]

        await self.insert_both_prefix_command(prefixes, commands)

        for _, x, prefix, _, _ in prefixes:
            prefixes = self.all_bot_prefixes.setdefault(x, set())
            prefixes.add(prefix)

        for _, bot, command, _ in commands:
            commands = self.all_bot_commands.setdefault(bot, set())
            commands.add(command)

        self.update_compile()

    @commands.Cog.listener("on_message")
    @wait_ready()
    @listen_for_guilds()
    @is_user()
    async def find_bot_prefixes(self, message: discord.Message):
        """This function is responsible for point of entry of the bot detection. All bot must went into here
           in order to be detected."""
        def check_jsk(m):
            possible_text = ("Jishaku", "discord.py", "Python ", "Module ", "guild(s)", "user(s).")
            return all(text in m.content for text in possible_text)

        def search(*text_list):
            def actual_search(search_text):
                return any(f"{t}" in search_text.casefold() for t in text_list)
            return actual_search

        def check_help(m):
            target = search("command", "help", "category", "categories")
            content = target(m.content)
            embeds = any(target(str(e.to_dict())) for e in m.embeds)
            return content or embeds

        def check_ping(m):
            target = search("ping", "ms", "pong", "latency", "websocket", "bot", "database")
            content = target(m.content)
            embeds = any(target(str(e.to_dict())) for e in m.embeds)
            return content or embeds

        for func in filter(lambda x: getattr(x, "__name__", "").startswith("check"), locals().values()):
            name = func.__name__.replace("check_", "")
            if match := re.match("(?P<prefix>^.{{1,30}}?(?={}$))".format(name), message.content, re.I):
                if name not in match["prefix"]:
                    return await self.update_prefix_bot(message, func, match["prefix"], name)

    async def search_respond(
            self,
            callback: Callable[[Tuple[ctypes.c_char_p, int], ctypes.c_char_p], Coroutine[Any, Any, List[str]]],
            message: discord.Message, word: str, type: str
    ) -> Optional[Tuple[filter, List[str], Dict[int, discord.Message]]]:
        """Gets the prefix/command that are in this message, gets the bot that responded
           and return them."""
        content_compiled = ctypes.create_string_buffer(word.encode("utf-8"))
        if not (result := await callback(getattr(self, f"compiled_{type}"), content_compiled)):
            return

        singular = type[:len(type) - ((type != "commands") + 1)]

        def check(msg):
            return msg.channel == message.channel

        bot_found, after = await self.listen_for_bots_at(message, check)
        if not bot_found and not after:
            return

        bot_found.update(after)
        bot_found_keys = list(bot_found)
        query = f"SELECT DISTINCT bot_id, {singular} FROM {type}_list " \
                f"WHERE guild_id=$1 AND bot_id=ANY($2::BIGINT[]) AND {singular}=ANY($3::VARCHAR[])"
        bots = await self.bot.pool_pg.fetch(query, message.guild.id, bot_found_keys, result)
        responded = filter(lambda x: x["bot_id"] in bot_found, bots)
        return responded, result, bot_found

    async def insert_both_prefix_command(self, prefixes: List[Union[int, str]], commands: List[Union[int, str]]) -> None:
        commands_query = "INSERT INTO commands_list VALUES($1, $2, $3, $4)"
        prefixes_query = "INSERT INTO prefixes_list VALUES($1, $2, $3, $4, $5) " \
                         "ON CONFLICT (guild_id, bot_id, prefix) DO " \
                         "UPDATE SET usage=prefixes_list.usage + 1, last_usage=$5"
        
        for type in "commands", "prefixes":
            await self.bot.pool_pg.executemany(locals()[f"{type}_query"], locals()[type])

    # @commands.Cog.listener("on_message")
    # @wait_ready()
    # @listen_for_guilds()
    # @prefix_cache_ready()
    # @is_user()
    async def find_bot_commands(self, message: discord.Message):
        """Get a prefix based on known command used.
           Disabled for now, as the derive detection is dumb."""
        word, _, _ = message.content.partition("\n")
        to_find = textwrap.shorten(word, width=100, placeholder="")
        if not (received := await self.search_respond(search_commands, message, to_find.casefold(), "commands")):
            return

        responded, result, message_sent = received
        prefixes_values = []
        commands_values = []
        exist_query = "SELECT * FROM prefixes_list WHERE guild_id=$1 AND bot_id=$2"
        for command, bot in itertools.product(result, responded):
            if bot["command"] == command:
                bot_id = bot['bot_id']
                message_respond = message_sent[bot_id].created_at.replace(tzinfo=None)
                target = re.escape(command)
                if (match := re.match("(?P<prefix>^.{{1,100}}?(?={}))".format(target), word, re.I)) and len(match["prefix"]) < 31:
                    existing = await self.bot.pool_pg.fetch(exist_query, message.guild.id, bot_id)
                    prefix = match["prefix"]
                    if any(x['prefix'] != prefix and prefix.startswith(x["prefix"]) for x in existing):
                        continue
                    prefixes_values.append((message.guild.id, bot_id, prefix, 1, message_respond))

                if message.content.casefold().startswith(command):
                    commands_values.append((message.guild.id, bot_id, command, message_respond))

        for _, bot, prefix, _, _ in prefixes_values:
            prefixes = self.all_bot_prefixes.setdefault(bot, set())
            prefixes.add(prefix)
        self.update_compile()

        await self.insert_both_prefix_command(prefixes_values, commands_values)

    @commands.Cog.listener("on_message")
    @wait_ready()
    @listen_for_guilds()
    @prefix_cache_ready()
    async def command_count(self, message: discord.Message):
        """
        Checks if the message contains a valid prefix, which will wait for the bot to respond to count that message
        as a command.
        """
        limit = min(len(message.content), 31)
        if not (received := await self.search_respond(search_prefixes, message, message.content[:limit], "prefixes")):
            return

        responded, result, message_sent = received
        commands_values = []
        prefixes_values = []
        for prefix, bot in itertools.product(result, responded):
            if bot["prefix"] == prefix:
                bot_id = bot['bot_id']
                message_respond = message_sent[bot_id].created_at.replace(tzinfo=None)
                prefixes_values.append((message.guild.id, bot_id, prefix, 1, message_respond))
                command = message.content[len(prefix):]
                word, _, _ = command.partition("\n")
                got_command, _, _ = word.partition(" ")
                if got_command:
                    commands_values.append((message.guild.id, bot_id, got_command, message_respond))

        for _, bot, command, _ in commands_values:
            commands = self.all_bot_commands.setdefault(bot, set())
            commands.add(command)
        self.update_compile()

        await self.insert_both_prefix_command(prefixes_values, commands_values)

    @commands.Cog.listener("on_message")
    @wait_ready()
    @event_check(lambda _, m: m.author.bot)
    async def is_it_bot_repo(self, message: discord.Message):
        def get_content(m: discord.Message) -> str:
            values = m.content
            if m.embeds:
                embed = m.embeds[0]
                values += " / " + str(embed.to_dict())
            return values

        content = get_content(message)
        bot = message.author
        potential = []
        for match in self.re_github.finditer(content):
            repo_name = match['repo_name']
            predicting_name = fuzz.ratio(repo_name, bot.name)
            predicting_display = fuzz.ratio(repo_name, bot.display_name)
            if (predict := max([predicting_display, predicting_name])) >= 50:
                potential.append((match, predict))

        if potential:
            match, predict = max(potential, key=operator.itemgetter(1))
            sql = "INSERT INTO bot_repo VALUES($1, $2, $3, $4) " \
                  "ON CONFLICT (bot_id) DO UPDATE SET owner_repo=$2, bot_name=$3, certainty=$4 " \
                  "WHERE bot_repo.certainty < $4"
            values = (bot.id, match["repo_owner"], match["repo_name"], predict)
            await self.bot.pool_pg.execute(sql, *values)

    @commands.Cog.listener("on_message")
    @wait_ready()
    @is_user()
    async def addbot_command_tracker(self, message: discord.Message):
        """Tracks ?addbot command. This is an exact copy of R. Danny code."""
        if message.channel.id not in (559455534965850142, 381963689470984203, 381963705686032394):
            return
        if result := await self.is_valid_addbot(message, check=True):
            confirm = False

            def terms_acceptance(msg):
                nonlocal confirm
                if msg.author.id != message.author.id:
                    return False
                if msg.channel.id != message.channel.id:
                    return False
                if msg.content in ('**I agree**', 'I agree'):
                    confirm = True
                    return True
                elif msg.content in ('**Abort**', 'Abort'):
                    return True
                return False

            try:
                await self.bot.wait_for("message", check=terms_acceptance, timeout=60)
            except asyncio.TimeoutError:
                return

            if not confirm:
                return
            await self.update_pending(result)

    async def check_author(self, bot_id: int, author_id: int, mode: str) -> Optional[bool]:
        """Checks if the author of a bot is the same as what is stored in the database."""
        if data := await self.bot.pool_pg.fetchrow(f"SELECT * FROM {mode} WHERE bot_id=$1", bot_id):
            old_author = data['author_id']
            return old_author == author_id

    async def is_valid_addbot(self, message: discord.Message, check: Optional[bool] = False) -> Optional[BotAdded]:
        """Check if a message is a valid ?addbot command."""
        if result := re.match(self.re_addbot, message.content):
            reason = result["reason"]
            get_member = message.guild.get_member
            if not check:
                member = get_member(int(result["id"]))
                six_days = datetime.datetime.utcnow() - datetime.timedelta(days=6)
                if not member and message.created_at.replace(tzinfo=None) > six_days:
                    member = await try_call(self.bot.fetch_user, int(result["id"]), exception=discord.NotFound)
                    if all((reason, member and member.bot and str(member.id) not in self.bot.pending_bots)):
                        if str(member.id) not in self.bot.confirmed_bots:
                            await self.update_pending(
                                BotAdded(author=message.author,
                                         bot=member,
                                         reason=reason,
                                         requested_at=message.created_at.replace(tzinfo=None),
                                         jump_url=message.jump_url))
                        return

            else:
                if member := get_member(int(result["id"])):
                    if int(result["id"]) not in self.bot.confirmed_bots and \
                            await self.check_author(member.id, message.author.id, "confirmed_bots"):
                        newAddBot = BotAdded(author=message.author,
                                             bot=member,
                                             reason=reason,
                                             requested_at=message.created_at.replace(tzinfo=None),
                                             jump_url=message.jump_url,
                                             joined_at=member.joined_at.replace(tzinfo=None))
                        await self.update_confirm(newAddBot)
                    return
                member = await try_call(self.bot.fetch_user, int(result["id"]), exception=discord.NotFound)
            if all((reason, member and member.bot)):
                join = None
                if isinstance(member, discord.Member):
                    join = member.joined_at
                    if join < message.created_at:
                        return
                return BotAdded(author=message.author,
                                bot=member,
                                reason=reason,
                                requested_at=message.created_at.replace(tzinfo=None),
                                jump_url=message.jump_url,
                                joined_at=join)

    async def update_pending(self, result: BotAdded) -> None:
        """Insert a new addbot request which is yet to enter the discord.py server."""
        query = """INSERT INTO pending_bots VALUES($1, $2, $3, $4, $5) 
                   ON CONFLICT (bot_id) DO
                   UPDATE SET reason = $3, requested_at=$4, jump_url=$5"""
        value = (result.bot.id, result.author.id, result.reason, result.requested_at, result.jump_url)
        await self.bot.pool_pg.execute(query, *value)
        if result.bot.id not in self.bot.pending_bots:
            self.bot.pending_bots.add(result.bot.id)

    async def update_confirm(self, result: BotAdded) -> None:
        """Inserts a new confirmed bot with an author where the bot is actually in the discord.py server."""
        query = """INSERT INTO confirmed_bots VALUES($1, $2, $3, $4, $5, $6) 
                   ON CONFLICT (bot_id) DO
                   UPDATE SET reason = $3, requested_at=$4, jump_url=$5, joined_at=$6"""
        if not result.author:
            return self.bot.pending_bots.remove(result.bot.id)

        author_id = getattr(result.author, "id", result.author)
        value = (result.bot.id, author_id, result.reason, result.requested_at, result.jump_url, result.joined_at)
        await self.bot.pool_pg.execute(query, *value)
        if result.bot.id in self.bot.pending_bots:
            self.bot.pending_bots.remove(result.bot.id)
        self.bot.confirmed_bots.add(result.bot.id)

    @commands.command(aliases=["owns", "userowns", "whatadds", "whatadded"],
                      brief="Shows what bot the user owns in discord.py.",
                      help="Shows the name of the bot that the user has added in discord.py. "
                           "This is useful for flexing for no reason."
                      )
    @is_discordpy()
    async def whatadd(self, ctx: StellaContext, *, author: IsBot(is_bot=False, user_check=False) = None):
        author = author or ctx.author
        if author.bot:
            return await ctx.maybe_reply("That's a bot lol")
        query = "SELECT * FROM {}_bots WHERE author_id=$1"
        total_list = [await self.bot.pool_pg.fetch(query.format(x), author.id) for x in ("pending", "confirmed")]
        total_list = itertools.chain.from_iterable(total_list)

        async def get_member(b_id: int) -> Union[discord.Member, discord.User]:
            return ctx.guild.get_member(b_id) or await self.bot.fetch_user(b_id)
        list_bots = [BotAdded.from_json(await get_member(x["bot_id"]), **x) for x in total_list]
        embed = BaseEmbed.default(ctx, title=plural(f"{author}'s bot(s)", len(list_bots)))
        for dbot in list_bots:
            bot_id = dbot.bot.id
            value = ""
            if bprefix := await try_call(BotPrefixes.convert, ctx, str(bot_id)):
                value += f"**Most Used Prefix:** `{self.clean_prefix(ctx, bprefix.prefix)}`\n"
            if buse := await try_call(BotCommands.convert, ctx, str(bot_id)):
                high_use = buse.highest_command
                value += f"**Top Command:** `{high_use}`[`{buse.get_command(high_use)}`]\n"
                value += f"**Total Usage:** `{buse.total_usage}`\n"

            value += f"**Created at:** `{default_date(dbot.bot.created_at)}`"
            embed.add_field(name=dbot, value=value, inline=False)
        embed.set_thumbnail(url=author.avatar.url)
        if not list_bots:
            embed.description = f"{author} doesnt own any bot here."
        await ctx.embed(embed=embed)

    @commands.command(aliases=["whoowns", "whosebot", "whoadds", "whoadded"],
                      brief="Shows who added the bot.",
                      help="Shows who added the bot, when they requested it and when the bot was added including the "
                           "jump url to the original request message in discord.py.")
    @is_discordpy()
    async def whoadd(self, ctx: StellaContext, *, bot: BotAdded):
        data = bot
        author = bot.author
        if not isinstance(author, discord.User):
            author = await try_call(commands.UserConverter().convert, ctx, str(data.author), exception=UserNotFound)

        embed = discord.Embed(title=str(data.bot))
        embed.set_thumbnail(url=data.bot.avatar.url)

        def or_none(condition: bool, func: Callable[[bool], Any]) -> Optional[Any]:
            if condition:
                return func(condition)

        fields = (("Added by", f"{author.mention} (`{author.id}`)"),
                  ("Reason", textwrap.shorten(data.reason, width=1000, placeholder='...')),
                  ("Requested", or_none(data.requested_at, default_date)),
                  ("Joined", or_none(data.joined_at, default_date)),
                  ("Message Request", or_none(data.jump_url, "[jump]({})".format)))

        await ctx.embed(embed=embed, fields=fields)

    def clean_prefix(self, ctx: StellaContext, prefix: str) -> str:
        value = (ctx.guild, ctx.bot)[ctx.guild is None]
        prefix = pprefix(value, prefix)
        if prefix == "":
            prefix = "\u200b"
        return re.sub("`", "`\u200b", prefix)

    @commands.command(aliases=["wp", "whatprefixes"],
                      brief="Shows the bot prefix.",
                      help="Shows what the bot's prefix. This is sometimes inaccurate. Don't rely on it too much. "
                           "This also does not know it's aliases prefixes.")
    @commands.guild_only()
    async def whatprefix(self, ctx: StellaContext, *, member: BotPrefixes):
        show_prefix = functools.partial(self.clean_prefix, ctx)
        prefix = show_prefix(member.prefix)
        alias = '`, `'.join(map(show_prefix, member.aliases))
        e = discord.Embed()
        e.add_field(name="Current", value=f"`{prefix}`")
        if member.aliases:
            e.add_field(name="Potential Aliases", value=f"`{alias}`")
        await ctx.embed(title=f"{member}'s Prefix", embed=e)

    @commands.command(aliases=["pu", "shares", "puse"],
                      brief="Shows the amount of bot that uses the same prefix.",
                      help="Shows the number of bot that shares a prefix between bots.")
    @commands.guild_only()
    async def prefixuse(self, ctx: StellaContext, prefix: str):
        instance_bot = await self.get_all_prefix(ctx, prefix)
        prefix = self.clean_prefix(ctx, prefix)
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
        prefix = self.clean_prefix(ctx, prefix)
        desk = f"Bot(s) with `{prefix}` as prefix\n{list_bot}"
        await ctx.embed(description=plural(desk, len(list_bot)))

    @commands.command(aliases=["ap", "aprefix", "allprefixes"],
                      brief="Shows every bot's prefix in the server.",
                      help="Shows a list of every single bot's prefix in a server.",
                      cls=flg.SFlagCommand)
    @commands.guild_only()
    @flg.add_flag("--count", type=bool, default=False, action="store_true",
                  help="Create a rank of the highest prefix that is being use by bots. This flag accepts True or False, "
                       "defaults to False if not stated.")
    @flg.add_flag("--reverse", type=bool, default=False, action="store_true",
                  help="Reverses the list. This flag accepts True or False, default to False if not stated.")
    async def allprefix(self, ctx: StellaContext, **flags: bool):
        if not (bots := await self.bot.pool_pg.fetch("SELECT * FROM prefixes_list WHERE guild_id=$1", ctx.guild.id)):
            return await ctx.embed(description="Looks like I don't have any data in this server on bot prefixes.")

        attr = "count" if (count_mode := flags.pop("count", False)) else "prefix"
        reverse = flags.pop("reverse", False)

        def mem(x):
            return ctx.guild.get_member(x)

        data = []
        for bot in filter(lambda b: mem(b["bot_id"]), bots):
            bot_id = bot["bot_id"]
            data.append(await BotPrefixes.convert(ctx, str(bot_id)))

        if count_mode:
            PrefixCount = collections.namedtuple("PrefixCount", "prefix count")
            prefixes = itertools.chain.from_iterable(map(lambda x: x.all_raw_prefixes, data))
            count_prefixes = collections.Counter(prefixes)
            data = [PrefixCount(*a) for a in count_prefixes.items()]

        data.sort(key=lambda x: getattr(x, attr), reverse=count_mode is not reverse)
        menu = InteractionPages(source=AllPrefixes(data, count_mode))
        await menu.start(ctx)

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
        # TODO: I said this 3 months ago to redo this, but im lazy
        titles = (("Bot Prefix", "{0.allprefixes}", BotPrefixes),
                  ("Command Usage", "{0.total_usage}", BotCommands),
                  (("Bot Invited by", "{0.author}"),
                   (("Reason", "reason"),
                    ("Requested at", 'requested_at')),
                   BotAdded))
        embed = BaseEmbed.default(ctx, title=str(bot))
        embed.set_thumbnail(url=bot.avatar.url)
        embed.add_field(name="ID", value=f"`{bot.id}`")
        for title, attrib, converter in reversed(titles):
            with contextlib.suppress(Exception):
                if obj := await converter.convert(ctx, str(bot.id)):
                    if isinstance(attrib, tuple):
                        for t, a in attrib:
                            if dat := getattr(obj, a):
                                dat = dat if not isinstance(dat, datetime.datetime) else default_date(dat)
                                embed.add_field(name=t, value=f"`{dat}`", inline=False)

                        title, attrib = title
                        if title == "Reason":
                            attrib = textwrap.shorten(attrib, width=1000, placeholder='...')
                    embed.add_field(name=title, value=f"{attrib.format(obj)}", inline=False)

        embed.add_field(name="Created at", value=f"`{default_date(bot.created_at)}`")
        embed.add_field(name="Joined at", value=f"`{default_date(bot.joined_at)}`")
        await ctx.embed(embed=embed)

    @commands.command(aliases=["rba", "recentbot", "recentadd"],
                      brief="Shows a list of bots that has been added in a day.",
                      help="Shows a list of bots that has been added in a day along with the owner that requested it, "
                           "and how long ago it was added.",
                      cls=flg.SFlagCommand)
    @is_discordpy()
    @flg.add_flag("--reverse", type=bool, default=False, action="store_true",
                  help="Reverses the list. This flag accepts True or False, default to False if not stated.")
    async def recentbotadd(self, ctx: StellaContext, **flags: bool):
        reverse = flags.pop("reverse", False)

        def predicate(m):
            return m.bot and m.joined_at.replace(tzinfo=None) > ctx.message.created_at.replace(tzinfo=None) - datetime.timedelta(days=1)
        members = {m.id: m for m in filter(predicate, ctx.guild.members)}
        if not members:
            member = max(filter(lambda x: x.bot, ctx.guild.members), key=lambda x: x.joined_at)
            time_add = discord.utils.format_dt(member.joined_at, "R")
            return await ctx.embed(
                            title="Bots added today",
                            description="Looks like there are no bots added in the span of 24 hours.\n"
                                        f"The last time a bot was added was {time_add} for `{member}`"
            )
        db_data = await self.bot.pool_pg.fetch("SELECT * FROM confirmed_bots WHERE bot_id=ANY($1::BIGINT[])", list(members))
        member_data = [BotAdded.from_json(bot=members[data["bot_id"]], **data) for data in db_data]
        member_data.sort(key=lambda x: x.joined_at, reverse=not reverse)
        menu = InteractionPages(source=bot_added_list(member_data))
        await menu.start(ctx)

    @greedy_parser.command(aliases=["br", "brrrr", "botranks", "botpos", "botposition", "botpositions"],
                           help="Shows all bot's command usage in the server on a sorted list.")
    @flg.add_flag("--reverse", type=bool, default=False, action="store_true",
                  help="Reverses the list. This flag accepts True or False, default to False if not stated.")
    async def botrank(self, ctx: StellaContext, bot: greedy_parser.UntilFlag[BotCommands] = None, **flags: bool):
        reverse = flags.pop("reverse", False)
        bots = {x.id: x for x in ctx.guild.members if x.bot}
        query = "SELECT bot_id, COUNT(command) AS total_usage FROM commands_list " \
                "WHERE guild_id=$1 AND bot_id=ANY($2::BIGINT[]) " \
                "GROUP BY bot_id"
        record = await self.bot.pool_pg.fetch(query, ctx.guild.id, list(bots))
        bot_data = [BotCommands(bots[r["bot_id"]], 0, 0, r["total_usage"]) for r in record]
        bot_data.sort(key=lambda x: x.total_usage, reverse=not reverse)
        if not bot:
            menu = InteractionPages(source=all_bot_count(bot_data))
            await menu.start(ctx)
        else:
            key = "(\u200b|\u200b)"
            idx = [*map(int, bot_data)].index(bot.bot.id)
            scope_bot = bot_data[idx:min(idx + len(bot_data[idx:]), idx + 10)]
            contents = ["`{0}. {1} {2} {1.total_usage}`".format(i + idx + 1, b, key) for i, b in enumerate(scope_bot)]
            await ctx.embed(title="Bot Command Rank", description="\n".join(realign(contents, key)))

    @commands.command(aliases=["pendingbot", "penbot", "peb"],
                      help="A bot that registered to ?addbot command of R. Danny but never joined the server.")
    @is_discordpy()
    async def pendingbots(self, ctx: StellaContext, *, flag: BotPendingFlag):
        sql = "SELECT * FROM pending_bots ORDER BY requested_at "
        sql += "DESC" if not flag.reverse else ""
        bots = await self.bot.pool_pg.fetch(sql)
        menu = InteractionPages(bot_pending_list(bots))
        if data := flag.bot:
            bot_target = data.bot.id
            get_bot_id = operator.itemgetter("bot_id")
            # It's impossible for it to be None, both came from pending_bots table. Unless race condition occurs
            index, _ = discord.utils.find(lambda b: get_bot_id(b[1]) == bot_target, enumerate(bots))
            menu.current_page = index

        menu.cached_bots = self.cached_bots
        await menu.start(ctx)

    @commands.command(aliases=["botcommand", "bc", "bcs"],
                      help="Predicting the bot's command based on the message history.")
    @commands.guild_only()
    async def botcommands(self, ctx: StellaContext, *, bot: BotCommands):
        owner_info = None
        if ctx.guild.id == DISCORD_PY:
            owner_info = await try_call(BotAdded.convert, ctx, str(int(bot)))

        @pages(per_page=6)
        def each_page(self, menu: menus.MenuPages, entries: List[str]) -> discord.Embed:
            number = menu.current_page * self.per_page + 1
            list_commands = "\n".join(f"{x}. {c}[`{bot.get_command(c)}`]" for x, c in enumerate(entries, start=number))
            embed = BaseEmbed.default(ctx, title=f"{bot} Commands[`{bot.total_usage}`]", description=list_commands)
            if owner_info and owner_info.author:
                embed.set_author(icon_url=owner_info.author.avatar.url, name=f"Owner {owner_info.author}")

            return embed.set_thumbnail(url=bot.bot.avatar.url)
        menu = InteractionPages(each_page(bot.commands))
        await menu.start(ctx)

    @commands.group(name="bot",
                    help="A group command that are related to all the bot that is stored in my database.")
    @commands.guild_only()
    async def _bot(self, ctx: StellaContext):
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @_bot.command(cls=greedy_parser.GreedyParser,
                  aliases=["ci", "changeinfos"],
                  brief="Allows you to change your own bot's information in whoadd/whatadd command.",
                  help="Allows you to change your own bot's information  in whoadd/whatadd command, "
                       "only applicable for discord.py server. The user is only allowed to change their own bot, "
                       "which they are able to change 'requested', 'reason' and 'jump url' values.")
    @is_discordpy()
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
        async def show_result(self, menu: menus.MenuPages, entry: List[Dict[str, str]]) -> discord.Embed:
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

    @commands.command(cls=flg.SFlagCommand,
                      brief="Get all unique command for all bot in a server.",
                      help="Get all unique command for all bot in a server that are shown in an "
                           "descending order for the unique.",
                      aliases=["ac", "acc", "allcommand", "acktually", "act"])
    @commands.guild_only()
    @flg.add_flag("--reverse", default=False, action="store_true",
                  help="Creates a list in an ascending order from the lowest usage to the highest.")
    async def allcommands(self, ctx: StellaContext, **flags: bool):
        reverse = flags.get("reverse", False)
        query = "SELECT * FROM " \
                "(SELECT command, COUNT(command) AS command_count FROM " \
                "(SELECT DISTINCT bot_id, command FROM commands_list " \
                "WHERE guild_id=$1 " \
                "GROUP BY bot_id, command) AS _ " \
                "GROUP BY command) AS _ " \
                f"ORDER BY command_count {('DESC', '')[reverse]}"

        data = await self.bot.pool_pg.fetch(query, ctx.guild.id)

        @pages(per_page=6)
        async def each_commands_list(self, menu: InteractionPages,
                                     entries: List[Dict[str, Union[str, int]]]) -> discord.Embed:
            offset = menu.current_page * self.per_page
            embed = BaseEmbed(title=f"All Commands")
            key = "(\u200b|\u200b)"
            contents = ["`{i}. {command}{k}{command_count}`".format(i=i, k=key, **b)
                        for i, b in enumerate(entries, start=offset + 1)]
            embed.description = "\n".join(realign(contents, key))
            return embed

        menu = InteractionPages(each_commands_list(data))
        await menu.start(ctx)

    @commands.command(aliases=["wgithub", "github", "botgithub"], help="Tries to show the given bot's GitHub repository.")
    async def whatgithub(self, ctx: StellaContext, bot: BotRepo):
        repo = bot.repo
        author = await self.bot.git.get_user(repo.owner.login)
        embed = BaseEmbed.default(
            ctx,
            title=repo.full_name,
            description=f"**About: **\n{repo.description}\n\n",
            url=repo.html_url
        )
        embed.set_thumbnail(url=bot.bot.avatar)

        async def aislice(citerator, cut):
            i = 0
            async for v in citerator:
                i += 1
                yield v
                if i == cut:
                    break

        async def formatted_commits():
            async for c in aislice(repo.get_commits(), 5):
                commit = c['commit']
                time_created = datetime.datetime.strptime(commit['author']['date'], "%Y-%m-%dT%H:%M:%SZ")
                message = commit['message']
                url = c['html_url']
                sha = c['sha'][:6]
                yield f'[{aware_utc(time_created, mode="R")}] [{message}]({url} "{sha}")'

        embed.description += "**Recent Commits:** \n" + "\n".join([o async for o in formatted_commits()])

        value = [f'{u.login}(`{u.contributions}`)' async for u in aislice(repo.get_contributors(), 3)]
        embed.description += plural("\n\n**Top Contributor(s)**\n", len(value)) + ", ".join(value)

        embed.add_field(name=plural("Star(s)", repo.stargazers_count), value=repo.stargazers_count)
        embed.add_field(name=plural("Fork(s)", repo.forks_count), value=repo.forks_count)
        embed.add_field(name="Language", value=repo.language)

        if issue := repo.open_issues_count:
            embed.add_field(name=plural("Open Issue(s)", issue), value=issue)

        embed.add_field(name="Created At", value=aware_utc(repo.created_at))
        embed.set_author(name=f"Repository by {author.name}", icon_url=author.avatar_url)
        await ctx.maybe_reply(embed=embed)

    @commands.command(aliases=["agithub", "ag", "everygithub", "allgithubs"],
                      help="Shows all bot's github that it knows from a server.")
    async def allgithub(self, ctx):
        bots = [b.id for b in ctx.guild.members if b.bot]
        data = await self.bot.pool_pg.fetch("SELECT * FROM bot_repo WHERE bot_id=ANY($1::BIGINT[])", bots)

        if not data:
            return await ctx.reply("I dont know any github here.")

        @pages(per_page=6)
        async def each_git_list(self, menu: InteractionPages,
                                     entries: List[Dict[str, Union[str, int]]]) -> discord.Embed:
            offset = menu.current_page * self.per_page
            embed = BaseEmbed(title=f"All GitHub Repository in {ctx.guild}")
            members = [ctx.guild.get_member(b['bot_id']) for b in entries]
            contents = ["{i}. [{m}](https://github.com/{owner_repo}/{bot_name})".format(i=i, m=m, **b)
                        for (i, b), m in zip(enumerate(entries, start=offset + 1), members)]
            embed.description = "\n".join(contents)
            return embed

        menu = InteractionPages(each_git_list(data))
        await menu.start(ctx)

    @commands.Cog.listener('on_message')
    @event_check(lambda _, m: m.author.bot)
    async def is_bot_triggered(self, message: discord.Message):
        def resolve_message(m):
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


def setup(bot: StellaBot) -> None:
    bot.add_cog(FindBot(bot))
