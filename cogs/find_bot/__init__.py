from __future__ import annotations
from typing import TYPE_CHECKING

from .addbot_handler import AddBotHandler
from .bots import BotHandler
from .command_handler import CommandHandler
from .github import GithubHandler
from .prefix_command_listeners import PrefixCommandListeners
from .task_handlers import TaskHandler

if TYPE_CHECKING:
    from main import StellaBot


features = AddBotHandler, BotHandler, CommandHandler, GithubHandler, PrefixCommandListeners, TaskHandler


class Bots(*features):
    """Most bot related commands"""
    def __init__(self, bot: StellaBot):
        super().__init__(bot)
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.loop.create_task(self.task_handler())
        self.bot.loop.create_task(self.loading_all_prefixes())


async def setup(bot: StellaBot) -> None:
    await bot.add_cog(Bots(bot))
