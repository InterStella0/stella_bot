import inspect
import discord
import datetime
import ctypes
import traceback
import sys
import asyncio
import contextlib
from utils.decorators import pages, in_executor
from utils.greedy_parser import WithCommaStringView
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
    def __init__(self, color=0xffcccb, timestamp=None, fields=(), field_inline=False, **kwargs):
        super().__init__(color=color, timestamp=timestamp or datetime.datetime.utcnow(), **kwargs)
        for n, v in fields:
            self.add_field(name=n, value=v, inline=field_inline)

    @classmethod
    def default(cls, ctx, **kwargs):
        instance = cls(**kwargs)
        instance.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.avatar_url)
        return instance

    @classmethod
    def to_error(cls, title="Error", color=discord.Color.red(), **kwargs):
        return cls(title=title, color=color, **kwargs)


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

        # Remind me to redo this dumb code
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
        no_ping = {'allowed_mentions': discord.AllowedMentions(replied_user=False)}
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
                if embed_dict := getattr(content, "_author", None):
                    embed_dict["name"] += f"[{page.replace('Page ', '')}]"
                    return content
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


lib = ctypes.CDLL("./c_codes/parse_find.so")
multi_find_prefix = lib.multi_find_prefix
freeing = lib.free_result
multi_find_prefix.restype = ctypes.c_void_p
find_commands = lib.find_commands
find_commands.restype = ctypes.c_void_p


class RESULT(ctypes.Structure):
    _fields_ = [('found_array', ctypes.POINTER(ctypes.c_char_p)),
                ('size', ctypes.c_int)]


def compile_array(string_list):
    """Converts a list of strings that are sorted into binary that will be accepted by C code."""
    ArrString = ctypes.c_char_p * len(string_list)
    binary_array = (x.encode('utf-8') for x in string_list)
    array_string = ArrString(*binary_array)
    return array_string, len(string_list)


def decode_result(return_result):
    """Creates a RESULT structure from address given and return a list of the address"""
    result = RESULT.from_address(return_result)
    to_return = [x.decode("utf-8") for x in result.found_array[:result.size]]
    freeing(ctypes.byref(result))
    return to_return


def actually_calls(param, callback):
    """Handles C functions and return value."""
    array_stuff, content_buffer = param
    if array_stuff:
        array_string, size = array_stuff
        callback.argtypes = [ctypes.c_char_p * size, ctypes.c_char_p, ctypes.c_int]
        return_result = callback(array_string, content_buffer, size)
        return decode_result(return_result)


@in_executor()
def search_prefixes(*args):
    """Pass multi_find_prefix function from C."""
    return actually_calls(args, multi_find_prefix)


@in_executor()
def search_commands(*args):
    """Pass find_commands function from C."""
    return actually_calls(args, find_commands)


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
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.view = WithCommaStringView(kwargs.get("view"))
        self.__dict__.update(dict.fromkeys(["waiting", "result", "channel_used", "running", "failed"]))

    async def maybe_reply(self, content=None, mention_author=False, **kwargs):
        """Replies if there is a message in between the command invoker and the bot's message."""
        await asyncio.sleep(0.05)
        with contextlib.suppress(discord.HTTPException):
            if getattr(self.channel,"last_message", False) != self.message:
                return await self.reply(content, mention_author=mention_author, **kwargs)
        return await self.send(content, **kwargs)

    async def embed(self, content=None, *, reply=True, mention_author=False, embed=None, **kwargs):
        ori_embed = BaseEmbed.default(self, **kwargs)
        if embed:
            new_embed = embed.to_dict()
            new_embed.update(ori_embed.to_dict())
            ori_embed = discord.Embed.from_dict(new_embed)
        to_send = (self.send, self.maybe_reply)[reply]
        if not self.me.permissions_in(self.channel).embed_links:
            raise commands.BotMissingPermissions(["embed_links"])
        return await to_send(content, mention_author=mention_author, embed=ori_embed)

    def confirmed(self, message_id=None):
        message = self.message if not message_id else self.get_partial_message(message_id)
        return message.add_reaction("<:checkmark:753619798021373974>")


async def maybe_method(func, cls=None, *args, **kwargs):
    """Pass the class if func is not a method."""
    if not inspect.ismethod(func):
        return await maybe_coroutine(func, cls, *args, **kwargs)
    return await maybe_coroutine(func, *args, **kwargs)


@pages()
def empty_page_format(_, __, entry):
    """This is for Code Block ListPageSource and for help Cog ListPageSource"""
    return entry


class ListCall(list):
    """Quick data structure for calling every element in the array regardless of awaitable or not"""
    def append(self, rhs):
        return super().append(rhs)

    def call(self, *args, **kwargs):
        return asyncio.gather(*(maybe_coroutine(func, *args, **kwargs) for func in self))

def in_local(func, target):
    return func()[target]
