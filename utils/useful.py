import discord
import datetime
import ctypes
from discord.utils import maybe_coroutine
from discord.ext import commands


async def try_call(code, exception, ret=False, args: tuple = (), kwargs: dict = None):
    if kwargs is None:
        kwargs = {}
    try:
        return await maybe_coroutine(code, *args, **kwargs) if args or kwargs else await code
    except exception as e:
        return e if ret else None


class BaseEmbed(discord.Embed):
    def __init__(self, color=0xffcccb, timestamp=datetime.datetime.utcnow(), **kwargs):
        super(BaseEmbed, self).__init__(color=color, timestamp=timestamp, **kwargs)

    @classmethod
    def default(cls, ctx, **kwargs):
        instance = cls(**kwargs)
        instance.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.avatar_url)
        return instance

    @classmethod
    def to_error(cls, color=discord.Color.red(), **kwargs):
        return cls(color=color, **kwargs)


class AfterGreedy(commands.Command):
    async def _transform_greedy_pos(self, ctx, param, required, converter):
        result = await super()._transform_greedy_pos(ctx, param, required, converter)
        if hasattr(converter, 'after_greedy'):
            return await converter.after_greedy(ctx, result)
        return result


# flatten list of list and list
def unpack(li: list):
    for item in li:
        if isinstance(item, list):
            yield from unpack(item)
        else:
            yield item


lib = ctypes.CDLL("c_codes/binary_prefix.so")
find_prefix = lib.find_prefix
find_prefix.restypes = [ctypes.c_char_p]


def compile_prefix(prefixes):
    ArrString = ctypes.c_char_p * len(prefixes)

    pre = [x.encode('utf-8') for x in prefixes]
    array_string = ArrString(*pre)
    return array_string


def search_prefix(array_string, content_buffer, _size):
    find_prefix.argtypes = [ctypes.c_char_p * _size, ctypes.c_char_p, ctypes.c_int]
    result = find_prefix(array_string, content_buffer, _size)
    c_obj = ctypes.c_char_p(result)
    return c_obj.value.decode('utf-8')
