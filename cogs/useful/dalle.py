from __future__ import annotations
import asyncio
import base64
import enum
import functools
import io
import itertools
import os
import time
from dataclasses import dataclass
from typing import List, Dict

import aiohttp
import discord
from typing_extensions import Self

from utils.buttons import InteractionPages
from utils.decorators import pages
from utils.ipc import StellaAPI, StellaFile
from utils.useful import StellaEmbed, StellaContext


@dataclass
class PartialDallEImage:
    image_decoded: bytes

    @classmethod
    def from_base64(cls, image: str):
        return cls(base64.b64decode(image))

    def to_file(self):
        return discord.File(io.BytesIO(self.image_decoded), filename="file.png")

    async def fetch(self, api: StellaAPI) -> DallEImage:
        return await DallEImage.create(api, self)


class DallEImage(PartialDallEImage):
    def __init__(self, api: StellaAPI, partial: PartialDallEImage) -> None:
        self.api = api
        self.image_decoded = partial.image_decoded
        self._file = None
        self.name = os.urandom(10).hex() + ".png"

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

    async def generate(self, prompt: str):
        self.prompt = prompt
        await self.dalle.generate(prompt)

    async def loading_generation(self):
        start = time.perf_counter()

        def get_embed() -> StellaEmbed:
            sec = time.perf_counter() - start
            return StellaEmbed.default(
                self.ctx,
                title="<a:loading:747680523459231834> Generating",
                description=f"[{sec:.0f}s] Please wait as dall-e is generating images..."
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
        if not (full := self._cached.get(partial.image_decoded)):
            full = await partial.fetch(self.api)
            self._cached[partial.image_decoded] = full
        return full

    def cleanup(self):
        if self._generate_task is not None:
            self._generate_task.cancel()

    async def on_finished(self, images: List[PartialDallEImage]):
        @pages(per_page=1)
        async def show_page(inner_self, menu, image: PartialDallEImage):
            fullimage = await image.fetch(self.api)
            image_name = f"Image {menu.current_page + 1}"
            return StellaEmbed.default(
                self.ctx, title=f"Prompt: {self.prompt}", description=image_name
            ).set_image(url=fullimage.url)

        await InteractionPages(show_page(images), message=self.message).start(self.ctx)

    async def on_error(self, error: Exception):
        self.cleanup()
        await self.message.edit(embed=StellaEmbed.to_error(title="<:crossmark:753620331851284480> Error occured",
                                                           description=str(error)))

