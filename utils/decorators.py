from __future__ import annotations

import asyncio
import functools

from typing import Any, Awaitable, Callable, Coroutine, Optional, Sequence, Type, TypeVar, Union

import discord

from discord.ext import commands, menus
from typing_extensions import ParamSpec

from utils.errors import NotInDpy
from utils.menus import MenuBase

DISCORD_PY = 336642139381301249

T = TypeVar("T")
P = ParamSpec("P")

Coro = Coroutine[Any, Any, T]
MaybeCoro = Union[T, Coroutine[Any, Any, T]]


def is_discordpy(silent: bool = False) -> Callable[[T], T]:
    """A check that only allows certain command to be only be invoked in discord.py server. Otherwise it is ignored."""
    async def predicate(ctx: commands.Context) -> bool:
        if ctx.guild and ctx.guild.id == DISCORD_PY:
            return True
        if silent:
            return False
        raise NotInDpy()  # type: ignore[no-untyped-call]

    return commands.check(predicate)


_Event = Callable[..., Coro[None]]
_WrappedEvent = Callable[[_Event], _Event]


def event_check(event_predicate: Callable[P, MaybeCoro[bool]]) -> _WrappedEvent:
    """Event decorator check."""
    def event_wrapper(event: _Event) -> _Event:
        setattr(event, "callback", event)

        @functools.wraps(event)
        async def inner(*args: P.args, **kwargs: P.kwargs) -> None:
            if await discord.utils.maybe_coroutine(event_predicate, *args, **kwargs):  # type: ignore[no-untyped-call]
                await event(*args, **kwargs)
        return inner

    setattr(event_wrapper, "predicate", event_predicate)

    return event_wrapper


def wait_ready(bot: Optional[commands.Bot] = None) -> _WrappedEvent:
    async def predicate(*args: Any, **_: Any) -> bool:
        nonlocal bot
        self = args[0] if args else None
        if hasattr(self, "bot") and isinstance(self, commands.Cog):
            bot = bot or self.bot
        if not isinstance(bot, commands.Bot):
            raise Exception(f"bot must derived from commands.Bot not {bot.__class__.__name__}")
        await bot.wait_until_ready()
        return True
    return event_check(predicate)


def listen_for_guilds() -> _WrappedEvent:
    def predicate(self_or_message: Any, *args: Any) -> bool:
        """Only allow message event to be called in guilds"""
        message = args[0] if args else self_or_message
        return message.guild is not None
    return event_check(predicate)


_FormatPageSignature = Callable[[menus.ListPageSource, MenuBase, Any], MaybeCoro[discord.Embed]]


def pages(per_page: int = 1, show_page: bool = True) -> Callable[[_FormatPageSignature], Type[menus.ListPageSource]]:
    """Compact ListPageSource that was originally made teru but was modified"""
    def page_source(coro: _FormatPageSignature) -> Type[menus.ListPageSource]:
        async def create_page_header(self: menus.ListPageSource, menu: MenuBase,
                                     entry: Any) -> Union[discord.Embed, str]:
            result = await discord.utils.maybe_coroutine(coro, self, menu, entry)  # type: ignore[no-untyped-call]
            return menu.generate_page(result, self._max_pages)

        def __init__(self: menus.ListPageSource, list_pages: Sequence[Any]) -> None:
            super(self.__class__, self).__init__(list_pages, per_page=per_page)
        kwargs = {
            '__init__': __init__,
            'format_page': (coro, create_page_header)[show_page]
        }
        return type(coro.__name__, (menus.ListPageSource,), kwargs)
    return page_source


_MaybeEventLoop = Optional[asyncio.AbstractEventLoop]


def in_executor(loop: _MaybeEventLoop = None) -> Callable[[Callable[P, T]], Callable[P, Awaitable[T]]]:
    """Makes a sync blocking function unblocking"""
    loop_ = loop or asyncio.get_event_loop()

    def inner_function(func: Callable[P, T]) -> Callable[P, Awaitable[T]]:
        @functools.wraps(func)
        def function(*args: P.args, **kwargs: P.kwargs) -> Awaitable[T]:
            partial = functools.partial(func, *args, **kwargs)
            return loop_.run_in_executor(None, partial)
        return function
    return inner_function
