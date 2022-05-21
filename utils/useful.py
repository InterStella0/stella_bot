from __future__ import annotations

import asyncio
import contextlib
import ctypes
import datetime
import inspect
import operator
import os
import sys
import textwrap
import traceback
import typing

from typing import (Any, AsyncGenerator, Awaitable, Callable, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple,
                    Type, TypeVar, Union)

import discord
import pytz

from discord.ext import commands
from discord.utils import maybe_coroutine

from utils.context_managers import BreakableTyping
from utils.decorators import in_executor, pages

# TODO: do some detail documentation, cause im lazy


async def try_call(method: Union[Awaitable[Any], Callable[..., Any]], *args: Any,
                   exception: Type[Exception] = Exception, ret: bool = False, **kwargs: Any) -> Any:
    """one liner method that handles all errors in a single line which returns None, or Error instance depending on ret
       value.
    """
    try:
        return await maybe_coroutine(method, *args, **kwargs)  # type: ignore[no-untyped-call]
    except exception as e:
        return (None, e)[ret]


def call(func: Callable[..., Any], *args: Any,
         exception: Type[Exception] = Exception, ret: bool = False, **kwargs: Any) -> Any:
    """one liner method that handles all errors in a single line which returns None, or Error instance depending on ret
       value.
    """
    try:
        return func(*args, **kwargs)
    except exception as e:
        return (None, e)[ret]


class StellaEmbed(discord.Embed):
    """Main purpose is to get the usual setup of Embed for a command or an error embed"""
    def __init__(self, color: Union[discord.Color, int] = 0xffcccb, timestamp: Optional[datetime.datetime] = None,
                 fields: Iterable[Tuple[str, str]] = (), field_inline: bool = False, **kwargs: Any):
        super().__init__(color=color, timestamp=timestamp or discord.utils.utcnow(), **kwargs)
        for n, v in fields:
            self.add_field(name=n, value=v, inline=field_inline)

    @classmethod
    def default(cls, ctx: commands.Context, **kwargs: Any) -> StellaEmbed:
        instance = cls(**kwargs)
        instance.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.display_avatar)
        return instance

    @classmethod
    def to_error(cls, title: Optional[str] = "Error",
                 color: Union[discord.Color, int] = discord.Color.red(), **kwargs: Any) -> StellaEmbed:
        return cls(title=title, color=color, **kwargs)


T = TypeVar("T")


# this cannot be typed properly yet
# stella, check this periodically https://github.com/python/mypy/issues/731
def unpack(li: List[Union[List[T], T]], /) -> Iterable[T]:
    """Flattens list of list while leaving alone any other element."""
    for item in li:
        if isinstance(item, list):
            yield from unpack(item)  # type: ignore
        else:
            yield item


def default_date(datetime_var: datetime.datetime) -> str:
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


def compile_array(string_list: List[str], /) -> Tuple[ctypes.c_char_p, int]:
    """Converts a list of strings that are sorted into binary that will be accepted by C code."""
    ArrString = ctypes.c_char_p * len(string_list)
    binary_array = (x.encode('utf-8') for x in string_list)
    array_string = ArrString(*binary_array)
    return array_string, len(string_list)


def decode_result(return_result: int, /) -> List[Any]:
    """Creates a RESULT structure from address given and return a list of the address"""
    result = RESULT.from_address(return_result)
    to_return = [x.decode("utf-8") for x in result.found_array[:result.size]]
    freeing(ctypes.byref(result))
    return to_return


def actually_calls(param: Tuple[Any, Any], callback: Callable[[Any, Any, Any], int], /) -> Optional[List[Any]]:
    """Handles C functions and return value."""
    array_stuff, content_buffer = param
    if array_stuff:
        array_string, size = array_stuff
        callback.argtypes = [ctypes.c_char_p * size, ctypes.c_char_p, ctypes.c_int]
        return_result = callback(array_string, content_buffer, size)
        return decode_result(return_result)


@in_executor()
def search_prefixes(*args: Any) -> List[Any]:
    """Pass multi_find_prefix function from C."""
    return actually_calls(args, multi_find_prefix)


@in_executor()
def search_commands(*args: Any) -> List[Any]:
    """Pass find_commands function from C."""
    return actually_calls(args, find_commands)


def print_exception(text: str, error: Exception, *, _print: bool = True) -> str:
    """Prints the exception with proper traceback."""
    if _print:
        print(text, file=sys.stderr)
    traceback.print_exception(type(error), error, error.__traceback__, file=sys.stderr)
    etype = type(error)
    trace = error.__traceback__
    lines = traceback.format_exception(etype, error, trace)
    return "".join(lines)


