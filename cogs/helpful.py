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
from fuzzywuzzy import process
from discord.ext import commands
from utils.useful import BaseEmbed, plural, empty_page_format, unpack, StellaContext
from utils.errors import CantRun
from utils.parser import ReplReader
from utils.greedy_parser import UntilFlag, command
from utils.buttons import BaseButton, InteractionPages, MenuViewBase, ViewButtonIteration
from utils.menus import ListPageInteractionBase, HelpMenuBase, MenuViewInteractionBase
from utils import flags as flg
from collections import namedtuple
from jishaku.codeblocks import codeblock_converter
from typing import Any, Tuple, List, Union, Optional, Literal, Dict, TYPE_CHECKING

if TYPE_CHECKING:
    from main import StellaBot

CommandGroup = Union[commands.Command, commands.Group]
CogHelp = namedtuple("CogAmount", 'name commands emoji description')
CommandHelp = namedtuple("CommandHelp", 'command brief command_obj')
emoji_dict = {"Bots": '<:robot_mark:848257366587211798>',
              "Useful": '<:useful:848258928772776037>',
              "Helpful": '<:helpful:848260729916227645>',
              "Statistic": '<:statis_mark:848262218554408988>',
              "Myself": '<:me:848262873783205888>',
              None: '<:question:848263403604934729>'}
home_emoji = '<:house_mark:848227746378809354>'


class HelpSource(ListPageInteractionBase):
    """This ListPageSource is meant to be used with view, format_page method is called first
       after that would be the format_view method which must return a View, or None to remove."""

    async def format_page(self, menu: "HelpMenu", entry: Tuple[commands.Cog, List[CommandHelp]]) -> discord.Embed:
        """This is for the help command ListPageSource"""
        cog, list_commands = entry
        new_line = "\n"
        embed = discord.Embed(title=f"{getattr(cog, 'qualified_name', 'No')} Category",
                              description=new_line.join(f'{command_help.command}{new_line}{command_help.brief}'
                                                        for command_help in list_commands),
                              color=menu.bot.color)
        author = menu.ctx.author
        return embed.set_footer(text=f"Requested by {author}", icon_url=author.avatar.url)

    async def format_view(self, menu: "HelpMenu", entry: Tuple[Optional[commands.Cog], List[CommandHelp]]) -> "HelpMenuView":
        if not menu._running:
            return
        _, list_commands = entry
        commands = [c.command_obj.name for c in list_commands]
        menu.view.clear_items()
        menu.view.add_item(HomeButton(style=discord.ButtonStyle.success, selected="Home", row=None, emoji=home_emoji))
        for c in commands:
            menu.view.add_item(HelpSearchButton(style=discord.ButtonStyle.secondary, selected=c, row=None))

        return menu.view


