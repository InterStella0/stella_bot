from __future__ import annotations

import datetime
import itertools
import time

import discord
import humanize
from discord.ext import commands
from pygit2 import Repository, GIT_SORT_TOPOLOGICAL

from cogs.helpful.baseclass import BaseHelpfulCog
from utils.buttons import PersistentRespondView
from utils.errors import BypassError
from utils.useful import StellaEmbed, StellaContext, aware_utc

SOURCE_URL = 'https://github.com/InterStella0/stella_bot'


class Miscellaneous(BaseHelpfulCog):
    @commands.command(aliases=["pping", "p"],
                      help="Shows the bot latency from the discord websocket.")
    async def ping(self, ctx: StellaContext):
        async def measure_ping(coro):
            start = time.monotonic()
            await coro
            return time.monotonic() - start

        db = await measure_ping(ctx.bot.pool_pg.fetch("SELECT 1"))
        websocket = await measure_ping(self.bot.ipc_client.request("ping"))
        api = await measure_ping(self.bot.stella_api._request("GET", "/"))
        await ctx.embed(
            title="<:checkmark:753619798021373974> Ping",
            fields=[
                ("Discord", f"`{self.bot.latency * 1000:.2f}`ms"),
                ("Database", f"`{db * 1000:.2f}`ms"),
                ("Stella Websocket", f"`{websocket * 1000:.2f}`ms"),
                ("Stella API", f"`{api * 1000:.2f}`ms"),
            ],
            field_inline=True
        )

    @commands.command(aliases=["up"],
                      help="Shows the bot uptime from when it was started.")
    async def uptime(self, ctx: StellaContext):
        c_uptime = datetime.datetime.utcnow() - self.bot.uptime
        await ctx.embed(
            title="Uptime",
            description=f"Current uptime: `{humanize.precisedelta(c_uptime)}`"
        )

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
        embed = StellaEmbed.default(
            ctx,
            title=f"About {self.bot.user}",
            description=self.bot.description.format(self.bot.stella),
            url=SOURCE_URL
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
            repo_link = f"{SOURCE_URL}/commit/{c.hex}"
            message, *_ = c.message.partition("\n")
            return f"[`{c.hex[:6]}`] [{message}]({repo_link}) ({aware_utc(time, mode='R')})"

        embed.add_field(name="Recent Changes", value="\n".join(map(format_commit, iterator)), inline=False)
        embed.add_field(name="Launch Time", value=f"{aware_utc(self.bot.uptime, mode='R')}")
        embed.add_field(name="Bot Ping", value=f"{self.bot.latency * 1000:.2f}ms")
        bots = sum(u.bot for u in self.bot.users)
        content = f"`{len(self.bot.guilds):,}` servers, `{len(self.bot.users) - bots:,}` users, `{bots:,}` bots"
        embed.add_field(name="Users", value=content)
        stella_owner = await self.bot.git.get_user(self.stella_github.owner.login)
        embed.set_author(name=f"By {stella_owner.name}", icon_url=stella_owner.avatar_url)
        view = discord.ui.View()
        button1 = discord.ui.Button(
            emoji="<:hmpf:946828106675662888>", label="Website", url="https://www.interstella.online"
        )
        button2 = discord.ui.Button(
            emoji="<:github:744345792172654643>", label="Github", url=SOURCE_URL
        )
        view.add_item(button1).add_item(button2)
        await ctx.embed(embed=embed, view=view)
