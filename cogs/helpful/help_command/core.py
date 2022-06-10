from __future__ import annotations

import contextlib
import itertools
from typing import Optional, List, Tuple

import discord
from discord.ext import commands
from fuzzywuzzy import process

from utils import flags as flg
from utils.errors import CantRun
from utils.useful import StellaEmbed, plural
from .interaction import BotHelpView, CogHelpPages, HelpSearchView, HomeButton
from .model import BotHelpMap, StellaCommands


class StellaHelpCommand(commands.MinimalHelpCommand):
    def get_command_signature(self, command: StellaCommands, /, ctx=None) -> str:
        if ctx:
            self.context = ctx
        return super().get_command_signature(command)

    async def send_bot_help(self, mapping: BotHelpMap, /) -> None:
        filters = []
        for cog, cmds in mapping.items():
            if fcmds := await self.filter_commands(cmds, sort=True):
                filters.append((cog, fcmds))
        filters.sort(key=lambda v: len(v[1]), reverse=True)
        sorted_dict = {k: v for k, v in filters}
        await BotHelpView(self, sorted_dict).start(self.context)

    async def search_command(self, cmd: str) -> Optional[HelpSearchView]:
        to_search = [x.name for x in self.context.bot.commands]
        filtered = filter(lambda x: x[1] > 50, process.extract(cmd, to_search, limit=25))
        result = itertools.starmap(lambda x, *_: x, filtered)
        unfiltered_cmds = [self.context.bot.get_command(name) for name in result]
        cmds = await self.filter_commands(unfiltered_cmds)
        if cmds:
            return HelpSearchView(self, cmds)

    def get_flag_help(self, command: StellaCommands) -> Tuple[List[str], List[str]]:
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

    def get_command_help(self, command: commands.Command) -> discord.Embed:
        """Returns an Embed version of the command object given."""
        embed = StellaEmbed.default(self.context)
        embed.title = self.get_command_signature(command)
        embed.description = command.short_doc or "This command is not documented."
        if alias := command.aliases:
            embed.add_field(name="Aliases", value=f'[{" | ".join(f"`{x}`" for x in alias)}]', inline=False)

        for name, flags in zip(["Required Flags", "Optional Flags"], self.get_flag_help(command)):
            if flags:
                embed.add_field(name=name, value="\n".join(flags), inline=False)

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

    async def send_cog_help(self, cog: commands.Cog) -> None:
        """Gets invoke when `uwu help <cog>` is invoked."""
        cog_commands = await self.filter_commands(cog.walk_commands(), sort=True)
        view = discord.ui.View()
        view.message = None
        view.help_command = self
        view = CogHelpPages(view, cog, cog_commands)
        view.remove_item(discord.utils.find(lambda x: isinstance(x, HomeButton), view.children))
        await view.start(self.context)

    async def send_command_help(self, command: commands.Command) -> None:
        """Gets invoke when `uwu help <command>` is invoked."""
        await self.handle_help(command)

    async def send_group_help(self, group: commands.Group) -> None:
        """Gets invoke when `uwu help <group>` is invoked."""
        await self.handle_help(group)
