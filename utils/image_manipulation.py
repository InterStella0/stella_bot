import datetime
import io
import math
from typing import Coroutine, Any, List, Optional

import discord
from PIL import Image, ImageEnhance, ImageFilter
import matplotlib.dates as mdates
import matplotlib.colors as mcolors
from matplotlib.axes import Axes
from matplotlib.figure import Figure
import matplotlib.patheffects as peffects
from matplotlib import pyplot as plt

import numpy as np
from matplotlib.patches import Polygon
from scipy.interpolate import make_interp_spline

from utils.decorators import in_executor


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
    spl = make_interp_spline(date_num, value_np, k=2 - (not kwargs.get("smooth")))
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

    # yes i'm aware on how this looks, but it must be done.
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
    h = len(x_val) * .48
    fig, axes = plt.subplots(figsize=(6.4, h))
    bars = axes.barh(x_val, y_val, edgecolor=color)

    temp = discord.Color(int(color.replace("#", "0x"), base=16))
    comp = str((comp_color := complement_color(*temp.to_rgb())))
    inverse = str(discord.Color.from_rgb(*inverse_color(*comp_color.to_rgb())))
    maximum_size = max(y_val)

    def percent(per: int) -> int:
        return maximum_size * per

    for i, v in enumerate(y_val):
        pixel = percent(.025) + percent(.001)
        text_size = len(str(v))
        offset = pixel * text_size
        actual_val = v - offset
        actual_val += ((pixel + percent(.010)) * text_size) * (actual_val <= (0 + percent(.01)))
        axes.text(actual_val, i - .15, f"{v:,}", color=comp, fontweight='bold',
                  path_effects=[peffects.withStroke(linewidth=.8, foreground=inverse)])

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
def process_image(avatar_bytes: io.BytesIO, target: io.BytesIO) -> io.BytesIO:
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
def get_majority_color(b: io.BytesIO) -> discord.Color:
    with Image.open(b) as target:
        smol = target.quantize(4)
        return discord.Color.from_rgb(*smol.getpalette()[:3])


def islight(r: int, g: int, b: int) -> bool:
    # Found this equation in http://alienryderflex.com/hsp.html, fucking insane i tell ya
    hsp = math.sqrt(.299 * r ** 2 + .587 * g ** 2 + .114 * b ** 2)
    return hsp > 127.5