def plural(text: str, size: int, *, selections: Iterable[Tuple[str, Tuple[str, str]]] = ()) -> str:
    """Auto corrects text to show plural or singular depending on the size number."""
    logic = size == 1
    target = (("(s)", ("s", "")), ("(is/are)", ("are", "is")), *selections)
    for x, y in target:
        text = text.replace(x, y[logic])
    return text


def realign(iterable: Iterable[str], key: str, discrim: str = '|') -> List[str]:
    """Auto align a list of str with the highest substring before the key."""
    high = max(cont.index(key) for cont in iterable)
    reform = [high - cont.index(key) for cont in iterable]
    return [x.replace(key, f'{" " * off} {discrim}') for x, off in zip(iterable, reform)]


# mypy does not pick up star imports in discord.ext.commands. possible solution is using `namespace_packages` option,
# but it makes mypy crash. ignore disallow_subclassing_any for now, also explicitly define message and channel types
# see https://github.com/python/mypy/issues/12257 for details
class StellaContext(commands.Context):  # type: ignore[misc]
    message: discord.Message
    channel: discord.abc.MessageableChannel

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        from utils.greedy_parser import WithCommaStringView
        self.view = WithCommaStringView(kwargs.get("view"))
        self.__dict__.update(dict.fromkeys(["waiting", "result", "channel_used", "running", "failed", "done"]))
        self.sent_messages: Dict[int, discord.Message] = {}
        self.reinvoked = False

    async def reinvoke(self, *, message: Optional[discord.Message] = None, **kwargs: Any) -> None:
        self.reinvoked = True
        if message is None:
            await super().reinvoke(**kwargs)
        else:
            if self.message != message:
                raise Exception("Context Message and Given Message does not match.")
            self.message = message
            new_ctx = await self.bot.get_context(message)
            self.view = new_ctx.view
            self.invoked_with = new_ctx.invoked_with
            self.prefix = new_ctx.prefix
            self.command = new_ctx.command
            await self.bot.invoke(self)

    async def edit_if_found(self, callback: Callable[..., Awaitable[discord.Message]], /,
                            *args: Any, **kwargs: Any) -> discord.Message:
        if self.reinvoked and self.sent_messages:
            message = discord.utils.find(
                lambda m: not getattr(m, "to_delete", False), reversed(self.sent_messages.values())
            )
            if message is not None:
                if args:
                    kwargs.update({"content": args[0]})

                if "mention_author" in kwargs:
                    value = kwargs.pop("mention_author")
                    if "allowed_mentions" not in kwargs:
                        kwargs.update({"allowed_mentions": discord.AllowedMentions(replied_user=value)})
                    else:
                        kwargs["allowed_mentions"].replied_user = value

                allowed_kwargs = list(inspect.signature(discord.Message.edit).parameters)
                for key in list(kwargs):
                    if key not in allowed_kwargs:
                        kwargs.pop(key)

                return await message.edit(**kwargs)

        message = await callback(*args, **kwargs)
        return message

    async def send(self, *args: Any, **kwargs: Any) -> discord.Message:
        message = await self.edit_if_found(super().send, *args, **kwargs)
        return self.process_message(message)

    async def reply(self, *args: Any, **kwargs: Any) -> discord.Message:
        message = await self.edit_if_found(super().reply, *args, **kwargs)
        return self.process_message(message)

    def process_message(self, message: discord.Message) -> discord.Message:
        self.sent_messages.update({message.id: message})
        return message

    async def delete_all(self) -> None:
        if self.channel.permissions_for(self.me).manage_messages:
            with contextlib.suppress(discord.NotFound):
                await self.channel.delete_messages(self.sent_messages.values())
        else:
            for message in self.sent_messages.values():
                await message.delete(delay=0)

        self.sent_messages.clear()

    def get_message(self, message_id: int) -> Optional[discord.Message]:
        return self.sent_messages.get(message_id)

    @property
    def created_at(self) -> datetime.datetime:
        return self.message.created_at

    def remove_message(self, message_id: int) -> Optional[discord.Message]:
        return self.sent_messages.pop(message_id, None)

    async def maybe_reply(self, content: Optional[str] = None, mention_author: bool = False,
                          **kwargs: Any) -> discord.Message:
        """Replies if there is a message in between the command invoker and the bot's message."""
        await asyncio.sleep(0.05)
        with contextlib.suppress(discord.HTTPException):
            if ref := self.message.reference:
                # it is very unlikely for this to not be cached
                author = ref.cached_message.author  # type: ignore
                if not mention_author:
                    mention_author = author in self.message.mentions and author.id not in self.message.raw_mentions
                return await self.send(content, mention_author=mention_author, reference=ref, **kwargs)

            if getattr(self.channel, "last_message", None) != self.message:
                return await self.reply(content, mention_author=mention_author, **kwargs)
        return await self.send(content, **kwargs)

    async def embed(self, content: Optional[str] = None, *, reply: bool = True, mention_author: bool = False,
                    embed: Optional[discord.Embed] = None, **kwargs: Any) -> discord.Message:
        embed_only_kwargs = [
            "colour", "color", "title", "type", "url", "description", "timestamp", "fields", "field_inline"
        ]
        ori_embed = StellaEmbed.default(
            self, **{key: value for key, value in kwargs.items() if key in embed_only_kwargs}
        )
        if embed:
            new_embed = embed.to_dict()
            new_embed.update(ori_embed.to_dict())
            ori_embed = StellaEmbed.from_dict(new_embed)
        to_send = (self.send, self.maybe_reply)[reply]
        if not self.channel.permissions_for(self.me).embed_links:
            raise commands.BotMissingPermissions(["embed_links"])
        send_dict = {x: y for x, y in kwargs.items() if x not in embed_only_kwargs}
        return await to_send(content, mention_author=mention_author, embed=ori_embed, **send_dict)

    def confirmed(self, message_id: Optional[int] = None) -> Awaitable[None]:
        message = self.message if not message_id else self.channel.get_partial_message(message_id)
        # discord.py adds method aliases to PartialMessage which accept Message as self, wrong typing
        return message.add_reaction("<:checkmark:753619798021373974>")  # type: ignore[misc]

    async def confirmation(self, content: str, delete_after: bool = False, *,
                           to_respond: Optional[Union[discord.User, discord.Member]] = None,
                           **kwargs: Any) -> Optional[bool]:
        from utils.buttons import ConfirmView
        return await ConfirmView(self, to_respond=to_respond, delete_after=delete_after).send(content, **kwargs)

    def breaktyping(self, /, *, limit: Optional[int] = None) -> BreakableTyping:
        return BreakableTyping(self, limit=limit)


