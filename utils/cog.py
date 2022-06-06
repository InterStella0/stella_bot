from __future__ import annotations
from typing import TYPE_CHECKING

from discord import app_commands
from discord.ext import commands

if TYPE_CHECKING:
    from main import StellaBot


class StellaBaseCog(commands.Cog):
    def __init__(self, bot: StellaBot):
        self.bot = bot
        self.load_context_menu()

    def load_context_menu(self):
        for name in dir(self):
            if not name.startswith("on_context_"):
                continue

            *_, clean_name = name.partition("on_context_")
            print("adding", clean_name)
            setattr(self, f"{name}_register", app_commands.ContextMenu(
                name=clean_name,
                callback=getattr(self, name)
            ))
            self.bot.tree.add_command(getattr(self, f"{name}_register"))

    async def cog_unload(self) -> None:
        for name in dir(self):
            if not (name.startswith("cog_context_") and name.endswith("_register")):
                continue

            print("removing", name)
            menu: app_commands.ContextMenu = getattr(self, name)
            self.bot.tree.remove_command(menu.name, type=menu.type)
