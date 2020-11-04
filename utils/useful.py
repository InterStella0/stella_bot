from collections import namedtuple

import discord
import datetime
import ctypes
import platform

from discord.ext.menus import First, Last, Button
from discord.utils import maybe_coroutine
from discord.ext import commands, menus


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

class MenuBase(menus.MenuPages):
    """This is a MenuPages class that is used every single paginator menus. All it does is replace the default emoji
       with a custom emoji, and keep the functionality."""
    def __init__(self, source, **kwargs):
        super().__init__(source, **kwargs)
        self.info = False

        EmojiB = namedtuple("EmojiB", "emoji position explain")
        self.dict_emoji = {'\N{BLACK LEFT-POINTING DOUBLE TRIANGLE WITH VERTICAL BAR}\ufe0f':
                           EmojiB("<:before_fast_check:754948796139569224>", First(0), "Goes to the first page."),

                           '\N{BLACK LEFT-POINTING TRIANGLE}\ufe0f':
                           EmojiB("<:before_check:754948796487565332>", First(1), "Goes to the previous page."),

                           '\N{BLACK RIGHT-POINTING TRIANGLE}\ufe0f':
                           EmojiB("<:next_check:754948796361736213>", Last(1), "Goes to the next page."),

                           '\N{BLACK RIGHT-POINTING DOUBLE TRIANGLE WITH VERTICAL BAR}\ufe0f':
                           EmojiB("<:next_fast_check:754948796391227442>", Last(2), "Goes to the last page."),

                           '\N{BLACK SQUARE FOR STOP}\ufe0f':
                           EmojiB("<:stop_check:754948796365930517>", Last(0), "Remove this message.")
                           }

        for emoji in super().buttons:
            callback = super().buttons[emoji].action  # gets the function that would be called for that button
            if emoji.name not in self.dict_emoji:
                continue
            new_butO = self.dict_emoji[emoji.name]
            new_button = Button(new_butO.emoji, callback, position=new_butO.position)
            del self.dict_emoji[emoji.name]
            self.dict_emoji[new_butO.emoji] = new_butO
            super().add_button(new_button)
            super().remove_button(emoji)


lib = ctypes.CDLL("c_codes/binary_prefix.so")
find_prefix = lib.find_prefix
find_prefix.restypes = [ctypes.c_int]


def compile_prefix(prefixes):
    ArrString = ctypes.c_char_p * len(prefixes)

    pre = [x.encode('utf-8') for x in prefixes]
    array_string = ArrString(*pre)
    return array_string, prefixes


def search_prefix(array_string, content_buffer, ori, _size):
    find_prefix.argtypes = [ctypes.c_char_p * _size, ctypes.c_char_p, ctypes.c_int]
    result = find_prefix(array_string, content_buffer, _size)
    return ori[result] if result != -1 else None
