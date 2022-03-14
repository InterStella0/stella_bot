from __future__ import annotations
import datetime
import discord
import matplotlib
import io
from typing import Union, Literal, TYPE_CHECKING, Optional
from utils import flags as flg
from utils.greedy_parser import UntilFlag, command
from utils.image_manipulation import get_majority_color, islight, create_graph, process_image, create_bar
from utils.new_converters import TimeConverter, IsBot
from utils.useful import StellaContext
from discord.ext import commands

if TYPE_CHECKING:
    from main import StellaBot

matplotlib.use('Agg')
TimeConvert = TimeConverter(datetime.timedelta(days=2), datetime.timedelta(weeks=8))


class ElseConverter(commands.Converter):
    valid_conversion = {"all": ["al", "a", "guild", "g", "guilds"],
                        "this": ["thi", "th", "me", "myself"]}

    async def convert(self, ctx: StellaContext, argument: str) -> Union[discord.Guild, discord.Member, discord.User, str]:
        found = None
        for k, v in self.valid_conversion.items():
            if k == argument or argument in v:
                found = k

        if self.valid_conversion.get(found):
            if found == "all":
                return ctx.guild
            elif found == "this":
                return ctx.me
        raise commands.CommandError("No valid else conversion.")


class ColorFlag(commands.FlagConverter):
    color: Optional[discord.Color] = flg.flag(
        aliases=["colour", "C"],
        help="Changes the graph's color depending on the hex given. "
             "This defaults to the bot's avatar color, or if it's too dark, pink color, cause i like pink.",
        default=None
    )


class BotActivityFlag(ColorFlag):
    time: Optional[TimeConvert] = flg.flag(
        aliases=["T"],
        help="Time given for the bot, this flag must be more than 2 days and less than 2 months. "
             "Defaults to 2 days when not given.",
        default=None
    )
    smooth: Optional[bool] = flg.flag(
        aliases=["S"],
        help="Makes the graph curvy, rather than a straight cut. Defaults to False.",
        default=False
    )


class Stat(commands.Cog, name="Statistic"):
    """Statistic related commands"""
    def __init__(self, bot: StellaBot):
        self.bot = bot

    @command(aliases=["botactivitys", "ba"], 
             help="Creates a graph that represents the bot's usage in a server, which shows the command "
                  "invoke happening for a bot.")
    @commands.guild_only()
    @commands.cooldown(1, 30, commands.BucketType.user)
    async def botactivity(self, ctx: StellaContext, member: UntilFlag[Union[Literal["guild", "me"], IsBot]],
                          *, flags: BotActivityFlag):
        target = member
        if isinstance(target, str):
            target = await ElseConverter().convert(ctx, target)

        time_rn = datetime.datetime.utcnow()
        flags = dict(flags)
        time_given = flags.get("time") or time_rn - datetime.timedelta(days=2)
        if isinstance(target, discord.Member):
            query = "SELECT * FROM commands_list WHERE guild_id=$1 AND bot_id=$2 AND time_used > $3"
            values = (ctx.guild.id, target.id, time_given)
            error = "Looks like no data is present for this bot."
            method = "display_avatar"
        else:
            query = "SELECT * FROM commands_list WHERE guild_id=$1 AND time_used > $2"
            values = (target.id, time_given)
            error = "Looks like i dont know anything in this server."
            method = "icon"
        
        data = await self.bot.pool_pg.fetch(query, *values)
        if not data:
            raise commands.CommandError(error)
        bot_based_time = {}
        total_seconds = (time_rn - time_given).total_seconds()
        each_time = datetime.timedelta(seconds=total_seconds / 10)
        for each in range(10):
            within_time = []
            after = time_rn - each_time * each
            before = time_rn - each_time * (each + 1)
            for row in data:
                if before < row["time_used"] < after:
                    within_time.append(row)

            bot_based_time.update({before: len(within_time)})

        x = list(bot_based_time)
        y = list(bot_based_time.values())

        asset = getattr(target, method)
        async with ctx.typing():
            avatar_bytes = io.BytesIO(await asset.read())
            if not flags.get("color"):
                new_color = major = await get_majority_color(avatar_bytes)
                if not islight(*major.to_rgb()) or member == ctx.me:
                    new_color = discord.Color(ctx.bot.color)
                flags["color"] = new_color

            graph = await create_graph(x, y, **flags)
            to_send = await process_image(avatar_bytes, graph)
        embed = discord.Embed()
        embed.set_image(url="attachment://picture.png")
        embed.set_author(name=target, icon_url=asset)
        await ctx.embed(embed=embed, file=discord.File(to_send, filename="picture.png"))
        graph.close()
        avatar_bytes.close()
        to_send.close()

        del graph
        del avatar_bytes
        del to_send

    @command(aliases=["topcommand", "tc", "tcs"],
             help="Generate a bar graph for 10 most used command for a bot.")
    @commands.guild_only()
    @commands.cooldown(1, 30, commands.BucketType.user)
    async def topcommands(self, ctx: StellaContext, member: UntilFlag[Union[Literal["guild", "me"], IsBot]],
                          *, flags: ColorFlag):
        target = member
        if isinstance(target, discord.Member):
            query = "SELECT command, COUNT(command) AS usage FROM commands_list " \
                    "WHERE guild_id=$1 AND bot_id=$2 " \
                    "GROUP BY bot_id, command " \
                    "ORDER BY usage DESC LIMIT 10"
            values = (ctx.guild.id, target.id)
            error = "Looks like no data is present for this bot."
            method = "display_avatar"
        else:
            target = await ElseConverter().convert(ctx, target)
            query = "SELECT command, COUNT(command) AS usage FROM commands_list " \
                    "WHERE guild_id=$1 " \
                    "GROUP BY command " \
                    "ORDER BY usage DESC LIMIT 10;"
            values = (target.id,)
            error = "Looks like i dont know anything in this server."
            method = "icon"

        data = await self.bot.pool_pg.fetch(query, *values)
        if not data:
            raise commands.CommandError(error)

        data.reverse()
        names = [v["command"] for v in data]
        usages = [v["usage"] for v in data]
        payload = dict(title=f"Top {len(names)} commands for {member}",
                       xlabel="Usage",
                       ylabel="Commands")

        asset = getattr(target, method)
        async with ctx.typing():
            avatar_bytes = io.BytesIO(await asset.read())
            if not (color := flags.color):
                color = major = await get_majority_color(avatar_bytes)
                if not islight(*major.to_rgb()) or member == ctx.me:
                    color = discord.Color(ctx.bot.color)

            bar = await create_bar(names, usages, str(color), **payload)
            to_send = await process_image(avatar_bytes, bar)

        embed = discord.Embed()
        embed.set_image(url="attachment://picture.png")
        embed.set_author(name=target, icon_url=asset)
        await ctx.embed(embed=embed, file=discord.File(to_send, filename="picture.png"))
        bar.close()
        avatar_bytes.close()
        to_send.close()


async def setup(bot: StellaBot) -> None:
    await bot.add_cog(Stat(bot))
