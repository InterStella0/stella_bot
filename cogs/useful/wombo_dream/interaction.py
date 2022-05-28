from __future__ import annotations
import asyncio
import contextlib
import io
import os
import random
import shutil
from typing import Optional, List, Dict, Union

import asyncpg
import discord
from PIL import Image
from discord.ext import commands

from utils.decorators import in_executor, pages
from utils.errors import ErrorNoSignature
from utils.ipc import StellaFile
from utils.prefix_ai import MobileNetNSFW
from .model import ImageSaved, ArtStyle, ImageDescription, PayloadTask, ImageMetaData
from utils.buttons import BaseView, ViewAuthor, InteractionPages, button
from utils.useful import StellaContext, StellaEmbed, plural, print_exception, aware_utc, ensure_execute
from .core import DreamWombo


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
        self.most_used: Optional[ArtStyle] = None

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

    async def start(self, image_desc: ImageDescription) -> Optional[ArtStyle]:
        await self.update_count_select()
        self.most_used = max([*self.art_styles.values()], key=lambda x: x.count)
        self.message = await self.context.send(f"Choose an art style for `{image_desc.name}`!", view=self)
        await self.wait()
        await asyncio.sleep(0.01)  # race condition on on_timeout, no i dont care 0.01
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
        buttons = [n for n in self.children if isinstance(n, discord.ui.Button)]
        label = discord.utils.get(buttons, label="Most Used")
        label.disabled = art is self.most_used
        embed = StellaEmbed.default(self.context, title=f"Art Style Selected: {art.name}")
        embed.description = '**Press "Generate" to start generating with your style!**'
        embed.set_image(url=art.photo_url)
        self.selected = art
        confirm = discord.utils.get(buttons, label="Generate")
        confirm.disabled = False
        await interaction.response.edit_message(content=None, embed=embed, view=self)

    @discord.ui.button(emoji='<:checkmark:753619798021373974>', label="Generate", row=2,
                       style=discord.ButtonStyle.success, disabled=True)
    async def on_confirm(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(emoji='<:stopmark:753625001009348678>', label="Cancel", row=2, style=discord.ButtonStyle.danger)
    async def on_cancel(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.defer()
        self.context.command.reset_cooldown(self.context)
        self._is_cancelled = True
        self.stop()

    @discord.ui.button(emoji='🔝', label="Most Used", style=discord.ButtonStyle.blurple)
    async def on_most_used(self, interaction: discord.Interaction, _: discord.ui.Button):
        art = max(self.art_styles.values(), key=lambda x: x.count)
        await self.set_value(art, interaction)

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

    @button(emoji="<:house_mark:848227746378809354>", label=MENU, row=1, stay_active=True,
            style=discord.ButtonStyle.success)
    async def on_menu_click(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        view = self.view
        embeds = await view.showing_original()
        if view.final_button in view.children:
            view.remove_item(view.final_button)
            view.add_item_pos(view.gen_button, 0)

        await interaction.response.edit_message(content=None, embeds=embeds, view=view)
        self.stop()


class WomboSave(discord.ui.Modal, title="Saving generated image"):
    name = discord.ui.TextInput(label="Name", min_length=3, max_length=100, placeholder="Name for your image")

    def __init__(self, result: WomboResult):
        super().__init__()
        wombo = result.wombo
        self.result = result
        self.ctx = wombo.ctx
        self.bot = self.ctx.bot
        self.image_desc = wombo.image_desc
        self.name.default = self.image_desc.name

    async def on_submit(self, interaction: discord.Interaction) -> None:
        query = "INSERT INTO wombo_saved VALUES($1, $2, $3, $4, $5, $6, $7)"
        if not (name := self.name.value.strip()):
            raise ErrorNoSignature("'Name' cannot be empty.")

        if len(name) < 3:
            raise ErrorNoSignature("'Name' cannot be less than 3.")

        result = self.result
        img_desc = self.image_desc
        art_name = getattr(self.result.wombo.art_style, "name", None)

        if await self.bot.pool_pg.fetchrow("SELECT * FROM wombo_saved WHERE LOWER(name)=$1", name):
            raise asyncpg.UniqueViolationError()

        values = [name, self.ctx.author.id, result._original_photo.url, 0, img_desc.nsfw, img_desc.name, art_name]
        await self.bot.pool_pg.execute(query, *values)
        prefix = self.ctx.clean_prefix
        saved = f"Your image has been saved! Type '`{prefix}arts {name}`' to view your image."
        await interaction.response.send_message(saved, ephemeral=True)
        if saver := discord.utils.get([x for x in result.children if hasattr(x, "label")], label="Save"):
            saver.disabled = True
            await self.result.message.edit(view=result)
            result.reset_timeout()

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        resp = None
        if isinstance(error, ErrorNoSignature):
            resp = str(error)
        elif isinstance(error, asyncpg.UniqueViolationError):
            resp = f'Image with "{self.name}" already exist. Please try something else.'

        if resp is not None:
            return await interaction.response.send_message(resp, ephemeral=True)

        fallback = f"Something went wrong. Please try again later. Error: {error}"
        await interaction.response.send_message(fallback, ephemeral=True)
        exc = print_exception("Error occurred when saving image.", error)
        await self.bot.error_channel.send(embed=StellaEmbed.to_error(description=exc))


class WomboResult(ViewAuthor):
    FINAL_IMAGE = "Final Image"
    IMG_GENERATION = "Image Evolution"

    def __init__(self, wombo: DreamWombo):
        super().__init__(wombo.ctx)
        self.result = None
        self.image_description = wombo.image_desc
        self.message = wombo.message
        self.http = wombo.http_art
        self.wombo = wombo
        self._original_photo: Optional[StellaFile] = None
        self._original_gif: Optional[StellaFile] = None
        self.final_button: discord.ui.Button = discord.utils.get(self.children, label=self.FINAL_IMAGE)
        self.remove_item(self.final_button)
        self.gen_button = discord.utils.get(self.children, label=self.IMG_GENERATION)
        self.input_save: Optional[WomboSave] = None
        self._nsfw_rating: Optional[Union[bool, str]] = None

    async def home_embed(self) -> StellaEmbed:
        value = self.result
        amount_pic = len(value.photo_url_list)
        return StellaEmbed.default(
            self.context, title=self.image_description.name.title(), url=self._original_photo
        ).set_image(
            url=self._original_photo
        ).add_field(
            name="Image Generation", value=f"`{amount_pic}`"
        ).add_field(
            name="Style", value=self.wombo.art_style
        ).add_field(
            name="Safe Rating", value=await self.nsfw_rating() or "N/A"
        ).add_field(
            name="Created", value=aware_utc(value.created_at)
        )

    async def showing_original(self) -> List[StellaEmbed]:
        embed1 = await self.home_embed()
        embed2 = await self.home_embed()
        embed2.set_image(url=self._original_gif)
        return [embed1, embed2]

    async def display(self, result: PayloadTask) -> None:
        self.result = result
        self._original_photo = await self.context.cog.get_local_url(result.result['final'])
        kwargs = {}
        try:
            self._original_gif = await self.generate_gif_url()
        except Exception as e:
            error = print_exception("Ignoring error in generate gif url", e)
            await self.context.bot.error_channel.send(embed=StellaEmbed.to_error(description=error))
            kwargs = {'embed': await self.home_embed()}
        else:
            kwargs = {'embeds': await self.showing_original()}
        finally:
            await self.message.edit(**kwargs, view=self, content=None)

    @in_executor()
    def generate_gif(self, image_bytes: List[bytes]) -> io.BytesIO:
        images: List[Image.Image] = [Image.open(io.BytesIO(image_byte)) for image_byte in image_bytes]
        file_name = os.urandom(16).hex()
        folder = "nsfw" if self.image_description.nsfw else "sfw"
        style = self.wombo.art_style.name
        folder_path = fr"data/{folder}/{style}"
        if not os.path.exists(folder_path):
            os.makedirs(folder_path)
        for i, img in enumerate(images[-5:]):
            img = img.resize((512, 512))
            filepath = fr"{folder_path}/{file_name}_{i}.png"
            img.save(filepath, format="PNG")
        *rest_images, final_image = images
        width, height = final_image.size
        final_size = int(width / 2), int(height / 2)
        resized_images = [image.resize(final_size) for image in images]
        byte = io.BytesIO()
        durations = [*[300 for _ in rest_images], 3000]
        first_image, *rest_images = resized_images
        first_image.save(byte, format="GIF", save_all=True, append_images=rest_images, optimize=False,
                         duration=durations, loop=0)
        byte.seek(0)
        return byte

    async def nsfw_rating(self):
        if self._nsfw_rating is not None:
            return self._nsfw_rating

        model: MobileNetNSFW = self.context.cog.get_model_style(self.wombo.art_style.name)
        if model is None:
            self._nsfw_rating = False
            return

        with Image.open(io.BytesIO(self._original_photo.byte)) as img:
            result = await model.predict(img)

        self._nsfw_rating = to_return = result.class_name.upper()
        return to_return

    @button(emoji="<:house_mark:848227746378809354>", label=FINAL_IMAGE, style=discord.ButtonStyle.success, row=0)
    async def on_menu_click(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        embeds = await self.showing_original()
        self.remove_item(button)
        self.add_item_pos(self.gen_button, 0)
        await interaction.response.edit_message(content=None, embeds=embeds, view=self)

    async def generate_gif_url(self) -> StellaFile:
        image_bytes = []
        for i, url in enumerate(self.result.photo_url_list):
            if byte := await self.wombo.get_image(i + 1, fallback=url):
                image_bytes.append(byte)

        if len(image_bytes) < 3:
            raise Exception("Failure to download most of the images")

        new_gif = await self.generate_gif(image_bytes)
        filename = os.urandom(16).hex() + ".gif"
        return await self.context.bot.upload_file(byte=new_gif.read(), filename=filename)

    @button(emoji='<a:OMPS_flecha:834116301483540531>', label=IMG_GENERATION, style=discord.ButtonStyle.blurple, row=0)
    async def show_gif(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self._original_gif is None:
            for item in self.children:
                item.disabled = True

            prev_embed = await self.home_embed()
            prev_embed.set_image(url=None)
            desc = "<a:typing:597589448607399949> **Generating a GIF image. This may take a few seconds or longer**"
            prev_embed.description = desc
            await interaction.response.edit_message(view=self, embed=prev_embed)
            self._original_gif = await self.generate_gif_url()
            self.reset_timeout()
        else:
            await interaction.response.defer()

        embed = await self.home_embed()
        for item in self.children:
            item.disabled = False

        self.remove_item(button)
        self.add_item_pos(self.final_button, 0)
        await self.message.edit(embed=embed.set_image(url=self._original_gif), view=self)

    def add_item_pos(self, button: discord.ui.Button, pos: int):
        self.add_item(button)
        self.children.remove(button)
        self.children.insert(pos, button)

    @button(emoji='<:statis_mark:848262218554408988>', label="Image Generation", style=discord.ButtonStyle.blurple,
            row=0)
    async def show_images(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.defer()

        @pages()
        async def show_image(_, menu, item):
            generation = menu.current_page + 1
            defer = menu.current_interaction.response.defer
            url = await ensure_execute(menu.ctx.cog.get_local_url(item), defer, timeout=2)
            return StellaEmbed.default(menu.ctx, title=f"Image Generation {generation}").set_image(url=url)

        pager = WomboGeneration(show_image(self.result.photo_url_list), self)
        await pager.start(self.context, interaction=interaction)

    @button(emoji="<:download:316264057659326464>", label="Save", style=discord.ButtonStyle.success, row=1)
    async def save_image(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.input_save = WomboSave(self) if self.input_save is None else self.input_save
        await interaction.response.send_modal(self.input_save)

    @button(emoji='🗑️', label="Delete", style=discord.ButtonStyle.danger, row=1)
    async def delete_image(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.stop()
        await interaction.response.defer()
        await self.message.delete()

    def stop(self) -> None:
        super().stop()
        if self.input_save is not None and not self.is_finished():
            self.input_save.stop()

    async def on_timeout(self) -> None:
        if self.input_save is not None and not self.is_finished():
            self.input_save.stop()

        if self.context.bot.get_message(self.message.id) is None:
            return

        for item in self.children:
            item.disabled = True

        await self.message.edit(view=self)


class ImageVote(BaseView):
    def __init__(self, art: ImageSaved):
        super().__init__()
        self.art = art
        self.context: Optional[StellaContext] = None
        self.message: Optional[discord.Message] = None

    def create_embed(self) -> StellaEmbed:
        ctx = self.context
        art = self.art
        user_id = art.user_id
        user = ctx.guild and ctx.guild.get_member(user_id) or ctx.bot.get_user(user_id)
        user_name = user or user_id
        return StellaEmbed.default(
            ctx,
            title=art.name,
            description=f"**Prompt:** `{art.prompt}`\n"
                        f"**Style:** `{art.art_style}`\n"
                        f"**Owned by:** `{user_name}`\n"
                        f"**Like(s):** `{art.vote}`",
            url=art.image_url
        ).set_image(url=art.image_url)

    async def start(self, ctx: StellaContext) -> None:
        self.context = ctx
        self.message = await ctx.maybe_reply(view=self, embed=self.create_embed())
        await self.wait()

    async def on_timeout(self) -> None:
        if not self.context.bot.get_message(self.message.id):
            return

        for x in self.children:
            x.disabled = True

        await self.message.edit(view=self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        author_id = interaction.user.id
        art = self.art
        if author_id == art.user_id:
            await interaction.response.send_message("You cannot like your own image.", ephemeral=True)
            return False

        query = "SELECT * FROM wombo_liker WHERE user_id=$1 and name=$2"
        result = await self.context.bot.pool_pg.fetchrow(query, author_id, art.name)
        if result is not None:
            await interaction.response.send_message("You've already liked this image.", ephemeral=True)
            return False
        return True

    @discord.ui.button(emoji="👍", label="Like", style=discord.ButtonStyle.success)
    async def on_like(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        art = self.art
        query = "INSERT INTO wombo_liker VALUES($1, $2)"
        await interaction.client.pool_pg.execute(query, art.name, interaction.user.id)
        user_id = art.user_id
        name = self.context.guild and self.context.guild.get_member(user_id) or self.context.bot.get_user(user_id)
        await interaction.response.send_message(f"You've liked this image. `{name}` says thanks.", ephemeral=True)
        art.vote += 1
        await self.message.edit(embed=self.create_embed())


class ImageManagementView(ViewAuthor):
    ROOT_FOLDER = "data"

    def __init__(self, ctx: StellaContext):
        super().__init__(ctx)
        self.safety = "sfw"
        self.bot = ctx.bot
        self._cached_urls = {}
        self.select_data: List[ImageMetaData] = self._get_recursive_data()
        self.selected_image: Optional[ImageMetaData] = None
        self.message = None
        self.current_page = 0

    async def start(self):
        self.message = await self.context.embed(description="Select a category", view=self)

    def _get_recursive_data(self) -> Dict[str, List[ImageMetaData]]:
        data = {}
        for folder in os.listdir(self.ROOT_FOLDER):
            fp = rf"{self.ROOT_FOLDER}/{folder}"
            data[folder] = folder_data = []
            for category in os.listdir(fp):
                file_fp = rf"{fp}/{category}"
                files = [ImageMetaData(file_fp, category, folder == "nsfw", x) for x in os.listdir(file_fp)]
                for file in files:
                    if file in folder_data:
                        continue
                    folder_data.append(file)

        return data

    @discord.ui.select(placeholder="Safety", options=[
        discord.SelectOption(label="Sfw", value="sfw"),
        discord.SelectOption(label="Nsfw", value="nsfw")
    ])
    async def on_safety_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.safety = select.values[0]
        self.update_data()
        await self.update_interface(interaction, 0)

    def update_data(self):
        self.select_data = self._get_recursive_data()[self.safety]

    async def get_image(self, fp: ImageMetaData):
        if not (url := self._cached_urls.get(fp)):
            with open(fp.full_fp, "rb") as r:
                file = await self.bot.upload_file(byte=r.read(), filename=fp.name)
            self._cached_urls[fp] = url = file.url
        return url

    async def create_embed(self, image: ImageMetaData):
        embed = StellaEmbed.default(self.context)
        embed.add_field(name="Page", value=f"{self.current_page + 1}/{len(self.select_data)}")
        embed.add_field(name="category", value=image.category)
        return embed.set_image(url=await self.get_image(image))

    async def update_interface(self, interaction: discord.Interaction, target_page: int):
        await interaction.response.defer()
        try:
            img = self.select_data[target_page]
        except IndexError:
            embed = StellaEmbed.default(self.context, description="No data")
            self.set_nsfw.disabled = False
            self.set_sfw.disabled = False
        else:
            self.current_page = target_page
            embed = await self.create_embed(img)
            self.set_nsfw.disabled = img.is_nsfw
            self.set_sfw.disabled = not img.is_nsfw
            self.selected_image = img
            self.on_seen.disabled = False
            v = await self.bot.pool_pg.fetchrow("SELECT * FROM wombo_data_seen WHERE name=$1", img.clean_name)
            if v:
                self.on_seen.disabled = v['seen']

        self.next_page.disabled = False
        self.before_page.disabled = False
        if target_page >= len(self.select_data) - 1:
            self.next_page.disabled = True
        if target_page == 0:
            self.before_page.disabled = True

        await self.message.edit(embed=embed, view=self)

    @discord.ui.button(emoji="<:before_check:754948796487565332>", row=1, disabled=True)
    async def before_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        page = max(self.current_page - 1, 0)
        await self.update_interface(interaction, page)

    @discord.ui.button(emoji="<:stop_check:754948796365930517>", row=1)
    async def stop_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.message.delete(delay=0)
        self.stop()

    @discord.ui.button(emoji="<:next_check:754948796361736213>", row=1, disabled=True)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        page = min(self.current_page + 1, len(self.select_data) - 1)
        await self.update_interface(interaction, page)

    @in_executor()
    def change_image_file(self, safety_folder):
        image = self.selected_image
        changed_fp = rf"{self.ROOT_FOLDER}/{safety_folder}/{image.category}"
        if not os.path.exists(changed_fp):
            os.makedirs(changed_fp)

        for filename in image.all_names():
            shutil.move(rf"{image.fp}/{filename}", rf"{changed_fp}/{filename}")

    async def handle_change_position(self, safety, interaction):
        await self.change_image_file(safety)
        self.update_data()
        await self.update_interface(interaction, max(self.current_page - 1, 0))

    @discord.ui.button(label="NSFW", style=discord.ButtonStyle.danger, row=2, disabled=True)
    async def set_nsfw(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_change_position("nsfw", interaction)

    @discord.ui.button(label="SFW", style=discord.ButtonStyle.success, row=2, disabled=True)
    async def set_sfw(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_change_position("sfw", interaction)

    @discord.ui.button(label="Seen", style=discord.ButtonStyle.blurple, row=2, disabled=True)
    async def on_seen(self, interaction: discord.Interaction, button: discord.ui.Button):
        query = "INSERT INTO wombo_data_seen VALUES($1, $2)"
        await self.bot.pool_pg.execute(query, self.selected_image.clean_name, True)
        button.disabled = True
        await interaction.response.edit_message(view=self)
