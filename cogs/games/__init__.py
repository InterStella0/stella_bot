from __future__ import annotations
from typing import TYPE_CHECKING

from .wordle import WordleCommandCog

if TYPE_CHECKING:
    from main import StellaBot


class GamesCog(WordleCommandCog, name="Games"):
    """Contains games that stella made."""


def setup(bot: StellaBot):
    bot.add_cog(GamesCog(bot))