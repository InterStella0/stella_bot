from __future__ import annotations
import discord
import functools
import asyncio
from typing import Callable, Optional, Any, Union, Coroutine, Type, Iterable, TYPE_CHECKING
from utils.menus import MenuBase
from discord.ext import commands, menus
from utils.errors import NotInDpy

if TYPE_CHECKING:
    from main import StellaBot


def is_discordpy(silent: Optional[bool] = False) -> Callable:
    """A check that only allows certain command to be only be invoked in discord.py server. Otherwise it is ignored."""
    async def predicate(ctx: commands.Context) -> bool:
        if ctx.guild and ctx.guild.id == 336642139381301249:
            return True
        else:
            if not silent:
                raise NotInDpy()
    return commands.check(predicate)


def event_check(func: Callable[[Any], Union[Coroutine[Any, Any, bool], bool]]) -> Callable[[Callable], Callable]:
    """Event decorator check."""
    def check(method: Callable[..., Coroutine[Any, Any, None]]) -> Callable[..., Coroutine[Any, Any, None]]:
        method.callback = method

        @functools.wraps(method)
        async def wrapper(*args: Any, **kwargs: Any) -> None:
            if await discord.utils.maybe_coroutine(func, *args, **kwargs):
                await method(*args, **kwargs)
        return wrapper
    return check


def wait_ready(bot: Optional[Union[StellaBot, commands.Bot]] = None) -> Callable:
    async def predicate(*args: Any, **_: Any) -> bool:
        nonlocal bot
        self = args[0] if args else None
        if hasattr(self, "bot") and isinstance(self, commands.Cog):
            bot = bot or self.bot
        if not isinstance(bot, commands.Bot):
            name = bot.__class__.__name__ if bot is not None else "None"
            raise Exception(f"bot must derived from commands.Bot not {name}")
        await bot.wait_until_ready()
        return True
    return event_check(predicate)


def pages(per_page: Optional[int] = 1, show_page: Optional[bool] = True) -> Callable:
    """Compact ListPageSource that was originally made teru but was modified"""
    def page_source(coro: Callable[[MenuBase, Any], Coroutine[Any, Any, discord.Embed]]) -> Type[menus.ListPageSource]:
        async def create_page_header(self, menu: MenuBase, entry: Any) -> Union[discord.Embed, str]:
            result = await discord.utils.maybe_coroutine(coro, self, menu, entry)
            return menu.generate_page(result, self._max_pages)

        def __init__(self, list_pages: Iterable):
            super(self.__class__, self).__init__(list_pages, per_page=per_page)
        kwargs = {
            '__init__': __init__,
            'format_page': (coro, create_page_header)[show_page]
        }
        return type(coro.__name__, (menus.ListPageSource,), kwargs)
    return page_source


def listen_for_guilds() -> Callable:
    def predicate(*args: Any):
        """Only allow message event to be called in guilds"""
        message = args[len(args) != 1]
        return message.guild is not None
    return event_check(predicate)


def in_executor(loop: Optional[asyncio.AbstractEventLoop] = None) -> Callable[..., Coroutine[Any, Any, Any]]:
    """Makes a sync blocking function unblocking"""
    loop = loop or asyncio.get_event_loop()

    def inner_function(func: Callable) -> Callable:
        @functools.wraps(func)
        def function(*args: Any, **kwargs: Any) -> Coroutine:
            partial = functools.partial(func, *args, **kwargs)
            return loop.run_in_executor(None, partial)
        return function
    return inner_function
