from __future__ import annotations
import discord
import base64
import datetime
import random
import itertools
import functools
from discord.ext import commands
from collections import namedtuple
from utils.useful import try_call, call, StellaContext, aware_utc
from typing import Union, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from main import StellaBot


class Useful(commands.Cog):
    """Command what I think is useful."""
    def __init__(self, bot: StellaBot):
        self.bot = bot

    def parse_date(self, token: str) -> datetime.datetime:
        token_epoch = 1293840000
        bytes_int = base64.standard_b64decode(token + "==")
        decoded = int.from_bytes(bytes_int, "big")
        timestamp = datetime.datetime.utcfromtimestamp(decoded)

        # sometime works
        if timestamp.year < 2015:
            timestamp = datetime.datetime.utcfromtimestamp(decoded + token_epoch)
        return timestamp

    @commands.command(aliases=["pt", "ptoken"],
                      brief="Decodes the token and showing user id and the token creation date.",
                      help="Decodes the token by splitting the token into 3 parts that was split in a period. "
                           "First part is a user id where it was decoded from base 64 into str. The second part "
                           "is the creation of the token, which is converted from base 64 into int. The last part "
                           "cannot be decoded due to discord encryption.")
    async def parse_token(self, ctx: StellaContext, token: str):
        token_part = token.split(".")
        if len(token_part) != 3:
            return await ctx.maybe_reply("Invalid token")

        def decode_user(user: str) -> str:
            user_bytes = user.encode()
            user_id_decoded = base64.b64decode(user_bytes)
            return user_id_decoded.decode("ascii")

        str_id = call(decode_user, token_part[0])
        if not str_id or not str_id.isdigit():
            return await ctx.maybe_reply("Invalid user")

        user_id = int(str_id)
        coro_user = functools.partial(try_call, self.bot.fetch_user, user_id, exception=discord.NotFound)
        member = None
        if ctx.guild:
            member = ctx.guild.get_member(user_id)

        member = member or self.bot.get_user(user_id) or await coro_user()
        if not member:
            return await ctx.maybe_reply("Invalid user")
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
        await ctx.embed(embed=embed)

    @commands.command(aliases=["gt", "gtoken"],
                      brief="Generate a new token given a user.",
                      help="Generate a new token for a given user or it defaults to the command author. "
                           "This works by encoding the user id into base 64 str. While the current datetime in utc "
                           "is converted into timestamp and gets converted into base64 using the standard b64 encoding. "
                           "The final part of the token is randomly generated.")
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

    @commands.command(aliases=["replycounts", "repliescount", "replyscounts", "threadcount"],
                      help="Finds the original message of a thread. This shows the amount of reply counts, the message itself, "
                           "the url message of the thread and the author.",
                      brief="Finds the original message of a thread.")
    async def replycount(self, ctx: StellaContext, message: discord.Message):
        def count_reply(m: Optional[Union[discord.MessageReference, discord.Message]],
                        replies: Optional[int] = 0) -> Tuple[discord.Message, int]:
            if isinstance(m, discord.MessageReference):
                return count_reply(m.cached_message, replies)
            if isinstance(m, discord.Message):
                if not m.reference:
                    return m, replies
                replies += 1
                return count_reply(m.reference, replies)

        msg, count = count_reply(message)
        embed_dict = {
            "title": "Reply Count",
            "description": f"**Original:** `{msg.author}`\n"
                           f"**Message:** {msg.clean_content}\n"
                           f"**Replies:** `{count}`\n"
                           f"**Origin:** [`jump`]({msg.jump_url})"
        }
        await ctx.embed(**embed_dict)

    @commands.command(aliases=["find_type", "findtypes", "idtype", "id_type", "idtypes"],
                      help="Try to find the type of an ID.")
    @commands.cooldown(1, 60, commands.BucketType.user)
    async def findtype(self, ctx: StellaContext, id: discord.Object):
        bot = self.bot

        async def found_message(type_id: str) -> None:
            await ctx.embed(title="Type Finder",
                            description=f"**ID**: `{id.id}`\n"
                                        f"**Type:** `{type_id.capitalize()}`\n"
                                        f"**Created:** `{id.created_at}`")

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
        await ctx.maybe_reply("idk")

    @commands.command(help="Gives a timestamp format based on the discord ID given.")
    async def timestamp(self, ctx: StellaContext, id: discord.Object, mode: Optional[str] = 'R'):
        content = discord.utils.format_dt(id.created_at, mode)
        await ctx.maybe_reply(f"```py\n{content}\n```")


async def setup(bot: StellaBot) -> None:
    await bot.add_cog(Useful(bot))
