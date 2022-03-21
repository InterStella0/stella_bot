import time
import discord
from discord import ui


class BaseModal(ui.Modal):
    async def _scheduled_task(self, interaction: discord.Interaction):
        try:
            if self.timeout:
                self.__timeout_expiry = time.monotonic() + self.timeout

            allow = await self.interaction_check(interaction)
            if not allow:
                return

            await self.on_submit(interaction)

            if not interaction.response._responded:
                await interaction.response.defer()
        except Exception as e:
            return await self.on_error(e, interaction)