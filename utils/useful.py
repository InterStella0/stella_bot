import discord
from discord.utils import maybe_coroutine
from discord.ext import commands


async def try_call(code, exception, ret=False, args=None, kwargs=None):
    try:
        return await maybe_coroutine(code, *args, **kwargs) if args or kwargs else await code
    except exception as e:
        return e if ret else None


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


class AfterGreedy(commands.Command):
    async def _transform_greedy_pos(self, ctx, param, required, converter):
        result = await super()._transform_greedy_pos(ctx, param, required, converter)
        if hasattr(converter, 'after_greedy'):
            return await converter.after_greedy(ctx, result)
        return result
