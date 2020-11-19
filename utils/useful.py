from collections import namedtuple
import discord
import datetime
import ctypes
import traceback
import sys
import functools
from dataclasses import dataclass, field
from discord.ext.menus import First, Last, Button
from discord.utils import maybe_coroutine
from discord.ext import commands, menus


async def try_call(code, exception, ret=False, args: tuple = (), kwargs: dict = None):
    """one liner method that handles all errors in a single line which returns None, or Error instance depending on ret
       value.
    """
    if kwargs is None:
        kwargs = {}
    try:
        return await maybe_coroutine(code, *args, **kwargs) if args or kwargs else await code
    except exception as e:
        return (None, e)[ret]


def call(func, *args, exception=Exception, ret=False, **kwargs):
    """one liner method that handles all errors in a single line which returns None, or Error instance depending on ret
       value.
    """
    try:
        return func(*args, **kwargs)
    except exception as e:
        return (None, e)[ret]


class BaseEmbed(discord.Embed):
    """Main purpose is to get the usual setup of Embed for a command or an error embed"""
    def __init__(self, color=0xffcccb, timestamp=None, **kwargs):
        super(BaseEmbed, self).__init__(color=color, timestamp=timestamp or datetime.datetime.utcnow(), **kwargs)

    @classmethod
    def default(cls, ctx, **kwargs):
        instance = cls(**kwargs)
        instance.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.avatar_url)
        return instance

    @classmethod
    def to_error(cls, color=discord.Color.red(), **kwargs):
        return cls(color=color, **kwargs)


class AfterGreedy(commands.Command):
    """Allows the ability to process Greedy converter result before it is passed into the command parameter."""
    async def _transform_greedy_pos(self, ctx, param, required, converter):
        result = await super()._transform_greedy_pos(ctx, param, required, converter)
        if hasattr(converter, 'after_greedy'):
            return await converter.after_greedy(ctx, result)
        return result


def unpack(li: list):
    """Flattens list of list where it is a list, while leaving alone any other element."""
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
            new_but = self.dict_emoji[emoji.name]
            new_button = Button(new_but.emoji, callback, position=new_but.position)
            del self.dict_emoji[emoji.name]
            self.dict_emoji[new_but.emoji] = new_but
            super().add_button(new_button)
            super().remove_button(emoji)


def default_date(datetime_var):
    """The default date format that are used across this bot."""
    return datetime_var.strftime('%d %b %Y %I:%M %p %Z')


lib = ctypes.CDLL("c_codes/binary_prefix.so")
find_prefix = lib.find_prefix
find_prefix.restype = ctypes.c_char_p


def compile_prefix(prefixes):
    """Converts a list of strings that are sorted into binary that will be accepted by C code."""
    ArrString = ctypes.c_char_p * len(prefixes)
    pre = (x.encode('utf-8') for x in prefixes)
    array_string = ArrString(*pre)
    size = len(prefixes)
    return array_string, size


def search_prefix(array_result, content_buffer):
    """Calls a function called find_prefix from C."""
    array_string, size = array_result
    find_prefix.argtypes = [ctypes.c_char_p * size, ctypes.c_char_p, ctypes.c_int]
    result = find_prefix(array_string, content_buffer, size)
    strresult = ctypes.c_char_p(result).value
    return strresult.decode('utf-8')


@dataclass
class DecoStore:
    """Class that stores event callbacks for the source command"""
    functions: dict = field(default_factory=dict)

    def get(self, content):
        if content in self.functions:
            return self.functions[content]

    def update(self, func):
        self.functions.update({f"{func.__module__}.{func.__name__}": func})


decorator_store = DecoStore()


def event_check(func):
    """Event decorator check"""
    def check(method):
        decorator_store.update(method)

        @functools.wraps(method)
        async def wrapper(*args, **kwargs):
            if func(*args, **kwargs):
                await method(*args, **kwargs)
        return wrapper
    return check


def print_exception(text, error):
    print(text, file=sys.stderr)
    traceback.print_exception(type(error), error, error.__traceback__, file=sys.stderr)