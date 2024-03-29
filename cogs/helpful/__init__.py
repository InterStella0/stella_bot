from __future__ import annotations
from typing import TYPE_CHECKING

from .cmd_msg_remover import CommandMessageRemoverHandler
from .eval import EvalHandler
from .help_command.core import StellaHelpCommand
from .miscellaneous import Miscellaneous
from .source import SourceCog

if TYPE_CHECKING:
    from main import StellaBot


features = EvalHandler, CommandMessageRemoverHandler, Miscellaneous, SourceCog


class Helpful(*features):
    """Commands that I think are helpful for users"""
    def __init__(self, bot: StellaBot):
        super().__init__(bot)
        self._default_help_command = bot.help_command
        bot.help_command = StellaHelpCommand()
        bot.help_command.cog = self
        self.bot = bot

    def cog_unload(self) -> None:
        self.bot.help_command = self._default_help_command


async def setup(bot: StellaBot) -> None:
    await bot.add_cog(Helpful(bot))