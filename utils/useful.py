import inspect
import discord
import datetime
import ctypes
import traceback
import sys
import functools
import asyncio
import contextlib
from collections import namedtuple
from discord.ext.menus import First, Last, Button
from discord.utils import maybe_coroutine
from discord.ext import commands, menus


async def try_call(method, *args, exception=Exception, ret=False, **kwargs):
    """one liner method that handles all errors in a single line which returns None, or Error instance depending on ret
       value.
    """
    try:
        return await maybe_coroutine(method, *args, **kwargs)
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
        super().__init__(color=color, timestamp=timestamp or datetime.datetime.utcnow(), **kwargs)

    @classmethod
    def default(cls, ctx, **kwargs):
        instance = cls(**kwargs)
        instance.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.avatar_url)
        return instance

    @classmethod
    def to_error(cls, title="Error", color=discord.Color.red(), **kwargs):
        return cls(title=title, color=color, **kwargs)


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
    def __init__(self, source, dict_emoji=None, **kwargs):
        super().__init__(source, delete_message_after=kwargs.pop('delete_message_after', True), **kwargs)
        self.info = False

        EmojiB = namedtuple("EmojiB", "emoji position explain")
        def_dict_emoji = {'\N{BLACK LEFT-POINTING DOUBLE TRIANGLE WITH VERTICAL BAR}\ufe0f':
                          EmojiB("<:before_fast_check:754948796139569224>", First(0),
                                 "Goes to the first page."),

                          '\N{BLACK LEFT-POINTING TRIANGLE}\ufe0f':
                          EmojiB("<:before_check:754948796487565332>", First(1),
                                 "Goes to the previous page."),

                          '\N{BLACK RIGHT-POINTING TRIANGLE}\ufe0f':
                          EmojiB("<:next_check:754948796361736213>", Last(1),
                                 "Goes to the next page."),

                          '\N{BLACK RIGHT-POINTING DOUBLE TRIANGLE WITH VERTICAL BAR}\ufe0f':
                          EmojiB("<:next_fast_check:754948796391227442>", Last(2),
                                 "Goes to the last page."),

                          '\N{BLACK SQUARE FOR STOP}\ufe0f':
                          EmojiB("<:stop_check:754948796365930517>", Last(0),
                                 "Remove this message.")
                          }
        self.dict_emoji = dict_emoji or def_dict_emoji
        for emoji in self.buttons:
            callback = self.buttons[emoji].action
            if emoji.name not in self.dict_emoji:
                continue
            new_but = self.dict_emoji[emoji.name]
            new_button = Button(new_but.emoji, callback, position=new_but.position)
            del self.dict_emoji[emoji.name]
            self.dict_emoji[new_but.emoji] = new_but
            self.add_button(new_button)
            self.remove_button(emoji)

    async def _get_kwargs_from_page(self, page):
        value = await discord.utils.maybe_coroutine(self._source.format_page, self, page)
        no_ping = {'mention_author': False}
        if isinstance(value, dict):
            value.update(no_ping)
        elif isinstance(value, str):
            no_ping.update({'content': value})
        elif isinstance(value, discord.Embed):
            no_ping.update({'embed': value, 'content': None})
        return no_ping

    def generate_page(self, content, maximum):
        if maximum > 1:
            page = f"Page {self.current_page + 1}/{maximum}"
            if isinstance(content, discord.Embed):
                return content.set_author(name=page)
            elif isinstance(content, str):
                return f"{page}\n{content}"
        return content

    async def send_initial_message(self, ctx, channel):
        page = await self._source.get_page(0)
        kwargs = await self._get_kwargs_from_page(page)
        return await ctx.reply(**kwargs)


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


def event_check(func):
    """Event decorator check."""
    def check(method):
        method.callback = method

        @functools.wraps(method)
        async def wrapper(*args, **kwargs):
            if await maybe_coroutine(func, *args, **kwargs):
                await method(*args, **kwargs)
        return wrapper
    return check


def print_exception(text, error):
    """Prints the exception with proper traceback."""
    print(text, file=sys.stderr)
    traceback.print_exception(type(error), error, error.__traceback__, file=sys.stderr)
    etype = type(error)
    trace = error.__traceback__
    lines = traceback.format_exception(etype, error, trace)
    return "".join(lines)


def plural(text, size):
    """Auto corrects text to show plural or singular depending on the size number."""
    logic = size == 1
    target = (("(s)", ("s", "")), ("(is/are)", ("are", "is")))
    for x, y in target:
        text = text.replace(x, y[logic])
    return text


def realign(iterable, key, discrim='|'):
    """Auto align a list of str with the highest substring before the key."""
    high = max(cont.index(key) for cont in iterable)
    reform = [high - cont.index(key) for cont in iterable]
    return [x.replace(key, f'{" " * off} {discrim}') for x, off in zip(iterable, reform)]


class StellaContext(commands.Context):
    async def maybe_reply(self, content=None, mention_author=False, **kwargs):
        """Replies if there is a message in between the command invoker and the bot's message."""
        await asyncio.sleep(0.05)
        with contextlib.suppress(discord.HTTPException):
            if self.channel.last_message != self.message:
                return await self.reply(content, mention_author=mention_author, **kwargs)
        await self.send(content, **kwargs)


async def maybe_method(func, cls=None, *args, **kwargs):
    """Pass the class if func is not a method."""
    if not inspect.ismethod(func):
        return await maybe_coroutine(func, cls, *args, **kwargs)
    return await maybe_coroutine(func, *args, **kwargs)
