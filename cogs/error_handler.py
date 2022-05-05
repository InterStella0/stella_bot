from __future__ import annotations

import asyncio
import contextlib
import copy
import textwrap

from typing import TYPE_CHECKING, Any

import discord

from discord.ext import commands

from utils.buttons import BaseButton, ViewIterationAuthor
from utils.errors import BypassError, ErrorNoSignature
from utils.useful import StellaEmbed, multiget, print_exception

if TYPE_CHECKING:
    from main import StellaBot
    from utils.useful import StellaContext


class MissingButton(BaseButton):
    def __init__(self, error: commands.MissingRequiredArgument, embed: discord.Embed, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.error = error
        self.embed = embed

    async def callback(self, interaction: discord.Interaction) -> None:
        ctx = self.view.context
        param = self.error.param
        m = f"Please enter your argument for `{param.name}`."
        await interaction.response.edit_message(content=m, embed=None, view=None)

        def check(m: discord.Message) -> bool:
            return m.author == ctx.author and m.channel == ctx.channel
        with contextlib.suppress(asyncio.TimeoutError):
            message = await ctx.bot.wait_for('message', check=check, timeout=60)
            new_message = copy.copy(ctx.message)
            new_message.content += f" {message.content}"
            await ctx.bot.process_commands(new_message)


class ErrorHandler(commands.Cog):
    def __init__(self, bot: StellaBot):
        self.bot = bot
        self.error_cooldown = commands.CooldownMapping.from_cooldown(1, 20, commands.BucketType.user)

    @commands.Cog.listener()
    async def on_command_error(self, ctx: StellaContext, error: commands.CommandError) -> None:
        """The event triggered when an error is raised while invoking a command."""
        async def send_del(*args: Any, **kwargs: Any) -> None:
            if embed := kwargs.get("embed"):
                command = self.bot.get_command('report')
                command_sig = f"{ctx.clean_prefix}{command.qualified_name} {command.signature}"
                text = f"If you think this is an error. Report via `{command_sig}`"
                embed.set_footer(icon_url=self.bot.user.display_avatar, text=text)
            await ctx.reply(*args, delete_after=60, **kwargs)
            if ctx.channel.permissions_for(ctx.me).manage_messages:
                await ctx.message.delete(delay=60)

        async def handle_missing_param(template: discord.Embed) -> None:
            name = error.param.name
            payload = {
                "selected": name,
                "label": f"Enter required argument for '{name}'",
                "row": None,
                "style": discord.ButtonStyle.success
            }
            button = MissingButton(error, template, **payload)
            await send_del(embed=template, view=ViewIterationAuthor(ctx, [button]))

        if ctx.command and ctx.command.has_error_handler() and not isinstance(error, BypassError):
            return

        if cog := ctx.cog:
            if cog.has_error_handler():
                return

        ignored = (commands.CommandNotFound,)
        default_error = (commands.NotOwner, commands.TooManyArguments, ErrorNoSignature)

        error = getattr(error, 'original', error)

        if isinstance(error, ignored):
            return

        if isinstance(error, commands.DisabledCommand):
            await send_del(f'{ctx.command} has been disabled.')
        elif isinstance(error, commands.MaxConcurrencyReached):
            if error.per in (commands.BucketType.user, commands.BucketType.member):
                fmt = f"You can only use this command {error.number} at a time."
            else:
                fmt, *_ = str(error).partition(".")

            contexts = multiget(
                reversed(self.bot.cached_context),
                size=5,
                author__id=ctx.author.id,
                command__qualified_name=ctx.command.qualified_name
            )
            contexts = filter(lambda c: c is not ctx and c.done, contexts)

            def context_format(c):
                content, *_ = c.message.content.partition("\n")
                shorten = textwrap.shorten(content, 50)
                return f"[{shorten}]({c.message.jump_url})"

            formatted = reversed([*map(context_format, contexts)])
            embed = StellaEmbed.to_error(
                title="Concurrency Error",
                description="{}\n**Current Running Command(s):**\n{}".format(fmt, "\n".join(formatted))
            )
            await send_del(embed=embed)

        elif isinstance(error, commands.CommandOnCooldown):
            if await self.bot.is_owner(ctx.author):
                return await ctx.reinvoke()
            await send_del(embed=StellaEmbed.to_error(
                title="Cooldown Error",
                description=f"You're on cooldown. Retry after `{error.retry_after:.2f}` seconds")
            )
        elif isinstance(error, default_error):
            await send_del(embed=StellaEmbed.to_error(description=f"{error}"))
        else:
            if isinstance(error, commands.CommandError):
                try:
                    if template := await self.generate_signature_error(ctx, error):
                        if isinstance(error, commands.MissingRequiredArgument):
                            return await handle_missing_param(template)
                        return await send_del(embed=template)
                except Exception as e:
                    error = e  # Failure during signature generation.

            await send_del(embed=StellaEmbed.to_error(description=error))
            traceback_error = print_exception(f'Ignoring exception in command {ctx.command}:', error, _print=True)
            if not self.bot.tester:
                error_message = f"**Command:** `{ctx.message.content}`\n" \
                                f"**Message ID:** `{ctx.message.id}`\n" \
                                f"**Author:** `{ctx.author}`\n" \
                                f"**Guild:** `{ctx.guild}`\n" \
                                f"**Channel:** `{ctx.channel}`\n" \
                                f"**Jump:** [`jump`]({ctx.message.jump_url})```py\n" \
                                f"{traceback_error}```"
                await self.bot.error_channel.send(embed=StellaEmbed.default(ctx, description=error_message))

    async def generate_signature_error(self, ctx: StellaContext, error: commands.CommandError):
        command = ctx.command
        help_com = self.bot.help_command
        if help_com is None:
            return

        help_com.context = ctx
        real_signature = self.bot.get_command_signature(ctx, command)
        if ctx.current_parameter is None:
            if not isinstance(error, commands.MissingRequiredArgument):
                return

            ctx.current_parameter = error.param

        parameter = [*ctx.command.params.values()]
        pos = parameter.index(ctx.current_parameter) + 1
        list_sig = real_signature.split()
        try:
            pos += list_sig.index(ctx.invoked_with)
        except ValueError:  # It errors if a prefix does not have space, causing not in list error
            try:
                pos += list_sig.index(ctx.prefix + ctx.invoked_with)
            except ValueError:
                return

        target = list_sig[pos]
        target_list = list(target)
        alpha_index = [i for i, a in enumerate(target) if a.isalnum() or a in ("|", '"')]
        minimum, maximum = min(alpha_index), max(alpha_index)
        target_list[minimum] = target_list[minimum].capitalize()
        list_sig[pos] = "".join(target_list)
        space = " " * sum([len(x) + 1 for x in list_sig[:pos]])
        offset = " " * int((minimum + 1 + (maximum - minimum)) / 2)
        embed = StellaEmbed.to_error(description=f"```{error}```\n")
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
        embed.set_footer(
            icon_url=ctx.me.display_avatar,
            text=f"{ctx.clean_prefix}help {ctx.command.qualified_name} for more information."
        )
        return embed


async def setup(bot: StellaBot) -> None:
    await bot.add_cog(ErrorHandler(bot))
