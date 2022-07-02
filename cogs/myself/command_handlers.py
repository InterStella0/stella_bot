import contextlib
from typing import Union

import discord
from discord.ext import commands

from utils.decorators import event_check, pages
from .baseclass import BaseMyselfCog
from utils import greedy_parser, flags as flg, menus
from utils.buttons import InteractionPages
from utils.useful import StellaContext, print_exception, text_chunker


@pages()
async def show_result(self, menu: menus.MenuBase, entry: str) -> str:
    return f"```py\n{entry}```"


class CommandHandlers(BaseMyselfCog):
    @commands.command()
    async def su(self, ctx: StellaContext, member: Union[discord.Member, discord.User], *, content: str):
        message = ctx.message
        message.author = member
        message.content = ctx.prefix + content
        self.bot.dispatch("message", message)
        await ctx.confirmed()

    @greedy_parser.command()
    async def reinvoke(self, ctx: StellaContext, command: greedy_parser.UntilFlag[str], *, flags: flg.ReinvokeFlag):
        message = ctx.message
        message.author = flags.user or ctx.author
        message.content = ctx.prefix + command
        context = await self.bot.get_context(message)
        try:
            c_flags = dict(flags)
            if c_flags.pop("redirect", True):
                c_flags["redirect_error"] = True
                c_flags["dispatch"] = False
            await self.bot.invoke(context, in_task=False, **c_flags)
            await ctx.confirmed()
        except commands.CommandError as e:
            error = print_exception(f'Exception raised while reinvoking {context.command}:', e, _print=False)
            chunked = text_chunker(error, max_newline=10)
            await InteractionPages(show_result(chunked)).start(ctx)

    @commands.command()
    async def cancel(self, ctx: StellaContext, message: Union[discord.Message, discord.Object]):
        with contextlib.suppress(KeyError):
            task = self.bot.command_running.pop(message.id)
            if task is not None and not task.done():
                task.cancel()
                await message.reply("This command was cancelled.")
            else:
                await ctx.maybe_reply("This command was already done.")
            return await ctx.confirmed()
        await ctx.maybe_reply("Unable to find a running command from this message.")

    @commands.Cog.listener()
    @event_check(lambda s, b, a: (b.content and a.content) or b.author.bot)
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if await self.bot.is_owner(before.author) and not before.embeds and not after.embeds:
            if context := discord.utils.find(lambda ctx: ctx.message == after, self.bot.cached_context):
                await context.reinvoke(message=after)
            else:
                await self.bot.process_commands(after)
