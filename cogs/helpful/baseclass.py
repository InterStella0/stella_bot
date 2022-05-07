from __future__ import annotations
from typing import Optional, TYPE_CHECKING

from aiogithub.objects import Repo
from discord.ext import commands

if TYPE_CHECKING:
    from main import StellaBot


class BaseHelpfulCog(commands.Cog):
    def __init__(self, bot: StellaBot):
        self.bot = bot
        self.cooldown_report = commands.CooldownMapping.from_cooldown(5, 30, commands.BucketType.user)
        self.stella_github: Optional[Repo] = None

    async def cog_load(self) -> None:
        self.stella_github = await self.bot.git.get_repo("InterStella0", "stella_bot")
