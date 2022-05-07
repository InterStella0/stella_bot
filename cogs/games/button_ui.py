from __future__ import annotations
from typing import TYPE_CHECKING

import discord
import humanize

from utils.useful import plural

if TYPE_CHECKING:
    from main import StellaBot


class ButtonGame(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Click", custom_id="click_game:click")
    async def on_click_click(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        query = "INSERT INTO button_game VALUES($1) " \
                "ON CONFLICT(user_id) " \
                "DO UPDATE SET amount = button_game.amount + 1 "
        client: StellaBot = interaction.client
        author = interaction.user.id
        await client.pool_pg.fetchrow(query, author)

    @discord.ui.button(label="Click Amount", custom_id="click_game:amount")
    async def on_amount_click(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(thinking=True, ephemeral=True)
        query = "SELECT * " \
                "FROM (" \
                "   SELECT  user_id, " \
                "           amount," \
                "           ROW_NUMBER() OVER (ORDER BY amount DESC) as rn" \
                "   FROM button_game" \
                "   GROUP BY user_id" \
                ") sorted_table " \
                "WHERE user_id = $1"

        value = await interaction.client.pool_pg.fetchrow(query, interaction.user.id)
        if value is None:
            await interaction.followup.send("You have no rank")
            return

        clicks = plural("click(s)", amount := value['amount'])
        await interaction.followup.send(f"You're rank {humanize.ordinal(value['rn'])} with `{amount}` {clicks}")


class UserUnknown(discord.Object):
    name = "Unknown User"
    discriminator = "0000"

    def __str__(self):
        return "{0.name}#{0.discriminator}".format(self)
