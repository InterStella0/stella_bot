from __future__ import annotations

import asyncio
import base64
import contextlib
import datetime
import itertools
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Optional, List, Any, Dict, Union

import aiohttp
import dateutil
import discord
from discord.ext import commands

from utils.buttons import ViewAuthor, InteractionPages, button
from utils.decorators import pages
from utils.errors import ErrorNoSignature
from utils.useful import StellaContext, StellaEmbed, print_exception, aware_utc
from .baseclass import BaseUsefulCog


@dataclass
class PayloadTask:
    id: str
    created_at: datetime.datetime
    generated_photo_keys: List[str]
    input_spec: Dict[str, str]
    photo_url_list: List[str]
    premium: False
    result: Dict[str, str]
    state: str
    updated_at: datetime.datetime
    user_id: str

    @staticmethod
    def convert_if_value(date_str: Optional[str]) -> datetime.datetime:
        if date_str is None:
            return

        return dateutil.parser.parse(date_str)

    @classmethod
    def from_response(cls, payload: Dict[str, Any]):
        return cls(
            payload['id'],
            cls.convert_if_value(payload['created_at']),
            payload['generated_photo_keys'],
            payload['input_spec'],
            payload['photo_url_list'],
            payload['premium'],
            payload['result'],
            payload['state'],
            cls.convert_if_value(payload['updated_at']),
            payload['user_id']
        )


def convert_expiry_date(seconds: str):
    return datetime.datetime.utcnow() + datetime.timedelta(seconds=int(seconds))


@dataclass
class PayloadToken:
    kind: str
    id_token: str
    refresh_token: str
    expires_in: datetime.datetime
    local_id: str


