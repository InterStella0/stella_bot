from __future__ import annotations

import contextlib
import copy
import itertools
import json
import re
import textwrap
from collections import namedtuple
from typing import Tuple, List, Optional, TYPE_CHECKING, Union, Any, Dict

import discord
from discord.ext import commands
from discord import ui
from discord.ext.commands.help import _HelpCommandImpl
from discord.ui import View
from fuzzywuzzy import process

from utils import flags as flg
from utils.buttons import BaseButton, BaseView, MenuViewBase
from utils.errors import CantRun
from utils.greedy_parser import GreedyParser
from utils.menus import MenuViewInteractionBase, HelpMenuBase, ListPageInteractionBase
from utils.modal import BaseModal
from utils.useful import plural, StellaContext, StellaEmbed, unpack, empty_page_format

if TYPE_CHECKING:
    from main import StellaBot


CommandGroup = Union[commands.Command, commands.Group, GreedyParser]
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

    async def format_page(self, menu: HelpMenu, entry: Tuple[commands.Cog, List[CommandHelp]]) -> discord.Embed:
        """This is for the help command ListPageSource"""
        cog, list_commands = entry
        new_line = "\n"
        embed = discord.Embed(title=f"{getattr(cog, 'qualified_name', 'No')} Category",
                              description=new_line.join(f'{command_help.command}{new_line}{command_help.brief}'
                                                        for command_help in list_commands),
                              color=menu.bot.color)
        author = menu.ctx.author
        return embed.set_footer(text=f"Requested by {author}", icon_url=author.display_avatar)

    async def format_view(self, menu: HelpMenu,
                          entry: Tuple[Optional[commands.Cog], List[CommandHelp]]) -> HelpMenuView:
        if not menu._running:
            return
        _, list_commands = entry
        commands = [c.command_obj.name for c in list_commands]
        menu.view.clear_items()
        menu.view.add_item(HomeButton())
        for c in commands:
            menu.view.add_item(HelpSearchButton(style=discord.ButtonStyle.secondary, selected=c, row=None))

        return menu.view


class SearchHelp(discord.ui.Button):
    def __init__(self):
        super().__init__(
            emoji="<:search:945890885533573150>",
            label="Search Command",
            row=3,
            style=discord.ButtonStyle.success
        )

    async def callback(self, interaction: discord.Interaction):
        prompter = self.view.get_prompt_search()
        await interaction.response.send_modal(prompter)


