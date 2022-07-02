from __future__ import annotations
import asyncio
import base64
import enum
import io
import itertools
import os
import time
from typing import List, Dict

import aiohttp
import discord
from discord.ext import menus
from typing_extensions import Self

from utils.buttons import InteractionPages, button
from utils.ipc import StellaAPI, StellaFile
from utils.useful import StellaEmbed, StellaContext


class PartialDallEImage:
    def __init__(self, image_decoded):
        self.image_decoded = image_decoded
        self.name = os.urandom(10).hex() + ".png"

    @classmethod
    def from_base64(cls, image: str):
        return cls(base64.b64decode(image))

    def to_file(self):
        return discord.File(io.BytesIO(self.image_decoded), filename="file.png")

    async def fetch(self, api: StellaAPI) -> DallEImage:
        return await DallEImage.create(api, self)


class DallEImage(PartialDallEImage):
    def __init__(self, api: StellaAPI, partial: PartialDallEImage) -> None:
        super().__init__(partial.image_decoded)
        self.api = api
        self.name = partial.name
        self._file = None

    @classmethod
    async def create(cls, api: StellaAPI, partial: PartialDallEImage) -> Self:
        self = cls(api, partial)
        file = await self.create_url()
        self._file = file
        return self

    @property
    def url(self) -> str:
        return self._file.url

    async def create_url(self) -> StellaFile:
        return await self.api.upload_file(file=self.image_decoded, filename=self.name)


class DallE:
    BASE = "https://bf.dallemini.ai/"

    class Status(enum.Enum):
        GENERATING = "GENERATING"
        PROCESSING = "PROCESSING"
        FINISHED = "FINISHED"
        ERROR = "ERROR"

    def __init__(self, http: aiohttp.ClientSession) -> None:
        self.http = http
        self.handler = None

    def set_handler(self, handler: DallEHandler):
        self.handler = handler

    async def update(self, status: Status, *args, **kwargs):
        if self.handler is None:
            return

        if not hasattr(status, "value"):
            raise Exception(f"Unknown status: {status} value was passed.")

        method_name = "on_" + status.value.lower()
        if method := getattr(self.handler, method_name, None):
            await method(*args, **kwargs)

    async def _generate(self, prompt: str) -> Dict[str, List[str]]:
        payload = {
            "prompt": prompt
        }
        await self.update(self.Status.GENERATING)
        async with self.http.post(self.BASE + "generate", json=payload) as response:
            return await response.json()

    async def generate(self, prompt: str) -> List[PartialDallEImage]:
        try:
            raw_json = await self._generate(prompt)
            await self.update(self.Status.PROCESSING)
            if not isinstance(raw_json, dict) or "images" not in raw_json:
                raise Exception("Failure to fetch images from dall-e mini.")

            partials = [PartialDallEImage.from_base64(image) for image in raw_json["images"]]
        except Exception as e:
            await self.update(self.Status.ERROR, e)
        else:
            await self.update(self.Status.FINISHED, partials)
            return partials


class DallEHandler:
    def __init__(self, ctx: StellaContext):
        self.dalle = DallE(ctx.cog.http_dall)
        self.api = ctx.bot.stella_api
        self.dalle.set_handler(self)
        self.prompt = None
        self.ctx = ctx
        self.message = None
        self._generate_task = None
        self._cached = {}
        self._wait = asyncio.Event()

    async def start(self, prompt: str):
        self.prompt = prompt
        await self.dalle.generate(prompt)
        await self.wait()

    async def loading_generation(self):
        start = time.perf_counter()

        def get_embed() -> StellaEmbed:
            nonlocal self
            sec = time.perf_counter() - start
            desc = f"[`{sec:.2f}s`] Please wait as dall-e is generating images..."
            desc = f"**Prompt:** `{self.prompt}`\n{desc}"
            return StellaEmbed.default(
                self.ctx,
                title="<a:loading:747680523459231834> Generating",
                description=desc
            )

        self.message = await self.ctx.maybe_reply(embed=get_embed())
        for x in itertools.count(1):
            if x >= 42:
                await self.on_error(asyncio.TimeoutError("Timeout after 210 seconds."))
            await asyncio.sleep(5)
            await self.message.edit(embed=get_embed())

    async def on_generating(self):
        self._generate_task = asyncio.create_task(self.loading_generation())

    async def on_processing(self):
        self._generate_task.cancel()
        self._generate_task = None
        await self.message.edit(embed=StellaEmbed.default(
            self.ctx,
            title="<a:typing:597589448607399949> Processing",
            description="Please wait we're processing the results..."
        ))

    async def retrieve_full(self, partial: PartialDallEImage):
        if not (full := self._cached.get(partial.name)):
            full = await partial.fetch(self.api)
            self._cached[partial.name] = full
        return full

    def cleanup(self):
        if self._generate_task is not None:
            self._generate_task.cancel()

    async def on_finished(self, images: List[PartialDallEImage]):
        pages = InteractionImages(self, images, message=self.message)
        await pages.start(self.ctx)
        self._wait.set()

    async def on_error(self, error: Exception):
        self.cleanup()
        await self.message.edit(embed=StellaEmbed.to_error(
            title="<:crossmark:753620331851284480> Error occured",
            description=str(error))
        )
        self._wait.set()

    async def wait(self) -> None:
        return await self._wait.wait()


class OneImage(menus.ListPageSource):
    def __init__(self, handler: DallEHandler, images: List[PartialDallEImage]):
        super().__init__(images, per_page=1)
        self.handler = handler

    async def format_page(self, menu, image):
        fullimage = await self.handler.retrieve_full(image)
        return StellaEmbed.default(
            menu.ctx, title=f"Prompt: {self.handler.prompt}"
        ).set_image(url=fullimage.url)


class FourImage(menus.ListPageSource):
    def __init__(self, handler: DallEHandler, images: List[PartialDallEImage]):
        super().__init__(images, per_page=4)
        self.handler = handler

    async def format_page(self, menu, images):
        fullimages = await asyncio.gather(*map(self.handler.retrieve_full, images))
        embeds = [StellaEmbed.default(
            menu.ctx, title=f"Prompt: {self.handler.prompt}", url=fullimages[0].url
        ).set_image(url=fullimage.url) for fullimage in fullimages]
        return {"embeds": embeds}


class InteractionImages(InteractionPages):
    def __init__(self, handler: DallEHandler, images: List[PartialDallEImage], message: discord.Message):
        self.one_source = OneImage(handler, images)
        self.four_source = FourImage(handler, images)
        super().__init__(self.four_source, message=message, delete_after=False)
        self.mode = 4

    def toggle_mode(self):
        self.mode = 1 if self.mode == 4 else 4

    async def setup_toggle(self):
        self.toggle_mode()
        source = self.one_source if self.mode == 1 else self.four_source
        await self.change_source(source)

    @button(emoji="1\U000020e3", label="Image Mode", stay_active=True)
    async def on_change_mode(self, interaction, button):
        button.emoji = "1\U000020e3" if self.mode == 1 else "🔢"
        await interaction.response.defer()
        await self.setup_toggle()

