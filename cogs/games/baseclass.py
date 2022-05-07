from __future__ import annotations
from typing import TYPE_CHECKING, Dict

import discord
from discord.ext import commands


if TYPE_CHECKING:
    from main import StellaBot


class BaseGameCog(commands.Cog):
    def __init__(self, bot: StellaBot):
        self.bot = bot
        self.lewdle_query = "SELECT word FROM wordle_word WHERE tag='lewdle' AND LENGTH(word) = $1"
        self.button_rank_cache: Dict[int, discord.User] = {}
