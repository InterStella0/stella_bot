from __future__ import annotations
from typing import TYPE_CHECKING

from cogs.useful.art_ai_generation import ArtAI
from cogs.useful.useful import Etc

if TYPE_CHECKING:
    from main import StellaBot


features = Etc, ArtAI


class Useful(*features):
    """Command what I think is useful."""


async def setup(bot: StellaBot) -> None:
    await bot.add_cog(Useful(bot))
