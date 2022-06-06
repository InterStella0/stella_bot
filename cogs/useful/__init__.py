from __future__ import annotations
from typing import TYPE_CHECKING

from discord import app_commands

from .art_ai_generation import ArtAI
from .useful import Etc

if TYPE_CHECKING:
    from main import StellaBot


features = Etc, ArtAI


class Useful(*features):
    """Command what I think is useful."""


async def setup(bot: StellaBot) -> None:
    await bot.add_cog(Useful(bot))
