from __future__ import annotations
import datetime
import discord
import matplotlib
import math
import io
import numpy as np
from PIL import Image, ImageEnhance, ImageFilter
from scipy.interpolate import make_interp_spline
from matplotlib import pyplot as plt
from matplotlib.figure import Figure
from matplotlib.axes import Axes
import matplotlib.dates as mdates
import matplotlib.colors as mcolors
import matplotlib.patheffects as peffects
from typing import Union, Literal, Optional, List, Any, Coroutine, TYPE_CHECKING
from matplotlib.patches import Polygon
from utils import flags as flg
from utils.greedy_parser import UntilFlag, command
from utils.new_converters import TimeConverter, IsBot
from utils.decorators import in_executor
from utils.useful import StellaContext
from discord.ext import commands

if TYPE_CHECKING:
    from main import StellaBot

matplotlib.use('Agg')
TimeConvert = TimeConverter(datetime.timedelta(days=2), datetime.timedelta(weeks=8))


def create_gradient_array(color: str, *, alpha_min: Optional[int] = 0, alpha_max: Optional[int] = 1) -> np.array:
    z = np.empty((100, 1, 4), dtype=float)
    z[:, :, :3] = mcolors.colorConverter.to_rgb(color)
    z[:, :, -1] = np.linspace(alpha_min, alpha_max, 100)[:, None]
    return z


@in_executor()
def create_graph(x: List[datetime.datetime], y: List[int], **kwargs: int):
    color = str(kwargs.get("color"))
    fig, axes = plt.subplots()
    date_np = np.array(sorted(x))
    value_np = np.array([*reversed(y)])
    date_num = mdates.date2num(date_np)

    # Graph smoothen
    date_num_smooth = np.linspace(date_num.min(), date_num.max(), 100) 
    spl = make_interp_spline(date_num, value_np, k=2 - kwargs.get("accurate"))
    value_np_smooth = spl(date_num_smooth)

    line, = axes.plot(mdates.num2date(date_num_smooth), value_np_smooth, color=color)

    if (alpha := line.get_alpha()) is None:
        alpha = 1.0
    z = create_gradient_array(color, alpha_max=alpha)
    offset = value_np.max() * 0.20
    xmin, xmax, ymin, ymax = date_num.min(), date_num.max(), value_np.min(), value_np.max() + offset
    payload = dict(aspect='auto', extent=[xmin, xmax, ymin, ymax],
                   origin='lower', zorder=line.get_zorder())
    im = axes.imshow(z, **payload)

    xy = np.column_stack([date_num_smooth, value_np_smooth])
    xy = np.vstack([[xmin, ymin], xy, [xmax, ymin], [xmin, ymin]])
    clip_path = Polygon(xy, facecolor='none', edgecolor='none', closed=True)
    axes.add_patch(clip_path)
    im.set_clip_path(clip_path)

    for side in 'bottom', 'top', 'left', 'right':
        axes.spines[side].set_color('white')

    for side, name in zip(("x", "y"), ("Time (UTC)", "Command Usage")):
        getattr(axes, side + 'axis').label.set_color('white')
        axes.tick_params(axis=side, colors=color)
        getattr(axes, f"set_{side}label")(name, fontsize=17)

    axes.get_xaxis().set_major_formatter(mdates.DateFormatter('%d/%m'))
    axes.grid(True)
    axes.autoscale(True)
    value = save_matplotlib(fig, axes)
    del im
    del clip_path
    del axes
    del line
    return value


def hilo(a: int, b: int, c: int) -> int:
    if c < b: 
        b, c = c, b
    if b < a: 
        a, b = b, a
    if c < b: 
        b, c = c, b
    return a + c


def complement_color(*rgb: int) -> discord.Color:
    k = hilo(*rgb)
    return discord.Color.from_rgb(*tuple(k - u for u in rgb))


def inverse_color(*rgb: int) -> List[int]:
    return [*map(lambda x: 255 - x, rgb)]


@in_executor()
def create_bar(x_val: List[Any], y_val: List[Any], color: str, **kwargs: Any) -> Coroutine[Any, Any, io.BytesIO]:
    fig, axes = plt.subplots()
    bars = axes.barh(x_val, y_val, edgecolor=color)

    temp = discord.Color(int(color.replace("#", "0x"), base=16))
    comp = str((comp_color := complement_color(*temp.to_rgb())))
    inverse = str(discord.Color.from_rgb(*inverse_color(*comp_color.to_rgb())))
    maximum_size = max(y_val)

    def percent(per: int) -> int:
        return maximum_size * per

    for i, v in enumerate(y_val):
        pixel = percent(0.025) + percent(0.001)
        text_size = len(str(v))
        offset = pixel * text_size
        actual_val = v - offset
        actual_val += ((pixel + percent(0.010)) * text_size) * (actual_val <= (0 + percent(0.01)))
        axes.text(actual_val, i - .15, f"{v:,}", color=comp, fontweight='bold',
                  path_effects=[peffects.withStroke(linewidth=0.8, foreground=inverse)])
        
    for attr, value in kwargs.items():
        getattr(axes, f"set_{attr}")(value, color='w')

    for side in 'bottom', 'top', 'left', 'right':
        axes.spines[side].set_color('white')

    for side in "x", "y":
        axes.tick_params(axis=side, colors=color)

    lim = axes.get_xlim() + axes.get_ylim()
    for bar in bars:
        bar.set_zorder(1)
        bar.set_facecolor("none")
        x, y = bar.get_xy()
        w, h = bar.get_width(), bar.get_height()
        a_min = 0.4
        inverse = 1 - a_min
        maximum = a_min + (inverse * w / max(y_val))
        z = create_gradient_array(color, alpha_min=a_min, alpha_max=maximum)
        z = np.rot90(z)
        payload = dict(extent=[x, x + w, y, y + h], aspect="auto", zorder=0, 
                       norm=mcolors.NoNorm(vmin=0, vmax=1))
        axes.imshow(z, **payload)

    axes.axis(lim)
    return save_matplotlib(fig, axes)


