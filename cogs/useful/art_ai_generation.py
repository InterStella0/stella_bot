from __future__ import annotations

import io
import os
import re

import aiohttp
import discord
from PIL import Image
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Converter

from utils.buttons import InteractionPages
from utils.decorators import pages, in_executor
from utils.errors import ErrorNoSignature
from utils.prefix_ai import MobileNetNSFW, PredictionNSFW
from utils.useful import StellaContext, StellaEmbed, print_exception, realign, \
    except_retry
from .baseclass import BaseUsefulCog
from .wombo_dream.interaction import ImageVote, ChooseArtStyle, WomboResult, ImageManagementView
from .wombo_dream.model import ImageSaved, ImageDescription, ArtStyle
from .wombo_dream.core import DreamWombo


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

        result = await ctx.bot.stella_api.is_nsfw(image_desc)
        if not hasattr(ctx.channel, "is_nsfw") or ctx.channel.is_nsfw():
            return ImageDescription(image_desc, result.get("suggestive", False))

        if is_nsfw := result.get("suggestive"):
            raise commands.BadArgument("Unsafe image description given. Please use this prompt inside an nsfw channel.")

        return ImageDescription(image_desc, bool(is_nsfw))


class AIModel(Converter):
    async def convert(self, ctx: StellaContext, argument: str) -> MobileNetNSFW:
        model = ctx.cog.get_model_style(argument)
        if not model:
            raise commands.BadArgument(f"Model with '{argument}' style does not exist.")

        return model


