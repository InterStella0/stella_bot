from __future__ import annotations

import contextlib
import json

import aiohttp
import discord
import base64
import datetime
import random
import itertools
import functools

from discord import app_commands
from discord.app_commands import Choice
from discord.ext import commands
from collections import namedtuple

from cogs.useful.art_ai_generation import ProfanityImageDesc
from cogs.useful.baseclass import BaseUsefulCog
from cogs.useful.dalle import DallEHandler
from cogs.useful.wombo_dream.model import ImageDescription
from utils import cog
from utils.buttons import InteractionPages
from utils.decorators import pages
from utils.useful import try_call, call, StellaContext, aware_utc, text_chunker, newline_chunker
from typing import Union, Optional, Tuple


class Etc(BaseUsefulCog):
    def parse_date(self, token: str) -> datetime.datetime:
        token_epoch = 1293840000
        bytes_int = base64.standard_b64decode(token + "==")
        decoded = int.from_bytes(bytes_int, "big")
        timestamp = datetime.datetime.utcfromtimestamp(decoded)

        # sometime works
        if timestamp.year < 2015:
            timestamp = datetime.datetime.utcfromtimestamp(decoded + token_epoch)
        return timestamp

    @commands.hybrid_command(
        aliases=["pt", "ptoken"],
        brief="Decodes the token and showing user id and the token creation date.",
        help="Decodes the token by splitting the token into 3 parts that was split in a period. "
             "First part is a user id where it was decoded from base 64 into str. The second part "
             "is the creation of the token, which is converted from base 64 into int. The last part "
             "cannot be decoded due to discord encryption."
    )
    @app_commands.describe(token="The discord token to be parsed.")
    async def parse_token(self, ctx: StellaContext, token: str):
        token_part = token.split(".")
        if len(token_part) != 3:
            return await ctx.maybe_reply("Invalid token", ephemeral=True)

        def decode_user(user: str) -> str:
            user_bytes = user.encode()
            user_id_decoded = base64.b64decode(user_bytes)
            return user_id_decoded.decode("ascii")

        str_id = call(decode_user, token_part[0])
        if not str_id or not str_id.isdigit():
            return await ctx.maybe_reply("Invalid user", ephemeral=True)

        user_id = int(str_id)
        coro_user = functools.partial(try_call, self.bot.fetch_user, user_id, exception=discord.NotFound)
        member = None
        if ctx.guild:
            member = ctx.guild.get_member(user_id)

        member = member or self.bot.get_user(user_id) or await coro_user()
        if not member:
            return await ctx.maybe_reply("Invalid user", ephemeral=True)
        timestamp = call(self.parse_date, token_part[1]) or "Invalid date"

        embed = discord.Embed(
            title=f"{member.display_name}'s token",
            description=f"**User:** `{member}`\n"
                        f"**ID:** `{member.id}`\n"
                        f"**Bot:** `{member.bot}`\n"
                        f"**Created:** {aware_utc(member.created_at, mode='f')}\n"
                        f"**Token Created:** `{timestamp}`"
        )
        embed.set_thumbnail(url=member.display_avatar)
        await ctx.embed(ephemeral=True, embed=embed)

    @cog.context_menu(name="parse token")
    async def on_context_parse_token(self, interaction: discord.Interaction, message: discord.Message):
        context = await StellaContext.from_interaction(interaction)
        await self.parse_token(context, message.content)

    @commands.hybrid_command(
        aliases=["gt", "gtoken"],
        brief="Generate a new token given a user.",
        help="Generate a new token for a given user or it defaults to the command author. "
             "This works by encoding the user id into base 64 str. While the current datetime in utc "
             "is converted into timestamp and gets converted into base64 using the standard b64 encoding. "
             "The final part of the token is randomly generated."
    )
    @app_commands.describe(member="The discord user to be converted into a token. Default to yourself.")
    async def generate_token(self, ctx: StellaContext, member: Union[discord.Member, discord.User] = None):
        if not member:
            member = ctx.author
        byte_first = str(member.id).encode('ascii')
        first_encode = base64.b64encode(byte_first)
        first = first_encode.decode('ascii')
        time_rn = datetime.datetime.utcnow()
        epoch_offset = int(time_rn.timestamp())
        bytes_int = int(epoch_offset).to_bytes(10, "big")
        bytes_clean = bytes_int.lstrip(b"\x00")
        unclean_middle = base64.standard_b64encode(bytes_clean)
        middle = unclean_middle.decode('utf-8').rstrip("==")
        Pair = namedtuple("Pair", "min max")
        num = Pair(48, 57)  # 0 - 9
        cap_alp = Pair(65, 90)  # A - Z
        cap = Pair(97, 122)  # a - z
        select = (num, cap_alp, cap)
        last = ""
        for each in range(27):
            pair = random.choice(select)
            last += str(chr(random.randint(pair.min, pair.max)))

        complete = ".".join((first, middle, last))
        fields = (("Token created:", f"`{time_rn}`"),
                  ("Generated Token:", f"`{complete}`"))

        embed = discord.Embed(
            title=f"{member.display_name}'s token",
            description=f"**User:** `{member}`\n"
                        f"**ID:** `{member.id}`\n"
                        f"**Bot:** `{member.bot}`"
        )
        embed.set_thumbnail(url=member.display_avatar)
        await ctx.embed(embed=embed, fields=fields)

    @commands.hybrid_command(
        aliases=["replycounts", "repliescount", "replyscounts", "threadcount"],
        help="Finds the original message of a thread. This shows the amount of reply counts, the message "
             "itself, the url message of the thread and the author.",
        brief="Finds the original message of a thread."
    )
    @app_commands.describe(message="The target message to count the reply.")
    async def replycount(self, ctx: StellaContext, message: discord.Message):
        class DeletedMessage:
            def __getattr__(self, item):
                return "<deleted>"

        async def count_reply(m: Optional[Union[discord.MessageReference, discord.Message]],
                              replies: Optional[int] = 0) -> Tuple[discord.Message, int]:

            if isinstance(m, discord.MessageReference):
                if m.cached_message is None:
                    ref = m.resolved
                    if isinstance(ref, discord.DeletedReferencedMessage):
                        return DeletedMessage(), replies
                    with contextlib.suppress(discord.NotFound):
                        return await ctx.fetch_message(m.message_id), replies
                    return DeletedMessage(), replies
                return await count_reply(m.cached_message, replies)
            if isinstance(m, discord.Message):
                if not m.reference:
                    return m, replies
                replies += 1
                return await count_reply(m.reference, replies)

        msg, count = await count_reply(message)
        embed_dict = {
            "title": "Reply Count",
            "description": f"**Original:** `{msg.author}`\n"
                           f"**Message:** {msg.clean_content}\n"
                           f"**Replies:** `{count}`\n"
                           f"**Origin:** [`jump`]({msg.jump_url})"
        }
        await ctx.embed(ephemeral=True, **embed_dict)

    @cog.context_menu(name="count reply")
    async def on_context_replycount(self, interaction: discord.Interaction, message: discord.Message):
        context = await StellaContext.from_interaction(interaction)
        await self.replycount(context, message)

    @commands.hybrid_command(
        aliases=["find_type", "findtypes", "idtype", "id_type", "idtypes"],
        help="Try to find the type of an ID."
    )
    @app_commands.describe(id="Discord ID of your target.")
    @commands.cooldown(1, 60, commands.BucketType.user)
    async def findtype(self, ctx: StellaContext, id: discord.Object):
        bot = self.bot

        async def found_message(type_id: str) -> None:
            await ctx.embed(title="Type Finder",
                            description=f"**ID**: `{id.id}`\n"
                                        f"**Type:** `{type_id.capitalize()}`\n"
                                        f"**Created:** `{id.created_at}`",
                            ephemeral=True)

        async def find(w: str, t: str) -> Optional[bool]:
            try:
                method = getattr(bot, f"{w}_{t}")
                if result := await discord.utils.maybe_coroutine(method, id.id):
                    return result is not None
            except discord.Forbidden:
                return ("fetch", "guild") != (w, t)
            except (discord.NotFound, AttributeError):
                pass

        m = await bot.http._HTTPClient__session.get(f"https://cdn.discordapp.com/emojis/{id.id}")
        if m.status == 200:
            return await found_message("emoji")

        if await try_call(commands.MessageConverter().convert, ctx, str(id.id)):
            return await found_message("message")

        for way, typeobj in itertools.product(("get", "fetch"), ("channel", "user", "webhook", "guild")):
            if await find(way, typeobj):
                return await found_message(typeobj)
        await ctx.maybe_reply("idk", ephemeral=True)

    @commands.hybrid_command(help="Gives a timestamp format based on the discord ID given.")
    @app_commands.describe(id="Discord ID to be converted.", mode="Timestamp mode, defaults to R")
    @app_commands.choices(
        mode=[
            Choice(name="t | Short Time | 22:57", value="t"),
            Choice(name="T | Long Time | 22:57:58", value="T"),
            Choice(name="d | Short Date | 17/05/2016", value="d"),
            Choice(name="D | Long Date | 17 May 2016", value="D"),
            Choice(name="f | Short Date Time | 17 May 2016 22:57", value="f"),
            Choice(name="F | Long Date Time | Tuesday, 17 May 2016 22:57", value="F"),
            Choice(name="R | Relative Time | 5 years ago (default)", value="R"),
        ]
    )
    async def timestamp(self, ctx: StellaContext, id: discord.Object,
                        mode: Union[Choice[str], discord.utils.TimestampStyle] = commands.param(
                            converter=discord.utils.TimestampStyle, default="R"
                        )):
        content = discord.utils.format_dt(id.created_at, getattr(mode, "value", mode))
        await ctx.maybe_reply(f"```py\n{content}\n```\n**Display:**{content}", ephemeral=True)

    @cog.context_menu(name="timestamp")
    async def on_context_timestamp(self, interaction: discord.Interaction, message: discord.Message):
        context = await StellaContext.from_interaction(interaction)
        await self.timestamp(context, message, mode="R")

    @commands.hybrid_command(name="json", aliases=["raw", "jsons"], help="Convert discord message into JSON.")
    @app_commands.describe(message="Discord message you want to convert. Default to current message.")
    async def _json(self, ctx: StellaContext, message: discord.Message = commands.param(converter=discord.Message)):
        raw_data = await self.bot.http.get_message(message.channel.id, message.id)
        json_formatted = json.dumps(raw_data, indent=4)

        if len(json_formatted) >= 1980:
            @pages()
            async def display(self, menu, json_raw):
                return f"```json\n{json_raw}```"

            data = newline_chunker(json_formatted, width=1800)
            await InteractionPages(display(data)).start(ctx)
        else:
            await ctx.maybe_reply(f"```json\n{json_formatted}```", ephemeral=True)

    @cog.context_menu(name="json")
    async def on_context_json(self, interaction: discord.Interaction, message: discord.Message):
        ctx = await StellaContext.from_interaction(interaction)
        await self._json(ctx, message)

    @cog.context_menu(name="Save Sticker")
    async def on_context_sticker(self, interaction: discord.Interaction, message: discord.Message):
        try:
            sticker, = message.stickers
        except ValueError:
            await interaction.response.send_message("No sticker attached", ephemeral=True)
        else:
            file = await sticker.to_file()
            await interaction.response.send_message(f"Here is your sticker: {sticker}", file=file, ephemeral=True)

    @commands.hybrid_command(aliases=["imagine"], help="Create image from a given prompt using Dall-E Mini AI.")
    @app_commands.describe(image_description="Prompt for describing the image. Range of 3 - 100 characters.")
    @commands.max_concurrency(1, commands.BucketType.user)
    async def dalle(self, ctx: StellaContext, *,
                    image_description: ImageDescription = commands.param(converter=ProfanityImageDesc)):
        handler = DallEHandler(ctx)
        await handler.start(image_description.name)
