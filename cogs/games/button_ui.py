from __future__ import annotations

import operator
import re
from typing import TYPE_CHECKING, Dict

import discord
import humanize

from utils.useful import plural

if TYPE_CHECKING:
    from main import StellaBot


class ButtonGame(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @staticmethod
    def set_user_desc(interaction: discord.Interaction, description: str, amount: int):
        client: StellaBot = interaction.client
        username = str(interaction.user)
        lists = [*re.finditer(r"\d+\. (?P<user>(.{2,32})#\d{4}): \(`(?P<click>\d+)`\)", description)]
        to_insert = {"user": username, "click": amount}
        user_in_list = False
        for i, found in enumerate(lists):
            user = found["user"]
            clicks = int(found["click"])
            if user == username:
                lists[i] = to_insert
                user_in_list = True
                continue

            if (user_amount := client.button_click_cached.get(user)) is not None:
                lists[i] = {"user": user, "click": user_amount}
            else:
                client.button_click_cached[user] = clicks

        client.button_click_cached[username] = amount
        if not user_in_list:
            lists.append(to_insert)

        lists.sort(key=lambda x: int(x["click"]), reverse=True)
        return "\n".join(f"{i}. {x['user']}: (`{x['click']}`)" for i, x in enumerate(lists, start=1))

    @discord.ui.button(emoji='🖱️', label="Click", custom_id="click_game:click", style=discord.ButtonStyle.success)
    async def on_click_click(self, interaction: discord.Interaction, button: discord.ui.Button):
        query = "INSERT INTO button_game VALUES($1) " \
                "ON CONFLICT(user_id) " \
                "DO UPDATE SET amount = button_game.amount + 1 " \
                "RETURNING *"
        client: StellaBot = interaction.client
        author = interaction.user.id
        values = await client.pool_pg.fetchrow(query, author)
        message = interaction.message or await interaction.original_message()
        embed, *_ = message.embeds
        seconds = embed.description.splitlines()[:2]
        form_list = f"\n{self.set_user_desc(interaction, embed.description, values['amount'])}"
        embed.description = "\n".join(seconds) + form_list
        await interaction.response.edit_message(embed=embed)

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
