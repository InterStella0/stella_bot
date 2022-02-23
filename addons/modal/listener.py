import asyncio
import sys
from typing import Optional, List

import discord
from discord.ext import commands

from . import Modal
from . import InteractionType


class ModalStore:
    def __init__(self):
        self._modals = {}

    def get_modal(self, custom_id: str) -> Optional[List[Modal]]:
        return self._modals.get(custom_id)

    def add_modal(self, modal: Modal) -> None:
        modals = self._modals.setdefault(modal.custom_id, [])
        if modal in modals:
            return

        modals.append(modal)

    def remove_modal(self, modal: Modal) -> Optional[Modal]:
        modals = self.get_modal(modal.custom_id)
        if modals is None:
            return

        if not modals:
            self._modals.pop(modal.custom_id, None)
            return

        try:
            modals.remove(modal)
        except IndexError:
            pass

    def dispatch(self, custom_id: str, interaction: discord.Interaction) -> None:
        modals: Optional[List[Modal]] = self.get_modal(custom_id)
        if not modals:
            return

        for modal in modals:
            asyncio.create_task(modal.invoke(interaction))


class ModalListener(commands.Cog, name='Modal Listener'):
    def __init__(self, bot):
        self.bot = bot
        self.store = ModalStore()
        # bot._connection is a ConnectionState object that is available in Interaction._state
        # We do this for listening
        self.bot._connection._modal_store = self.store

    @commands.Cog.listener("on_interaction")
    async def _handle_modal_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.type.value == InteractionType.modal_submit.value:  # type: ignore
            interaction.type = InteractionType.modal_submit  # this is due to dpy not supporting it.
            custom_id = interaction.data.get('custom_id')
            self.store.dispatch(custom_id, interaction)

    def add_modal(self, modal: Modal) -> None:
        self.store.add_modal(modal)

    def remove_modal(self, modal: Modal) -> None:
        self.store.remove_modal(modal)


def setup(bot):
    try:
        bot.add_cog(ModalListener(bot))
    except Exception:
        print("Failure on adding listener to modal. Aborting...", file=sys.stderr)
        raise
