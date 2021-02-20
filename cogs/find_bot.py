import discord
import datetime
import re
import asyncio
import itertools
import ctypes
import contextlib
import humanize
import functools
import collections
from dataclasses import dataclass
from discord.ext import commands
from discord.ext.commands import UserNotFound
from discord.ext.menus import ListPageSource
from utils import flags as flg
from utils.new_converters import BotPrefixes, IsBot, BotCommands, JumpValidator, DatetimeConverter
from utils.useful import try_call, BaseEmbed, compile_array, search_prefixes, MenuBase, default_date, plural, realign, search_commands
from utils.errors import NotInDatabase, BotNotFound
from utils.decorators import is_discordpy, event_check, wait_ready, pages, listen_for_guilds


ReactRespond = collections.namedtuple("ReactRespond", "created_at author reference")

DISCORD_PY = 336642139381301249

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
    def from_json(cls, bot=None, *, bot_id=None, **data):
        """factory method on data from a dictionary like object into BotAdded."""
        author = data.pop("author_id", None)
        join = data.pop("joined_at", None)
        bot = bot or bot_id
        if isinstance(bot, discord.Member):
            join = bot.joined_at
            author = bot.guild.get_member(author) or author

        return cls(author=author, bot=bot, joined_at=join, **data)

    @classmethod
    async def convert(cls, ctx, argument):
        """Invokes when the BotAdded is use as a typehint."""
        with contextlib.suppress(commands.BadArgument):
            if user := await IsBot().convert(ctx, argument, cls=BotAdded):
                for attribute in ("pending", "confirmed")[isinstance(user, discord.Member):]:
                    attribute += "_bots"
                    if user.id in getattr(ctx.bot, attribute):
                        data = await ctx.bot.pool_pg.fetchrow(f"SELECT * FROM {attribute} WHERE bot_id = $1", user.id)
                        return cls.from_json(user, **data)
                raise NotInDatabase(user, converter=cls)
        raise BotNotFound(argument, converter=cls)

    def __str__(self):
        return str(self.bot or "")


class BotOwner(BotAdded):
    """Raises an error if the bot does not belong to the context author"""
    @classmethod
    async def convert(cls, ctx, argument):
        botdata = await super().convert(ctx, argument)
        if botdata.author != ctx.author:
            raise commands.CommandError(f"Sorry you can only change your own bot's information. This bot belongs to {botdata.author}.")
        
        if not ctx.guild.get_member(botdata.bot.id):
            raise commands.CommandError("This bot must be in the server.")
        return botdata

class AuthorMessage(commands.MessageConverter):
    """Only allows messages that belong to the context author"""
    async def convert(self, ctx, argument):
        message = await super().convert(ctx, argument)
        if message.author != ctx.author:
            raise commands.CommandError("The author of this message must be your own message.")
        return message

class AuthorJump_url(JumpValidator):
    """Yes i fetch message twice, I'm lazy to copy paste."""
    async def convert(self, ctx, argument):
        message = await AuthorMessage().convert(ctx, await super().convert(ctx, argument))
        return message.jump_url

def pprefix(bot_guild, prefix):
    if content := re.search("<@(!?)(?P<id>[0-9]*)>", prefix):
        method = getattr(bot_guild, ("get_user","get_member")[isinstance(bot_guild, discord.Guild)])
        if user := method(int(content["id"])):
            return f"@{user.display_name} "
    return prefix


class AllPrefixes(ListPageSource):
    """Menu for allprefix command."""
    def __init__(self, data, count_mode):
        super().__init__(data, per_page=6)
        self.count_mode = count_mode

    async def format_page(self, menu: MenuBase, entries):
        key = "(\u200b|\u200b)"
        offset = menu.current_page * self.per_page
        content = "`{no}. {prefix} {key} {b.count}`" if self.count_mode else "`{no}. {b} {key} {prefix}`"
        contents = [content.format(no=i+1, b=b, key=key, prefix=pprefix(menu.ctx.guild, b.prefix)) for i, b in enumerate(entries, start=offset)]
        embed = BaseEmbed(title="All Prefixes",
                          description="\n".join(realign(contents, key)))
        return menu.generate_page(embed, self._max_pages)


