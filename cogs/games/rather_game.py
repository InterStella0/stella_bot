from discord.ext import commands

from .baseclass import BaseGameCog
from .either_io.core import EitherIOView
from utils.useful import StellaContext


class RatherCog(BaseGameCog):
    @commands.hybrid_command(
        aliases=["either.io", "either", "rathers", "rather-game", "rather-games"],
        help="Play would you rather. All data are fetched from either.io website."
    )
    async def rather(self, ctx: StellaContext):
        handler = EitherIOView(self.http_rather)
        await handler.start(ctx)