class ArtAI(BaseUsefulCog):
    @commands.hybrid_command(help="Generate art work with description given using Dream Wombo AI.")
    @app_commands.describe(
        image_description="Describe the image you want to generate. This is limit to 100 characters.")
    @commands.cooldown(1, 60, commands.BucketType.user)
    async def art(self, ctx: StellaContext,
                  *, image_description: ImageDescription = commands.param(converter=ProfanityImageDesc)):
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

    @commands.hybrid_command(help="Shows a specific saved image according to 'art_name'. "
                                  "No argument to show your own list of saved images.",
                             brief="Shows a specific saved image according to 'art_name'.")
    @app_commands.describe(art_name="The name the art was named as.")
    async def arts(self, ctx: StellaContext, *, art_name: ImageSaved = None):
        if art_name is None:
            await self._handle_art_no_arg(ctx)
        else:
            await self._handle_art_arg(ctx, art_name)

    @commands.hybrid_command(help="Display a list of all images that was saved.")
    async def allarts(self, ctx: StellaContext):
        @pages(per_page=10)
        async def show_images(inner_self, menu, raw_arts):
            offset = menu.current_page * inner_self.per_page
            arts = [*map(ImageSaved.from_record, raw_arts)]
            key = "(\u200b|\u200b)"
            content = "`{no}. {b.name} {key} {b.vote}`"
            iterable = [content.format(no=i + 1, b=b, key=key) for i, b in enumerate(arts, start=offset)]
            return StellaEmbed.default(
                ctx,
                title="All Saved Art",
                description="\n".join(realign(iterable, key))
            )

        is_nsfw = getattr(ctx.channel, "is_nsfw", lambda: True)()
        sql = ('SELECT ws.*, ('
               'SELECT COUNT(*) FROM wombo_liker WHERE name=ws.name'
               ') "count" FROM wombo_saved ws '
               'ORDER BY count DESC')
        values = ()
        if not is_nsfw:
            sql = ('SELECT ws.*, ('
                   'SELECT COUNT(*) FROM wombo_liker WHERE name=ws.name'
                   ') "count" FROM wombo_saved ws WHERE is_nsfw=$1 '
                   'ORDER BY count DESC')
            values = (False,)
        all_arts = await self.bot.pool_pg.fetch(sql, *values)

        if not all_arts:
            raise ErrorNoSignature("Looks like no images has been saved.")
        await InteractionPages(show_images(all_arts)).start(ctx)

    async def _handle_art_arg(self, ctx: StellaContext, art_name: ImageSaved):
        await ImageVote(art_name).start(ctx)

    async def _handle_art_no_arg(self, ctx: StellaContext):
        @pages()
        async def show_images(inner_self, menu, raw_art):
            art = ImageSaved.from_record(raw_art)
            return StellaEmbed.default(
                ctx,
                title=art.name,
                description=f"**Prompt:** `{art.prompt}`\n"
                            f"**Style:** `{art.art_style}`\n"
                            f"**Like(s):** `{art.vote}`",
                url=art.image_url
            ).set_image(url=art.image_url)

        is_nsfw = getattr(ctx.channel, "is_nsfw", lambda: True)()
        sql = ('SELECT ws.*, ('
               'SELECT COUNT(*) FROM wombo_liker WHERE name=ws.name'
               ') "count" FROM wombo_saved ws WHERE user_id=$1'
               'ORDER BY count DESC')
        values = (ctx.author.id,)
        if not is_nsfw:
            sql = ('SELECT ws.*, ('
                   'SELECT COUNT(*) FROM wombo_liker WHERE name=ws.name'
                   ') "count" FROM wombo_saved ws WHERE is_nsfw=$1 and user_id=$2 '
                   'ORDER BY count DESC')
            values = (False, ctx.author.id)
        all_arts = await self.bot.pool_pg.fetch(sql, *values)
        if not all_arts:
            raise ErrorNoSignature("Looks like you have no image saved.")

        await InteractionPages(show_images(all_arts)).start(ctx)

    async def _update_art_style(self, art: ArtStyle) -> None:
        query = ("INSERT INTO wombo_style VALUES($1) " 
                 "ON CONFLICT(style_id) " 
                 "DO UPDATE SET "
                 "style_count = wombo_style.style_count + 1")

        await self.bot.pool_pg.execute(query, art.id)
        if art.emoji is None:
            guild = self.bot.get_guild(self.bot.bot_guild_id)
            clean_name = re.sub("[. ]", "_", art.name)
            if not (emoji := discord.utils.get(guild.emojis, name=clean_name)):
                byte = await self.get_read_url(art.photo_url)
                if (emoji := await self.__create_emoji(guild, clean_name, byte)) is None:
                    return

            art.emoji = emoji
            await self.bot.pool_pg.execute("UPDATE wombo_style SET style_emoji=$1 WHERE style_id=$2", emoji.id, art.id)

    @in_executor()
    def __reduce_emoji_size(self, byte: bytes) -> bytes:
        with Image.open(io.BytesIO(byte)) as img:
            width, height = img.size
            img.thumbnail((int(width / 2), int(height / 2)))
            b = io.BytesIO()
            img.save(b, format="PNG")
            b.seek(0)
            return b.read()

    async def __create_emoji(self, guild, clean_name, byte):
        try:
            # Warning: This may raise an error if limit is reached.
            return await guild.create_custom_emoji(name=clean_name, image=byte, reason="Art Style Emoji")
        except discord.HTTPException as e:
            if e.code == 50045:  # exceeds max
                byte = await self.__reduce_emoji_size(byte)
                return await self.__create_emoji(guild, clean_name, byte)

            print_exception("Ignoring error on creating emoji:", e)
            return

    async def get_read_url(self, url: str) -> bytes:
        async def callback():
            async with self.http_art.get(url) as response:
                return await response.read()

        return await except_retry(callback, error=aiohttp.ServerDisconnectedError)

    async def get_local_url(self, url: str) -> str:
        if (local_url := self._cached_image.get(url)) is None:
            byte = await self.get_read_url(url)
            filename = os.urandom(10).hex() + ".png"
            local_url = await self.bot.upload_file(byte=byte, filename=filename)
            self._cached_image[url] = local_url

        return local_url

    @commands.command()
    @commands.is_owner()
    async def manage_data_art(self, ctx):
        await ImageManagementView(ctx).start()

    def get_model_style(self, style: str) -> MobileNetNSFW:
        if not (model := self.cached_models.get(style)):
            path = f"saved_model/Mobile model_{style}.h5"
            if not os.path.exists(path):
                return
            self.cached_models[style] = model = MobileNetNSFW.load_from_save(path)
        return model

    @commands.command()
    @commands.is_owner()
    async def rate(self, ctx: StellaContext, art_style: MobileNetNSFW = commands.param(converter=AIModel),
                   attachment: discord.Attachment = commands.param(converter=discord.Attachment)):
        model = art_style
        byte = await attachment.read()

        with Image.open(io.BytesIO(byte)) as img:
            predicted: PredictionNSFW = await model.predict(img)

        await ctx.embed(
            description=f"**NSFW Score:** {predicted.nsfw_score}\n"
                        f"**SFW Score:** {predicted.sfw_score}\n"
                        f"**Conclude:** {predicted.class_name} (`{predicted.confidence:.2%}`)",
        )


