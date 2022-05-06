from __future__ import annotations
from typing import TYPE_CHECKING

from .cog_handlers import CogHandler
from .command_handlers import CommandHandlers
from .miscellaneous import Miscellaneous

if TYPE_CHECKING:
    from main import StellaBot


features = CogHandler, CommandHandlers, Miscellaneous


class Myself(*features):
    """Commands for stella"""


async def setup(bot: StellaBot) -> None:
    await bot.add_cog(Myself(bot))
