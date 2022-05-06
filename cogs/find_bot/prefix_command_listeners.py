from __future__ import annotations

import asyncio
import collections
import contextlib
import ctypes
import datetime
import itertools
import re
import textwrap
from typing import Callable, Union, Dict, Tuple, Coroutine, Any, List, Optional

import discord
from discord.ext import commands

from .baseclass import FindBotCog
from .decorators import is_user, deco_event
from utils.decorators import listen_for_guilds, wait_ready, event_check
from utils.useful import compile_array, search_commands, search_prefixes

ReactRespond = collections.namedtuple("ReactRespond", "created_at author reference")


def prefix_cache_ready() -> deco_event:
    """Event check for command_count"""
    def predicate(self, message: discord.Message) -> bool:
        return self.compiled_prefixes and self.compiled_commands and not message.author.bot
    return event_check(predicate)


class PrefixCommandListeners(FindBotCog):
    async def loading_all_prefixes(self) -> None:
        """Loads all unique prefix when it loads and set compiled_pref for C code."""
        await self.bot.wait_until_ready()
        prefix_data = await self.bot.pool_pg.fetch("SELECT DISTINCT bot_id, prefix FROM prefixes_list")
        commands_data = await self.bot.pool_pg.fetch("SELECT DISTINCT bot_id, command FROM commands_list")
        for prefix, command in itertools.zip_longest(prefix_data, commands_data):
            if prefix:
                prefix_list = self.all_bot_prefixes.setdefault(prefix["bot_id"], set())
                prefix_list.add(prefix["prefix"])
            if command:
                command_list = self.all_bot_commands.setdefault(command["bot_id"], set())
                command_list.add(command["command"])
        self.update_compile()

    def update_compile(self) -> None:
        temp = [*{prefix for prefix_list in self.all_bot_prefixes.values() for prefix in prefix_list}]
        cmds = [*{command for command_list in self.all_bot_commands.values() for command in command_list}]
        self.compiled_prefixes = compile_array(sorted(temp))
        self.compiled_commands = compile_array(sorted(x[::-1] for x in cmds))

    async def listen_for_bots_at(self, message: discord.Message, message_check: Callable[[discord.Message], bool]) -> \
            Tuple[Dict[int, Union[discord.Message, ReactRespond]], Dict[int, Union[discord.Message, ReactRespond]]]:
        """Listens for bots responding and terminating when a user respond"""
        bots = {}
        after_user = {}
        time_to_listen = message.created_at + datetime.timedelta(seconds=5)
        flip = 0

        def reaction_add_check(reaction: discord.Reaction, _: discord.User) -> bool:
            return reaction.message == message

        stuff_here = locals()
        with contextlib.suppress(asyncio.TimeoutError):
            while time_to_listen > (time_rn := discord.utils.utcnow()):
                time_left = (time_to_listen - time_rn).total_seconds()
                done, didnt = await asyncio.wait(
                    [self.bot.wait_for(event, check=stuff_here[f"{event}_check"], timeout=time_left)
                     for event in ("reaction_add", "message")],
                    return_when=asyncio.FIRST_COMPLETED
                )
                for coro in done:
                    coro.exception()

                responded = done.pop().result()
                if isinstance(responded, tuple):
                    responded = ReactRespond(datetime.datetime.utcnow(), responded[1], None)

                for coro in didnt:
                    coro.cancel()

                if any(responded.author.id in respondance for respondance in (bots, after_user)):
                    continue
                flip |= not responded.author.bot
                if not responded.author.bot:
                    continue

                if not flip:
                    bots.update({responded.author.id: responded})
                elif getattr(responded.reference, "cached_message", None) == message:
                    after_user.update({responded.author.id: responded})

        return bots, after_user

    async def update_prefix_bot(self, message: discord.Message, func: Callable[[discord.Message], bool],
                                prefix: str, command: str) -> None:
        """Updates the prefix of a bot, or multiple bot where it waits for the bot to respond. It updates in the database."""

        def setting(inner):
            def check(msg):
                return msg.channel == message.channel and not msg.author.bot or inner(msg)

            return check

        message_sent, after = await self.listen_for_bots_at(message, setting(func))
        if not message_sent and not after:
            return

        message_sent.update(after)
        bots_responded = list(message_sent)
        # Possibility of duplication removal
        exist_query = "SELECT * FROM prefixes_list WHERE guild_id=$1 AND bot_id=ANY($2::BIGINT[])"
        existing = await self.bot.pool_pg.fetch(exist_query, message.guild.id, bots_responded)
        for x in existing:
            if prefix.startswith(x["prefix"]) and x["bot_id"] in bots_responded:
                message_sent.pop(x["bot_id"], None)

        if not message_sent:
            return

        prefix_list = [(message.guild.id, x, prefix, 1, m.created_at.replace(tzinfo=None)) for x, m in
                       message_sent.items()]
        command_list = [(message.guild.id, x, command, m.created_at.replace(tzinfo=None)) for x, m in
                        message_sent.items()]

        await self.insert_both_prefix_command(prefix_list, command_list)

        for _, x, prefix, _, _ in prefix_list:
            prefix_list = self.all_bot_prefixes.setdefault(x, set())
            prefix_list.add(prefix)

        for _, bot, command, _ in command_list:
            command_list = self.all_bot_commands.setdefault(bot, set())
            command_list.add(command)

        self.update_compile()

    @commands.Cog.listener("on_message")
    @wait_ready()
    @listen_for_guilds()
    @is_user()
    async def find_bot_prefixes(self, message: discord.Message):
        """This function is responsible for point of entry of the bot detection. All bot must went into here
           in order to be detected."""

        def check_jsk(m):
            possible_text = ("Jishaku", "discord.py", "Python ", "Module ", "guild(s)", "user(s).")
            return all(text in m.content for text in possible_text)

        def search(*text_list):
            def actual_search(search_text):
                return any(f"{t}" in search_text.casefold() for t in text_list)

            return actual_search

        def check_help(m):
            target = search("command", "help", "category", "categories")
            content = target(m.content)
            embeds = any(target(str(e.to_dict())) for e in m.embeds)
            return content or embeds

        def check_ping(m):
            target = search("ping", "ms", "pong", "latency", "websocket", "bot", "database")
            content = target(m.content)
            embeds = any(target(str(e.to_dict())) for e in m.embeds)
            return content or embeds

        for func in filter(lambda x: getattr(x, "__name__", "").startswith("check"), locals().values()):
            name = func.__name__.replace("check_", "")
            if match := re.match("(?P<prefix>^.{{1,30}}?(?={}$))".format(name), message.content, re.I):
                if name not in match["prefix"]:
                    return await self.update_prefix_bot(message, func, match["prefix"], name)

    async def search_respond(
            self,
            callback: Callable[[Tuple[ctypes.c_char_p, int], ctypes.c_char_p], Coroutine[Any, Any, List[str]]],
            message: discord.Message, word: str, _type: str
    ) -> Optional[Tuple[filter, List[str], Dict[int, discord.Message]]]:
        """Gets the prefix/command that are in this message, gets the bot that responded
           and return them."""
        content_compiled = ctypes.create_string_buffer(word.encode("utf-8"))
        if not (result := await callback(getattr(self, f"compiled_{_type}"), content_compiled)):
            return

        singular = _type[:len(_type) - ((_type != "commands") + 1)]

        def check(msg):
            return msg.channel == message.channel

        bot_found, after = await self.listen_for_bots_at(message, check)
        if not bot_found and not after:
            return

        bot_found.update(after)
        bot_found_keys = list(bot_found)
        query = f"SELECT DISTINCT bot_id, {singular} FROM {_type}_list " \
                f"WHERE guild_id=$1 AND bot_id=ANY($2::BIGINT[]) AND {singular}=ANY($3::VARCHAR[])"
        bots = await self.bot.pool_pg.fetch(query, message.guild.id, bot_found_keys, result)
        responded = filter(lambda x: x["bot_id"] in bot_found, bots)
        return responded, result, bot_found

    async def insert_both_prefix_command(self, prefix_list: List[Union[int, str]],
                                         command_list: List[Union[int, str]]) -> None:
        command_list_query = "INSERT INTO commands_list VALUES($1, $2, $3, $4)"
        prefix_list_query = "INSERT INTO prefixes_list VALUES($1, $2, $3, $4, $5) " \
                            "ON CONFLICT (guild_id, bot_id, prefix) DO " \
                            "UPDATE SET usage=prefixes_list.usage + 1, last_usage=$5"

        for key in "command_list", "prefix_list":
            await self.bot.pool_pg.executemany(locals()[f"{key}_query"], locals()[key])

    # @commands.Cog.listener("on_message")
    # @wait_ready()
    # @listen_for_guilds()
    # @prefix_cache_ready()
    # @is_user()
    async def find_bot_commands(self, message: discord.Message):
        """Get a prefix based on known command used.
           Disabled for now, as the derive detection is dumb."""
        word, _, _ = message.content.partition("\n")
        to_find = textwrap.shorten(word, width=100, placeholder="")
        if not (received := await self.search_respond(search_commands, message, to_find.casefold(), "commands")):
            return

        responded, result, message_sent = received
        prefixes_values = []
        commands_values = []
        exist_query = "SELECT * FROM prefixes_list WHERE guild_id=$1 AND bot_id=$2"
        for command, bot in itertools.product(result, responded):
            if bot["command"] == command:
                bot_id = bot['bot_id']
                message_respond = message_sent[bot_id].created_at.replace(tzinfo=None)
                target = re.escape(command)
                if (match := re.match("(?P<prefix>^.{{1,100}}?(?={}))".format(target), word, re.I)) and len(
                        match["prefix"]) < 31:
                    existing = await self.bot.pool_pg.fetch(exist_query, message.guild.id, bot_id)
                    prefix = match["prefix"]
                    if any(x['prefix'] != prefix and prefix.startswith(x["prefix"]) for x in existing):
                        continue
                    prefixes_values.append((message.guild.id, bot_id, prefix, 1, message_respond))

                if message.content.casefold().startswith(command):
                    commands_values.append((message.guild.id, bot_id, command, message_respond))

        for _, bot, prefix, _, _ in prefixes_values:
            prefixes = self.all_bot_prefixes.setdefault(bot, set())
            prefixes.add(prefix)
        self.update_compile()

        await self.insert_both_prefix_command(prefixes_values, commands_values)

    @commands.Cog.listener("on_message")
    @wait_ready()
    @listen_for_guilds()
    @prefix_cache_ready()
    async def command_count(self, message: discord.Message):
        """
        Checks if the message contains a valid prefix, which will wait for the bot to respond to count that message
        as a command.
        """
        if not (received := await self.search_respond(search_prefixes, message, message.content[:31], "prefixes")):
            return

        responded, result, message_sent = received
        commands_values = []
        prefixes_values = []
        for prefix, bot in itertools.product(result, responded):
            if bot["prefix"] == prefix:
                bot_id = bot['bot_id']
                message_respond = message_sent[bot_id].created_at.replace(tzinfo=None)
                prefixes_values.append((message.guild.id, bot_id, prefix, 1, message_respond))
                command = message.content[len(prefix):]
                word, _, _ = command.partition("\n")
                got_command, _, _ = word.partition(" ")
                if got_command:
                    commands_values.append((message.guild.id, bot_id, got_command, message_respond))

        for _, bot, command, _ in commands_values:
            command_list = self.all_bot_commands.setdefault(bot, set())
            command_list.add(command)
        self.update_compile()

        await self.insert_both_prefix_command(prefixes_values, commands_values)
