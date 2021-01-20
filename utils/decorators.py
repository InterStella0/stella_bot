import discord
import functools
from discord.ext import commands
from utils.errors import NotInDpy


def is_discordpy(silent=False):
    """A check that only allows certain command to be only be invoked in discord.py server. Otherwise it is ignored."""
    async def predicate(ctx):
        if ctx.guild and ctx.guild.id == 336642139381301249:
            return True
        else:
            if not silent:
                raise NotInDpy()
            else:
                raise
    return commands.check(predicate)


def event_check(func):
    """Event decorator check."""
    def check(method):
        method.callback = method

        @functools.wraps(method)
        async def wrapper(*args, **kwargs):
            if await discord.utils.maybe_coroutine(func, *args, **kwargs):
                await method(*args, **kwargs)
        return wrapper
    return check


def wait_ready(bot=None):
    async def predicate(*args, **_):
        nonlocal bot
        self = args[0] if args else None
        if isinstance(self, commands.Cog):
            bot = bot or self.bot
        if not isinstance(bot, commands.Bot):
            raise Exception(f"bot must derived from commands.Bot not {bot.__class__.__name__}")
        await bot.wait_until_ready()
        return True
    return event_check(predicate)