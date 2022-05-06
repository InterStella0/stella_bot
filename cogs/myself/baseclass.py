from __future__ import annotations
from typing import TYPE_CHECKING

from discord.ext import commands

from utils.useful import StellaContext

if TYPE_CHECKING:
    from main import StellaBot


class BaseMyselfCog(commands.Cog):
    def __init__(self, bot: StellaBot):
        self.bot = bot

    async def cog_check(self, ctx: StellaContext) -> bool:
        return await commands.is_owner().predicate(ctx)
