from __future__ import annotations
import inspect
from dataclasses import dataclass

import discord
import humanize
import datetime
import itertools

from aiogithub.objects import Repo, User
from pygit2 import Repository, GIT_SORT_TOPOLOGICAL
from discord.ext import commands

from utils.decorators import pages
from utils.useful import StellaEmbed, StellaContext, aware_utc, text_chunker, count_source_lines, plural, aislice
from utils.errors import BypassError
from utils.buttons import InteractionPages, PersistentRespondView, ViewAuthor, button
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from main import StellaBot

SOURCE_URL = 'https://github.com/InterStella0/stella_bot'


@dataclass
class SourceData:
    file: str
    url: str
    codeblock: str
    target: str
    lineno: int
    lastlineno: int
    repo: Repo
    author: User


class SourcePaginator(InteractionPages):
    def __init__(self, source, view: SourceMenu, nolines: List[int]):
        super().__init__(source, delete_after=False, message=view.message)
        self.view = view
        self.nolines = nolines
        self.github_link = discord.ui.Button(emoji='<:github:744345792172654643>', label="Github", url=view.data.url)
        self.add_item(self.github_link)

    @button(emoji='<:stop_check:754948796365930517>', style=discord.ButtonStyle.blurple)
    async def stop_page(self, interaction: discord.Interaction, __: discord.ui.Button) -> None:
        if self.delete_after:
            await self.message.delete(delay=0)
            return

        for x in self.children:
            if not isinstance(x, discord.ui.Button) or x.label != "Menu":
                x.disabled = True

        await interaction.response.edit_message(view=self)

    @button(emoji="<:house_mark:848227746378809354>", label="Menu", row=1, stay_active=True)
    async def on_menu_click(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = self.view.form_embed()
        await interaction.response.edit_message(content=None, embed=embed, view=self.view)
        self.stop()


@pages()
async def source_format(self, menu, items):
    lines = sum(menu.nolines[:menu.current_page])
    current = menu.nolines[menu.current_page]
    data = menu.view.data
    start = data.lineno + lines
    title = "**{0.target} at stella_bot/{0.file} on line {1} - {2}**".format(data, start, start + current - 1)
    url = f'{SOURCE_URL}/blob/master/{data.file}#L{start}-L{start + current - 1}'
    menu.github_link.url = url
    codeblock = f"```py\n{items}```"
    return f"{title}\n{codeblock}"


class SourceMenu(ViewAuthor):
    def __init__(self, ctx: StellaContext, data: SourceData):
        super().__init__(ctx)
        self.data = data
        self.message = None
        self.add_item(discord.ui.Button(emoji='<:github:744345792172654643>', label="Github", url=data.url))

    def form_embed(self):
        data = self.data
        ori_codeblock = data.codeblock.splitlines()
        shorten = ori_codeblock[:5]
        leading = f"\n...({amount} lines more)" if (amount := len(ori_codeblock)) > 5 else ""
        return StellaEmbed.default(
            self.context,
            title="{0.target} at stella_bot/{0.file}".format(data),
            description="```py\n" + f"\n".join(shorten) + leading + "```"
        ).set_author(
            name=data.repo.full_name, icon_url=data.author.avatar_url, url=data.repo.html_url
        )

    async def send(self):
        self.message = await self.context.maybe_reply(embed=self.form_embed(), view=self)

    @discord.ui.button(emoji='\U0001f5a5', label="Show Code", style=discord.ButtonStyle.green)
    async def on_show_click(self, interaction: discord.Interaction, button: discord.ui.Button):
        linecodes = text_chunker(self.data.codeblock, width=1000, max_newline=10)
        nolinecodes = [*map(len, map(str.splitlines, linecodes))]
        source = SourcePaginator(source_format(linecodes), self, nolinecodes)
        await source.start(self.context)


class Miscellaneous(commands.Cog):
    def __init__(self, bot: StellaBot):
        self.bot = bot
        self.cooldown_report = commands.CooldownMapping.from_cooldown(5, 30, commands.BucketType.user)
        self.stella_github: Optional[Repo] = None

    async def cog_load(self) -> None:
        self.stella_github = await self.bot.git.get_repo("InterStella0", "stella_bot")

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
    async def source(self, ctx: StellaContext, *, content: str = None):
        repo = self.stella_github
        author = await self.bot.git.get_user(repo.owner.login)
        if not content:
            embed = StellaEmbed.default(
                ctx,
                title=f"Github - {repo.full_name}",
                description=repo.description,
                url=repo.html_url
            )
            embed.set_thumbnail(url=ctx.me.display_avatar)
            embed.set_author(name=f"Made by {author.name}", icon_url=author.avatar_url, url=author.html_url)
            embed.add_field(name="Line of codes",value=f"{count_source_lines('.'):,}")
            embed.add_field(name=plural("Star(s)", repo.stargazers_count), value=repo.stargazers_count)
            embed.add_field(name=plural("Fork(s)", repo.forks_count), value=repo.forks_count)
            embed.add_field(name="Language", value=repo.language)
            value = [f'{u.login}(`{u.contributions}`)' async for u in aislice(repo.get_contributors(), 3)]
            embed.add_field(name="Top Contributors", value="\n".join(f"{i}. {e}" for i, e in enumerate(value, start=1)))
            return await ctx.maybe_reply(embed=embed)

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

        lines, firstlineno = inspect.getsourcelines(src)
        location = module.replace('.', '/') + '.py'  # type: ignore
        url = f'{SOURCE_URL}/blob/master/{location}#L{firstlineno}-L{firstlineno + len(lines) - 1}'
        data = SourceData(location, url, ''.join(lines), content, firstlineno, firstlineno + len(lines) - 1, repo, author)
        await SourceMenu(ctx, data).send()

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
        await ctx.embed(embed=embed)
