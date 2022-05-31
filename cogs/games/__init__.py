from __future__ import annotations
from typing import TYPE_CHECKING

from .button import ButtonCommandCog
from .button_ui import ButtonGame
from .wordle import WordleCommandCog

if TYPE_CHECKING:
    from main import StellaBot


games = ButtonCommandCog, WordleCommandCog


class GamesCog(*games, name="Games"):
    """Contains games that stella made."""
    async def cog_load(self) -> None:
        if not self.bot.tester:
            self.bot.add_view(ButtonGame())


async def setup(bot: StellaBot):
    await bot.add_cog(GamesCog(bot))