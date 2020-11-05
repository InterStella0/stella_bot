import discord
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
