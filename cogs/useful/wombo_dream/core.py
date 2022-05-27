import asyncio
import datetime
import itertools
import json
import re
import time
from typing import Optional, List, Dict, Union

import aiohttp
import discord

from utils.errors import ErrorNoSignature
from .model import ArtStyle, ImageDescription, PayloadTask, PayloadToken, PayloadAccessToken
from utils.useful import StellaContext, aware_utc, StellaEmbed, except_retry, print_exception
from ..baseclass import BaseUsefulCog


class DreamWombo:
    BASE = "https://app.wombo.art"
    # This is a default secret_key, its not really a secret tbh
    # SERCRET_KEY refers to the google API that the website is permanently using it for, it will be the same
    # for all users, like yourself, go ahead and go to app.wombo.art and you can see the same key being used
    # in their source code. (please stop mentioning this to me)
    SECRET_KEY = "AIzaSyDCvp5MTJLUdtBYEKYWXJrlLzu1zuKM6Xw"
    TOKEN_GENERATOR = 'https://www.googleapis.com/identitytoolkit/v3/relyingparty/signupNewUser'
    TOKEN_REFRESH = 'https://securetoken.googleapis.com/v1/token'

    def __init__(self, http: aiohttp.ClientSession):
        self.http_art = http
        self.token = None
        self.ctx: Optional[StellaContext] = None
        self.art_style: Optional[ArtStyle] = None
        self.image_desc: Optional[ImageDescription] = None
        self.message: Optional[discord.Message] = None
        self.cog: Optional[BaseUsefulCog] = None
        self.__previous = time.time()
        self.__already_downloaded = 0
        self.__failure_gif_download = None
        self.cached_images = {}

    async def get_image(self, i: int, fallback: str = None) -> bytes:
        value = self.cached_images.get(i)
        if isinstance(value, asyncio.Event):
            await value.wait()
            value = self.cached_images.get(i)
        elif value is None and fallback is not None:
            await self.download_images(i, [fallback])
            return await self.get_image(i)
        return value

    async def generate(self, ctx: StellaContext, art_style: ArtStyle, image_desc: ImageDescription,
                       message: discord.Message) -> PayloadTask:
        self.ctx = ctx
        self.cog = ctx.cog
        self.art_style = art_style
        self.image_desc = image_desc
        self.message = message
        return await self._progress()

    async def update_interface(self, payload: PayloadTask, *, bypass_time: bool = False) -> None:
        if not bypass_time and time.time() - self.__previous < 5:
            return

        urls = payload.photo_url_list[self.__already_downloaded:]
        start_id = self.__already_downloaded
        self.__already_downloaded += len(urls)
        if urls:
            self.ctx.bot.loop.create_task(self.download_images(start_id + 1, urls))

        emoji_status = {"pending": "<a:loading:747680523459231834>",
                        "generating": "<a:typing:597589448607399949>",
                        "completed": "<:checkmark:753619798021373974>"}

        description = f"**Prompt:** `{payload.input_spec['prompt']}`\n"\
                      f"**Style:** `{self.art_style.name}`\n" \
                      f"**Updated:** {aware_utc(payload.updated_at)}"

        status = payload.state.casefold()
        embed = StellaEmbed.default(
            self.ctx,
            title=f"Status: {status.capitalize()} {emoji_status.get(status)}",
        )
        to_url_show = None
        if photos := payload.photo_url_list:
            size = len(photos)
            description += f"\n**Image Generation: ** `{size}` (`{size / 20:.0%}`)"
            if status == "completed":
                description += "\n**Downloading Images**" + emoji_status['generating']

            to_url_show = photos[-1] if payload.result is None else payload.result.get('final')
            if to_url_show is None:  # fail safe for result final dict
                to_url_show = photos[-1]

        if to_url_show is not None:
            url = await self.ctx.cog.get_local_url(to_url_show)
            embed.set_image(url=url)

        embed.description = description
        await self.message.edit(embed=embed, view=None)

    async def download_image(self, url: str) -> bytes:
        return await except_retry(self._download_image, url)

    async def _download_image(self, url: str) -> bytes:
        async with self.http_art.get(url) as response:
            return await response.read()

    async def download_images(self, start_id: int, urls: List[str]) -> None:
        try:
            await self._download_images(start_id, urls)
        except Exception as e:
            print_exception("Ignoring error while downloading images:", e)
            self.__failure_gif_download = True
        else:
            self.__failure_gif_download = False

    async def _download_images(self, start_id: int, urls: List[str]) -> None:
        tasks = []
        for i, url in enumerate(urls, start=start_id):
            waiter = asyncio.Event()
            self.cached_images[i] = waiter
            tasks.append(asyncio.create_task(self.download_image(url)))
            await asyncio.sleep(0.1)

        await asyncio.wait(tasks)
        for i, task in enumerate(tasks, start=start_id):
            result = None
            try:
                result = task.result()
            except Exception as e:
                error = print_exception(f"Failure to download {start_id} image.", e)
                await self.ctx.bot.error_channel.send(embed=StellaEmbed.to_error(description=error))
            finally:
                self.cached_images[i].set()

            self.cached_images[i] = result

    async def _progress(self) -> PayloadTask:
        self.token = await self.get_authentication()

        task = await self.request_task()
        payload = await self.start_task(task)
        prev_status = None
        for sequence in itertools.count(1):
            payload = await self.update_task(payload)
            if payload.state.lower() == prev_status and prev_status == 'pending':
                await asyncio.sleep(1)
                continue

            prev_status = payload.state.lower()

            if payload.state.lower() not in ('generating', 'pending'):
                break
            try:
                await self.update_interface(payload)
            except Exception as e:
                print_exception("Ignoring error during interface update:", e)

            await asyncio.sleep(1)
            if sequence > 180:
                raise ErrorNoSignature("Exceeded the time limit of waiting.")

        await self.update_interface(payload, bypass_time=True)
        return payload

    async def get_cached_authentication(self) -> PayloadToken:
        if self.cog.cache_authentication is None:
            self.cog.cache_authentication = await self.generate_token()
        return self.cog.cache_authentication

    async def get_cached_access_auth(self) -> PayloadAccessToken:
        if self.cog.cache_authentication_access is None:
            new_token = await self.get_cached_authentication()
            self.cog.cache_authentication_access = await self.request_access_token(new_token.refresh_token)

        return self.cog.cache_authentication_access

    async def get_authentication(self) -> str:
        access_token = await self.get_cached_access_auth()
        if access_token.expires_in < datetime.datetime.utcnow():
            refresh = access_token.refresh_token
            self.cog.cache_authentication_access = access_token = await self.request_access_token(refresh)

        return f"bearer {access_token.access_token}"

    async def generate_token(self) -> PayloadToken:
        async with self.http_art.post(self.TOKEN_GENERATOR, params={'key': self.SECRET_KEY}) as response:
            payload = await response.json()
            return PayloadToken.from_json(payload)

    async def request_access_token(self, refresh_token: str) -> PayloadAccessToken:
        payload = {
            'grant_type': 'refresh_token',
            'refresh_token': refresh_token
        }
        async with self.http_art.post(self.TOKEN_REFRESH, params={'key': self.SECRET_KEY}, json=payload) as response:
            payload = await response.json()
            return PayloadAccessToken.from_json(payload)

    async def update_task(self, task: PayloadTask) -> PayloadTask:
        response = await self.request("GET", "/api/tasks/" + task.id)
        return PayloadTask.from_response(response)

    async def start_task(self, task: PayloadTask) -> PayloadTask:
        payload = {
            'input_spec': {
                'display_freq': 10,
                'prompt': self.image_desc.name,
                'style': self.art_style.id
            }
        }
        response = await self.request("PUT", "/api/tasks/" + task.id, json=payload)
        return PayloadTask.from_response(response)

    async def request_task(self) -> PayloadTask:
        response = await self.request("POST", "/api/tasks", json={"premium": False})
        return PayloadTask.from_response(response)

    async def request(self, method, url: str, **kwargs) -> Dict[str, Union[int, str]]:
        data = kwargs
        if 'json' in kwargs:
            data['data'] = discord.utils._to_json(kwargs.pop('json'))

        data['headers'] = {"Authorization": self.token}

        async with self.http_art.request(method, self.BASE + url, **data) as response:
            responded = await response.json()
            if msg := responded.get('msg') or responded.get('detail'):
                value = f"Failure to reach API on endpoint {method} {url} with data {data}:\n{responded}"
                await self.ctx.bot.error_channel.send(embed=StellaEmbed.to_error(description=value))
                raise ErrorNoSignature("Something went wrong, Please try again later.\nFailure to reach API: " + msg)
            return responded

    async def get_art_styles(self) -> List[ArtStyle]:
        regex = r'\{\"props\"\:.*\}'
        async with self.http_art.get(self.BASE) as result:
            value = await result.text()
            raw_data = re.search(regex, value)
            if raw_data is None:
                raise ErrorNoSignature("Something went wrong. Please try again later. API is not available.")

            data = json.loads(raw_data.group(0))
        try:
            json_data = data['props']['pageProps']['artStyles']
        except KeyError:
            raise ErrorNoSignature("Something went wrong. Please try again later. Failure to extract art styles")
        else:
            return [ArtStyle.from_json(dat) for dat in json_data]
