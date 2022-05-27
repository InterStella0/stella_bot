from __future__ import annotations
from typing import Optional, TYPE_CHECKING, Dict

import discord
from discord import ui
from discord.ext import commands
from jishaku.codeblocks import Codeblock

from cogs.helpful.baseclass import BaseHelpfulCog
from utils import flags as flg
from utils.decorators import in_executor, pages
from utils.errors import ErrorNoSignature
from utils.new_converters import CodeblockConverter
from utils.parser import ReplReader, repl_wrap
from utils.buttons import InteractionPages, button, ViewAuthor, ButtonView
from utils.greedy_parser import command, UntilFlag
from utils.modal import BaseModal
from utils.useful import StellaContext, StellaEmbed, text_chunker

if TYPE_CHECKING:
    from main import StellaBot


@pages()
async def formatter(self, menu, entry):
    return f"```py\n{entry}```"


class Paginator(InteractionPages):
    def __init__(self, source, ori_view):
        super().__init__(source, message=ori_view.message, delete_after=False)
        self.ori_view = ori_view

    @button(emoji='<:stop_check:754948796365930517>', style=discord.ButtonStyle.blurple)
    async def stop_page(self, interaction: discord.Interaction, __: ui.Button) -> None:
        if self.delete_after:
            await self.message.delete(delay=0)
            return

        for x in self.children:
            if not isinstance(x, ui.Button) or x.label != "Menu":
                x.disabled = True

        await interaction.response.edit_message(view=self)

    @button(emoji="<:house_mark:848227746378809354>", label="Menu", row=1, stay_active=True)
    async def on_menu_click(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.edit_message(content=None, embed=self.ori_view.form_embed(), view=self.ori_view)
        self.stop()


class EvalModal(BaseModal, title="Python Mobile Eval"):
    code = discord.ui.TextInput(label="code", style=discord.TextStyle.long, placeholder="Enter your code here")
    output = discord.ui.TextInput(label="output", style=discord.TextStyle.long, required=False,
                                  placeholder="Execution Output")

    def __init__(self, view):
        super().__init__()
        self.view = view

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if len(self.code.value.strip()) == 0:
            await interaction.response.send_message("No Code Given", ephemeral=True)
            return False
        if self.view.context.author.id != interaction.user.id:
            await interaction.response.send_message(f"Only {interaction.user} can respond to this modal.",
                                                    ephemeral=True)
            return False
        return True

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True, ephemeral=True)
        await self.view.execute_python(interaction)