def save_matplotlib(fig: Figure, axes: Axes) -> io.BytesIO:
    fig.delaxes(axes)
    fig.add_axes(axes)
    buffer = io.BytesIO()
    fig.savefig(buffer, transparent=True, bbox_inches="tight")
    axes.clear()
    fig.clf()
    plt.close(fig)
    return buffer


@in_executor()
def process_image(avatar_bytes: io.BytesIO, target: io.BytesIO) -> Coroutine[Any, Any, io.BytesIO]:
    with Image.open(avatar_bytes).convert('RGBA') as avatar, Image.open(target) as target:
        side = max(avatar.size)
        avatar = avatar.crop((0, 0, side, side)) 
        w, h = target.size
        avatar = avatar.resize((w, w))
        offset_below = 10
        avatar = avatar.crop((0, 0, w, h + offset_below)) 
        reducer = ImageEnhance.Brightness(avatar)
        background = reducer.enhance(0.378)
        background = background.filter(ImageFilter.GaussianBlur(8))
        gray_back = Image.new('RGBA', avatar.size, (*discord.Color.dark_theme().to_rgb(), 255))
        gray_back.paste(background, [0, 0], mask=background)
        background = gray_back
        background.paste(target, [0, 0], mask=target)
        to_send = io.BytesIO()
        background.save(to_send, format="PNG")
        gray_back.close()
        to_send.seek(0)
        return to_send


@in_executor()
def get_majority_color(b: io.BytesIO) -> Coroutine[Any, Any, discord.Color]:
    with Image.open(b) as target:
        smol = target.quantize(4)
        return discord.Color.from_rgb(*smol.getpalette()[:3])


def islight(r: int, g: int, b: int) -> bool:
    # Found this equation in http://alienryderflex.com/hsp.html, fucking insane i tell ya
    hsp = math.sqrt(0.299 * (r * r) + 0.587 * (g * g) + 0.114 * (b * b))
    return hsp > 127.5


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


class Stat(commands.Cog, name="Statistic"):
    """Statistic related commands"""
    def __init__(self, bot: StellaBot):
        self.bot = bot

    @command(aliases=["botactivitys", "ba"], 
             help="Creates a graph that represents the bot's usage in a server, which shows the command "
                  "invoke happening for a bot.")
    @commands.guild_only()
    @flg.add_flag("--time", "-T", type=TimeConvert, 
                  help="Time given for the bot, this flag must be more than 2 days and less than 2 months. "
                       "Defaults to 2 days when not given.")
    @flg.add_flag("--accurate", "-A", action="store_true", default=False,
                  help="Makes the graph the exact value, rather than a smooth curve. Defaults to False.")
    @flg.add_flag("--color", "--colour", "-C", type=discord.Color, default=None, 
                  help="Changes the graph's color depending on the hex given. "
                       "This defaults to the bot's avatar color, or if it's too dark, pink color, cause i like pink.")
    @commands.cooldown(1, 30, commands.BucketType.user)
    async def botactivity(self, ctx: StellaContext, member: UntilFlag[Union[Literal["guild", "me"], IsBot]],
                          **flags: Union[datetime.datetime, bool, discord.Color]):
        target = member
        if isinstance(target, str):
            target = await ElseConverter().convert(ctx, target)

        time_rn = datetime.datetime.utcnow()
        time_given = flags.get("time") or time_rn - datetime.timedelta(days=2)
        if isinstance(target, discord.Member):
            query = "SELECT * FROM commands_list WHERE guild_id=$1 AND bot_id=$2 AND time_used > $3"
            values = (ctx.guild.id, target.id, time_given)
            error = "Looks like no data is present for this bot."
            method = "avatar"
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
        for each in range(1, 11):
            within_time = []
            after = time_rn - each_time * (each - 1)
            before = time_rn - each_time * each
            for row in data:
                if before < row["time_used"] < after:
                    within_time.append(row)

            bot_based_time.update({before: len(within_time)})

        x = list(bot_based_time)
        y = list(bot_based_time.values())

        asset = getattr(target, method)
        with ctx.typing():
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
    @flg.add_flag("--color", "--colour", "-C", type=discord.Color, default=None, 
                  help="Changes the graph's color depending on the hex given. "
                       "This defaults to the bot's avatar color, or if it's too dark, pink color, cause i like pink.")
    async def topcommands(self, ctx: StellaContext, member: UntilFlag[Union[Literal["guild", "me"], IsBot]],
                          **flags: discord.Color):
        target = member
        if isinstance(target, discord.Member):
            query = "SELECT bot_id, command, COUNT(command) AS usage FROM commands_list " \
                    "WHERE guild_id=$1 AND bot_id=$2 " \
                    "GROUP BY bot_id, command " \
                    "ORDER BY usage DESC LIMIT 10"
            values = (ctx.guild.id, target.id)
            error = "Looks like no data is present for this bot."
            method = "avatar"
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
        with ctx.typing():
            avatar_bytes = io.BytesIO(await asset.read())
            if not (color := flags.get("color")):
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


def setup(bot: StellaBot) -> None:
    bot.add_cog(Stat(bot))
