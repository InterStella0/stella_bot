from __future__ import annotations
from typing import TYPE_CHECKING, Dict, Optional

import aiohttp
from discord.ext import commands

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

    async def cog_load(self) -> None:
        self.http_art = aiohttp.ClientSession()

    async def cog_unload(self) -> None:
        if self.http_art:
            await self.http_art.close()
