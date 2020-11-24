import discord
import datetime
import re
import asyncio
import itertools
import ctypes
import contextlib
import humanize
from dataclasses import dataclass
from discord.ext import commands
from discord.ext.commands import BucketType, UserNotFound
from discord.ext.menus import ListPageSource, MenuPages
from utils.new_converters import BotPrefix, BotUsage, IsBot, FetchUser
from utils.useful import try_call, BaseEmbed, compile_prefix, search_prefix, MenuBase, default_date, event_check, plural
from utils.errors import NotInDatabase, BotNotFound, NotBot
from utils.decorators import is_discordpy


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
    def from_json(cls, bot=None, bot_id=None, author_id=None, joined_at=None, **data):
        """factory method on data from a dictionary like object into BotAdded."""
        author = author_id
        join = joined_at
        bot = bot or bot_id
        if bot and isinstance(bot, discord.Member):
            join = bot.joined_at
            author = bot.guild.get_member(author)

        return cls(author=author, bot=bot, joined_at=join, **data)

    @classmethod
    async def convert(cls, ctx, argument):
        """Invokes when the BotAdded is use as a typehint."""
        for inst in commands.MemberConverter(), FetchUser():
            with contextlib.suppress(commands.BadArgument):
                if user := await inst.convert(ctx, argument):
                    if not user.bot:
                        raise NotBot(user, converter=cls)
                    for attribute in ("pending", "confirmed")[isinstance(inst, commands.MemberConverter):]:
                        attribute += "_bots"
                        if user.id in getattr(ctx.bot, attribute):
                            data = await ctx.bot.pool_pg.fetchrow(f"SELECT * FROM {attribute} WHERE bot_id = $1", user.id)
                            return cls.from_json(user, **data)
                    raise NotInDatabase(user, converter=cls)
        raise BotNotFound(argument, converter=cls)

    def __str__(self):
        return str(self.bot or "")


async def pprefix(ctx, prefix):
    if re.search("<@(!?)([0-9]*)>", prefix):
        with contextlib.suppress(discord.NotFound):
            user = await commands.UserConverter().convert(ctx, re.sub(" ", "", prefix))
            return f"@{user.display_name} "
    return prefix


class AllPrefixes(ListPageSource):
    """Menu for allprefix command."""
    def __init__(self, data):
        super().__init__(data, per_page=6)

    async def format_page(self, menu: MenuPages, entries):
        key = "(\u200b|\u200b)"
        offset = menu.current_page * self.per_page

        contents = [f'`{i + 1}. {b} {key} {await pprefix(menu.ctx, b.prefix)}`' for i, b in enumerate(entries, start=offset)]
        high = max(cont.index(key) for cont in contents)
        reform = [high - cont.index(key) for cont in contents]
        true_form = [x.replace(key, f'{" " * off} |') for x, off in zip(contents, reform)]
        embed = BaseEmbed(title="All Prefixes",
                          description="\n".join(true_form))
        embed.set_author(name=f"Page {menu.current_page + 1}/{self._max_pages}")
        return embed


class BotAddedList(ListPageSource):
    """Menu for recentbotadd command."""
    def __init__(self, data):
        super().__init__(data, per_page=6)

    async def format_page(self, menu: MenuPages, entries):
        offset = menu.current_page * self.per_page
        contents = ((f"{b.author}", f'**{b}** `{humanize.precisedelta(b.joined_at)}`')
                    for i, b in enumerate(entries, start=offset))

        embed = BaseEmbed(title="Bots added today")
        for n, v in contents:
            embed.add_field(name=n, value=v, inline=False)
        embed.set_author(name=f"Page {menu.current_page + 1}/{self._max_pages}")
        return embed


async def is_user(self, m):
    """Event check for returning true if it's a bot."""
    await self.bot.wait_until_ready()
    return not m.author.bot


async def command_count_check(self, message):
    """Event check for command_count"""
    await self.bot.wait_until_ready()
    return self.compiled_pref and not message.author.bot and message.guild


