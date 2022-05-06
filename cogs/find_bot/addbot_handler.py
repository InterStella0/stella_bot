from __future__ import annotations
import asyncio
import datetime
import itertools
import operator
import re
import textwrap
from typing import Optional, Callable, Any, Union, List, Dict

import discord
from discord.ext import commands
from discord.ext.commands import UserNotFound

from utils import greedy_parser
from utils.buttons import InteractionPages
from utils.new_converters import BotPrefixes, BotCommands, IsBot
from utils.useful import try_call, StellaContext, default_date, plural, StellaEmbed, realign, aware_utc
from .baseclass import FindBotCog
from .converters import BotListReverse, BotPendingFlag, clean_prefix
from .decorators import is_user, deco_event
from .errors import NoPendingBots
from .models import BotAdded, DeletedUser
from utils.decorators import wait_ready, event_check, DISCORD_PY, is_discordpy, pages


def dpy_bot() -> deco_event:
    """Event check for dpy_bots"""
    return event_check(lambda _, member: member.bot and member.guild.id == DISCORD_PY)


@pages(per_page=6)
async def bot_added_list(self, menu: InteractionPages, entries: List[BotAdded]) -> discord.Embed:
    """Menu for recentbotadd command."""
    offset = menu.current_page * self.per_page
    contents = ((f"{b.author}", f'**{b}** {discord.utils.format_dt(b.joined_at, "R")}')
                for i, b in enumerate(entries, start=offset))
    return StellaEmbed(title="Bots added today", fields=contents)


@pages(per_page=10)
async def all_bot_count(self, menu: InteractionPages, entries: List[BotCommands]) -> discord.Embed:
    """Menu for botrank command."""
    key = "(\u200b|\u200b)"
    offset = menu.current_page * self.per_page
    content = "`{no}. {b} {key} {b.total_usage}`"
    contents = [content.format(no=i+1, b=b, key=key) for i, b in enumerate(entries, start=offset)]
    return StellaEmbed(title="Bot Command Rank",
                       description="\n".join(realign(contents, key)))


@pages()
async def bot_pending_list(_, menu: Any, entry: Dict[str, Union[datetime.datetime, int, str]]) -> discord.Embed:
    stellabot = menu.ctx.bot
    bot_id = entry["bot_id"]
    if not (bot := menu.cached_bots.get(bot_id)):
        if not (bot := stellabot.get_user(bot_id)):
            try:
                bot = await stellabot.fetch_user(bot_id)
            except discord.NotFound:
                bot = DeletedUser(bot_id)
            finally:
                # since this cache is only ever used here, it's safe to put DeletedUser objects there
                # if it ever gets used anywhere else, care should be taken
                menu.cached_bots[bot_id] = bot

    fields = (("Requested by", stellabot.get_user(entry["author_id"]) or "idk really"),
              ("Reason", textwrap.shorten(entry["reason"], width=1000, placeholder="...")),
              ("Created at", aware_utc(bot.created_at)),
              ("Requested at", aware_utc(entry["requested_at"])),
              ("Message", f"[jump]({entry['jump_url']})"))
    embed = StellaEmbed(title=f"{bot}(`{bot.id}`)", fields=fields)
    embed.set_thumbnail(url=bot.display_avatar)
    return embed


