from __future__ import annotations
from typing import TYPE_CHECKING, Dict, Optional

import aiohttp
import discord
from discord.ext import commands

from .button_ui import ButtonGame

if TYPE_CHECKING:
    from main import StellaBot


class BaseGameCog(commands.Cog):
    def __init__(self, bot: StellaBot):
        self.bot = bot
        self.lewdle_query = "SELECT word FROM wordle_word WHERE tag='lewdle' AND LENGTH(word) = $1"
        self.button_rank_cache: Dict[int, discord.User] = {}
        self.http_rather: Optional[aiohttp.ClientSession] = None

    async def cog_load(self) -> None:
        self.http_rather = aiohttp.ClientSession()
        if not self.bot.tester:
            self.bot.add_view(ButtonGame())

    async def cog_unload(self) -> None:
        if self.http_rather:
            await self.http_rather.close()
