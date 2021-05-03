import discord
import re
import inspect
import typing_inspect
import contextlib
import traceback
from discord.ext import commands, flags
from utils.useful import BaseEmbed, print_exception, call
from utils.errors import NotInDpy


class ErrorHandler(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.error_cooldown = commands.CooldownMapping.from_cooldown(1, 20, commands.BucketType.user)

    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        """The event triggered when an error is raised while invoking a command."""
        async def send_del(*args, **kwargs):
            await ctx.reply(*args, delete_after=60, **kwargs)
            if ctx.channel.permissions_for(ctx.me).manage_messages:
                with contextlib.suppress(discord.NotFound):
                    await ctx.message.delete(delay=60)
        if hasattr(ctx.command, 'on_error'):
            return

        cog = ctx.cog
        if cog:
            if cog._get_overridden_method(cog.cog_command_error) is not None:
                return

        ignored = (commands.CommandNotFound,)
        default_error = (commands.NotOwner, commands.TooManyArguments, flags.ArgumentParsingError, NotInDpy,
                         commands.MaxConcurrencyReached)

        error = getattr(error, 'original', error)

        if isinstance(error, ignored):
            return

        if isinstance(error, commands.DisabledCommand):
            await send_del(f'{ctx.command} has been disabled.')

        elif isinstance(error, commands.CommandOnCooldown):
            if ctx.author == self.bot.stella:
                return await ctx.reinvoke()
            await send_del(embed=BaseEmbed.to_error(
                title="Cooldown Error",
                description=f"You're on cooldown. Retry after `{error.retry_after:.2f}` seconds")
            )
        elif isinstance(error, default_error):
            await send_del(embed=BaseEmbed.to_error(description=f"{error}"))
        else:
            if template := await self.generate_signature_error(ctx, error):
                await send_del(embed=template)
            else:
                await send_del(embed=BaseEmbed.to_error(description=f"{error}"))
                traceback_error = print_exception(f'Ignoring exception in command {ctx.command}:', error)
                if not self.bot.tester:
                    error_message = f"**Command:** {ctx.message.content}\n" \
                                    f"**Message ID:** `{ctx.message.id}`\n" \
                                    f"**Author:** `{ctx.author}`\n" \
                                    f"**Guild:** `{ctx.guild}`\n" \
                                    f"**Channel:** `{ctx.channel}`\n" \
                                    f"**Jump:** [`jump`]({ctx.message.jump_url})```py\n" \
                                    f"{traceback_error}\n" \
                                    f"```"
                    await self.bot.error_channel.send(embed=BaseEmbed.default(ctx, description=error_message))

    async def generate_signature_error(self, ctx, error):
        command = ctx.command
        help_com = self.bot.help_command
        help_com.context = ctx
        real_signature = help_com.get_command_signature(command, ctx)
        if ctx.current_parameter is None:
            return
        
        parameter = [*ctx.command.params.values()][ctx.command.cog is not None:]
        pos = parameter.index(ctx.current_parameter) 
        list_sig = real_signature.split()
        try:
            pos += list_sig.index(ctx.invoked_with)
        except ValueError:# It errors if a prefix does not have space, causing not in list error
            pass
        else:
            pos += list_sig.index(ctx.prefix + ctx.invoked_with)

        target = list_sig[pos]
        target_list = list(target)
        alpha_index = [i for i, a in enumerate(target) if a.isalnum() or a in ("|", '"')]
        minimum, maximum = min(alpha_index), max(alpha_index)
        target_list[minimum] = target_list[minimum].capitalize()
        list_sig[pos] = "".join(target_list)
        space = " " * sum([len(x) + 1 for x in list_sig[:pos]])
        offset = " " * int((minimum + 1 + (maximum - minimum)) / 2)
        embed = BaseEmbed.to_error(description=f"```{error}```\n")
        embed.description += f"**Errored at**\n" \
                             f"```prolog\n" \
                             f"{' '.join(list_sig)}\n" \
                             f"{space}{offset}^\n" \
                             f"```\n"
        if (demo := help_com.get_demo(command)) and isinstance(error, commands.MissingRequiredArgument):
            cooldown = self.error_cooldown
            bucket = cooldown.get_bucket(ctx.message)
            if not bucket.update_rate_limit():
                embed.description += "**Command Example**"
                embed.set_image(url=demo)
        embed.set_footer(icon_url=ctx.me.avatar.url, text=f"{ctx.clean_prefix}help {ctx.command.qualified_name} for more information.")
        return embed


def setup(bot):
    bot.add_cog(ErrorHandler(bot))
