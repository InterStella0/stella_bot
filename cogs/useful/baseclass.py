from __future__ import annotations
from typing import TYPE_CHECKING, Dict, Optional

import aiohttp

from utils.cog import StellaCog
from utils.prefix_ai import MobileNetNSFW

if TYPE_CHECKING:
    from main import StellaBot
    from .art_ai_generation import PayloadToken, PayloadAccessToken


class BaseUsefulCog(StellaCog):
    def __init__(self, bot: StellaBot):
        self.bot = bot
        self.cache_authentication: Optional[PayloadToken] = None
        self.cache_authentication_access: Optional[PayloadAccessToken] = None
        self._cached_image: Dict[str, str] = {}
        self.http_art: Optional[aiohttp.ClientSession] = None
        self.cached_models: Dict[str, MobileNetNSFW] = {}

    async def cog_load(self) -> None:
        await super().cog_load()
        self.http_art = aiohttp.ClientSession()

    async def cog_unload(self) -> None:
        await super().cog_unload()
        if self.http_art:
            await self.http_art.close()
