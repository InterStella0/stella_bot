from __future__ import annotations
from typing import TYPE_CHECKING, Dict, Optional

import aiohttp
from discord.ext import commands

from utils.prefix_ai import MobileNetNSFW

if TYPE_CHECKING:
    from main import StellaBot
    from .art_ai_generation import PayloadToken, PayloadAccessToken


class BaseUsefulCog(commands.Cog):
    def __init__(self, bot: StellaBot):
        self.bot = bot
        self.cache_authentication: Optional[PayloadToken] = None
        self.cache_authentication_access: Optional[PayloadAccessToken] = None
        self._cached_image: Dict[str, str] = {}
        self.http_art: Optional[aiohttp.ClientSession] = None
        self.http_rather: Optional[aiohttp.ClientSession] = None
        self.cached_models: Dict[str, MobileNetNSFW] = {}

    async def cog_load(self) -> None:
        self.http_art = aiohttp.ClientSession()
        self.http_rather = aiohttp.ClientSession()

    async def cog_unload(self) -> None:
        if self.http_art:
            await self.http_art.close()

        if self.http_rather:
            await self.http_rather.close()