class EvalView(ViewAuthor):
    def __init__(self, ctx: StellaContext):
        super().__init__(ctx)
        self.modal = None
        self.previous_output = None
        self.message = None
        self.execution_count = 0

    def form_embed(self):
        description = ("press 'Enter' to enter your code.\n"
                       "press 'Full Output' to view full output.\n"
                       "press 'Stop' to stop the menu.")
        if count := self.execution_count:
            description = f"Execution Count: `{count:,}`\n" + description
        return StellaEmbed.default(self.context, title="Python Eval Menu", description=description)

    async def start(self):
        self.message = await self.context.maybe_reply(embed=self.form_embed(), view=self)
        await self.wait()

    async def show_modal(self, interaction: discord.Interaction):
        if self.modal is None:
            self.modal = EvalModal(self)

        await interaction.response.send_modal(self.modal)

    @staticmethod
    def shorten_output(width: int, output: str):
        leading = ""
        if (amount := len(output)) > width:
            left = amount - width
            leading = f"... ({left:,} characters left)"
        return output[:width] + leading

    async def setting_python(self, code: str):
        return code

    async def execute_python(self, interaction: discord.Interaction):
        code = self.modal.code.value
        prepared = await self.setting_python(code)
        result = await interaction.client.stella_api.execute_python(prepared)
        if (output := result.get("output")) is not None:
            output = output or "No Output"
        else:
            output = result.get("reason") or "Execution Failure"

        self.execution_count += 1
        self.modal.output.default = self.shorten_output(3900, output)
        self.modal.code.default = code
        formatted = f"```py\n{self.shorten_output(1900, output)}```"
        await interaction.followup.send(formatted, ephemeral=True)
        to_edit = {'embed': self.form_embed()}
        if self.previous_output is None:
            discord.utils.get(self.children, label="Full Output").disabled = False
            to_edit['view'] = self

        self.previous_output = output
        await self.message.edit(**to_edit)

    @discord.ui.button(emoji='\U000023ef', label="Enter", style=discord.ButtonStyle.success)
    async def on_enter_click(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self.show_modal(interaction)

    @discord.ui.button(emoji='\U0001f5a5', label="Full Output", disabled=True)
    async def on_output_click(self, interaction: discord.Interaction, _: discord.ui.Button):
        if self.previous_output is None:
            await interaction.response.send_message("No Previous Output", ephemeral=True)
            return

        await interaction.response.defer()
        text = text_chunker(self.previous_output, width=1900, max_newline=20)
        pager = Paginator(formatter(text), self)
        await pager.start(self.context)

    @discord.ui.button(emoji='<:stop_check:754948796365930517>', label="Stop", style=discord.ButtonStyle.danger)
    async def on_stop_click(self, interaction: discord.Interaction, __: discord.ui.Button):
        await interaction.response.send_message("Stopping...", ephemeral=True)
        self.stop()

    async def on_stop(self):
        for item in self.children:
            item.disabled = True

        if self.context.bot.get_message(self.message.id):
            await self.message.edit(view=self)

    async def on_timeout(self) -> None:
        self.stop()

    def stop(self):
        self.context.bot.loop.create_task(self.on_stop())
        super().stop()
        if (modal := self.modal) is not None:
            modal.stop()


class ReplView(EvalView):
    def __init__(self, ctx: StellaContext, cog: EvalHandler, **flags: bool):
        super().__init__(ctx)
        self.cog = cog
        self.flags = flags

    async def setting_python(self, code: str):
        codeblock = Codeblock("py", code)
        return await self.cog.get_wrapped(self.context, codeblock, **self.flags)


class EvalHandler(BaseHelpfulCog):
    @command(name="eval", aliases=["e"],
             brief="Public python eval execution in discord.",
             help="Public python eval execution in discord. Using this command without argument will activate the "
                  "mobile mode.")
    @commands.max_concurrency(1, commands.BucketType.user)
    async def _eval(self, ctx: StellaContext, *,
                    code: Optional[Codeblock] = commands.param(converter=CodeblockConverter, default=None)):
        await self.repl_handler(ctx, code, exec=True, symbol_mode=False, inner_func_check=False, counter=False)

    @in_executor()
    def get_wrapped(self, ctx: StellaContext, code: Codeblock, **flags: Optional[bool]):
        if ctx.guild:
            guild_values = [{"channel__id": c.id, "channel__name": c.name, "guild__id": c.guild.id}
                            for c in ctx.guild.text_channels]
            user_values = [{"user__id": u.id, "user__name": u.name, "user__nick": u.nick, "user__bot": u.bot,
                            "user__discriminator": u.discriminator}
                           for u in ctx.guild.members[:100]]
            message_values = [{"message__id": m.id, "message__content": m.content,
                               "message__author": m.author.id, "channel_id": m.channel.id,
                               "guild__id": m.guild.id}
                              for m in self.bot.cached_messages if m.guild == ctx.guild][:100]
        else:
            c = ctx.channel
            u = ctx.author
            guild_values = [{"channel__id": c.id, "channel__name": None, "guild__id": None}]
            user_values = [{"user__id": u.id, "user__name": u.name, "user__nick": None, "user__bot": u.bot,
                            "user__discriminator": u.discriminator}]
            message_values = [{"message__id": m.id, "message__content": m.content, "message__author": u.id,
                               "channel_id": m.channel.id, "guild__id": m.guild}
                              for m in self.bot.cached_messages if m.channel.id == ctx.channel.id]
        context = {
            "context": {
                "channel_id": ctx.channel.id,
                "message_id": ctx.message.id,
                "bot__id": ctx.me.id,
                "prefix": ctx.clean_prefix
            },
            "_bot": {
                "channels": guild_values,
                "guilds": [{"guild__id": ctx.guild.id, "guild__name": ctx.guild.name}] if ctx.guild else []
            },
            "members": user_values,
            "cached_messages": message_values
        }  # Allowed variables to be passed

        return repl_wrap(code.content, context, **flags)

    @command(help="Simulate a live python interpreter interface when given a python code. Please use this public eval"
                  "wisely as any execution that takes >= 3 seconds are terminated. Using this command "
                  "without argument will activate the mobile mode.")
    @commands.max_concurrency(1, commands.BucketType.user)
    async def repl(self, ctx: StellaContext, *,
                   code: Optional[Codeblock] = commands.param(converter=CodeblockConverter, default=None)):
        await self.repl_handler(ctx, code, exec=True, inner_func_check=False, counter=False)

    def repl_handler(self, ctx: StellaContext, code: Optional[Codeblock], **flags):
        if code is None:
            return ReplView(ctx, self, **flags).start()
        else:
            return self.execute_repl(ctx, code, **flags)

    async def execute_repl(self, ctx: StellaContext, code: Codeblock, **flags: Optional[bool]):
        globals_ = {
            'ctx': ctx,
            'author': ctx.author,
            'guild': ctx.guild,
            'bot': self.bot,
            'discord': discord,
            'commands': commands
        }
        if code.language is None:
            content = code.content
            code = Codeblock("py", f"\n{content}\n")

        code = Codeblock(code.language, code.content.rstrip("\n"))
        if flags.get('exec') and not await self.bot.is_owner(ctx.author):
            coded = await self.get_wrapped(ctx, code, **flags)
            accepted = await self.bot.stella_api.execute_python(code=coded)
        else:
            code = "\n".join([o async for o in ReplReader(code, _globals=globals_, **flags)])
            accepted = {"output": code}

        concurrent = ctx.command._max_concurrency
        await concurrent.release(ctx.message)
        await self._handle_output(ctx, accepted)

    async def _handle_output(self, ctx: StellaContext, accepted: Dict[str, str]):
        if (output := accepted.get("output")) is not None:
            code = output
        elif reason := accepted.get("reason"):
            raise ErrorNoSignature(reason)
        else:
            raise ErrorNoSignature(f"It died sorry dan maaf")

        text = text_chunker(code, width=1900, max_newline=20)

        if len(text) > 1:
            menu = InteractionPages(formatter(text))
            await menu.start(ctx)
        elif len(text) == 1:
            code, = text
            view = ButtonView(ctx)
            await ctx.maybe_reply(f"```py\n{code}```", view=view, allowed_mentions=discord.AllowedMentions.none())
        else:
            await ctx.maybe_reply(f"```py\nNo Output```")

    @command(help="A timeit command for your python code. Execution timeout are set to 3 seconds. Using this command "
                  "without argument will activate the mobile mode.")
    @commands.max_concurrency(1, commands.BucketType.user)
    async def timeit(self, ctx: StellaContext, *,
                     code: Optional[Codeblock] = commands.param(converter=CodeblockConverter, default=None)):
        await self.repl_handler(ctx, code, exec=True, exec_timer=True, inner_func_check=False, counter=False)
