import discord
import traceback
import sys
from discord.ext import commands
from utils import errors, useful
from utils.useful import BaseEmbed, print_exception


class ErrorHandler(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        """The event triggered when an error is raised while invoking a command.
        Parameters
        ------------
        ctx: commands.Context
            The context used for command invocation.
        error: commands.CommandError
            The Exception raised.
        """
        # This prevents any commands with local handlers being handled here in on_command_error.
        if hasattr(ctx.command, 'on_error'):
            return

        # This prevents any cogs with an overwritten cog_command_error being handled here.
        cog = ctx.cog
        if cog:
            if cog._get_overridden_method(cog.cog_command_error) is not None:
                return

        ignored = (commands.CommandNotFound,)

        # Allows us to check for original exceptions raised and sent to CommandInvokeError.
        # If nothing is found. We keep the exception passed to on_command_error.
        error = getattr(error, 'original', error)

        # Anything in ignored will return and prevent anything happening.
        if isinstance(error, ignored):
            return

        if isinstance(error, commands.DisabledCommand):
            await ctx.send(f'{ctx.command} has been disabled.')

        elif isinstance(error, commands.CommandOnCooldown):
            await ctx.send(embed=BaseEmbed.to_error(
                title="Cooldown Error",
                description=f"You're on cooldown. Retry after `{error.retry_after}` seconds"))
        else:
            # All other Errors not returned come here. And we can just print the default TraceBack.
            await ctx.send(embed=BaseEmbed.to_error(
                title="Error",
                description=f"{error}"
            ))
            print_exception(f'Ignoring exception in command {ctx.command}:', error)


def setup(bot):
    bot.add_cog(ErrorHandler(bot))