class HelpDropDown(discord.ui.Select):
    def __init__(self, cmds: List[CommandGroup]):
        self.commands = cmds = {cmd.name: cmd for cmd in cmds}
        options = [discord.SelectOption(
            label=cmd.name,
            description=textwrap.shorten(cmd.short_doc, width=80)
        ) for cmd in self.commands.values()]
        amount = len(cmds)
        value = plural(f'{amount} result(s)', amount)
        super().__init__(placeholder=value, min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if not self.values:
            return

        help_obj = self.view.help_command
        command_name = self.values[0]
        command = self.commands.get(command_name)
        embed = help_obj.get_command_help(command)
        await interaction.response.send_message(content=f"Help for **{command_name}**", embed=embed, ephemeral=True)


class HelpMenuView(MenuViewBase):
    """This class is responsible for starting the view + menus activity for the help command.
       This accepts embed, help_command, context, page_source, dataset and optionally Menu.
       """

    def __init__(self, *data: Any, embed: discord.Embed, help_object: StellaBotHelp, context: StellaContext,
                 **kwargs: Any):
        super().__init__(context, HelpSource, *data,
                         button=HelpButton,
                         menu=HelpMenu,
                         style=discord.ButtonStyle.primary,
                         **kwargs)
        self.original_embed = embed
        self.help_command = help_object
        self._search_prompt: Optional[BaseModal] = None
        self.add_item(SearchHelp())
        self.old_items = []

    class PromptSearch(BaseModal, title="Help Command Search"):
        text_command = ui.TextInput(label="command", max_length=20)

        def __init__(self, view: HelpMenuView):
            super().__init__()
            self.help_command = view.help_command
            self.original = view.message
            self.view = view

        async def on_submit(self, interaction: discord.Interaction) -> None:
            cmd = self.text_command.value.strip()
            if not cmd.strip():
                await interaction.response.send_message(content="I can't search an empty command", ephemeral=True)
                return

            if view := await self.help_command.search_command(cmd):
                message = f"Showing closest to `{cmd}` with :"
                view.add_item(HomeButton(view=self.view))
                await self.original.edit(content=message, embed=None, view=view)
            else:
                await interaction.response.send_message(f"No command with the name {cmd} found.", ephemeral=True)

    def get_prompt_search(self):
        if self._search_prompt is None:
            self._search_prompt = self.PromptSearch(self)

        return self._search_prompt

    def stop(self) -> None:
        if self._search_prompt is not None:
            self._search_prompt.stop()


class HomeButton(BaseButton):
    """This button redirects the view from the menu, into the category section, which
       adds the old buttons back."""

    def __init__(self, *, view=None):
        super().__init__(style=discord.ButtonStyle.success, selected="Home", row=None, emoji=home_emoji)
        self.diff_view = view

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.diff_view or self.view
        if self.diff_view is None:
            view.clear_items()
            for b in view.old_items:
                view.add_item(b)
        await interaction.message.edit(content=None, view=view, embed=view.original_embed)


class HelpButton(BaseButton):
    """This Button update the menu, and shows a list of commands for the cog.
       This saves the category buttons as old_items and adds relevant buttons that
       consist of HomeButton, and HelpSearchButton."""

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        bot: StellaBot = interaction.client
        select = self.selected or "No Category"
        cog = bot.get_cog(select)
        data = [(cog, commands_list) for commands_list in view.mapper.get(cog)]
        self.view.old_items = copy.copy(self.view.children)
        await view.update(self, interaction, data)


class HelpSearchView(BaseView):
    """This view class is specifically for command_callback method"""

    def __init__(self, help_object: StellaBotHelp, cmds: List[CommandGroup], *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.help_command = help_object
        self.ctx = help_object.context
        self.bot = help_object.context.bot
        self.add_item(HelpDropDown(cmds))


class HelpSearchButton(BaseButton):
    """This class is used inside a help command that shows a help for a specific command.
       This is also used inside help search command."""

    async def callback(self, interaction: discord.Interaction) -> None:
        help_obj = self.view.help_command
        bot: StellaBot = interaction.client
        command = bot.get_command(self.selected)
        embed = help_obj.get_command_help(command)
        await interaction.response.send_message(content=f"Help for **{self.selected}**", embed=embed, ephemeral=True)


class Information(HelpMenuBase):
    async def on_information_show(self, payload: discord.RawReactionActionEvent) -> None:
        ctx = self.ctx
        embed = StellaEmbed.default(ctx, title="Information", description=self.description)
        curr = self.current_page + 1 if (p := self.current_page > -1) else "cover page"
        pa = ("page", "the")[not p]
        embed.set_author(icon_url=ctx.bot.user.display_avatar, name=f"You were on {pa} {curr}")
        nav = '\n'.join(f"{e} {b.action.__doc__}" for e, b in super().buttons.items())
        embed.add_field(name="Navigation:", value=nav)
        await self.message.edit(embed=embed, allowed_mentions=discord.AllowedMentions(replied_user=False))


class HelpMenu(MenuViewInteractionBase, Information):
    """MenuPages class that is specifically for the help command."""

    def __init__(self, *args: Any, description: Optional[str] = None, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.description = description or """This shows each commands in this bot. Each page is a category that shows 
                                             what commands that the category have."""


class CogMenu(Information):
    def __init__(self, *args: Any, description: Optional[str] = None, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.description = description


class StellaBotHelp(commands.DefaultHelpCommand):
    def __init__(self, **options: Any):
        super().__init__(**options)
        with open("d_json/help.json") as r:
            self.help_gif = json.load(r)

    def get_command_signature(self, command: CommandGroup, ctx: Optional[StellaContext] = None) -> str:
        """Method to return a commands name and signature"""

        def get_invoke_with():
            msg = ctx.message.content
            prefixmax = re.match(f'{re.escape(ctx.prefix)}', ctx.message.content).regs[0][1]
            return msg[prefixmax:msg.rindex(ctx.invoked_with)]

        parent = command.parent
        with contextlib.suppress(ValueError):
            parent = get_invoke_with() if ctx else command.parent
        command_name = ctx.invoked_with if ctx else command.name
        prefix = (ctx or self.context).clean_prefix

        if not command.signature and not command.parent:
            return f'{prefix}{command_name}'
        if command.signature and not command.parent:
            return f'{prefix}{command_name} {command.signature}'
        if not command.signature and command.parent:
            return f'{prefix}{parent} {command_name}'
        else:
            return f'{prefix}{parent} {command_name} {command.signature}'

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
        for cog, unfiltered in mapping.items():
            if list_commands := await self.filter_commands(unfiltered, sort=True):
                lists = command_data.setdefault(cog, [])
                for chunks in discord.utils.as_chunks(list_commands, EACH_PAGE):
                    lists.append([*map(get_command_help, chunks)])

        mapped = itertools.starmap(get_cog_help, command_data.items())
        sort_cog = [*sorted(mapped, key=lambda c: c.commands, reverse=True)]
        stella = bot.stella
        embed = StellaEmbed.default(
            ctx,
            title=f"{home_emoji} Help Command",
            description=f"{bot.description.format(stella)}\n\n**Select a Category:**",
            fields=map(lambda ch: ("{0.emoji} {0.name} [`{0.commands}`]".format(ch), ch.description), sort_cog)
        )
        payload = {
            "bot_name": str(bot.user),
            "name": str(bot.stella),
            "author_avatar": ctx.author.display_avatar.url,
            "author_avatar_hash": ctx.author.display_avatar.key,
            "author_name": str(ctx.author)
        }
        banner = await bot.ipc_client.request("generate_banner", **payload)
        if isinstance(banner, str):
            embed.set_image(url=banner)
        embed.set_author(name=f"By {stella}", icon_url=stella.display_avatar)

        loads = {
            "embed": embed,
            "help_object": self,
            "context": ctx,
            "mapper": command_data
        }
        cog_names = [dict(selected=ch.name, emoji=ch.emoji) for ch in sort_cog]
        buttons = discord.utils.as_chunks(cog_names, 5)
        menu_view = HelpMenuView(*buttons, **loads)
        menu_view.message = await ctx.reply(embed=embed, view=menu_view)

    def get_command_help(self, command: commands.Command) -> discord.Embed:
        """Returns an Embed version of the command object given."""
        embed = StellaEmbed.default(self.context)
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
        cog_commands = [*map(self.get_command_help, await self.filter_commands(cog.walk_commands(), sort=True))]
        pagination = CogMenu(
            source=empty_page_format(cog_commands),
            description="This shows each commands in this category. Each page is a command "
                        "that shows what's the command is about and a demonstration of usage."
        )
        with contextlib.suppress(discord.NotFound, discord.Forbidden):
            await pagination.start(self.context, wait=True)
            await self.context.confirmed()

    def command_not_found(self, string: str) -> Tuple[str, str]:
        return super().command_not_found(string), string

    def subcommand_not_found(self, command: commands.Group, string: str) -> Tuple[str, str, commands.Group]:
        return super().subcommand_not_found(command, string), string, command

    async def send_error_message(self, error: Tuple[str, str, Optional[commands.Group]]) -> None:
        await self.handle_error_message(*error)

    async def search_command(self, cmd: str) -> Optional[View]:
        to_search = [x.name for x in self.context.bot.commands]
        filtered = filter(lambda x: x[1] > 50, process.extract(cmd, to_search, limit=25))
        result = itertools.starmap(lambda x, *_: x, filtered)
        unfiltered_cmds = [self.context.bot.get_command(name) for name in result]
        cmds = await self.filter_commands(unfiltered_cmds)
        if cmds:
            return HelpSearchView(self, cmds)

    async def handle_error_message(self, error: str, command: str, group: Optional[commands.Group] = None) -> None:
        ctx = self.context
        to_search = group.commands if group is not None and not isinstance(group, _HelpCommandImpl) else ctx.bot.commands
        filtered = filter(lambda x: x[1] > 50, process.extract(command, [x.name for x in to_search], limit=25))
        mapped = itertools.starmap(lambda x, *_: f"{group} {x}" if group else x, filtered)
        result = await self.filter_commands([ctx.bot.get_command(name) for name in mapped])
        if result:
            button_view = HelpSearchView(self, result)
            message = f"{error}.\nShowing results for the closest command to `{command}`:"
            await ctx.reply(message, view=button_view, delete_after=180)
        else:
            await super().send_error_message(error)
