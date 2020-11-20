import discord
import re
import inspect
import typing_inspect
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
        if hasattr(ctx.command, 'on_error'):
            return

        cog = ctx.cog
        if cog:
            if cog._get_overridden_method(cog.cog_command_error) is not None:
                return

        ignored = (commands.CommandNotFound,)

        error = getattr(error, 'original', error)

        if isinstance(error, ignored):
            return

        if isinstance(error, commands.DisabledCommand):
            await ctx.send(f'{ctx.command} has been disabled.')

        elif isinstance(error, commands.CommandOnCooldown):
            await ctx.send(embed=BaseEmbed.to_error(
                title="Cooldown Error",
                description=f"You're on cooldown. Retry after `{error.retry_after}` seconds"))
        else:
            if template := await self.generate_signature_error(ctx, error):
                await ctx.send(embed=BaseEmbed.to_error(description=template))
            else:
                await ctx.send(embed=BaseEmbed.to_error(description=f"{error}"))
                print_exception(f'Ignoring exception in command {ctx.command}:', error)

    async def generate_signature_error(self, ctx, error):
        command = ctx.command
        argument = ""
        found = False
        if _class := getattr(error, "converter", None):
            signature = inspect.signature(command.callback).parameters
            for typing in signature.values():
                if typing_inspect.is_union_type(typing):
                    checking = typing.annotation.__args__
                else:
                    checking = (typing.annotation,)
                for convert in checking:
                    if convert is _class:
                        found = True
                        argument = typing.name
                        break
        elif isinstance(error, (commands.MissingRequiredArgument, commands.BadUnionArgument)):
            argument = error.param.name
            found = True
        if not found:
            return
        help_com = self.bot.help_command
        help_com.context = ctx
        real_signature = help_com.get_command_signature(command).replace("`", "")
        pos = 0
        for word in real_signature.split(" "):  # by this logic, getting pos = 0 isn't possible
            filtered = re.sub("<|>|\[|\]|\.", "", word)
            if argument == filtered:
                break
            pos += 1
        list_sig = real_signature.split(" ")
        target = list_sig[pos]
        target_list = list(target)
        alpha_index = [i for i, a in enumerate(target) if a.isalpha()]
        target_list[min(alpha_index)] = target_list[min(alpha_index)].capitalize()
        list_sig[pos] = "".join(target_list)
        space = " " * sum([len(x) + 1 for x in list_sig[:pos]])
        offset = " " * int((min(alpha_index) + 1 + (max(alpha_index) - min(alpha_index))) / 2)

        template = f"{error}" \
                   f"```prolog\n" \
                   f"{' '.join(list_sig)}\n" \
                   f"{space}{offset}^\n" \
                   f"```"
        return template


def setup(bot):
    bot.add_cog(ErrorHandler(bot))