@pages(per_page=10)
async def all_bot_count(self, menu: MenuBase, entries):
    """Menu for botrank command."""
    key = "(\u200b|\u200b)"
    offset = menu.current_page * self.per_page
    content = "`{no}. {b} {key} {b.total_usage}`"
    contents = [content.format(no=i+1, b=b, key=key) for i, b in enumerate(entries, start=offset)]
    return BaseEmbed(title="Bot Command Rank",
                     description="\n".join(realign(contents, key)))


@pages(per_page=6)
async def bot_added_list(self, menu: MenuBase, entries):
    """Menu for recentbotadd command."""
    offset = menu.current_page * self.per_page
    contents = ((f"{b.author}", f'**{b}** `{humanize.precisedelta(b.joined_at)}`')
                for i, b in enumerate(entries, start=offset))
    return BaseEmbed(title="Bots added today", fields=contents)


@pages()
async def bot_pending_list(self, menu: MenuBase, entry):
    stellabot = menu.ctx.bot
    bot = menu.cached_bots.setdefault(entry["bot_id"], await stellabot.fetch_user(entry["bot_id"]))
    fields = (("Requested by", stellabot.get_user(entry["author_id"]) or "idk really"),
              ("Reason", entry["reason"]),
              ("Created at", default_date(bot.created_at)),
              ("Requested at", default_date(entry["requested_at"])),
              ("Message", f"[jump]({entry['jump_url']})"))
    embed = BaseEmbed(title=f"{bot}(`{bot.id}`)", fields=fields)
    embed.set_thumbnail(url=bot.avatar_url)
    return embed


def is_user():
    """Event check for returning true if it's a bot."""
    return event_check(lambda _, m: not m.author.bot)


def prefix_cache_ready():
    """Event check for command_count"""
    def predicate(self, message):
        return self.compiled_prefixes and self.compiled_commands and not message.author.bot
    return event_check(predicate)


def dpy_bot():
    """Event check for dpy_bots"""
    return event_check(lambda _, member: member.bot and member.guild.id == DISCORD_PY)


