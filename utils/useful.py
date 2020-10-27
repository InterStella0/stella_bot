import discord
from discord.ext import commands
from discord.utils import maybe_coroutine


async def try_call(code, exception, ret=False, args=None, kwargs=None):
    try:
        return await maybe_coroutine(code, *args, **kwargs) if args or kwargs else await code
    except exception as e:
        return e if ret else None


class FetchUser(commands.Converter):
    async def convert(self, ctx, argument):
        return await ctx.bot.fetch_user(int(argument))


class BaseEmbed(discord.Embed):
    def __init__(self, color=0xffcccb, **kwargs):
        super(BaseEmbed, self).__init__(color=color, **kwargs)

    @classmethod
    def default(cls, ctx, **kwargs):
        instance = cls(**kwargs)
        instance.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.avatar_url)
        return instance

    @classmethod
    def to_error(cls, color=discord.Color.red(), **kwargs):
        return cls(color=color, **kwargs)

