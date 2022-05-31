from __future__ import annotations

import discord
from discord.ext import commands

from cogs.games.baseclass import BaseGameCog
from cogs.games.button_ui import ButtonGame, UserUnknown
from utils.useful import StellaContext, realign


class ButtonCommandCog(BaseGameCog):
    @commands.group(aliases=["clicks"], ignore_extra=False)
    async def click(self, ctx: StellaContext):
        await ctx.embed(
            title="Click It",
            description="**How to play this game?**\nPress the button, the end.",
            view=ButtonGame()
        )

    async def get_or_fetch_user(self, user_id: int):
        if user := self.button_rank_cache.get(user_id):
            return user

        try:
            user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
        except discord.NotFound:
            user = UserUnknown(user_id)
        finally:
            self.button_rank_cache[user_id] = user
            return user

    @click.command(name="rank", aliases=["ranks", "top"])
    async def click_rank(self, ctx: StellaContext):
        query = "SELECT * " \
                "FROM (" \
                "   SELECT  user_id, " \
                "           amount," \
                "           ROW_NUMBER() OVER (ORDER BY amount DESC) as rn" \
                "   FROM button_game" \
                "   GROUP BY user_id" \
                ") sorted_table " \
                "LIMIT 10"

        results = await self.bot.pool_pg.fetch(query)
        values = [(await self.get_or_fetch_user(c['user_id']), c['amount']) for c in results]
        key = "\u200b"
        contents = [f"`{i}. {x} {key} {a}`" for i, (x, a) in enumerate(values, start=1)]
        await ctx.embed(
            title="Top 10 user(s) on Click Game",
            description="\n".join(realign(contents, key))
        )
