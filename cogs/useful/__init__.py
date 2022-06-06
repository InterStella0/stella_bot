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
    def __init__(self, bot: StellaBot):
        super().__init__(bot)
        self.load_context_menu()

    def load_context_menu(self):
        for name in dir(self):
            if not name.startswith("on_context_"):
                continue

            *_, clean_name = name.partition("on_context_")
            setattr(self, f"{name}_register", app_commands.ContextMenu(
                name=clean_name,
                callback=getattr(self, name)
            ))
            self.bot.tree.add_command(getattr(self, f"{name}_register"))


async def setup(bot: StellaBot) -> None:
    await bot.add_cog(Useful(bot))
