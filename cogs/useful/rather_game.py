from discord.ext import commands

from cogs.useful.baseclass import BaseUsefulCog
from cogs.useful.either_io.core import EitherIO
from utils.useful import StellaContext


class Rather(BaseUsefulCog):
    @commands.command(aliases=["either.io", "either", "rathers", "rather-game", "rather-games"])
    async def rather(self, ctx: StellaContext):
        handler = EitherIO(self.http_rather)
        await handler.start(ctx)
