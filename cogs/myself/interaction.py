import time
from typing import List, Optional, Awaitable, Any

import discord
from discord.ext import commands

from utils.buttons import InteractionPages, button
from utils.decorators import pages
from utils.modal import BaseModal
from utils.useful import StellaEmbed, aware_utc


class InputServer(BaseModal, title="Input a Server"):
    server = discord.ui.TextInput(label="Server", placeholder="Target Server (ex. name, id)")

    def __init__(self, view, **kwargs):
        super().__init__(**kwargs)
        self.view = view
        self.selections: List[discord.Guild] = view._source.entries
        self.value: Optional[discord.Guild] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        value = self.server.value
        guild = discord.utils.find(lambda x: str(x) == value or value.isdigit() and x.id == int(value), self.selections)
        if not guild:
            raise commands.CommandError(f'"{self.server}" does not exist.')

        self.value = guild
        return True

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await interaction.response.send_message(error, ephemeral=True)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await self.view.select_guild(self.value)


class ConfirmServer(discord.ui.Modal, title="Server leave confirmation"):
    server = discord.ui.TextInput(label='Type "{}"')

    def __init__(self, view):
        super().__init__()
        server = view._source.entries[view.current_page]
        self.server.label = self.server.label.format(server)
        self.server.placeholder = str(server)
        self.server_selected = server
        self.owner = view.ctx.author
        self.view = view

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user != self.owner:
            raise commands.CommandError("You cannot fill this form.")

        if self.server.value != str(self.server_selected):
            raise commands.CommandError("Incorrect server written.")

        return True

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await interaction.response.send_message(error, ephemeral=True)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(f'Leaving "{self.server}" now...', ephemeral=True)
        await self.server_selected.leave()
        source = self.view._source
        entries = source.entries
        entries.remove(self.server_selected)
        current_page = max(self.view.current_page - 1, 0)
        await self.view.change_source(type(source)(entries))
        await self.view.show_checked_page(current_page)


class InteractionServers(InteractionPages):
    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._input_server = None

    def selecting_server(self, interaction: discord.Interaction) -> Awaitable[None]:
        prompt_timeout = 60
        self.timeout = time.monotonic() + self.timeout + prompt_timeout
        if self._input_server is None:
            self._input_server = InputServer(self, timeout=prompt_timeout)

        return interaction.response.send_modal(self._input_server)

    def stop(self) -> None:
        super().stop()
        if self._input_server is not None:
            self.stop()

    async def select_guild(self, guild: discord.Guild):
        values = self._source.entries
        index = values.index(guild)
        await self.show_checked_page(index)
        self.reset_timeout()

    @button(label="Select Server", row=1, stay_active=True)
    async def select_server(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self.selecting_server(interaction)

    @button(label="Leave Server", style=discord.ButtonStyle.danger, row=2, stay_active=True)
    async def leave_server(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.send_modal(ConfirmServer(self))


@pages()
async def show_server(self, menu: InteractionServers, server: discord.Guild):
    amount_users = sum([not b.bot for b in server.members])
    amount_bots = sum([b.bot for b in server.members])
    return StellaEmbed.default(
        menu.ctx, title=server,
        description=f"**Owner:** `{server.owner}` (`{server.owner_id}`)\n"
                    f"**Created At:** {aware_utc(server.created_at)}\n"
                    f"**Joined At:** {aware_utc(server.me.joined_at)}\n"
                    f"**Users:** `{amount_users:,}`\n"
                    f"**Bots:** `{amount_bots:,}`\n"
                    f"**Total:** `{server.member_count:,}`\n"
    )
