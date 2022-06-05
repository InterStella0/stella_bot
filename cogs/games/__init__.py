from __future__ import annotations
from typing import TYPE_CHECKING

from .button import ButtonCommandCog
from .button_ui import ButtonGame
from .rather_game import RatherCog
from .wordle import WordleCommandCog

if TYPE_CHECKING:
    from main import StellaBot


games = ButtonCommandCog, WordleCommandCog, RatherCog


class GamesCog(*games, name="Games"):
    """Contains games that stella made."""


async def setup(bot: StellaBot):
    await bot.add_cog(GamesCog(bot))