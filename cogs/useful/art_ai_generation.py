from __future__ import annotations

import asyncio
import contextlib
import datetime
import io
import itertools
import json
import operator
import os
import random
import re
import time
from dataclasses import dataclass
from typing import Optional, List, Any, Dict, Union

import aiohttp
import discord
from PIL import Image
from dateutil import parser
from discord.ext import commands
from typing_extensions import Self

from utils.buttons import ViewAuthor, InteractionPages, button
from utils.decorators import pages, in_executor
from utils.errors import ErrorNoSignature
from utils.useful import StellaContext, StellaEmbed, print_exception, aware_utc, plural
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

        return parser.parse(date_str)

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
        self.__already_downloaded = 0
        self.__failure_gif_download = None
        self.cached_images = {}

    async def get_image(self, i):
        value = self.cached_images.get(i)
        if isinstance(value, asyncio.Event):
            await value.wait()
            value = self.cached_images.get(i)
        return value

    async def generate(self, ctx: StellaContext, art_style: ArtStyle, image_desc: str,
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

    async def download_image(self, url: str, retry=3):
        backoff_multiplier = 3
        current_error = None
        for x in range(retry):
            try:
                return await self._download_image(url)
            except Exception as e:
                current_error = e
                backoff = backoff_multiplier ** x
                print(f"Failure to download", url, ". Retrying after", backoff, "seconds")
                await asyncio.sleep(backoff)

        raise current_error

    async def _download_image(self, url: str):
        async with self.http_art.get(url) as response:
            return await response.read()

    async def download_images(self, start_id: int, urls: List[str]):
        try:
            await self._download_images(start_id, urls)
        except Exception as e:
            print_exception("Ignoring error while downloading images:", e)
            self.__failure_gif_download = True
        else:
            self.__failure_gif_download = False

    async def _download_images(self, start_id: int, urls: List[str]):
        tasks = []
        for i, url in enumerate(urls, start=start_id):
            waiter = asyncio.Event()
            self.cached_images[i] = waiter
            tasks.append(asyncio.create_task(self.download_image(url)))
            await asyncio.sleep(0.1)

        await asyncio.wait(tasks)
        for i, task in enumerate(tasks, start=start_id):
            result = task.result()
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
            expire = convert_expiry_date(payload['expiresIn'])
            return PayloadToken(
                payload['kind'], payload['idToken'], payload['refreshToken'], expire, payload['localId']
            )

    async def request_access_token(self, refresh_token: str) -> PayloadAccessToken:
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

    async def get_art_styles(self) -> List[ArtStyle]:
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
    count: int = 0
    emoji: Optional[int] = None

    @classmethod
    def from_json(cls, raw_data) -> Self:
        return cls(
            raw_data['id'], raw_data['name'], raw_data['created_at'], raw_data['updated_at'], raw_data['deleted_at'],
            raw_data['photo_url'], raw_data['blurDataURL']
        )

    def __str__(self) -> str:
        return self.name


class ChooseArtStyle(ViewAuthor):
    SELECT_PLACEHOLDER = "Choose an art style"

    def __init__(self, arts: List[ArtStyle], ctx: StellaContext):
        super().__init__(ctx)
        options = [discord.SelectOption(label=a.name, value=a.id) for a in arts]
        self.art_styles: Dict[int, ArtStyle] = {a.id: a for a in arts}
        self.select = discord.utils.get(self.children, placeholder=self.SELECT_PLACEHOLDER)
        self.select.options = options
        self.message: Optional[discord.Message] = None
        self.selected: Optional[ArtStyle] = None
        self._is_cancelled = None
        self.bot = self.context.bot

    async def update_count_select(self):
        if not (options := getattr(self.select, "options", None)):
            return

        records = await self.bot.pool_pg.fetch("SELECT * FROM wombo_style")
        for record in records:
            style_id = record["style_id"]
            count = record["style_count"]
            emoji_id = record["style_emoji"]
            emoji = self.bot.get_emoji(emoji_id)
            option: discord.SelectOption = discord.utils.get(options, value=style_id)
            if option:
                if count:
                    option.description = plural(f'{count} use(s)', count)
                if emoji is not None:
                    option.emoji = emoji._to_partial()

            if art_style := self.art_styles.get(style_id):
                if count:
                    art_style.count = count
                if emoji:
                    art_style.emoji = emoji

        options.sort(key=lambda e: self.art_styles[e.value].count, reverse=True)

    async def start(self, image_desc: str) -> Optional[ArtStyle]:
        await self.update_count_select()
        self.message = await self.context.send(f"Choose an art style for `{image_desc}`!", view=self)
        await self.wait()
        await asyncio.sleep(0)  # race condition on on_timeout
        with contextlib.suppress(discord.NotFound):
            if self._is_cancelled:
                await self.message.edit(content="User cancelled art selecting art...", view=None, embed=None)
                raise commands.CommandError("Cancelled")

            if self._is_cancelled is False:
                content = "Did not confirm an art style in time..."
                await self.message.edit(content=content, view=None, embed=None)
                raise ErrorNoSignature("Timeout")

        return self.selected

    @discord.ui.select(placeholder=SELECT_PLACEHOLDER)
    async def on_selected_art(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:
        value = select.values[0]
        art = self.art_styles.get(int(value))
        await self.set_value(art, interaction)

    async def set_value(self, art: ArtStyle, interaction: discord.Interaction) -> None:
        embed = StellaEmbed.default(self.context, title=f"Art Style Selected: {art.name}")
        embed.description = '**Press "Generate" to start generating with your style!**'
        embed.set_image(url=art.photo_url)
        self.selected = art
        confirm = discord.utils.get([n for n in self.children if isinstance(n, discord.ui.Button)], label="Generate")
        confirm.disabled = False
        await interaction.response.edit_message(content=None, embed=embed, view=self)

    @discord.ui.button(emoji='<:checkmark:753619798021373974>', label="Generate", row=1, style=discord.ButtonStyle.success, disabled=True)
    async def on_confirm(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(emoji='<:stopmark:753625001009348678>', label="Cancel", style=discord.ButtonStyle.danger)
    async def on_cancel(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.defer()
        self._is_cancelled = True
        self.stop()

    @discord.ui.button(emoji='🔀', label="Random", style=discord.ButtonStyle.blurple)
    async def on_random(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        art = random.choice([*self.art_styles.values()])
        await self.set_value(art, interaction)

    async def on_timeout(self) -> None:
        self._is_cancelled = False

    async def disable_all(self) -> None:
        if self.bot.get_message(self.message.id) is None:
            return

        with contextlib.suppress(Exception):
            if not self._is_cancelled:
                await self.message.edit(content="Image generation has started...", embed=None, view=None)

    def stop(self) -> None:
        super().stop()
        self.bot.loop.create_task(self.disable_all())


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
    async def on_menu_click(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        view = self.view
        embeds = view.showing_original()
        if view.final_button in view.children:
            view.remove_item(view.final_button)
            view.add_item(view.gen_button)

        await interaction.response.edit_message(content=None, embeds=embeds, view=view)
        self.stop()


class WomboResult(ViewAuthor):
    FINAL_IMAGE = "Final Image"
    IMG_GENERATION = "Show Image Evolution"

    def __init__(self, wombo: DreamWombo):
        super().__init__(wombo.ctx)
        self.result = None
        self.image_description = wombo.image_desc
        self.message = wombo.message
        self.http = wombo.http_art
        self.wombo = wombo
        self._original_photo = None
        self._original_gif = None
        self.final_button = discord.utils.get(self.children, label=self.FINAL_IMAGE)
        self.remove_item(self.final_button)
        self.gen_button = discord.utils.get(self.children, label=self.IMG_GENERATION)

    def home_embed(self) -> StellaEmbed:
        value = self.result
        amount_pic = len(value.photo_url_list)
        return StellaEmbed.default(
            self.context, title=self.image_description.title(), url=self._original_photo
        ).set_image(
            url=self._original_photo
        ).add_field(
            name="Image Generation", value=f"`{amount_pic}`"
        ).add_field(
            name="Style", value=self.wombo.art_style
        ).add_field(
            name="Created", value=aware_utc(value.created_at)
        )

    def showing_original(self):
        embed1 = self.home_embed()
        embed2 = self.home_embed()
        embed2.set_image(url=self._original_gif)
        return [embed1, embed2]

    async def display(self, result: PayloadTask) -> None:
        self.result = result
        self._original_photo = await self.context.cog.get_local_url(result.result['final'])
        self._original_gif = await self.generate_gif_url()
        embeds = self.showing_original()
        await self.message.edit(embeds=embeds, view=self, content=None)

    @in_executor()
    def generate_gif(self, image_bytes: List[bytes]) -> io.BytesIO:
        images: List[Image.Image] = [Image.open(io.BytesIO(image_byte)) for image_byte in image_bytes]
        *rest_images, final_image = images
        width, height = final_image.size
        final_size = int(width / 2), int(height / 2)
        resized_images = [image.resize(final_size) for image in images]
        byte = io.BytesIO()
        durations = [*[300 for _ in rest_images], 3000]
        first_image, *rest_images = resized_images
        first_image.save(byte, format="GIF", save_all=True, append_images=rest_images, optimize=False, duration=durations, loop=0)
        byte.seek(0)
        return byte

    @button(emoji="<:house_mark:848227746378809354>", label=FINAL_IMAGE, style=discord.ButtonStyle.success, row=0)
    async def on_menu_click(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        embeds = self.showing_original()
        self.remove_item(button)
        self.add_item(self.gen_button)
        await interaction.response.edit_message(content=None, embeds=embeds, view=self)

    async def generate_gif_url(self):
        # TODO: Do more handling on wombo.get_image on failure
        image_bytes = [await self.wombo.get_image(i + 1) for i, _ in enumerate(self.result.photo_url_list)]
        new_gif = await self.generate_gif(image_bytes)
        filename = os.urandom(16).hex() + ".gif"
        return await self.context.bot.upload_file(byte=new_gif.read(), filename=filename)

    @button(emoji='<a:OMPS_flecha:834116301483540531>', label=IMG_GENERATION, style=discord.ButtonStyle.success, row=0)
    async def show_gif(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self._original_gif is None:
            for item in self.children:
                item.disabled = True

            prev_embed = self.home_embed()
            prev_embed.set_image(url=None)
            desc = "<a:typing:597589448607399949> **Generating a GIF image. This may take a 20 seconds or longer**"
            prev_embed.description = desc
            await interaction.response.edit_message(view=self, embed=prev_embed)
            self._original_gif = await self.generate_gif_url()
            self.reset_timeout()
        else:
            await interaction.response.defer()

        embed = self.home_embed()
        for item in self.children:
            item.disabled = False

        self.remove_item(button)
        self.add_item(self.final_button)
        await self.message.edit(embed=embed.set_image(url=self._original_gif), view=self)

    @button(emoji='<:statis_mark:848262218554408988>', label="Show Image Generation", style=discord.ButtonStyle.blurple,
            row=1)
    async def show_images(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.defer()

        @pages()
        async def show_image(_, menu, item):
            generation = menu.current_page + 1
            url = await menu.ctx.cog.get_local_url(item)
            return StellaEmbed.default(menu.ctx, title=f"Image Generation {generation}").set_image(url=url)

        pager = WomboGeneration(show_image(self.result.photo_url_list), self)
        await pager.start(self.context)

    @button(emoji='🗑️', label="Delete", style=discord.ButtonStyle.danger, row=1)
    async def delete_image(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.stop()
        await interaction.response.defer()
        await self.message.delete()

    async def on_timeout(self) -> None:
        if self.context.bot.get_message(self.message.id) is None:
            return

        for item in self.children:
            item.disabled = True

        await self.message.edit(view=self)


class ProfanityImageDesc(commands.Converter):
    async def convert(self, ctx: StellaContext, image_desc: str) -> str:
        regex = r'(<a?:(?P<name>[a-zA-Z0-9_]{2,32}):[0-9]{18,22}>)|<@!?(?P<id>[0-9]+)>'

        def replace(val):
            group = val.groupdict()
            if name := group["name"]:
                return name

            val_id = int(group['id'])
            user = None
            if ctx.guild:
                user = ctx.guild.get_member(val_id)
            user = user or ctx.bot.get_user(val_id)
            if user is None:
                return val.group(0)
            return user.display_name

        image_desc = re.sub(regex, replace, image_desc)

        if len(image_desc) < 3:
            raise commands.BadArgument("Image description must be more than 3 characters.")
        if len(image_desc) > 100:
            raise commands.BadArgument("Image description must be less than or equal to 100 characters.")

        if not hasattr(ctx.channel, "is_nsfw") or ctx.channel.is_nsfw():
            return image_desc

        result = await ctx.bot.ipc_client.request('simple_nsfw_detection', content=image_desc)
        if result.get("suggestive"):
            raise commands.BadArgument("Unsafe image description given. Please use this prompt inside an nsfw channel.")

        return image_desc


class ArtAI(BaseUsefulCog):
    @commands.command(help="Generate art work with description given using Dream Wombo AI.")
    @commands.cooldown(1, 60, commands.BucketType.user)
    async def art(self, ctx: StellaContext, *, image_description: str = commands.param(converter=ProfanityImageDesc)):
        # I'm gonna be honest, I can't find their API so im just gonna reverse engineer it.
        wombo = DreamWombo(self.http_art)
        art_styles = await wombo.get_art_styles()
        try:
            view = ChooseArtStyle(art_styles, ctx)
            art = await view.start(image_description)
        except commands.CommandError:
            return

        await self._update_art_style(art)
        result = await wombo.generate(ctx, art, image_description, view.message)
        await WomboResult(wombo).display(result)

    async def _update_art_style(self, art: ArtStyle) -> None:
        query = ("INSERT INTO wombo_style VALUES($1) " 
                 "ON CONFLICT(style_id) " 
                 "DO UPDATE SET "
                 "style_count = wombo_style.style_count + 1")

        await self.bot.pool_pg.execute(query, art.id)
        if art.emoji is None:
            guild = self.bot.get_guild(self.bot.bot_guild_id)
            clean_name = art.name.replace(" ", "_")
            if not (emoji := discord.utils.get(guild.emojis, name=clean_name)):
                byte = await self.get_read_url(art.photo_url)
                try:
                    # Warning: This may raise an error if limit is reached.
                    emoji = await guild.create_custom_emoji(name=clean_name, image=byte, reason="Art Style Emoji")
                except discord.HTTPException:
                    return

            art.emoji = emoji
            await self.bot.pool_pg.execute("UPDATE wombo_style SET style_emoji=$1 WHERE style_id=$2", emoji.id, art.id)

    async def get_read_url(self, url: str) -> bytes:
        async with self.http_art.get(url) as response:
            return await response.read()

    async def get_local_url(self, url: str) -> str:
        if (local_url := self._cached_image.get(url)) is None:
            byte = await self.get_read_url(url)
            filename = os.urandom(10).hex() + ".png"
            local_url = await self.bot.upload_file(byte=byte, filename=filename)
            self._cached_image[url] = local_url

        return local_url