async def dpy_bot(self, member):
    """Event check for dpy_bots"""
    await self.bot.wait_until_ready()
    return member.bot and member.guild.id == 336642139381301249


class FindBot(commands.Cog, name="Bots"):
    def __init__(self, bot):
        self.bot = bot
        self.help_trigger = {}
        valid_prefix = ("!", "?", "？", "<@(!?)80528701850124288> ")
        re_command = "(\{}|\{}|\{}|({}))addbot".format(*valid_prefix)
        re_bot = "[\s|\n]+(?P<id>[0-9]{17,19})[\s|\n]"
        re_reason = "+(?P<reason>.[\s\S\r]+)"
        self.re_addbot = re_command + re_bot + re_reason
        self.compiled_pref = None
        self.all_bot_prefixes = None
        bot.loop.create_task(self.loading_all_prefixes())

    async def loading_all_prefixes(self):
        """Loads all unique prefix when it loads and set compiled_pref for C code."""
        await self.bot.wait_until_ready()
        datas = await self.bot.pool_pg.fetch("SELECT * FROM bot_prefix")
        self.all_bot_prefixes = {data["bot_id"]: data["prefix"] for data in datas}
        temp = list(set(self.all_bot_prefixes.values()))
        self.compiled_pref = compile_prefix(sorted(temp))

    @commands.Cog.listener("on_member_join")
    @event_check(dpy_bot)
    async def join_bot_tracker(self, member):
        """Tracks when a bot joins in discord.py where it logs all the BotAdded information."""
        if member.id in self.bot.pending_bots:
            data = await self.bot.pool_pg.fetchrow("SELECT * FROM pending_bots WHERE bot_id = $1", member.id)
            await self.update_confirm(BotAdded.from_json(member, **data))
            await self.bot.pool_pg.execute("DELETE FROM pending_bots WHERE bot_id = $1", member.id)
        else:
            await self.update_confirm(BotAdded.from_json(member, joined_at=member.joined_at))

    @commands.Cog.listener("on_member_remove")
    @event_check(dpy_bot)
    async def remove_bot_tracker(self, member):
        if member.id in self.bot.confirmed_bots:
            await self.bot.pool_pg.execute("DELETE FROM confirmed_bots WHERE bot_id=$1", member.id)
            self.bot.confirmed_bots.remove(member.id)

    async def update_prefix_bot(self, message, func, prefix):
        """Updates the prefix of a bot, or multiple bot where it waits for the bot to respond. It updates in the database."""
        def setting(inner):
            def check(msg):
                if msg.channel != message.channel:
                    return False
                if not msg.author.bot:
                    return True
                return inner(msg)

            return check

        bots = []
        while message.created_at + datetime.timedelta(seconds=2) > datetime.datetime.utcnow():
            with contextlib.suppress(asyncio.TimeoutError):
                if m := await self.bot.wait_for("message", check=setting(func), timeout=1):
                    if not m.author.bot:  # TODO: Find out the cause of race condition here
                        break
                    if m.author.bot and not (m.author.id in self.all_bot_prefixes and self.all_bot_prefixes[m.author.id] == prefix):
                        bots.append(m.author.id)
        if not bots:
            return
        query = "INSERT INTO bot_prefix VALUES($1, $2) " \
                "ON CONFLICT (bot_id) DO " \
                "UPDATE SET prefix=$2"
        values = [(x, prefix) for x in bots]

        await self.bot.pool_pg.executemany(query, values)
        self.all_bot_prefixes.update({x: prefix for x, prefix in values})
        temp = list(set(self.all_bot_prefixes.values()))
        self.compiled_pref = compile_prefix(sorted(temp))

    @commands.Cog.listener("on_message")
    @event_check(is_user)
    async def find_bot_prefix(self, message):
        """Responsible for checking if a message has a prefix for a bot or not by checking if it's a jishaku or help command."""
        def check_jsk(m):
            possible_text = ("Jishaku", "discord.py", "Python ", "Module ", "guild(s)", "user(s).")
            return all(text in m.content for text in possible_text)

        def check_help(m):
            def search(search_text):
                possible_text = ("command", "help", "category", "categories")
                return any(f"{x}" in search_text.lower() for x in possible_text)
            content = search(m.content)
            embeds = any(search(str(x.to_dict())) for x in m.embeds)
            return content or embeds

        for x in "jsk", "help":
            if match := re.match("(?P<prefix>^.{{1,30}}?(?={}$))".format(x), message.content):
                if x not in match["prefix"]:
                    if x == "help":
                        self.help_trigger.update({message.channel.id: message})
                    return await self.update_prefix_bot(message, locals()[f"check_{x}"], match["prefix"])

    @commands.Cog.listener("on_message")
    @event_check(command_count_check)
    async def command_count(self, message):
        """
        Checks if the message contains a valid prefix, which will wait for the bot to respond to count that message
        as a command.
        """
        limit = len(message.content) if len(message.content) < 31 else 31
        content_compiled = ctypes.create_string_buffer(message.content[:limit].encode("utf-8"))
        if not (result := search_prefix(self.compiled_pref, content_compiled)):
            return

        bots = await self.bot.pool_pg.fetch("SELECT * FROM bot_prefix WHERE prefix=$1", result)
        match_bot = {bot["bot_id"] for bot in bots if message.guild.get_member(bot["bot_id"])}

        def check(msg):
            return msg.author.bot and msg.channel == message.channel and msg.author.id in match_bot

        bot_found = []
        while message.created_at + datetime.timedelta(seconds=5) > datetime.datetime.utcnow():
            with contextlib.suppress(asyncio.TimeoutError):
                if m := await self.bot.wait_for("message", check=check, timeout=1):
                    bot_found.append(m.author.id)
                if len(bot_found) == len(match_bot):
                    break
        if not bot_found:
            return
        query = "INSERT INTO bot_usage_count VALUES($1, $2) " \
                "ON CONFLICT (bot_id) DO " \
                "UPDATE SET count=bot_usage_count.count + 1"
        values = [(x, 1) for x in bot_found]

        await self.bot.pool_pg.executemany(query, values)

    @commands.Cog.listener("on_message")
    @event_check(is_user)
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
                    print("This bot", int(result["id"]), "is already in the guild.")
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
        if result.bot.id not in self.bot.confirmed_bots:
            self.bot.confirmed_bots.add(result.bot.id)

    @commands.command(aliases=["owns", "userowns", "whatadds", "whatadded"],
                      brief="Shows what bot the user owns in discord.py.",
                      help="Shows the name of the bot that the user has added in discord.py. "
                           "This is useful for flexing for no reason."
                      )
    @is_discordpy()
    async def whatadd(self, ctx, author: discord.Member = None):
        if not author:
            author = ctx.author
        if author.bot:
            return await ctx.maybe_reply("That's a bot lol")
        query = "SELECT * FROM {}_bots WHERE author_id=$1"
        total_list = [await self.bot.pool_pg.fetch(query.format(x), author.id) for x in ("pending", "confirmed")]
        total_list = list(itertools.chain.from_iterable(total_list))

        async def get_member(bot_id):
            return ctx.guild.get_member(bot_id) or await self.bot.fetch_user(bot_id)
        list_bots = [BotAdded.from_json(await get_member(x["bot_id"]), **x) for x in total_list]
        embed = BaseEmbed.default(ctx, title=plural(f"{author}'s bot(s)", len(list_bots)))
        for dbot in list_bots:
            bot_id = dbot.bot.id
            value = ""
            if buse := await try_call(BotUsage.convert, ctx, str(bot_id)):
                value += f"**Usage:** `{buse.count}`\n"
            if bprefix := await try_call(BotPrefix.convert, ctx, str(bot_id)):
                value += f"**Prefix:** `{await self.clean_prefix(ctx, bprefix.prefix)}`"
            if value:
                embed.add_field(name=dbot, value=value, inline=False)
        embed.set_thumbnail(url=author.avatar_url)
        if not list_bots:
            embed.description = f"{author} doesnt own any bot here."
        await ctx.maybe_reply(embed=embed)

    @commands.command(aliases=["whoowns", "whosebot", "whoadds", "whoadded"],
                      brief="Shows who added the bot.",
                      help="Shows who added the bot, when they requested it and when the bot was added including the "
                           "jump url to the original request message in discord.py.")
    @is_discordpy()
    async def whoadd(self, ctx, bot: BotAdded):
        data = bot
        author = await try_call(commands.UserConverter().convert, ctx, str(data.author), exception=UserNotFound)
        embed = discord.Embed(title=f"{data.bot}",
                              color=self.bot.color)
        request = default_date(data.requested_at) if data.requested_at else None
        join = default_date(data.joined_at) if data.joined_at else None
        embed.set_thumbnail(url=data.bot.avatar_url)
        fields = (("Reason", data.reason),
                  ("Requested", request),
                  ("Joined", join),
                  ("Message Request", f"[jump]({data.jump_url})" if data.jump_url else None))

        if author:
            embed.set_author(name=author, icon_url=author.avatar_url)
        for name, value in fields:
            if value:
                embed.add_field(name=name, value=value, inline=False)

        await ctx.maybe_reply(embed=embed)

    async def clean_prefix(self, ctx, prefix):
        prefix = await pprefix(ctx, prefix)
        if prefix == "":
            prefix = "\u200b"
        return re.sub("`", "`\u200b", prefix)

    @commands.command(aliases=["wp", "whatprefixes"],
                      brief="Shows the bot prefix.",
                      help="Shows what the bot's prefix. This is sometimes inaccurate. Don't rely on it too much. "
                           "This also does not know it's aliases prefixes.")
    @commands.guild_only()
    async def whatprefix(self, ctx, member: BotPrefix):
        prefix = await self.clean_prefix(ctx, member.prefix)
        embed = BaseEmbed.default(ctx,
                                  title=f"{member}'s Prefix",
                                  description=f"`{prefix}`")

        await ctx.maybe_reply(embed=embed)

    @commands.command(aliases=["pu", "shares", "puse"],
                      brief="Shows the amount of bot that uses the same prefix.",
                      help="Shows the number of bot that shares a prefix between bots.")
    @commands.guild_only()
    async def prefixuse(self, ctx, prefix):
        instance_bot = await self.get_all_prefix(ctx.guild, prefix)
        prefix = await self.clean_prefix(ctx, prefix)
        desk = plural(f"There (is/are) `{len(instance_bot)}` bot(s) that use `{prefix}` as prefix", len(instance_bot))
        await ctx.maybe_reply(embed=BaseEmbed.default(ctx, description=desk))

    async def get_all_prefix(self, guild, prefix):
        """Quick function that gets the amount of bots that has the same prefix in a server."""
        data = await self.bot.pool_pg.fetch("SELECT * FROM bot_prefix WHERE prefix=$1", prefix)

        def mem(x):
            return guild.get_member(x)

        return [mem(x['bot_id']) for x in data if mem(x['bot_id'])]

    @commands.command(aliases=["pb", "prefixbots", "pbots"],
                      brief="Shows the name of bot(s) have a given prefix.",
                      help="Shows a list of bot(s) name that have a given prefix.")
    @commands.guild_only()
    async def prefixbot(self, ctx, prefix):
        instance_bot = await self.get_all_prefix(ctx.guild, prefix)
        list_bot = "\n".join(f"`{no + 1}. {x}`" for no, x in enumerate(instance_bot)) or "`Not a single bot have it.`"
        prefix = await self.clean_prefix(ctx, prefix)
        desk = f"Bot(s) with `{prefix}` as prefix\n{list_bot}"
        await ctx.maybe_reply(embed=BaseEmbed.default(ctx, description=plural(desk, len(list_bot))))

    @commands.command(aliases=["ap", "aprefix", "allprefixes"],
                      brief="Shows every bot's prefix in the server.",
                      help="Shows a list of every single bot's prefix in a server.")
    @commands.guild_only()
    async def allprefix(self, ctx):
        bots = await self.bot.pool_pg.fetch("SELECT * FROM bot_prefix")

        def mem(x):
            return ctx.guild.get_member(x)
        members = [BotPrefix(mem(bot["bot_id"]), bot["prefix"]) for bot in bots if mem(bot["bot_id"])]
        members.sort(key=lambda x: x.prefix)
        menu = MenuBase(source=AllPrefixes(members), delete_message_after=True)
        await menu.start(ctx)

    @commands.command(aliases=["bot_use", "bu", "botusage", "botuses"],
                      brief="Show's how many command calls for a bot.",
                      help="Show's how many command calls for a given bot. This works by counting how many times "
                           "a message is considered a command for that bot where that bot has responded in less than "
                           "2 seconds.")
    async def botuse(self, ctx, bot: BotUsage):
        embed = BaseEmbed.default(ctx,
                                  title=f"{bot}'s Usage",
                                  description=plural(f"`{bot.count}` command(s) has been called for **{bot}**.", bot.count))

        await ctx.maybe_reply(embed=embed)

    @commands.command(aliases=["bot_info", "bi", "botinfos"],
                      brief="Shows the bot information such as bot owner, prefixes, command usage.",
                      help="Shows the bot information such as bot owner, it's prefixes, the amount of command it has "
                           "been called, the reason on why it was added, the time it was requested and the time it "
                           "joined the server.")
    @is_discordpy()
    async def botinfo(self, ctx, bot: IsBot):
        # TODO: this is pretty terrible, optimise this
        titles = (("Bot Prefix", "{0.prefix}", BotPrefix),
                  ("Command Usage", "{0.count}", BotUsage),
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
                    embed.add_field(name=title, value=f"`{attrib.format(obj)}`", inline=False)

        embed.add_field(name="Created at", value=f"`{default_date(bot.created_at)}`")
        embed.add_field(name="Joined at", value=f"`{default_date(bot.joined_at)}`")
        await ctx.maybe_reply(embed=embed)

    @commands.command(aliases=["rba", "recentbot", "recentadd"],
                      brief="Shows a list of bots that has been added in a day.",
                      help="Shows a list of bots that has been added in a day along with the owner that requested it, "
                           "and how long ago it was added.")
    @is_discordpy()
    async def recentbotadd(self, ctx):
        def predicate(m):
            return m.bot and m.joined_at > ctx.message.created_at - datetime.timedelta(days=1)
        members = {m.id: m for m in filter(predicate, ctx.guild.members)}
        if not members:
            await ctx.maybe_reply(
                embed=BaseEmbed.default(
                    ctx,
                    title="Bots added today",
                    description="Looks like there are no bots added in the span of 24 hours."))
            return
        db_data = await self.bot.pool_pg.fetch("SELECT * FROM confirmed_bots WHERE bot_id=ANY($1::BIGINT[])", list(members))
        member_data = [BotAdded.from_json(bot=members[data["bot_id"]], **data) for data in db_data]
        member_data.sort(key=lambda x: x.joined_at)
        menu = MenuBase(source=BotAddedList(member_data), delete_message_after=True)
        await menu.start(ctx)

    @commands.command(aliases=["rht", "recenthelptrip", "recenttrigger"],
                      brief="Shows the last message that triggers a help command in a channel.",
                      help="Shows the last message that triggers a help command in a channel that it was called from. "
                           "Useful for finding out who's the annoying person that uses common prefix help command.")
    async def recenthelptrigger(self, ctx):
        if message := self.help_trigger.get(ctx.channel.id):
            embed_dict = {
                "title": "Recent Help Trigger",
                "description": f"**Author:** `{message.author}`\n"
                               f"**Message ID:** `{message.id}`\n"
                               f"**Command:** `{message.content}`\n"
                               f"**Message Link:** [`jump`]({message.jump_url})",
            }
        else:
            embed_dict = {
                "title": "Recent Help Trigger",
                "description": "There is no help command triggered recently."
            }
        await ctx.maybe_reply(embed=BaseEmbed.default(ctx, **embed_dict))


def setup(bot):
    bot.add_cog(FindBot(bot))