class HelpMenuView(MenuViewBase):
    """This class is responsible for starting the view + menus activity for the help command.
       This accepts embed, help_command, context, page_source, dataset and optionally Menu.
       """

    def __init__(self, embed: discord.Embed, help_object: commands.HelpCommand, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.original_embed = embed
        self.help_command = help_object


class HomeButton(BaseButton):
    """This button redirects the view from the menu, into the category section, which
       adds the old buttons back."""

    async def callback(self, interaction: discord.Interaction) -> None:
        self.view.clear_items()
        for b in self.view.old_items:
            self.view.add_item(b)
        await interaction.message.edit(view=self.view, embed=self.view.original_embed)


class HelpButton(BaseButton):
    """This Button update the menu, and shows a list of commands for the cog.
       This saves the category buttons as old_items and adds relevant buttons that
       consist of HomeButton, and HelpSearchButton."""

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        bot = view.help_command.context.bot
        select = self.selected or "No Category"
        cog = bot.get_cog(select)
        data = [(cog, commands_list) for commands_list in view.mapper.get(cog)]
        self.view.old_items = copy.copy(self.view.children)
        await view.update(self, interaction, data)


class HelpSearchView(ViewButtonIteration):
    """This view class is specifically for command_callback method"""

    def __init__(self, help_object: commands.HelpCommand, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.help_command = help_object
        self.ctx = help_object.context
        self.bot = help_object.context.bot


class HelpSearchButton(BaseButton):
    """This class is used inside a help command that shows a help for a specific command.
       This is also used inside help search command."""

    async def callback(self, interaction: discord.Interaction) -> None:
        help_obj = self.view.help_command
        bot = help_obj.context.bot
        command = bot.get_command(self.selected)
        embed = help_obj.get_command_help(command)
        await interaction.response.send_message(content=f"Help for **{self.selected}**", embed=embed, ephemeral=True)


class HelpMenu(MenuViewInteractionBase):
    """MenuPages class that is specifically for the help command."""

    async def on_information_show(self, payload: discord.RawReactionActionEvent) -> None:
        ctx = self.ctx
        embed = BaseEmbed.default(
            ctx,
            title="Information",
            description="This shows each commands in this bot. Each page is a category that shows "
                        "what commands that the category have."
        )
        curr = self.current_page + 1 if (p := self.current_page > -1) else "cover page"
        pa = "page" if p else "the"
        embed.set_author(icon_url=ctx.bot.user.avatar.url,
                         name=f"You were on {pa} {curr}")
        nav = '\n'.join(f"{e} {b.action.__doc__}" for e, b in super().buttons.items())
        embed.add_field(name="Navigation:", value=nav)
        await self.message.edit(embed=embed, allowed_mentions=discord.AllowedMentions(replied_user=False))


class CogMenu(HelpMenuBase):
    """This is a MenuPages class that is used only in Cog help command. All it has is custom information and
       custom initial message."""

    async def on_information_show(self, payload: discord.RawReactionActionEvent) -> None:
        ctx = self.ctx
        embed = BaseEmbed.default(ctx,
                                  title="Information",
                                  description="This shows each commands in this category. Each page is a command that shows "
                                              "what's the command is about and a demonstration of usage.")
        curr = self.current_page + 1 if (p := self.current_page > -1) else "cover page"
        pa = "page" if p else "the"
        embed.set_author(icon_url=ctx.bot.user.avatar.url,
                         name=f"You were on {pa} {curr}")
        nav = '\n'.join(f"{e} {b.action.__doc__}" for e, b in super().buttons.items())
        embed.add_field(name="Navigation:", value=nav)
        await self.message.edit(embed=embed, allowed_mentions=discord.AllowedMentions(replied_user=False))


class StellaBotHelp(commands.DefaultHelpCommand):
    def __init__(self, **options: Any):
        super().__init__(**options)
        with open("d_json/help.json") as r:
            self.help_gif = json.load(r)

    def get_command_signature(self, command: CommandGroup, ctx: Optional[StellaContext] = None) -> str:
        """Method to return a commands name and signature"""
        if not ctx:
            prefix = self.context.clean_prefix
            if not command.signature and not command.parent:
                return f'`{prefix}{command.name}`'
            if command.signature and not command.parent:
                return f'`{prefix}{command.name}` `{command.signature}`'
            if not command.signature and command.parent:
                return f'`{prefix}{command.parent}` `{command.name}`'
            else:
                return f'`{prefix}{command.parent}` `{command.name}` `{command.signature}`'
        else:
            def get_invoke_with():
                msg = ctx.message.content
                prefixmax = re.match(f'{re.escape(ctx.prefix)}', ctx.message.content).regs[0][1]
                return msg[prefixmax:msg.rindex(ctx.invoked_with)]

            if not command.signature and not command.parent:
                return f'{ctx.prefix}{ctx.invoked_with}'
            if command.signature and not command.parent:
                return f'{ctx.prefix}{ctx.invoked_with} {command.signature}'
            if not command.signature and command.parent:
                return f'{ctx.prefix}{get_invoke_with()}{ctx.invoked_with}'
            else:
                return f'{ctx.prefix}{get_invoke_with()}{ctx.invoked_with} {command.signature}'

    def get_help(self, command: CommandGroup, brief: Optional[bool] = True) -> str:
        """Gets the command short_doc if brief is True while getting the longer help if it is false"""
        real_help = command.help or "This command is not documented."
        return real_help if not brief else command.short_doc or real_help

    def get_demo(self, command: CommandGroup) -> str:
        """Gets the gif demonstrating the command."""
        com = command.name
        if com not in self.help_gif:
            return ""
        return f"{self.context.bot.help_src}/{self.help_gif[com]}/{com}_help.gif"

    def get_aliases(self, command: CommandGroup) -> List[str]:
        """This isn't even needed jesus christ"""
        return command.aliases

    def get_old_flag_help(self, command: CommandGroup) -> List[str]:
        """Gets the flag help if there is any."""

        def c(x):
            return "_OPTIONAL" not in x.dest

        return ["**--{0.dest} |** {0.help}".format(action) for action in command.callback._def_parser._actions if
                c(action)]

    def get_flag_help(self, command: CommandGroup) -> Tuple[List[str], List[str]]:
        required_flags = []
        optional_flags = []
        if param := flg.find_flag(command):
            for name, flags in param.annotation.__commands_flags__.items():
                not_documented = "This flag is not documented."
                description = getattr(flags, "help", not_documented) or not_documented
                formatted = f"**{':** | **'.join(itertools.chain([name], flags.aliases))}:** **|** {description}"
                list_append = (required_flags, optional_flags)[command._is_typing_optional(flags.annotation)]
                list_append.append(formatted)
        return required_flags, optional_flags

    async def send_bot_help(self, mapping: Dict[Optional[commands.Cog], CommandGroup]) -> None:
        """Gets called when `uwu help` is invoked"""

        def get_command_help(com: CommandGroup) -> CommandHelp:
            signature = self.get_command_signature(com)
            desc = self.get_help(com)
            return CommandHelp(signature, desc, com)

        def get_cog_help(cog: Optional[commands.Cog],
                         cog_commands: List[List[CommandGroup]]) -> CogHelp:
            cog_name_none = getattr(cog, "qualified_name", None)
            cog_name = cog_name_none or "No Category"
            cog_description = getattr(cog, 'description', "Not documented")
            cog_emoji = emoji_dict.get(cog_name_none) or emoji_dict[None]
            cog_amount = len([*unpack(cog_commands)])
            return CogHelp(cog_name, cog_amount, cog_emoji, cog_description)

        ctx = self.context
        bot = ctx.bot
        EACH_PAGE = 4
        command_data = {}
        for cog, unfiltered_commands in mapping.items():
            if list_commands := await self.filter_commands(unfiltered_commands, sort=True):
                lists = command_data.setdefault(cog, [])
                for chunks in discord.utils.as_chunks(list_commands, EACH_PAGE):
                    lists.append([*map(get_command_help, chunks)])

        sort_cog = [*itertools.starmap(get_cog_help, command_data.items())]
        sort_cog.sort(key=lambda c: c.commands, reverse=True)
        cog_names = [dict(selected=ch.name, emoji=ch.emoji) for ch in sort_cog]
        fields = (("{0.emoji} {0.name} [`{0.commands}`]".format(ch), ch.description) for ch in sort_cog)
        stella = bot.stella
        embed = BaseEmbed.default(
            ctx,
            title=f"{home_emoji} Help Command",
            description=f"{bot.description.format(stella)}\n\n**Select a Category:**",
            fields=fields
        )
        embed.set_thumbnail(url=bot.user.avatar)
        embed.set_author(name=f"By {stella}", icon_url=stella.avatar)
        loads = {
            "style": discord.ButtonStyle.primary,
            "button": HelpButton,
            "mapper": command_data,
            "menu": HelpMenu
        }
        cog_names = [*discord.utils.as_chunks(cog_names, 5)]
        args = [embed, self, ctx, HelpSource, *cog_names]
        menu_view = HelpMenuView(*args, **loads)
        await ctx.reply(embed=embed, view=menu_view)

    def get_command_help(self, command: commands.Command) -> discord.Embed:
        """Returns an Embed version of the command object given."""
        embed = BaseEmbed.default(self.context)
        embed.title = self.get_command_signature(command)
        embed.description = self.get_help(command, brief=False)
        if demo := self.get_demo(command):
            embed.set_image(url=demo)
        if alias := self.get_aliases(command):
            embed.add_field(name="Aliases", value=f'[{" | ".join(f"`{x}`" for x in alias)}]', inline=False)

        required_flags, optional_flags = self.get_flag_help(command)
        if hasattr(command.callback, "_def_parser"):
            optional_flags.extend(self.get_old_flag_help(command))

        if required_flags:
            embed.add_field(name="Required Flags", value="\n".join(required_flags), inline=False)

        if optional_flags:
            embed.add_field(name="Optional Flags", value="\n".join(optional_flags), inline=False)

        if isinstance(command, commands.Group):
            subcommand = command.commands
            value = "\n".join(self.get_command_signature(c) for c in subcommand)
            embed.add_field(name=plural("Subcommand(s)", len(subcommand)), value=value)

        return embed

    async def handle_help(self, command: commands.Command) -> discord.Message:
        with contextlib.suppress(commands.CommandError):
            await command.can_run(self.context)
            return await self.context.reply(embed=self.get_command_help(command), mention_author=False)
        raise CantRun("You don't have enough permission to see this help.") from None

    async def send_command_help(self, command: commands.Command) -> None:
        """Gets invoke when `uwu help <command>` is invoked."""
        await self.handle_help(command)

    async def send_group_help(self, group: commands.Group) -> None:
        """Gets invoke when `uwu help <group>` is invoked."""
        await self.handle_help(group)

    async def send_cog_help(self, cog: commands.Cog) -> None:
        """Gets invoke when `uwu help <cog>` is invoked."""
        cog_commands = [self.get_command_help(c) for c in await self.filter_commands(cog.walk_commands(), sort=True)]
        pages = CogMenu(source=empty_page_format(cog_commands))
        with contextlib.suppress(discord.NotFound, discord.Forbidden):
            await pages.start(self.context, wait=True)
            await self.context.confirmed()

    async def command_callback(self, ctx, search: Optional[Literal['search', 'select']], *,
                               command: Optional[str] = None) -> Optional[Any]:
        if search:
            bot = ctx.bot
            if command is not None:
                iterator = filter(lambda x: x[1] > 50,
                                  process.extract(command, [x.name for x in bot.commands], limit=5))
                result = [*discord.utils.as_chunks(map(lambda x: x[0], iterator), 2)]
                if result:
                    button_view = HelpSearchView(self, *result, button=HelpSearchButton,
                                                 style=discord.ButtonStyle.secondary)
                    await ctx.send("**Searched Command:**", view=button_view, delete_after=180)
                else:
                    await self.send_error_message(f'Unable to find any command that is even close to "{command}"')
            else:
                param = bot.get_command('help').params['command']
                ctx.current_parameter = param
                raise commands.MissingRequiredArgument(param)

        else:
            return await super().command_callback(ctx, command=command)


class Helpful(commands.Cog):
    """Commands that I think are helpful for users"""

    def __init__(self, bot: StellaBot):
        self._default_help_command = bot.help_command
        bot.help_command = StellaBotHelp()
        bot.help_command.cog = self
        self.bot = bot

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
                           "Cog method must specify it's Cog name separate by a period and it's method.",
                      cls=flg.SFlagCommand)
    @flg.add_flag("--code", type=bool, action="store_true", default=False,
                  help="Shows the code block instead of the link. Accepts True or False, defaults to False if not stated.")
    async def source(self, ctx: StellaContext, content: str = None, **flags: bool):
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
        show_code = flags.pop("code", False)
        if show_code:
            param = {"text": inspect.getsource(src), "width": 1900, "replace_whitespace": False}
            list_codeblock = [f"```py\n{cb}\n```" for cb in textwrap.wrap(**param)]
            menu = InteractionPages(empty_page_format(list_codeblock))
            await menu.start(ctx)
        else:
            lines, firstlineno = inspect.getsourcelines(src)
            location = module.replace('.', '/') + '.py'
            url = f'<{source_url}/blob/master/{location}#L{firstlineno}-L{firstlineno + len(lines) - 1}>'
            await ctx.embed(title=f"Here's uh, {content}", description=f"[Click Here]({url})")

    @commands.command(help="Gives you the invite link")
    async def invite(self, ctx: StellaContext):
        await ctx.maybe_reply(f"Thx\n<{discord.utils.oauth_url(ctx.me.id)}>")

    @command(help="Simulate a live python interpreter interface when given a python code.")
    async def repl(self, ctx: StellaContext, code: UntilFlag[codeblock_converter], *, flags: flg.ReplFlag):
        globals_ = {
            'ctx': ctx,
            'author': ctx.author,
            'guild': ctx.guild,
            'bot': self.bot,
            'discord': discord,
            'commands': commands
        }
        flags = dict(flags)
        if flags.get('exec') and not await self.bot.is_owner(ctx.author):
            flags.update({"exec": False, "inner_func_check": True})
        code = "\n".join([o async for o in ReplReader(code, _globals=globals_, **flags)])
        await ctx.maybe_reply(f"```py\n{code}```")

    def cog_unload(self) -> None:
        self.bot.help_command = self._default_help_command


def setup(bot: StellaBot) -> None:
    bot.add_cog(Helpful(bot))
