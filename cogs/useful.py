import discord
import base64
import datetime
import random
from discord.ext import commands
from collections import namedtuple
from utils.useful import try_call
from utils.new_converters import FetchUser
from typing import Union


class Useful(commands.Cog):
    """Command what I think is useful."""
    def __init__(self, bot):
        self.bot = bot

    async def parse_date(self, token):
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
    async def parse_token(self, ctx, token):
        token_part = token.split(".")
        if len(token_part) != 3:
            return await ctx.send("Invalid token")

        def decode_user(user):
            user_bytes = user.encode()
            user_id_decoded = base64.b64decode(user_bytes)
            return user_id_decoded.decode("ascii")
        str_id = await try_call(decode_user, Exception, args=(token_part[0],))
        if not str_id or not str_id.isdigit():
            return await ctx.send("Invalid user")
        user_id = int(str_id)
        coro_user = try_call(self.bot.fetch_user(user_id), discord.NotFound)
        member = ctx.guild.get_member(user_id) or self.bot.get_user(user_id) or await coro_user
        if not member:
            return await ctx.send("Invalid user")
        timestamp = await try_call(self.parse_date(token_part[1]), Exception) or "Invalid date"

        embed = discord.Embed(title=f"{member.display_name}'s token",
                              description=f"**User:** `{member}`\n"
                                          f"**ID:** `{member.id}`\n"
                                          f"**Bot:** `{member.bot}`\n"
                                          f"**Created:** `{member.created_at}`\n"
                                          f"**Token Created:** `{timestamp}`",
                              color=self.bot.color,
                              timestamp=datetime.datetime.utcnow())
        embed.set_thumbnail(url=member.avatar_url)
        embed.set_footer(text=f"Requested by {ctx.author}",
                         icon_url=ctx.author.avatar_url)
        await ctx.send(embed=embed)

    @commands.command(aliases=["gt", "gtoken"],
                      brief="Generate a new token given a user.",
                      help="Generate a new token for a given user or it defaults to the command author. "
                           "This works by encoding the user id into base 64 str. While the current datetime in utc "
                           "is converted into timestamp and gets converted into base64 using the standard b64 encoding. "
                           "The final part of the token is randomly generated.")
    async def generate_token(self, ctx, member: Union[discord.Member, discord.User, FetchUser] = None):
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

        embed = discord.Embed(title=f"{member.display_name}'s token",
                              description=f"**User:** `{member}`\n"
                                          f"**ID:** `{member.id}`\n"
                                          f"**Bot:** `{member.bot}`",
                              color=self.bot.color,
                              timestamp=datetime.datetime.utcnow())
        for name, value in fields:
            embed.add_field(name=name, value=value, inline=False)
        embed.set_thumbnail(url=member.avatar_url)
        embed.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.avatar_url)
        await ctx.send(embed=embed)


def setup(bot):
    bot.add_cog(Useful(bot))
