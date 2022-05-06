from __future__ import annotations
import re
from typing import Optional, Union, TYPE_CHECKING

import discord
from discord.ext import commands

from cogs.find_bot.models import BotPending
from utils import flags as flg
from utils.useful import StellaContext

if TYPE_CHECKING:
    from main import StellaBot


class BotListReverse(commands.FlagConverter):
    reverse: Optional[bool] = flg.flag(aliases=["reverses"],
                                       help="Reverse the list order, this is False by default.", default=False)


class BotPendingFlag(commands.FlagConverter):
    reverse: Optional[bool] = flg.flag(aliases=["reverses"],
                                       help="Reverse the list order, this is False by default.", default=False)
    bot: Optional[BotPending] = flg.flag(help="Shows the bot's information on a specific page.")


def pprefix(bot_guild: Union[StellaBot, discord.Guild], prefix: str) -> str:
    if content := re.search("<@(!?)(?P<id>[0-9]*)>", prefix):
        method = getattr(bot_guild, ("get_user", "get_member")[isinstance(bot_guild, discord.Guild)])
        if user := method(int(content["id"])):
            return f"@{user.display_name} "
    return prefix


def clean_prefix(ctx: StellaContext, prefix: str) -> str:
    value = (ctx.guild, ctx.bot)[ctx.guild is None]
    prefix = pprefix(value, prefix)
    if prefix == "":
        prefix = "\u200b"
    return re.sub("`", "`\u200b", prefix)
