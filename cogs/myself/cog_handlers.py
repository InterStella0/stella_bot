import asyncio
from typing import Literal

from discord.ext import commands

from cogs.myself.baseclass import BaseMyselfCog
from utils.greedy_parser import GreedyParser, Separator
from utils.new_converters import ValidCog
from utils.useful import StellaContext


class CogHandler(BaseMyselfCog):
    async def cogs_handler(self, ctx: StellaContext, extensions: ValidCog,
                           method: Literal["load", "unload", "reload"]) -> None:
        async def do_cog(exts: str) -> str:
            try:
                func = getattr(self.bot, f"{method}_extension")
                await func(f"cogs.{exts}")
            except Exception as e:
                return f"cogs.{exts} failed to {method}: {e}"
            else:
                return f"cogs.{exts} is {method}ed"

        outputs = await asyncio.gather(*map(do_cog, extensions))
        await ctx.embed(description="\n".join(map(str, outputs)))

    @commands.command(name="load", aliases=["cload", "loads"], cls=GreedyParser)
    async def _cog_load(self, ctx, extension: Separator[ValidCog]):
        await self.cogs_handler(ctx, extension, "load")

    @commands.command(name="unload", aliases=["cunload", "unloads"], cls=GreedyParser)
    async def _cog_unload(self, ctx, extension: Separator[ValidCog]):
        await self.cogs_handler(ctx, extension, "unload")

    @commands.command(name="reload", aliases=["creload", "reloads"], cls=GreedyParser)
    async def _cog_reload(self, ctx, extension: Separator[ValidCog]):
        await self.cogs_handler(ctx, extension, "reload")
