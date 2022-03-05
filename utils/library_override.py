"""
Copyright (C) lmao this is just weird just take it i dont give a shit
if you copy this but make your repository private, ur weird
"""
import contextlib
import io
import os
import jishaku.shell
import jishaku.paginators
import jishaku.exception_handling
import jishaku.repl.compilation
import discord
import pathlib
import sys
import asyncio
import subprocess
import re
import inspect
from jishaku.functools import AsyncSender
from typing import Union, AsyncGenerator, Callable, Optional
from collections import namedtuple
from discord.ext import commands
from discord.ext.commands.converter import CONVERTER_MAPPING
EmojiSettings = namedtuple('EmojiSettings', 'start back forward end close')


class FakeEmote(discord.PartialEmoji):
    """
    Due to the nature of jishaku checking if an emoji object is the reaction, passing raw str into it will not work.
    Creating a PartialEmoji object is needed instead.
    """

    @classmethod
    def from_name(cls, name: str) -> "FakeEmote":
        emoji_name = re.sub("|<|>", "", name)
        a, name, _id = emoji_name.split(":")
        return cls(name=name, id=int(_id), animated=bool(a))


emote = EmojiSettings(
    start=FakeEmote.from_name("<:before_fast_check:754948796139569224>"),
    back=FakeEmote.from_name("<:before_check:754948796487565332>"),
    forward=FakeEmote.from_name("<:next_check:754948796361736213>"),
    end=FakeEmote.from_name("<:next_fast_check:754948796391227442>"),
    close=FakeEmote.from_name("<:stop_check:754948796365930517>")
)
jishaku.paginators.EMOJI_DEFAULT = emote  # Overrides jishaku emojis


async def attempt_add_reaction(msg: discord.Message, reaction: Union[str, discord.Emoji]) -> None:
    """
    This is responsible for every add reaction happening in jishaku. Instead of replacing each emoji that it uses in
    the source code, it will try to find the corresponding emoji that is being used instead.
    """
    reacts = {
        "\N{WHITE HEAVY CHECK MARK}": "<:checkmark:753619798021373974>",
        "\N{BLACK RIGHT-POINTING TRIANGLE}": emote.forward,
        "\N{HEAVY EXCLAMATION MARK SYMBOL}": "<:information_pp:754948796454010900>",
        "\N{DOUBLE EXCLAMATION MARK}": "<:crossmark:753620331851284480>",
        "\N{ALARM CLOCK}": emote.end
    }
    react = reacts[reaction] if reaction in reacts else reaction
    with contextlib.suppress(discord.HTTPException):
        return await msg.add_reaction(react)


jishaku.exception_handling.attempt_add_reaction = attempt_add_reaction


async def traverse(self, func: Callable) -> AsyncGenerator[str, None]:
    std = io.StringIO()
    with contextlib.redirect_stdout(std):
        if inspect.isasyncgenfunction(func):
            async for send, result in AsyncSender(func(*self.args)):
                if content := std.getvalue():
                    std.seek(0)
                    std.truncate(0)
                    yield content
                send((yield result))
        else:
            yield await func(*self.args)
            if content := std.getvalue():
                yield content


jishaku.repl.compilation.AsyncCodeExecutor.traverse = traverse

# Fix old flag converter pointing to core file
commands.core._convert_to_bool = commands.converter._convert_to_bool


class StellaMessage(discord.Message):
    __slots__ = ("_to_delete",)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._to_delete = False

    @property
    def to_delete(self):
        return self._to_delete

    @to_delete.setter
    def to_delete(self, value):
        self._to_delete = value

    async def delete(self, *, delay: Optional[float]=None):
        self.to_delete = True
        await super().delete(delay=delay)


discord.state.Message = StellaMessage
discord.message.Message = StellaMessage