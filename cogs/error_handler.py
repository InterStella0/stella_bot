import discord
import re
import inspect
import typing_inspect
import contextlib
from discord.ext import commands
from utils.useful import BaseEmbed, print_exception


class ErrorHandler(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.error_cooldown = commands.CooldownMapping.from_cooldown(1, 20, commands.BucketType.user)

    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        """The event triggered when an error is raised while invoking a command."""
        async def send_del(*args, **kwargs):
            await ctx.reply(*args, delete_after=60, allowed_mentions=discord.AllowedMentions(replied_user=False), **kwargs)
            if ctx.me.permissions_in(ctx.channel).manage_messages:
                with contextlib.suppress(discord.NotFound):
                    await ctx.message.delete(delay=60)
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
            await send_del(f'{ctx.command} has been disabled.')

        elif isinstance(error, commands.CommandOnCooldown):
            await send_del(embed=BaseEmbed.to_error(
                title="Cooldown Error",
                description=f"You're on cooldown. Retry after `{error.retry_after}` seconds"))
        else:
            if template := await self.generate_signature_error(ctx, error):
                await send_del(embed=template)
            else:
                await send_del(embed=BaseEmbed.to_error(description=f"{error}"))
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
        real_signature = help_com.get_command_signature(command, ctx)
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
        if demo := help_com.get_demo(command) and isinstance(error, commands.MissingRequiredArgument):
            cooldown = self.error_cooldown
            bucket = cooldown.get_bucket(ctx.message)
            if not bucket.update_rate_limit():
                embed.description += "**Command Example**"
                embed.set_image(url=demo)
        embed.set_footer(icon_url=ctx.guild.me.avatar_url, text="The argument that was capitalize is the error.")
        return embed


def setup(bot):
    bot.add_cog(ErrorHandler(bot))
