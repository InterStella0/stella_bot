from __future__ import annotations
from typing import TYPE_CHECKING, Any, List, Optional, Iterable

import discord
from discord import app_commands
from discord.abc import MISSING
from discord.ext import commands
from typing_extensions import Self

if TYPE_CHECKING:
    from main import StellaBot


def server_request():
    def inner(coro):
        coro.__server_request__ = True
        return coro
    return inner


def server_listen():
    def inner(coro):
        coro.__server_listen__ = True
        return coro
    return inner


def context_menu(*, name: str = MISSING, nsfw: bool = False, guilds: List[discord.abc.Snowflake] = MISSING):
    def inner(coro):
        nonlocal name
        coro.__context_menu_guilds__ = guilds
        if name is MISSING:
            name = coro.__name__

        coro.__context_menu__ = dict(name=name, nsfw=nsfw)
        return coro
    return inner


class StellaCog(commands.Cog):
    async def _inject(self, bot: StellaBot, override: bool, guild: Optional[discord.abc.Snowflake],
                      guilds: List[discord.abc.Snowflake]) -> Self:
        await super()._inject(bot, override, guild, guilds)
        for method_name in dir(self):
            method = getattr(self, method_name)
            if context_values := getattr(method, "__context_menu__", None):
                menu = app_commands.ContextMenu(callback=method, **context_values)
                context_values["context_menu_class"] = menu
                bot.tree.add_command(menu, guilds=method.__context_menu_guilds__)
            elif hasattr(method, "__server_request__"):
                bot.ipc_client.add_server_request_handler(method)
            elif hasattr(method, "__server_listen__"):
                bot.ipc_client.add_server_listener(method)

        return self

    async def _eject(self, bot: StellaBot, guild_ids: Optional[Iterable[int]]) -> None:
        await super()._eject(bot, guild_ids)
        for method_name in dir(self):
            method = getattr(self, method_name)
            if context_values := getattr(method, "__context_menu__", None):
                if menu := context_values.get("context_menu_class"):
                    bot.tree.remove_command(menu.name, type=menu.type)
            elif hasattr(method, "__server_request__"):
                bot.ipc_client.remove_server_request_handler(method.__name__)
            elif hasattr(method, "__server_listen__"):
                bot.ipc_client.remove_server_listener(method)
