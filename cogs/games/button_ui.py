from __future__ import annotations

import contextlib
import random
import re
import string
from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict

import discord
import humanize
from discord.ext import commands

from utils.useful import plural

if TYPE_CHECKING:
    from main import StellaBot


@dataclass
class CooldownUser:
    channel: discord.TextChannel
    author: discord.User

    @classmethod
    def from_interaction(cls, interaction: discord.Interaction):
        return cls(interaction.channel, interaction.user)


class ButtonGame(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.cooldown_update_message = commands.CooldownMapping.from_cooldown(2, 5, commands.BucketType.channel)
        self.cooldown_for_random = commands.CooldownMapping.from_cooldown(10, 180, commands.BucketType.channel)

    subscript = "⁰¹²³⁴⁵⁶⁷⁸⁹"

    @staticmethod
    def to_subscript(value: int) -> str:
        mapped = [string.digits.index(s) for s in str(value)]
        return "⁺" + "".join([ButtonGame.subscript[i] for i in mapped])

    @staticmethod
    def set_user_desc(interaction: discord.Interaction, description: str, amount: int):
        client: StellaBot = interaction.client
        username = str(interaction.user)

        regex = r"\d+\. (?P<user>(.{2,32})#\d{4}): \(`(?P<click>\d+)(⁺(?P<add>[⁰¹²³⁴⁵⁶⁷⁸⁹]+))?`\)(?P<total>\[`\d+`\])"
        lists = [*re.finditer(regex, description)]
        to_insert = {"user": username, "click": amount, "add": ButtonGame.to_subscript(0), "total": amount}
        user_in_list = False
        for i, found in enumerate(lists):
            user = found["user"]
            click = int(found["click"])
            if user == username:
                to_insert["add"] = ButtonGame.to_subscript(amount - click)
                to_insert["click"] = click
                lists[i] = to_insert
                user_in_list = True
                continue

            if (user_amount := client.button_click_cached.get(user)) is not None:
                add = ButtonGame.to_subscript(user_amount - click)
                lists[i] = {"user": user, "click": click, "add": add, "total": user_amount}

        client.button_click_cached[username] = amount
        if not user_in_list:
            lists.append(to_insert)

        lists.sort(key=lambda x: int(x["click"]), reverse=True)
        return "\n".join(f"{i}. {x['user']}: (`{x['click']}{x['add']}`)[`{x['total']}`]" for i, x in enumerate(lists, start=1))

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
        obj = CooldownUser.from_interaction(interaction)
        per_channel = self.cooldown_update_message.update_rate_limit(obj)
        per_user = client.cooldown_user_click.update_rate_limit(obj)
        with contextlib.suppress(discord.HTTPException):
            if not (per_channel or per_user):
                await interaction.response.edit_message(embed=embed, view=self)
            elif not per_user:
                if self.cooldown_for_random.update_rate_limit(obj):
                    self.randomize_pos(obj)
                await interaction.response.defer()

        await client.pool_pg.execute("INSERT INTO click_game_logger VALUES($1)", author)
        # Dont respond

    def randomize_pos(self, obj: CooldownUser):
        bucket = self.cooldown_for_random.get_bucket(obj)
        bucket.reset()
        buttons = [discord.ui.Button(label="Decoy") for _ in range(3)]
        buttons.append(self.on_amount_click)
        buttons.append(self.on_click_click)
        random.shuffle(buttons)
        self.clear_items()
        for button in buttons:
            self.add_item(button)

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
