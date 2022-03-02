from __future__ import annotations
from typing import TYPE_CHECKING

from cogs.games_part.lewdle import LewdleCommandCog

if TYPE_CHECKING:
    from main import StellaBot


class GamesCog(LewdleCommandCog, name="games"):
    pass


def setup(bot: StellaBot):
    bot.add_cog(GamesCog(bot))