class AddBotHandler(FindBotCog):
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

    async def update_pending(self, result: BotAdded) -> None:
        """Insert a new addbot request which is yet to enter the discord.py server."""
        query = """INSERT INTO pending_bots VALUES($1, $2, $3, $4, $5) 
                   ON CONFLICT (bot_id) DO
                   UPDATE SET reason = $3, requested_at=$4, jump_url=$5"""
        value = (result.bot.id, result.author.id, result.reason, result.requested_at, result.jump_url)
        await self.bot.pool_pg.execute(query, *value)
        if result.bot.id not in self.bot.pending_bots:
            self.bot.pending_bots.add(result.bot.id)

    @commands.Cog.listener("on_member_remove")
    @wait_ready()
    @dpy_bot()
    async def remove_bot_tracker(self, member: discord.Member):
        """Since there is no reason to store these bots after they left, best to delete them"""
        if member.id in self.bot.confirmed_bots:
            await self.bot.pool_pg.execute("DELETE FROM confirmed_bots WHERE bot_id=$1", member.id)
            self.bot.confirmed_bots.remove(member.id)

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
                                BotAdded(
                                    author=message.author,
                                    bot=member,
                                    reason=reason,
                                    requested_at=message.created_at.replace(tzinfo=None),
                                    jump_url=message.jump_url)
                            )
                        return

            else:
                if member := get_member(int(result["id"])):
                    if int(result["id"]) not in self.bot.confirmed_bots and \
                            await self.check_author(member.id, message.author.id, "confirmed_bots"):
                        new_add_bot = BotAdded(
                            author=message.author,
                            bot=member,
                            reason=reason,
                            requested_at=message.created_at.replace(tzinfo=None),
                            jump_url=message.jump_url,
                            joined_at=member.joined_at.replace(tzinfo=None)
                        )
                        await self.update_confirm(new_add_bot)
                    return
                member = await try_call(self.bot.fetch_user, int(result["id"]), exception=discord.NotFound)
            if all((reason, member and member.bot)):
                join = None
                if isinstance(member, discord.Member):
                    if (join := member.joined_at) < message.created_at:
                        return
                return BotAdded(
                    author=message.author,
                    bot=member,
                    reason=reason,
                    requested_at=message.created_at.replace(tzinfo=None),
                    jump_url=message.jump_url,
                    joined_at=join
                )

    async def check_author(self, bot_id: int, author_id: int, mode: str) -> Optional[bool]:
        """Checks if the author of a bot is the same as what is stored in the database."""
        if data := await self.bot.pool_pg.fetchrow(f"SELECT * FROM {mode} WHERE bot_id=$1", bot_id):
            old_author = data['author_id']
            return old_author == author_id

    @greedy_parser.command(
        aliases=["rba", "recentbot", "recentadd"],
        brief="Shows a list of bots that has been added in a day.",
        help="Shows a list of bots that has been added in a day along with the owner that requested it, "
             "and how long ago it was added.")
    @is_discordpy()
    async def recentbotadd(self, ctx: StellaContext, *, flags: BotListReverse):
        reverse = flags.reverse

        def predicate(m):
            return m.bot and \
                   m.joined_at.replace(tzinfo=None) > \
                   ctx.message.created_at.replace(tzinfo=None) - datetime.timedelta(days=1)

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
    async def botrank(self, ctx: StellaContext, bot: greedy_parser.UntilFlag[BotCommands] = None, *, flags: BotListReverse):
        reverse = flags.reverse
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
        if not bots:
            raise NoPendingBots()

        menu = InteractionPages(bot_pending_list(bots))
        if data := flag.bot:
            bot_target = data.bot.id
            get_bot_id = operator.itemgetter("bot_id")
            # It's impossible for it to be None, both came from pending_bots table. Unless race condition occurs
            index, _ = discord.utils.find(lambda b: get_bot_id(b[1]) == bot_target, enumerate(bots))
            menu.current_page = index

        menu.cached_bots = self.cached_bots
        await menu.start(ctx)

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
        embed = StellaEmbed.default(ctx, title=plural(f"{author}'s bot(s)", len(list_bots)))
        for dbot in list_bots:
            bot_id = dbot.bot.id
            value = ""
            if bprefix := await try_call(BotPrefixes.convert, ctx, str(bot_id)):
                value += f"**Most Used Prefix:** `{clean_prefix(ctx, bprefix.prefix)}`\n"
            if buse := await try_call(BotCommands.convert, ctx, str(bot_id)):
                high_use = buse.highest_command
                value += f"**Top Command:** `{high_use}`[`{buse.get_command(high_use)}`]\n"
                value += f"**Total Usage:** `{buse.total_usage}`\n"

            value += f"**Created at:** `{default_date(dbot.bot.created_at)}`"
            embed.add_field(name=dbot, value=value, inline=False)
        embed.set_thumbnail(url=author.display_avatar)
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
        embed.set_thumbnail(url=data.bot.display_avatar)

        def or_none(condition: bool, func: Callable[[bool], Any]) -> Optional[Any]:
            if condition:
                return func(condition)
        if not (reason := data.reason):
            reason = "Unknown"

        fields = (("Added by", f"{author.mention} (`{author.id}`)"),
                  ("Reason", textwrap.shorten(reason, width=1000, placeholder='...')),
                  ("Requested", or_none(data.requested_at, default_date)),
                  ("Joined", or_none(data.joined_at, default_date)),
                  ("Message Request", or_none(data.jump_url, "[jump]({})".format)))

        await ctx.embed(embed=embed, fields=fields)
