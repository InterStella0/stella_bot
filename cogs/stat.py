import datetime
import discord
import matplotlib
import itertools
import collections
import io
import numpy as np
import gc
from PIL import Image, ImageEnhance, ImageFilter
from scipy.interpolate import make_interp_spline
from matplotlib import pyplot as plt
import matplotlib.dates as mdates
import matplotlib.colors as mcolors
from matplotlib.patches import Polygon
from utils.greedy_parser import GreedyParser, Consumer
from utils.new_converters import TimeConverter, IsBot
from utils.decorators import in_executor
from discord.ext import commands

matplotlib.use('Agg')
TimeConverterMinMax = TimeConverter(datetime.timedelta(days=2), datetime.timedelta(weeks=8))

@in_executor()
def create_graph(x, y):
    color = '#ffcccb'
    fig, axes = plt.subplots()
    date_np = np.array(sorted(x))
    value_np = np.array([*reversed(y)])
    date_num = mdates.date2num(date_np)

    # Graph smoothen
    date_num_smooth = np.linspace(date_num.min(), date_num.max(), 100) 
    spl = make_interp_spline(date_num, value_np, k=2)
    value_np_smooth = spl(date_num_smooth)

    line, = axes.plot(mdates.num2date(date_num_smooth), value_np_smooth, color=color)
    # Graph gradient
    if (alpha := line.get_alpha()) is None:
        alpha = 1.0

    z = np.empty((100, 1, 4), dtype=float)
    rgb = mcolors.colorConverter.to_rgb(color)
    z[:,:,:3] = rgb
    z[:,:,-1] = np.linspace(0, alpha, 100)[:,None]

    offset = value_np.max() * 0.20
    xmin, xmax, ymin, ymax = date_num.min(), date_num.max(), value_np.min(), value_np.max() + offset
    im = axes.imshow(z, aspect='auto', extent=[xmin, xmax, ymin, ymax],
                   origin='lower', zorder=line.get_zorder())

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
    fig.delaxes(axes)
    fig.add_axes(axes)

    buffer = io.BytesIO()
    fig.savefig(buffer, transparent=True)

    axes.clear()
    fig.clf()
    plt.close(fig)

    buffer.seek(0)
    return buffer

@in_executor()
def create_bar(x, y, **kwargs):
    fig, axes = plt.subplots()
    axes.barh(x, y)
    for attr, value in kwargs.items():
        getattr(axes, f"set_{attr}")(value)
    fig.delaxes(axes)
    fig.add_axes(axes)
    buffer = io.BytesIO()
    fig.savefig(buffer, transparent=True , bbox_inches = "tight")
    axes.clear()
    fig.clf()
    plt.close(fig)
    return buffer


class Stat(commands.Cog, name="Statistic"):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(aliases=["botactivitys", "ba"], 
                      help="Creates a graph that represents the bot's usage in a server, which shows the command " \
                           "invoke happening for a bot.",
                      cls=GreedyParser)
    @commands.guild_only()
    async def botactivity(self, ctx, member: Consumer[IsBot(dont_fetch=True)], *, time: TimeConverterMinMax = None):
        time_rn = datetime.datetime.utcnow()
        time_given = time or time_rn - datetime.timedelta(days=2)
        query = "SELECT * FROM commands_list WHERE guild_id=$1 AND bot_id=$2 AND time_used > $3"
        data = await self.bot.pool_pg.fetch(query, ctx.guild.id, member.id, time_given)
        if not data:
            raise commands.CommandError("Looks like no data is present for this bot.")

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


        @in_executor()
        def process_image():
            nonlocal avatar_bytes, graph
            with Image.open(avatar_bytes).convert('RGBA') as avatar, Image.open(graph) as graph:
                side = max(avatar.size)
                avatar = avatar.crop((0, 0, side, side)) 
                w, h = graph.size
                avatar = avatar.resize((w, w))
                offset_below = 10
                avatar = avatar.crop((0, 0, w, h + offset_below)) 
                reducer = ImageEnhance.Brightness(avatar)
                background = reducer.enhance(0.378)
                background = background.filter(ImageFilter.GaussianBlur(8))
                gray_back = Image.new('RGBA', avatar.size, (*discord.Color.dark_theme().to_rgb(), 255))
                gray_back.paste(background, [0,0], mask=background)
                background = gray_back
                background.paste(graph, [0,0], mask=graph)
                to_send = io.BytesIO()
                background.save(to_send, format="PNG")
                to_send.seek(0)
                return to_send

        with ctx.typing():
            graph = await create_graph(x, y)
            avatar_bytes = io.BytesIO(await member.avatar_url.read())
            to_send = await process_image()
        embed = discord.Embed()
        embed.set_image(url="attachment://picture.png")
        embed.set_author(name=member, icon_url=member.avatar_url)
        await ctx.embed(embed=embed, file=discord.File(to_send, filename="picture.png"))
        graph.close()
        avatar_bytes.close()
        to_send.close()

    @commands.command(aliases=["topcommand", "tc", "tcs"], cls=GreedyParser)
    async def topcommands(self, ctx, *, member: IsBot(user_check=False)):
        query = "SELECT bot_id, command, COUNT(command) AS usage FROM commands_list " \
                "WHERE guild_id=$1 AND bot_id=$2 " \
                "GROUP BY bot_id, command " \
                "ORDER BY usage DESC LIMIT 10"

        data = await self.bot.pool_pg.fetch(query, ctx.guild.id, member.id)
        if not data:
            raise commands.CommandError("Looks like no data is present for this bot.")

        data.reverse()
        names = [v["command"] for v in data]
        usages = [v["usage"] for v in data]
        payload = dict(title=f"Top {len(names)} commands for {member}",
                       xlabel="Usage",
                       ylabel="Commands")
        bar = await create_bar(names, usages, **payload)
        embed = discord.Embed()
        embed.set_image(url="attachment://picture.png")
        embed.set_author(name=member, icon_url=member.avatar_url)
        await ctx.embed(embed=embed, file=discord.File(bar, filename="picture.png"))
        bar.close()
        gc.collect()


def setup(bot):
    bot.add_cog(Stat(bot))
