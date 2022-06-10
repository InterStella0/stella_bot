from __future__ import annotations

import itertools
import textwrap
from typing import Any, Optional, TYPE_CHECKING, List

import discord

from utils.buttons import BaseButton, InteractionPages, BaseView
from utils.cog import StellaCog
from utils.decorators import pages
from utils.modal import BaseModal
from utils.useful import StellaEmbed, StellaContext, plural
from .model import BotHelpMap, StellaCommands, CogEmoji

if TYPE_CHECKING:
    from main import StellaBot
    from .core import StellaHelpCommand


class CogSelector(discord.ui.Select):
    def __init__(self, mapping: BotHelpMap):
        super().__init__(placeholder="Select a category.")
        self.cog_mapping = {getattr(cog, "qualified_name", None): cog for cog, value in mapping.items()}
        self.options = [*itertools.starmap(self.create_option, mapping.items())]

    @staticmethod
    def create_option(cog: StellaCog, cmds: StellaCommands) -> discord.SelectOption:
        amount = len(cmds)
        value = getattr(cog, "qualified_name", None)
        no_none = value or "None"
        label = f'{value or "No category"}({amount})'
        description = getattr(cog, "description", "No documentation.")
        return discord.SelectOption(label=label, value=no_none, emoji=CogEmoji.get(no_none), description=description)

    async def callback(self, interaction: discord.Interaction) -> Any:
        value, = self.values
        key = value if value != "None" else None
        await self.view.selected_cog(interaction, self.cog_mapping[key])


class HomeButton(BaseButton):
    def __init__(self, view):
        super().__init__(emoji=CogEmoji.Home, label="Menu", stay_active=True, style=discord.ButtonStyle.green)
        self.ori_view = view

    async def callback(self, interaction: discord.Interaction) -> None:
        self.view.stop()
        view = self.ori_view
        view.reset_timeout()
        await interaction.response.edit_message(view=view, embed=view.embed)


class CogHelpPages(InteractionPages):
    def __init__(self, view: BotHelpView, cog: StellaCog, cmds: List[StellaCommands]):
        super().__init__(self.each_page_handler(cmds), message=view.message)
        self.view = view
        self.cog = cog
        self.add_item(HomeButton(view))

    @pages(per_page=5)
    async def each_page_handler(self, menu, cmds):
        title = getattr(menu.cog, "qualified_name", None)
        get_sig = menu.view.help_command.get_command_signature
        list_cmds = "\n".join([f"{get_sig(cmd)}\n{cmd.short_doc or 'No Documentation'}" for cmd in cmds])
        title = f"{CogEmoji.get(title)} {title or 'No category'}"
        return StellaEmbed.default(menu.ctx, title=title, description=list_cmds)


class BotHelpView(BaseView):
    def __init__(self, help_command: StellaHelpCommand, mapping: BotHelpMap):
        super().__init__()
        self.mapping = mapping
        self.help_command = help_command
        self.add_item(CogSelector(mapping))
        self.embed: Optional[StellaEmbed] = None
        self._search_prompt = None
        self.message = None

    async def create_embed(self):
        ctx = self.help_command.context
        bot: StellaBot = ctx.bot
        stella = bot.stella
        embed = StellaEmbed.default(
            ctx,
            title=f"{CogEmoji.Home} Help Command",
            description=ctx.bot.description.format(stella)
        )

        payload = {
            "bot_name": str(bot.user),
            "name": str(bot.stella),
            "author_avatar": ctx.author.display_avatar.url,
            "author_avatar_hash": ctx.author.display_avatar.key,
            "author_name": str(ctx.author)
        }

        banner = await bot.ipc_client.request("generate_banner", **payload)
        if isinstance(banner, str):
            embed.set_image(url=banner)
        return embed.set_author(name=f"By {stella}", icon_url=stella.display_avatar)

    async def start(self, ctx: StellaContext):
        self.embed = await self.create_embed()
        self.message = await ctx.maybe_reply(embed=self.embed, view=self)

    async def on_timeout(self) -> None:
        message = self.message
        if self.help_command.context.bot.get_message(message.id):
            await message.edit(view=None)

    async def selected_cog(self, interaction: discord.Interaction, cog: StellaCog):
        view = CogHelpPages(self, cog, self.mapping[cog])
        await view.start(self.help_command.context, interaction=interaction)

    class PromptSearch(BaseModal, title="Help Command Search"):
        text_command = discord.ui.TextInput(label="command", max_length=20)

        def __init__(self, view: BotHelpView):
            super().__init__()
            self.help_command = view.help_command
            self.original = view.message
            self.view = view

        async def on_submit(self, interaction: discord.Interaction) -> None:
            cmd = self.text_command.value.strip()
            if not cmd.strip():
                await interaction.response.send_message(content="I can't search an empty command", ephemeral=True)
                return

            if view := await self.help_command.search_command(cmd):
                message = f"Showing closest to `{cmd}` with :"
                view.add_item(HomeButton(self.view))
                await self.original.edit(content=message, embed=None, view=view)
            else:
                await interaction.response.send_message(f"No command with the name {cmd} found.", ephemeral=True)

    @discord.ui.button(emoji="<:search:945890885533573150>", label="Search Command", row=1,
                       style=discord.ButtonStyle.blurple)
    async def on_search_command(self, interaction: discord.Interaction, button: discord.ui.Button):
        prompter = self.get_prompt_search()
        await interaction.response.send_modal(prompter)

    @discord.ui.button(emoji="🗑️", row=1, style=discord.ButtonStyle.danger)
    async def on_removing(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(view=None)

    def get_prompt_search(self):
        if self._search_prompt is None:
            self._search_prompt = self.PromptSearch(self)

        return self._search_prompt

    def stop(self) -> None:
        super().stop()
        if self._search_prompt is not None:
            self._search_prompt.stop()


class HelpDropDown(discord.ui.Select):
    def __init__(self, cmds: List[StellaCommands]):
        self.commands = cmds = {cmd.name: cmd for cmd in cmds}
        options = [discord.SelectOption(
            label=cmd.name,
            description=textwrap.shorten(cmd.short_doc, width=80)
        ) for cmd in self.commands.values()]
        amount = len(cmds)
        value = plural(f'{amount} result(s)', amount)
        super().__init__(placeholder=value, min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if not self.values:
            return

        help_obj = self.view.help_command
        command_name, = self.values
        command = self.commands.get(command_name)
        embed = help_obj.get_command_help(command)
        await interaction.response.send_message(content=f"Help for **{command_name}**", embed=embed, ephemeral=True)


class HelpSearchView(BaseView):
    """This view class is specifically for command_callback method"""

    def __init__(self, help_object: StellaHelpCommand, cmds: List[StellaCommands]):
        super().__init__()
        self.help_command = help_object
        self.add_item(HelpDropDown(cmds))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        ctx = self.help_command.context
        if ctx.author == interaction.user:
            return True
        raise Exception(f"You can't use this. Only {ctx.author} can use this dropdown.")
