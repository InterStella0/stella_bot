from discord.ext import commands

from .baseclass import BaseGameCog
from .either_io.core import EitherIOView
from utils.useful import StellaContext


class RatherCog(BaseGameCog):
    @commands.command(aliases=["either.io", "either", "rathers", "rather-game", "rather-games"])
    async def rather(self, ctx: StellaContext):
        handler = EitherIOView(self.http_rather)
        await handler.start(ctx)
