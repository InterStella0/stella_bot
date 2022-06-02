from __future__ import annotations

import datetime
import io
from typing import Union, Optional, Literal

import discord
from discord.ext import commands
from discord.ext.commands import Author

from cogs.games.baseclass import BaseGameCog
from cogs.games.button_ui import ButtonGame, UserUnknown
from utils.image_manipulation import get_majority_color, islight, create_graph, process_image
from utils.useful import StellaContext, realign


class ButtonCommandCog(BaseGameCog):
    @commands.group(aliases=["clicks"], invoke_without_command=True, ignore_extra=False,
                    help="Click game where you literally just click the button repeatedly.")
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

    @click.command(name="rank", aliases=["ranks", "top"],
                   help="Shows top 10 of the leaderboard for the click game.")
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

    @click.command(name="stat", aliases=["statistic", "s", "statistics", "stats"],
                   help="Creates a graph that represents the user's click rate over the last 2 days.")
    async def click_stat(self, ctx: StellaContext, accurate: Optional[Literal['accurate']], *, member: Union[discord.Member, discord.User] = Author):
        # copy pasted code from code above cause stella is lazy
        time_rn = discord.utils.utcnow()
        time_given = time_rn - datetime.timedelta(days=2)
        query = "SELECT * FROM click_game_logger " \
                "WHERE click_time > $1 " \
                "AND user_id=$2"

        data = await self.bot.pool_pg.fetch(query, time_given, member.id)
        if not data:
            raise commands.CommandError(f'No data for "{member}" found in the last 2 days.')

        bot_based_time = {}
        total_seconds = (time_rn - time_given).total_seconds()
        each_time = datetime.timedelta(seconds=total_seconds / 10)
        for each in range(10):
            within_time = []
            after = time_rn - each_time * each
            before = time_rn - each_time * (each + 1)
            for row in data:
                if before < row["click_time"] < after:
                    within_time.append(row)

            bot_based_time.update({before: len(within_time)})

        x = list(bot_based_time)
        y = list(bot_based_time.values())

        asset = member.display_avatar
        async with ctx.typing():
            avatar_bytes = io.BytesIO(await asset.read())
            new_color = major = await get_majority_color(avatar_bytes)
            if not islight(*major.to_rgb()) or member == ctx.me:
                new_color = discord.Color(ctx.bot.color)

            graph = await create_graph(x, y, color=new_color, smooth=accurate is None)
            to_send = await process_image(avatar_bytes, graph)
        embed = discord.Embed()
        embed.set_image(url="attachment://picture.png")
        embed.set_author(name=member, icon_url=asset)
        await ctx.embed(embed=embed, file=discord.File(to_send, filename="picture.png"))
        graph.close()
        avatar_bytes.close()
        to_send.close()

        del graph
        del avatar_bytes
        del to_send