@dataclass
class PayloadAccessToken:
    access_token: str
    expires_in: int
    id_token: str
    project_id: int
    refresh_token: str
    token_type: str
    user_id: str


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
        self.image_desc: Optional[str] = None
        self.message: Optional[discord.Message] = None
        self.cog: Optional[BaseUsefulCog] = None
        self.__previous = time.time()

    async def generate(self, ctx: StellaContext, art_style: ArtStyle, image_desc: str,
                       message: discord.Message) -> PayloadTask:
        self.ctx = ctx
        self.cog = ctx.cog
        self.art_style = art_style
        self.image_desc = image_desc
        self.message = message
        return await self.progress()

    async def update_interface(self, payload: PayloadTask, *, bypass_time: bool = False):
        if not bypass_time and time.time() - self.__previous < 5:
            return

        emoji_status = {"pending": "<a:loading:747680523459231834>",
                        "generating": "<a:typing:597589448607399949>",
                        "completed": "<:checkmark:753619798021373974>"}

        description = f"**Prompt:** `{payload.input_spec['prompt']}`\n"\
                      f"**Style:** `{self.art_style.name}`\n" \
                      f"**Updated:** {aware_utc(payload.updated_at)}"

        status = payload.state.casefold()
        embed = StellaEmbed.default(
            self.ctx,
            title=f"Status: {emoji_status.get(status)} {status.capitalize()}",
        )
        to_url_show = None
        if photos := payload.photo_url_list:
            size = len(photos)
            description += f"\n**Image Generation: ** `{size}` (`{size / 20:.0%}`)"
            to_url_show = photos[-1] if payload.result is None else payload.result.get('final')
            if to_url_show is None:  # fail safe for result final dict
                to_url_show = photos[-1]

        if to_url_show is not None:
            url = await self.ctx.cog.get_local_url(self.http_art, to_url_show)
            embed.set_image(url=url)

        embed.description = description
        await self.message.edit(embed=embed, view=None)

    async def progress(self) -> PayloadTask:
        await self.get_authentication()

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

    async def get_cached_authentication(self):
        if self.cog.cache_authentication is None:
            self.cog.cache_authentication = await self.generate_token()
        return self.cog.cache_authentication

    async def get_cached_access_auth(self):
        if self.cog.cache_authentication_access is None:
            new_token = await self.get_cached_authentication()
            self.cog.cache_authentication_access = await self.request_access_token(new_token.refresh_token)

        return self.cog.cache_authentication_access

    async def get_authentication(self):
        access_token = await self.get_cached_access_auth()
        if access_token.expires_in < datetime.datetime.utcnow():
            refresh = access_token.refresh_token
            self.cog.cache_authentication_access = access_token = await self.request_access_token(refresh)

        self.token = f"bearer {access_token.access_token}"

    async def generate_token(self):
        async with self.http_art.post(self.TOKEN_GENERATOR, params={'key': self.SECRET_KEY}) as response:
            payload = await response.json()
            expire = convert_expiry_date(payload['expiresIn'])
            return PayloadToken(
                payload['kind'], payload['idToken'], payload['refreshToken'], expire, payload['localId']
            )

    async def request_access_token(self, refresh_token):
        payload = {
            'grant_type': 'refresh_token',
            'refresh_token': refresh_token
        }
        async with self.http_art.post(self.TOKEN_REFRESH, params={'key': self.SECRET_KEY}, json=payload) as response:
            payload = await response.json()
            expire = convert_expiry_date(payload['expires_in'])
            return PayloadAccessToken(
                payload['access_token'], expire, payload['id_token'], payload['project_id'],
                payload['refresh_token'], payload['token_type'], payload['user_id']
            )

    async def update_task(self, task: PayloadTask) -> PayloadTask:
        response = await self.request("GET", "/api/tasks/" + task.id)
        return PayloadTask.from_response(response)

    async def start_task(self, task: PayloadTask) -> PayloadTask:
        payload = {
            'input_spec': {
                'display_freq': 10,
                'prompt': self.image_desc,
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
                raise ErrorNoSignature("Failure to reach API: " + msg)
            return responded

    async def stop(self) -> None:
        await self.http_art.close()

    async def get_art_styles(self):
        regex = r'\{\"props\"\:.*\}'
        async with self.http_art.get(self.BASE) as result:
            value = await result.text()
            raw_data = re.search(regex, value)
            if raw_data is None:
                raise ErrorNoSignature("API is not available.")

            data = json.loads(raw_data.group(0))
        try:
            json_data = data['props']['pageProps']['artStyles']
        except KeyError:
            raise ErrorNoSignature("Failure to extract art styles")
        else:
            return [ArtStyle.from_json(dat) for dat in json_data]


@dataclass
class ArtStyle:
    id: int
    name: str
    created_at: datetime.datetime
    updated_at: datetime.datetime
    deleted_at: Optional[datetime.datetime]
    photo_url: str
    blur_data_url: str

    @classmethod
    def from_json(cls, raw_data):
        return cls(
            raw_data['id'], raw_data['name'], raw_data['created_at'], raw_data['updated_at'], raw_data['deleted_at'],
            raw_data['photo_url'], raw_data['blurDataURL']
        )


class ChooseArtStyle(ViewAuthor):
    SELECT_PLACEHOLDER = "Choose an art style"

    def __init__(self, arts: List[ArtStyle], ctx: StellaContext):
        super().__init__(ctx)
        options = [discord.SelectOption(label=a.name, value=a.id) for a in arts]
        self.art_styles: Dict[int, ArtStyle] = {a.id: a for a in arts}
        discord.utils.get(self.children, placeholder=self.SELECT_PLACEHOLDER).options = options
        self.message: Optional[discord.Message] = None
        self.selected: Optional[ArtStyle] = None
        self._is_cancelled = None

    async def start(self, image_desc: str):
        self.message = await self.context.send(f"Choose an art style for `{image_desc}`!", view=self)
        await self.wait()
        with contextlib.suppress(discord.NotFound):
            if self._is_cancelled:
                await self.message.edit(content="Cancelled", view=None, embed=None)
                raise commands.CommandError("Cancelled")

            if self._is_cancelled is False:
                await self.message.edit(content="Art selecting timeout", view=None, embed=None)
                raise ErrorNoSignature("Timeout")

        return self.selected

    @discord.ui.select(placeholder=SELECT_PLACEHOLDER)
    async def on_selected_art(self, interaction: discord.Interaction, select: discord.ui.Select):
        value = select.values[0]
        art = self.art_styles.get(int(value))
        embed = StellaEmbed.default(self.context, title=f"Art Style Selected: {art.name}")
        embed.description = "**Press Confirm to start generating!**"
        embed.set_image(url=art.photo_url)
        self.selected = art
        confirm = discord.utils.get([n for n in self.children if isinstance(n, discord.ui.Button)], label="Confirm")
        confirm.disabled = False
        await interaction.response.edit_message(content=None, embed=embed, view=self)

    @discord.ui.button(label="Confirm", row=1, style=discord.ButtonStyle.success, disabled=True)
    async def on_confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def on_cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self._is_cancelled = True
        self.stop()

    async def on_timeout(self) -> None:
        self._is_cancelled = False

    async def disable_all(self):
        if self.context.bot.get_message(self.message.id) is None:
            return

        with contextlib.suppress(Exception):
            if not self._is_cancelled:
                await self.message.edit(content="Image generation has started...", embed=None, view=None)

    def stop(self) -> None:
        super().stop()
        self.context.bot.loop.create_task(self.disable_all())


class WomboGeneration(InteractionPages):
    MENU = "Final Image"

    def __init__(self, source, view: WomboResult):
        super().__init__(source, message=view.message, delete_after=False)
        self.view = view

    @button(emoji='<:stop_check:754948796365930517>', style=discord.ButtonStyle.blurple)
    async def stop_page(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if self.delete_after:
            await self.message.delete(delay=0)
            return

        for x in self.children:
            if not isinstance(x, discord.ui.Button) or x.label != self.MENU:
                x.disabled = True

        await interaction.response.edit_message(view=self)

    @button(emoji="<:house_mark:848227746378809354>", label=MENU, row=1, stay_active=True, style=discord.ButtonStyle.success)
    async def on_menu_click(self, interaction: discord.Interaction, _: discord.ui.Button):
        embed = self.view.home_embed()
        await interaction.response.edit_message(content=None, embed=embed, view=self.view)
        self.stop()


class WomboResult(ViewAuthor):
    def __init__(self, wombo: DreamWombo):
        super().__init__(wombo.ctx)
        self.result = None
        self.image_description = wombo.image_desc
        self.message = wombo.message
        self.http = wombo.http_art
        self._original_photo = None

    def home_embed(self):
        value = self.result
        return StellaEmbed.default(
            self.context, title=self.image_description
        ).set_image(
            url=self._original_photo
        ).add_field(
            name="Image Generated", value=len(value.photo_url_list)
        ).add_field(
            name="Created", value=aware_utc(value.created_at)
        )

    async def display(self, result: PayloadTask):
        self.result = result
        self._original_photo = await self.context.cog.get_local_url(self.http, result.result['final'])
        await self.message.edit(embed=self.home_embed(), view=self, content=None)
        await self.wait()

    @button(emoji='<:statis_mark:848262218554408988>', label="Show Image Generation", style=discord.ButtonStyle.blurple)
    async def show_images(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()

        @pages()
        async def show_image(self, menu, item):
            generation = menu.current_page + 1
            url = await menu.ctx.cog.get_local_url(menu.view.http, item)
            return StellaEmbed.default(menu.ctx, title=f"Image Generation {generation}").set_image(url=url)

        pager = WomboGeneration(show_image(self.result.photo_url_list), self)
        await pager.start(self.context)

    @button(emoji='🗑️', label="Delete", style=discord.ButtonStyle.danger)
    async def delete_image(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.defer()
        await self.message.delete()


def image_desc(image_desc: str) -> str:
    if len(image_desc) < 3:
        raise commands.BadArgument("Image description must be more than 3 characters.")
    if len(image_desc) > 100:
        raise commands.BadArgument("Image description must be less than or equal to 100 characters.")

    return image_desc


class ArtAI(BaseUsefulCog):
    @commands.command(help="Generate art work with description given using Dream Wombo AI.")
    @commands.cooldown(1, 60, commands.BucketType.user)
    async def art(self, ctx: StellaContext, *, image_description: image_desc):
        # I'm gonna be honest, I can't find their API so im just gonna reverse engineer it.
        async with aiohttp.ClientSession() as http_art:
            wombo = DreamWombo(http_art)
            art_styles = await wombo.get_art_styles()
            try:
                view = ChooseArtStyle(art_styles, ctx)
                art = await view.start(image_description)
            except commands.CommandError:
                return

            result = await wombo.generate(ctx, art, image_description, view.message)
            await WomboResult(wombo).display(result)

    async def get_local_url(self, http: aiohttp.ClientSession, url: str):
        if (local_url := self._cached_image.get(url)) is None:
            async with http.get(url) as response:
                base = base64.b64encode(await response.read()).decode('utf-8')
            filename = os.urandom(10).hex() + ".png"
            local_url = await self.bot.ipc_client.request('upload_file', base64=base, filename=filename)
            self._cached_image[url] = local_url

        return local_url