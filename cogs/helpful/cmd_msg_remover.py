from __future__ import annotations
from typing import Optional, Callable, TYPE_CHECKING

import discord
from discord.ext import commands

from utils.decorators import event_check
from utils.useful import StellaContext

if TYPE_CHECKING:
    from main import StellaBot


def message_getter(message_id: int) -> Callable[[StellaContext], Optional[discord.Message]]:
    def inner(context: StellaContext) -> Optional[discord.Message]:
        return context.get_message(message_id)

    return inner


def is_message_older_context(bot: StellaBot, message_id: int) -> bool:
    if not (cached_context := bot.cached_context):
        return False

    return message_id < cached_context[0].message.id


def is_command_message():
    def inner(self, payload):
        bot = self.bot
        if is_message_older_context(bot, payload.message_id):
            return False

        return discord.utils.get(bot.cached_context, message__id=payload.message_id) is not None

    return event_check(inner)


def is_message_context():
    async def inner(self, payload):
        bot = self.bot
        if is_message_older_context(bot, payload.message_id):
            return False

        return discord.utils.find(message_getter(payload.message_id), bot.cached_context)

    return event_check(inner)


class CommandMessageRemoverHandler(commands.Cog):
    @commands.Cog.listener("on_raw_bulk_message_delete")
    async def remove_context_messages(self, payload: discord.RawBulkMessageDeleteEvent):
        bot = self.bot
        for message_id in payload.message_ids:
            if is_message_older_context(bot, message_id):
                continue

            if ctx := discord.utils.find(message_getter(message_id), bot.cached_context):
                ctx.remove_message(message_id)

    @commands.Cog.listener("on_raw_message_delete")
    @is_message_context()
    async def remove_context_message(self, payload: discord.RawMessageDeleteEvent):
        target = payload.message_id
        if ctx := discord.utils.find(message_getter(target), self.bot.cached_context):
            ctx.remove_message(target)

    @commands.Cog.listener("on_raw_message_delete")
    @is_command_message()
    async def on_command_delete(self, payload: discord.RawMessageDeleteEvent):
        context = discord.utils.get(self.bot.cached_context, message__id=payload.message_id)
        await context.delete_all()