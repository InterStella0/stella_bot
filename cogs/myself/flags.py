from typing import Optional, Tuple

import discord
from discord.ext import commands

from utils import flags as flg
from utils.new_converters import JumpValidator, DatetimeConverter


class AddBotFlag(commands.FlagConverter):
    joined_at: Optional[DatetimeConverter]
    jump_url: Optional[JumpValidator]
    requested_at: Optional[DatetimeConverter]
    reason: Optional[str]
    message: Optional[discord.Message]
    author: Optional[discord.Member]


class ClearFlag(commands.FlagConverter):
    must: Optional[bool] = flg.flag(default=True)
    messages: Optional[Tuple[discord.Message, ...]] = flg.flag(default=None)


class SQLFlag(commands.FlagConverter):
    not_number: Optional[bool] = flg.flag(aliases=["NN"], default=False)
    max_row: Optional[int] = flg.flag(aliases=["MR"], default=12)