async def maybe_method(func: Union[Awaitable[Any], Callable[..., Any]], cls: Optional[type] = None,
                       *args: Any, **kwargs: Any) -> Any:
    """Pass the class if func is not a method."""
    if not inspect.ismethod(func):
        return await maybe_coroutine(func, cls, *args, **kwargs)  # type: ignore[no-untyped-call]
    return await maybe_coroutine(func, *args, **kwargs)  # type: ignore[no-untyped-call]


@pages()
def empty_page_format(_: Any, __: Any, entry: T) -> T:
    """This is for Code Block ListPageSource and for help Cog ListPageSource"""
    return entry


class ListCall(List[Any]):
    """Quick data structure for calling every element in the array regardless of awaitable or not"""
    def append(self, rhs: Awaitable[Any]) -> None:
        return super().append(rhs)

    def call(self, *args: Any, **kwargs: Any) -> asyncio.Future[List[Any]]:
        return asyncio.gather(
            *(maybe_coroutine(func, *args, **kwargs) for func in self))  # type: ignore[no-untyped-call]


def in_local(func: Callable[[], Any], target: Any) -> Any:
    """Useless function"""
    return func()[target]


# note: do not even think about changing superclass, it will end badly
class RenameClass(typing._ProtocolMeta):
    """It rename a class based on name kwargs, what do you expect"""
    def __new__(cls, _orig_name: str, bases: Tuple[type, ...], attrs: Dict[str, Any], *, name: str) -> Any:
        new_class = super().__new__(cls, name, bases, attrs)
        if name:
            new_class.__name__ = name
        return new_class


def isiterable(obj: Any) -> Optional[bool]:
    try:
        iter(obj) and obj[0]
    except TypeError:
        return False
    except IndexError:
        pass
    return True


async def cancel_gen(agen: AsyncGenerator[Any, Any]) -> None:
    task = asyncio.create_task(agen.__anext__())
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    await agen.aclose()


def _iterate_source_line_counts(root: str) -> Iterator[int]:
    for child in os.listdir(root):
        # ignore nasty hidden files
        if child.startswith("."):
            continue

        path = f"{root}/{child}"
        if os.path.isdir(path):
            yield from _iterate_source_line_counts(path)
        else:
            if path.endswith((".py", ".c")):
                with open(path, encoding="utf8") as f:
                    yield len(f.readlines())


