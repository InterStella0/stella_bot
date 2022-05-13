from __future__ import annotations
import inspect
from dataclasses import dataclass
from typing import List

import discord
from aiogithub.objects import Repo, User
from discord.ext import commands

from cogs.helpful.baseclass import BaseHelpfulCog
from utils.buttons import ViewAuthor, InteractionPages, button, BaseButton
from utils.decorators import pages
from utils.useful import StellaContext, StellaEmbed, plural, count_source_lines, aislice, newline_chunker


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
        self.github_link = BaseButton(emoji='<:github:744345792172654643>', label="Github", url=view.data.url,
                                      stay_active=True, style=None)
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

    @button(emoji="<:house_mark:848227746378809354>", label="Menu", row=1, stay_active=True, style=discord.ButtonStyle.success)
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
    title = "**{0.target} at stella_bot/{0.file} on line {1}-{2}**".format(data, start, start + current - 1)
    url = f'{data.repo.html_url}/blob/master/{data.file}#L{start}-L{start + current - 1}'
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
        leading = f"\n...({amount - 6} lines more)" if (amount := len(ori_codeblock)) > 5 else ""
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
        linecodes = newline_chunker(self.data.codeblock, width=1900, max_newline=10)
        nolinecodes = [*map(len, map(str.splitlines, linecodes))]
        source = SourcePaginator(source_format(linecodes), self, nolinecodes)
        await source.start(self.context)


class SourceCog(BaseHelpfulCog):
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
        url = f'{repo.html_url}/blob/master/{location}#L{firstlineno}-L{firstlineno + len(lines) - 1}'
        data = SourceData(location, url, ''.join(lines), content, firstlineno, firstlineno + len(lines) - 1, repo, author)
        await SourceMenu(ctx, data).send()