class FindBot(commands.Cog, name="Bots"):
    def __init__(self, bot):
        self.bot = bot
        valid_prefix = ("!", "?", "？", "<@(!?)80528701850124288> ")
        re_command = "(\{}|\{}|\{}|({}))addbot".format(*valid_prefix)
        re_bot = "[\s|\n]+(?P<id>[0-9]{17,19})[\s|\n]"
        re_reason = "+(?P<reason>.[\s\S\r]+)"
        self.re_addbot = re_command + re_bot + re_reason
        self.cached_bots = {}
        self.compiled_prefixes = None
        self.compiled_commands = None
        self.all_bot_prefixes = {}
        self.all_bot_commands = {}
        bot.loop.create_task(self.loading_all_prefixes())

    async def loading_all_prefixes(self):
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

    def update_compile(self):
        temp = [*{prefix for prefixes in self.all_bot_prefixes.values() for prefix in prefixes}]
        cmds = [*{command for commands in self.all_bot_commands.values() for command in commands}]
        self.compiled_prefixes = compile_array(sorted(temp))
        self.compiled_commands = compile_array(sorted(x[::-1] for x in cmds))

    @commands.Cog.listener("on_member_join")
    @wait_ready()
    @dpy_bot()
    async def join_bot_tracker(self, member):
        """Tracks when a bot joins in discord.py where it logs all the BotAdded information."""
        if member.id in self.bot.pending_bots:
            data = await self.bot.pool_pg.fetchrow("SELECT * FROM pending_bots WHERE bot_id = $1", member.id)
            await self.update_confirm(BotAdded.from_json(member, **data))
            await self.bot.pool_pg.execute("DELETE FROM pending_bots WHERE bot_id = $1", member.id)
        else:
            await self.update_confirm(BotAdded.from_json(member, joined_at=member.joined_at))

    async def listen_for_bots_at(self, message, message_check):
        """Listens for bots responding and terminating when a user respond"""
        bots = {}
        after_user = {}
        time_to_listen = message.created_at + datetime.timedelta(seconds=5)
        flip = 0
        def reaction_add_check(reaction, user):
            return reaction.message == message

        stuff_here = locals()
        with contextlib.suppress(asyncio.TimeoutError):
            while time_to_listen > (time_rn := datetime.datetime.utcnow()):
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
    async def remove_bot_tracker(self, member):
        """Since there is no reason to store these bots after they left, best to delete them"""
        if member.id in self.bot.confirmed_bots:
            await self.bot.pool_pg.execute("DELETE FROM confirmed_bots WHERE bot_id=$1", member.id)
            self.bot.confirmed_bots.remove(member.id)

    async def update_prefix_bot(self, message, func, prefix, command):
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
                message_sent.pop(x["bot_id"])

        if not message_sent:
            return

        prefixes = [(message.guild.id, x, prefix, 1, m.created_at) for x, m in message_sent.items()]
        commands = [(message.guild.id, x, command, m.created_at) for x, m in message_sent.items()]

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
    async def find_bot_prefixes(self, message):
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

    async def search_respond(self, callback, message, word, type):
        """Gets the prefix/command that are in this message, gets the bot that responded
           and return them."""
        content_compiled = ctypes.create_string_buffer(word.encode("utf-8"))
        if not (result := callback(getattr(self, f"compiled_{type}"), content_compiled)):
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

    async def insert_both_prefix_command(self, prefixes, commands):
        commands_query = "INSERT INTO commands_list VALUES($1, $2, $3, $4)"
        prefixes_query = "INSERT INTO prefixes_list VALUES($1, $2, $3, $4, $5) " \
                         "ON CONFLICT (guild_id, bot_id, prefix) DO " \
                         "UPDATE SET usage=prefixes_list.usage + 1, last_usage=$5"
        
        for type in "commands", "prefixes":
            await self.bot.pool_pg.executemany(locals()[f"{type}_query"], locals()[type])

    @commands.Cog.listener("on_message")
    @wait_ready()
    @listen_for_guilds()
    @prefix_cache_ready()
    @is_user()
    async def find_bot_commands(self, message):
        """Get a prefix based on known command used."""
        word, _, _ = message.content.partition("\n")
        limit = min(len(word), 101)
        if not (received := await self.search_respond(search_commands, message, word[:limit].casefold(), "commands")):
            return

        responded, result, message_sent = received
        prefixes_values = []
        commands_values = []
        exist_query = "SELECT * FROM prefixes_list WHERE guild_id=$1 AND bot_id=$2"
        for command, bot in itertools.product(result, responded):
            if bot["command"] == command:
                bot_id = bot['bot_id']
                message_respond = message_sent[bot_id].created_at
                if (match := re.match("(?P<prefix>^.{{1,100}}?(?={}))".format(command), word, re.I)) and len(match["prefix"]) < 31:
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
    async def command_count(self, message):
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
                message_respond = message_sent[bot_id].created_at
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
    @is_user()
    async def addbot_command_tracker(self, message):
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

    async def check_author(self, bot_id, author_id, mode):
        """Checks if the author of a bot is the same as what is stored in the database."""
        if data := await self.bot.pool_pg.fetchrow(f"SELECT * FROM {mode} WHERE bot_id=$1", bot_id):
            old_author = data['author_id']
            return old_author == author_id

    async def is_valid_addbot(self, message, check=False):
        """Check if a message is a valid ?addbot command."""
        if result := re.match(self.re_addbot, message.content):
            reason = result["reason"]
            get_member = message.guild.get_member
            if not check:
                member = get_member(int(result["id"]))
                six_days = datetime.datetime.utcnow() - datetime.timedelta(days=6)
                if not member and message.created_at > six_days:
                    member = await try_call(self.bot.fetch_user, int(result["id"]), exception=discord.NotFound)
                    if all((reason, member and member.bot and str(member.id) not in self.bot.pending_bots)):
                        if str(member.id) not in self.bot.confirmed_bots:
                            await self.update_pending(
                                BotAdded(author=message.author,
                                         bot=member,
                                         reason=reason,
                                         requested_at=message.created_at,
                                         jump_url=message.jump_url))
                        return

            else:
                if member := get_member(int(result["id"])):
                    if int(result["id"]) not in self.bot.confirmed_bots and \
                            await self.check_author(member.id, message.author.id, "confirmed_bots"):
                        newAddBot = BotAdded(author=message.author,
                                             bot=member,
                                             reason=reason,
                                             requested_at=message.created_at,
                                             jump_url=message.jump_url,
                                             joined_at=member.joined_at)
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
                                requested_at=message.created_at,
                                jump_url=message.jump_url,
                                joined_at=join)

    async def update_pending(self, result):
        """Insert a new addbot request which is yet to enter the discord.py server."""
        query = """INSERT INTO pending_bots VALUES($1, $2, $3, $4, $5) 
                   ON CONFLICT (bot_id) DO
                   UPDATE SET reason = $3, requested_at=$4, jump_url=$5"""
        value = (result.bot.id, result.author.id, result.reason, result.requested_at, result.jump_url)
        await self.bot.pool_pg.execute(query, *value)
        if result.bot.id not in self.bot.pending_bots:
            self.bot.pending_bots.add(result.bot.id)

    async def update_confirm(self, result):
        """Inserts a new confirmed bot with an author where the bot is actually in the discord.py server."""
        query = """INSERT INTO confirmed_bots VALUES($1, $2, $3, $4, $5, $6) 
                   ON CONFLICT (bot_id) DO
                   UPDATE SET reason = $3, requested_at=$4, jump_url=$5, joined_at=$6"""
        if not result.author:
            return self.bot.pending_bots.remove(result.bot.id)

        value = (result.bot.id, result.author.id, result.reason, result.requested_at, result.jump_url, result.joined_at)
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
    async def whatadd(self, ctx, author: IsBot(is_bot=False, user_check=False) = None):
        author = author or ctx.author
        if author.bot:
            return await ctx.maybe_reply("That's a bot lol")
        query = "SELECT * FROM {}_bots WHERE author_id=$1"
        total_list = [await self.bot.pool_pg.fetch(query.format(x), author.id) for x in ("pending", "confirmed")]
        total_list = itertools.chain.from_iterable(total_list)

        async def get_member(b_id):
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
        embed.set_thumbnail(url=author.avatar_url)
        if not list_bots:
            embed.description = f"{author} doesnt own any bot here."
        await ctx.embed(embed=embed)

    @commands.command(aliases=["whoowns", "whosebot", "whoadds", "whoadded"],
                      brief="Shows who added the bot.",
                      help="Shows who added the bot, when they requested it and when the bot was added including the "
                           "jump url to the original request message in discord.py.")
    @is_discordpy()
    async def whoadd(self, ctx, bot: BotAdded):
        data = bot
        author = await try_call(commands.UserConverter().convert, ctx, str(data.author), exception=UserNotFound)
        embed = discord.Embed(title=str(data.bot))
        embed.set_thumbnail(url=data.bot.avatar_url)

        def or_none(condition, func):
            if condition:
                return func(condition)

        fields = (("Added by", f"{author.mention} (`{author.id}`)"),
                  ("Reason", data.reason),
                  ("Requested", or_none(data.requested_at, default_date)),
                  ("Joined", or_none(data.joined_at, default_date)),
                  ("Message Request", or_none(data.jump_url, "[jump]({})".format)))

        await ctx.embed(embed=embed, fields=fields)

    def clean_prefix(self, ctx, prefix):
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
    async def whatprefix(self, ctx, member: BotPrefixes):
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
    async def prefixuse(self, ctx, prefix):
        instance_bot = await self.get_all_prefix(ctx.guild, prefix)
        prefix = self.clean_prefix(ctx, prefix)
        desk = plural(f"There (is/are) `{len(instance_bot)}` bot(s) that use `{prefix}` as prefix", len(instance_bot))
        await ctx.embed(description=desk)

    async def get_all_prefix(self, guild, prefix):
        """Quick function that gets the amount of bots that has the same prefix in a server."""
        data = await self.bot.pool_pg.fetch("SELECT * FROM prefixes_list WHERE guild_id=$1 AND prefix=$2", guild.id, prefix)

        def mem(x):
            return guild.get_member(x)

        return [mem(x['bot_id']) for x in data if mem(x['bot_id'])]

    @commands.command(aliases=["prefixbots", "pbots"],
                      brief="Shows the name of bot(s) have a given prefix.",
                      help="Shows a list of bot(s) name that have a given prefix.")
    @commands.guild_only()
    async def prefixbot(self, ctx, prefix):
        instance_bot = await self.get_all_prefix(ctx.guild, prefix)
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
    async def allprefix(self, ctx, **flags):
        if not (bots := await self.bot.pool_pg.fetch("SELECT * FROM prefixes_list WHERE guild_id=$1", ctx.guild.id)):
            return await ctx.embed(description="Looks like I don't have any data in this server on bot prefixes.")

        attr = "count" if (count_mode := flags.pop("count", False)) else "prefix"
        reverse = flags.pop("reverse", False)

        def mem(x):
            return ctx.guild.get_member(x)

        temp = {}
        for bot in filter(lambda b: mem(b["bot_id"]), bots):
            prefixes = temp.setdefault(bot["bot_id"], {bot["prefix"]: bot["usage"]})
            prefixes.update({bot["prefix"]: bot["usage"]})
        data = [BotPrefixes(mem(b), v) for b, v in temp.items()]

        if count_mode:
            PrefixCount = collections.namedtuple("PrefixCount", "prefix count")
            aliases = itertools.chain.from_iterable(map(lambda x: x.aliases, data))
            count_prefixes = collections.Counter([*map(lambda x: x.prefix, data), *aliases])
            data = [PrefixCount(*a) for a in count_prefixes.items()]

        data.sort(key=lambda x: getattr(x, attr), reverse=count_mode is not reverse)
        menu = MenuBase(source=AllPrefixes(data, count_mode))
        await menu.start(ctx)

    @commands.command(aliases=["bot_use", "bu", "botusage", "botuses"],
                      brief="Show's how many command calls for a bot.",
                      help="Show's how many command calls for a given bot. This works by counting how many times "
                           "a message is considered a command for that bot where that bot has responded in less than "
                           "2 seconds.")
    async def botuse(self, ctx, bot: BotCommands):
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
    async def botinfo(self, ctx, bot: IsBot):
        # TODO: I said this 3 months ago to redo this, but im lazy
        titles = (("Bot Prefix", "{0.allprefixes}", BotPrefixes),
                  ("Command Usage", "{0.total_usage}", BotCommands),
                  (("Bot Invited by", "{0.author}"),
                   (("Reason", "reason"),
                    ("Requested at", 'requested_at')),
                   BotAdded))
        embed = BaseEmbed.default(ctx, title=str(bot))
        embed.set_thumbnail(url=bot.avatar_url)
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
    async def recentbotadd(self, ctx, **flags):
        reverse = flags.pop("reverse", False)

        def predicate(m):
            return m.bot and m.joined_at > ctx.message.created_at - datetime.timedelta(days=1)
        members = {m.id: m for m in filter(predicate, ctx.guild.members)}
        if not members:
            member = max(filter(lambda x: x.bot, ctx.guild.members), key=lambda x: x.joined_at)
            time_add = humanize.precisedelta(member.joined_at, minimum_unit="minutes")
            return await ctx.embed(
                            title="Bots added today",
                            description="Looks like there are no bots added in the span of 24 hours.\n"
                                        f"The last time a bot was added was `{time_add}` for `{member}`"
            )
        db_data = await self.bot.pool_pg.fetch("SELECT * FROM confirmed_bots WHERE bot_id=ANY($1::BIGINT[])", list(members))
        member_data = [BotAdded.from_json(bot=members[data["bot_id"]], **data) for data in db_data]
        member_data.sort(key=lambda x: x.joined_at, reverse=not reverse)
        menu = MenuBase(source=bot_added_list(member_data))
        await menu.start(ctx)


    @commands.command(aliases=["br", "brrrr", "botranks", "botpos", "botposition", "botpositions"],
                      help="Shows all bot's command usage in the server on a sorted list.",
                      cls=flg.SFlagCommand)
    @flg.add_flag("--reverse", type=bool, default=False, action="store_true",
                  help="Reverses the list. This flag accepts True or False, default to False if not stated.")
    async def botrank(self, ctx, bot: BotCommands = None, **flags):
        reverse = flags.pop("reverse", False)
        bots = {x.id: x for x in ctx.guild.members if x.bot}
        query = "SELECT bot_id, COUNT(command) AS total_usage FROM commands_list " \
                "WHERE guild_id=$1 AND bot_id=ANY($2::BIGINT[]) " \
                "GROUP BY bot_id"
        record = await self.bot.pool_pg.fetch(query, ctx.guild.id, list(bots))
        bot_data = [BotCommands(bots[r["bot_id"]], 0, 0, r["total_usage"]) for r in record]
        bot_data.sort(key=lambda x: x.total_usage, reverse=not reverse)
        if not bot:
            menu = MenuBase(source=all_bot_count(bot_data))
            await menu.start(ctx)
        else:
            key = "(\u200b|\u200b)"
            idx = [*map(int, bot_data)].index(bot.bot.id)
            scope_bot = bot_data[idx:min(idx + len(bot_data[idx:]), idx + 10)]
            contents = ["`{0}. {1} {2} {1.total_usage}`".format(i + idx + 1, b, key) for i, b in enumerate(scope_bot)]
            await ctx.embed(title="Bot Command Rank", description="\n".join(realign(contents, key)))

    @commands.command(aliases=["pendingbot", "penbot", "peb"],
                      help="A bot that registered to ?addbot command of R. Danny but never joined the server.",
                      cls=flg.SFlagCommand)
    @flg.add_flag("--reverse", type=bool, default=False, action="store_true",
                  help="Reverses the list based on the requested date. This flag accepts True or False, default to "
                       "False if not stated.")
    @is_discordpy()
    async def pendingbots(self, ctx, **flags):
        bots = await self.bot.pool_pg.fetch("SELECT * FROM pending_bots")
        menu = MenuBase(bot_pending_list(sorted(bots, key=lambda x: x["requested_at"], reverse=not flags.get("reverse", False))))
        menu.cached_bots = self.cached_bots
        await menu.start(ctx)

    @commands.command(aliases=["botcommand", "bc", "bcs"],
                      help="Predicting the bot's command based on the message history.")
    @commands.guild_only()
    async def botcommands(self, ctx, bot: BotCommands):
        owner_info = None
        if ctx.guild.id == DISCORD_PY:
            owner_info = await try_call(BotAdded.convert, ctx, str(int(bot)))

        @pages(per_page=6)
        def each_page(self, menu, entries):
            number = menu.current_page * self.per_page + 1
            list_commands = "\n".join(f"{x}. {c}[`{bot.get_command(c)}`]" for x, c in enumerate(entries, start=number))
            embed = BaseEmbed.default(ctx, title=f"{bot} Commands[`{bot.total_usage}`]", description=list_commands)
            if owner_info and owner_info.author:
                embed.set_author(icon_url=owner_info.author.avatar_url, name=f"Owner {owner_info.author}")

            return embed.set_thumbnail(url=bot.bot.avatar_url)
        menu = MenuBase(each_page(bot.commands))
        await menu.start(ctx)

    @commands.command(cls=flg.SFlagCommand, aliases=["botchange", "cb", "botchanges"],
                      brief="Allows you to change your own bot's information in whoadd/whatadd command.",
                      help="Allows you to change your own bot's information  in whoadd/whatadd command, "\
                           "only applicable for discord.py server. The user is only allowed to change their own bot, "\
                           "which they are able to change 'requested', 'reason' and 'jump url' values.")
    @is_discordpy()
    @flg.add_flag("--jump_url", type=AuthorJump_url, help="The jump url that will be displayed under 'Message Request'.")
    @flg.add_flag("--requested_at", type=DatetimeConverter, help="The date that is displayed under 'Requested'.")
    @flg.add_flag("--reason", nargs="+", help="The text that are displayed under 'Reason'.")
    @flg.add_flag("--message", type=AuthorMessage, 
                  help="This flag will override 'reason', 'requested' and 'jump url' according to the target message.")
    async def changebot(self, ctx, bot: BotOwner, **flags):
        bot = bot.bot
        if not any(flags.values()):
            raise commands.CommandError("No value were passed, at least put a flag." \
                                        f" Type {ctx.prefix}help {ctx.invoked_with} for more infomation")
        new_data = {'bot_id': bot.id}
        if message := flags.pop('message'):
            new_data['reason'] = message.content
            new_data['requested_at'] = message.created_at
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

    @commands.command(cls=flg.SFlagCommand,
                      brief="Get all unique command for all bot in a server.",
                      help="Get all unique command for all bot in a server that are shown in an "\
                           "descending order for the unique.",
                      aliases=["ac", "acc", "allcommand", "acktually", "act"])
    @commands.guild_only()
    @flg.add_flag("--reverse", default=False, action="store_true",
                    help="Creates a list in an ascending order from the lowest usage to the highest.")
    async def allcommands(self, ctx, **flags):
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
        async def each_commands_list(self, menu: MenuBase, entries):
            offset = menu.current_page * self.per_page
            embed = BaseEmbed(title=f"All Commands")
            key = "(\u200b|\u200b)"
            contents = ["`{i}. {command}{k}{command_count}`".format(i=i, k=key, **b)
                         for i, b in enumerate(entries, start=offset + 1)]
            embed.description = "\n".join(realign(contents, key))
            return embed

        menu = MenuBase(each_commands_list(data))
        await menu.start(ctx)
        


def setup(bot):
    bot.add_cog(FindBot(bot))