def count_source_lines(root: str) -> int:
    return sum(_iterate_source_line_counts(root))


def aware_utc(dt: datetime.datetime, format: bool = True, *,
              mode: Optional[discord.utils.TimestampStyle] = 'F') -> Union[datetime.datetime, str]:
    new_dt = dt.replace(tzinfo=pytz.UTC)
    if format:
        return discord.utils.format_dt(new_dt, mode)
    return dt.replace(tzinfo=pytz.UTC)


def islicechunk(sequence: Sequence[T], *, chunk: int = 1) -> Iterator[Sequence[T]]:
    """works like islice, it cuts a sequence into chunks, instead of only getting the end of the sequence elements
        sequence: Sequence[Any]
            The sequence that you want to cut
        chunk: int
            Cut the sequence every given number. Defaults to 1

        return: Iterator[Sequence[T]]
            An iterable that got cut up given by chunk
     """
    end = 0
    for i, x in enumerate(sequence):
        if not i % chunk:
            end += chunk
            yield sequence[end - chunk: end]


def text_chunker(text: str, *, width: int = 1880, max_newline: int = 20, wrap: bool = True,
                 wrap_during_chunk: bool = True, reserve_whole_line=False) -> List[str]:
    """Chunks a given text into a flattened list.
        text: str
            massive text that needs to be chunked
        width: int
            maximum character per chunks
        max_newline: int
            maximum new line per chunks
        wrap: bool
            whether to chunk before the max_newline pagination
        wrap_during_chunk: bool
            maximum character during max_newline pagination
        reserve_whole_line: bool
            reserve the entire line without splitting

        return: List[str]
    """
    # idk i just write this long ass doc so i remember how to use it later lmao
    wrapped_text = [text]
    if wrap:
        wrapped_text = textwrap.wrap(text, width=width, replace_whitespace=False)

    for i, each in enumerate(text):
        elems = each.splitlines()
        if len(elems) >= max_newline:
            new_elems: List[Union[str, List[str]]] = []
            for values in islicechunk(elems, chunk=20):
                elem = "\n".join(values)
                if wrap_during_chunk:
                    elem = textwrap.wrap(elem, width=width, replace_whitespace=False)  # type: ignore[assignment]
                new_elems.append(elem)
            wrapped_text[i] = new_elems  # type: ignore

    return [*unpack(wrapped_text)]  # type: ignore


def multiget(iterable: Iterable[T], *, size: int = 2, **kwargs: Any) -> List[T]:
    converted = [(operator.attrgetter(attr.replace('__', '.')), value) for attr, value in kwargs.items()]

    value = []
    for elem in iterable:
        if all(pred(elem) == value for pred, value in converted):
            value.append(elem)
        if len(value) >= size:
            break
    return value


async def aislice(citerator: AsyncGenerator[Any, Any], cut: int) -> AsyncGenerator[Any, Any]:
    i = 0
    async for v in citerator:
        i += 1
        yield v
        if i == cut:
            break


def nearest_nth(iterable: Iterable[str], *, chunk: int, width: int):
    while chunk:
        v = iterable[:chunk]
        size = len("\n".join(v))
        if size > width:
            chunk -= 1
            continue
        return chunk


def newline_chunker(text: str, *, width: int = 1500, max_newline: int = 10):
    lines = text.splitlines()
    build = []
    current_pos = 0
    while True:
        lines = lines[current_pos:]
        if not lines:
            break

        n = nearest_nth(lines, chunk=max_newline, width=width)
        if n is None:
            build.append(lines[0][:width])
            lines[0] = lines[0][width:]
            continue

        build.append("\n".join(lines[:n]))
        current_pos = n
    return build


async def ensure_execute(coro, timeout_callback, *, timeout=3):
    """Call timeout_callback when a timeout is reached while also returning the value of coroutine."""
    task = asyncio.create_task(coro)
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
    except asyncio.TimeoutError:
        with contextlib.suppress(Exception):
            await timeout_callback()
        return await task


async def except_retry(callback: Callable[..., Any], *args: Any, multiplier: int =3, retries: int= 3,
                       error: Type[Exception] = Exception, **kwargs: Any):
    last_err = None
    for retry in range(max(retries, 1)):
        try:
            return await callback(*args, **kwargs)
        except error as e:
            last_err = e
            wait = multiplier ** retry
            print("Failure to invoke", callback, "retrying after", wait, "seconds...")
            await asyncio.sleep(wait)

    raise last_err
