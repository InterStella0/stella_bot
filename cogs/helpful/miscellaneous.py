from __future__ import annotations
import contextlib
import inspect
import json
import re

import discord
import copy
import humanize
import datetime
import textwrap
import itertools

from pygit2 import Repository, GIT_SORT_TOPOLOGICAL
from discord.ext import commands

from utils.useful import StellaEmbed, empty_page_format, StellaContext, aware_utc
from utils.errors import BypassError
from utils.buttons import InteractionPages, PersistentRespondView
from utils import flags as flg
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from main import StellaBot


class SourceFlag(commands.FlagConverter):
    code: Optional[bool] = flg.flag(
        help="Shows the code block instead of the link. Accepts True or False, defaults to False if not stated.",
        default=False
    )


class Miscellaneous(commands.Cog):
    def __init__(self, bot: StellaBot):
        self.bot = bot
        self.cooldown_report = commands.CooldownMapping.from_cooldown(5, 30, commands.BucketType.user)

    @commands.command(aliases=["ping", "p"],
                      help="Shows the bot latency from the discord websocket.")
    async def pping(self, ctx: StellaContext):
        await ctx.embed(
            title="PP",
            description=f"Your pp lasted `{self.bot.latency * 1000:.2f}ms`"
        )

    @commands.command(aliases=["up"],
                      help="Shows the bot uptime from when it was started.")
    async def uptime(self, ctx: StellaContext):
        c_uptime = datetime.datetime.utcnow() - self.bot.uptime
        await ctx.embed(
            title="Uptime",
            description=f"Current uptime: `{humanize.precisedelta(c_uptime)}`"
        )

    @commands.command(aliases=["src", "sources"],
                      brief="Shows the source code link in github.",
                      help="Shows the source code in github given the cog/command name. "
                           "Defaults to the stella_bot source code link if not given any argument. "
                           "It accepts 2 types of content, the command name, or the Cog method name. "
                           "Cog method must specify it's Cog name separate by a period and it's method.")
    async def source(self, ctx: StellaContext, content: str = None, *, flags: SourceFlag):
        source_url = 'https://github.com/InterStella0/stella_bot'
        if not content:
            return await ctx.embed(title="here's the entire repo", description=source_url)
        src, module = None, None

        def command_check(command):
            nonlocal src, module
            if command == 'help':
                src = type(self.bot.help_command)
                module = src.__module__
            else:
                obj = self.bot.get_command(command.replace('.', ' '))
                if obj and obj.cog_name != "Jishaku":
                    src = obj.callback.__code__
                    module = obj.callback.__module__

        def cog_check(content):
            nonlocal src, module
            if "." not in content:
                return
            cog, _, method = content.partition(".")
            cog = self.bot.get_cog(cog)
            if method_func := getattr(cog, method, None):
                module = method_func.__module__
                target = getattr(method_func, "callback", method_func)
                src = target.__code__

        for func in (command_check, cog_check):
            if not src:
                func(content)
        if module is None:
            return await ctx.maybe_reply(f"Method {content} not found.")
        show_code = flags.code
        if show_code:
            param = {"text": inspect.getsource(src), "width": 1900, "replace_whitespace": False}
            list_codeblock = [f"```py\n{cb}\n```" for cb in textwrap.wrap(**param)]
            menu = InteractionPages(empty_page_format(list_codeblock))
            await menu.start(ctx)
        else:
            lines, firstlineno = inspect.getsourcelines(src)
            location = module.replace('.', '/') + '.py'  # type: ignore
            url = f'<{source_url}/blob/master/{location}#L{firstlineno}-L{firstlineno + len(lines) - 1}>'
            await ctx.embed(title=f"Here's uh, {content}", description=f"[Click Here]({url})")

    @commands.command(help="Gives you the invite link")
    async def invite(self, ctx: StellaContext):
        embed = StellaEmbed.default(
            ctx,
            title="Invite Me",
            description='You can invite me by clicking on the the "Invite Me".',
            url=discord.utils.oauth_url(ctx.me.id)
        )
        embed.set_author(name=self.bot.stella, icon_url=self.bot.stella.display_avatar)
        embed.set_thumbnail(url=ctx.me.display_avatar)
        embed.add_field(name="Total Guilds", value=len(self.bot.guilds))
        embed.add_field(name="Total Users", value=len(self.bot.users))
        await ctx.maybe_reply(embed=embed)

    @commands.command(help="Reports to the owner through the bot. Automatic blacklist if abuse.")
    @commands.cooldown(1, 60, commands.BucketType.user)
    async def report(self, ctx: StellaContext, *, message: str):
        usure = f"Are you sure you wanna send this message to `{self.bot.stella}`?"
        if not await ctx.confirmation(usure, delete_after=True):
            await ctx.confirmed()
            return

        try:
            embed = StellaEmbed.default(
                ctx,
                title=f"Report sent to {self.bot.stella}",
                description=f"**You sent:** {message}"
            )
            embed.set_author(name=f"Any respond from {self.bot.stella} will be through DM.")
            interface = await ctx.author.send(embed=embed)
        except discord.Forbidden:
            died = "Unable to send a DM, please enable DM as it is crucial for the report."
            raise commands.CommandError(died)
        else:
            query = "INSERT INTO reports VALUES(DEFAULT, $1, False, $2) RETURNING report_id"
            created_at = ctx.message.created_at.replace(tzinfo=None)
            report_id = await self.bot.pool_pg.fetchval(query, ctx.author.id, created_at, column='report_id')

            embed = StellaEmbed.default(ctx, title=f"Reported from {ctx.author} ({report_id})", description=message)
            msg = await self.bot.stella.send(embed=embed, view=PersistentRespondView(self.bot))
            await ctx.confirmed()

            query_msg = "INSERT INTO report_respond VALUES($1, $2, $3, $4, $5)"
            msg_values = (report_id, ctx.author.id, msg.id, interface.id, message)
            await self.bot.pool_pg.execute(query_msg, *msg_values)

    @report.error
    async def report_error(self, ctx: StellaContext, error: commands.CommandError):
        if isinstance(error, commands.CommandOnCooldown):
            if self.cooldown_report.update_rate_limit(ctx.message):
                await self.bot.add_blacklist(ctx.author.id, "Spamming cooldown report message.")
        self.bot.dispatch("command_error", ctx, BypassError(error))

    @commands.command(aliases=["aboutme"], help="Shows what the bot is about. It also shows recent changes and stuff.")
    async def about(self, ctx: StellaContext):
        REPO_URL = "https://github.com/InterStella0/stella_bot"
        embed = StellaEmbed.default(
            ctx,
            title=f"About {self.bot.user}",
            description=self.bot.description.format(self.bot.stella),
            url=REPO_URL
        )
        payload = {
            "bot_name": str(self.bot.user),
            "name": str(self.bot.stella),
            "author_avatar": ctx.author.display_avatar.url,
            "author_avatar_hash": ctx.author.display_avatar.key,
            "author_name": str(ctx.author)
        }
        banner = await self.bot.ipc_client.request("generate_banner", **payload)
        if isinstance(banner, str):
            embed.set_image(url=banner)
        repo = Repository('.git')
        HEAD = repo.head.target
        COMMIT_AMOUNT = 4
        iterator = itertools.islice(repo.walk(HEAD, GIT_SORT_TOPOLOGICAL), COMMIT_AMOUNT)

        def format_commit(c):
            time = datetime.datetime.fromtimestamp(c.commit_time)
            repo_link = f"{REPO_URL}/commit/{c.hex}"
            message, *_ = c.message.partition("\n")
            return f"[`{c.hex[:6]}`] [{message}]({repo_link}) ({aware_utc(time, mode='R')})"

        embed.add_field(name="Recent Changes", value="\n".join(map(format_commit, iterator)), inline=False)
        embed.add_field(name="Launch Time", value=f"{aware_utc(self.bot.uptime, mode='R')}")
        embed.add_field(name="Bot Ping", value=f"{self.bot.latency * 1000:.2f}ms")
        bots = sum(u.bot for u in self.bot.users)
        content = f"`{len(self.bot.guilds):,}` servers, `{len(self.bot.users) - bots:,}` users, `{bots:,}` bots"
        embed.add_field(name="Users", value=content)
        await ctx.embed(embed=embed)
