import asyncio
import sys
import traceback
from typing import Optional

import discord
from discord.ext import commands

from utils.modal import UpInteractionType, Modal


class ModalStore:
    def __init__(self):
        self._modals = {}

    def get_modal(self, custom_id: str) -> Optional[Modal]:
        return self._modals.get(custom_id)

    def add_modal(self, modal: Modal) -> None:
        self._modals[modal.custom_id] = modal

    def remove_modal(self, modal: Modal) -> Optional[Modal]:
        return self._modals.pop(modal.custom_id, None)

    def dispatch(self, custom_id: str, interaction: discord.Interaction) -> None:
        modal: Optional[Modal] = self.get_modal(custom_id)
        if modal:
            asyncio.create_task(modal.invoke(interaction))


class ModalListener(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.store = ModalStore()
        try:
            # bot._connection is a ConnectionState object that is available in Interaction._state
            # We do this for listening
            self.bot._connection._modal_store = self.store
        except Exception as e:
            # There is no reason for it to raise an error.
            print("Failure to attach store into ConnectionState:", e, file=sys.stderr)
            traceback.print_exc()

    @commands.Cog.listener("on_interaction")
    async def _handle_modal_interaction(self, interaction: discord.Interaction):
        if interaction.type.value == UpInteractionType.modal_submit.value:  # type: ignore
            interaction.type = UpInteractionType.modal_submit # this is due to dpy not supporting it.
            custom_id = interaction.data.get('custom_id')
            self.store.dispatch(custom_id, interaction)


def setup(bot):
    bot.add_cog(ModalListener(bot))