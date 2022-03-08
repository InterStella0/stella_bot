import typing

import discord
import os
import re
import contextlib
import datetime
import humanize
import numpy as np
from collections import namedtuple, Counter
from typing import Any, List, Optional, Union, Tuple, Generator, TypeVar
from jishaku.codeblocks import Codeblock, codeblock_converter
from fuzzywuzzy import fuzz
from discord.ext import commands
from utils.errors import NotValidCog, ThisEmpty, NotBot, NotInDatabase, UserNotFound, MustMember, NotOwnerConvert
from discord.utils import _unique
from utils.useful import unpack, RenameClass, StellaContext

T = TypeVar("T")


class CleanListGreedy:
    @classmethod
    async def after_greedy(cls, _: StellaContext, greedy_list: List[T]) -> List[T]:
        """
        This method will be called after greedy was processed. This will remove any duplicates of a list, putting list
        within a list into the current list. Set was not used to keep the original order.
        """
        unclean = [*unpack(greedy_list)]
        final = _unique(unclean)
        if not final:
            raise ThisEmpty(cls.__name__)
        return final


class ValidCog(CleanListGreedy):
    """Tries to convert into a valid cog"""
    @classmethod
    async def convert(cls, ctx: StellaContext, argument: str) -> List[str]:
        Valid = namedtuple("Valid", "ratio key")
        loaded_cog = {re.sub("(cogs)|\.|(cog)", "", x.__module__) for _, x in ctx.bot.cogs.items()}
        valid_cog = {x[:-3] for x in os.listdir("cogs") if x[-3:] == ".py"} | loaded_cog

        if any(argument == x for x in ("all", "*", "al", "everything", "every", "ever")):
            return list(valid_cog)
        maximum = max((Valid(fuzz.ratio(key, argument), key) for key in valid_cog), key=lambda v: v.ratio)
        if maximum.ratio >= 50:
            return maximum.key
        raise NotValidCog(argument)


class IsBot(commands.Converter[discord.Member], metaclass=RenameClass, name="Bot"):
    """Raises an error if the member is not a bot"""
    def __init__(self, is_bot: Optional[bool] = True, user_check: Optional[bool] = True,
                 dont_fetch: Optional[bool] = False):
        self.is_bot = is_bot
        self.user_check = user_check
        self.dont_fetch = dont_fetch

    async def convert(self, ctx: StellaContext, argument: str) -> Union[discord.Member, discord.User]:
        for converter in ("Member", "User")[:(not self.dont_fetch) + 1]:
            with contextlib.suppress(commands.BadArgument):
                user = await getattr(commands, f"{converter}Converter")().convert(ctx, argument)
                if user.bot is not self.is_bot:
                    raise NotBot(user, is_bot=self.is_bot)
                if isinstance(user, discord.User) and not self.user_check:
                    raise MustMember(user)
                return user
        raise UserNotFound(argument) from None


class BotData:
    """BotData Base for Bot data that was fetch from the database. It checks if it's a member and gets it's data."""
    __slots__ = ("bot",)

    def __init__(self, member: discord.Member):
        self.bot = member

    def __str__(self) -> str:
        return str(self.bot)

    @classmethod
    async def convert(cls, ctx: StellaContext, argument: str) -> Tuple[discord.Member, Any]:
        member = await IsBot().convert(ctx, argument)
        table = cls.__name__.replace("Bot", "").lower()
        query = f"SELECT * FROM {table}_list WHERE guild_id=$1 AND bot_id=$2"
        if data := await ctx.bot.pool_pg.fetch(query, ctx.guild.id, member.id):
            return member, data
        raise NotInDatabase(member)

    def __int__(self) -> int:
        return self.bot.id


class BotPrefixes(BotData):
    """Bot data for prefix"""
    __slots__ = ("predicted_data", )

    def __init__(self, member: discord.Member, predicted_data: dict):
        super().__init__(member)
        self.predicted_data = predicted_data

    @classmethod
    async def convert(cls, ctx: StellaContext, argument: str) -> "BotPrefixes":
        member, data = await super().convert(ctx, argument)
        return await cls.from_db(ctx, member, data)

    @classmethod
    async def from_db(cls, ctx: StellaContext, member, data):
        processed = [[x["prefix"], x["usage"], x["last_usage"].timestamp()] for x in data]
        prediction = await ctx.bot.get_prefixes_dataset(processed)
        return cls(member, prediction)

    @property
    def prefix(self) -> str:
        return str(self.predicted_data[self.predicted_data[:, 3].astype(np.float).argmax()][0])

    @property
    def aliases(self) -> str:
        prefixes = self.predicted_data
        potential = prefixes[prefixes[:, 3].astype(np.float) >= 50]
        alias = potential[potential[:, 0] != self.prefix]
        return alias[:, 0].tolist()

    @property
    def all_raw_prefixes(self):
        return [self.prefix, *self.aliases]

    @property
    def allprefixes(self) -> str:
        return ", ".join(map("`{0}`".format, [self.prefix, *self.aliases]))


class BotCommands(BotData):
    """Bot data for command counts"""
    __slots__ = ("_commands", "total_usage", "command_usages")

    def __init__(self, member: discord.Member, commands: dict, command_usages: dict, total_usage: int):
        super().__init__(member)
        self._commands = commands
        self.command_usages = command_usages
        self.total_usage = total_usage

    @classmethod
    async def convert(cls, ctx: StellaContext, argument: str) -> "BotCommands":
        member, data = await super().convert(ctx, argument)
        command_usages = {}
        for payload in data:
            command = command_usages.setdefault(payload["command"], [])
            command.append(payload["time_used"])

        for command in command_usages.values():
            command.sort(reverse=True)

        commands = Counter(payload["command"] for payload in data)
        total_usage = sum(v for v in commands.values())
        return cls(member, commands, command_usages, total_usage)

    def get_command(self, command: str) -> str:
        return self._commands.get(command)

    @property
    def commands(self) -> List[tuple]:
        total = sum(self._commands.values())
        pair = [(c, v) for c, v in self._commands.items() if v / total > 0.001]
        return [c[0] for c in sorted(pair, key=lambda x: x[1], reverse=True)]

    @property
    def highest_command(self) -> int:
        return max(self._commands, key=lambda x: self._commands[x])


class DatetimeConverter(commands.Converter):
    """Will try to convert into a valid datetime object based on a specific format"""
    async def convert(self, ctx: StellaContext, argument: str) -> str:
        def valid_replace(a: str) -> Generator[str]:
            multiple = {x: 1 for x in (" ", ":", "/")}
            multiple.update({"Y": 4})
            for x in a.replace("%", ""):
                yield x * multiple.get(x, 2)
        valid_conversion = ("%d/%m/%Y %H", "%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y", "%Y/%m/%d", "%Y/%d/%m", "%m/%d/%Y")
        for _format in valid_conversion:
            with contextlib.suppress(ValueError):
                return datetime.datetime.strptime(argument, _format)
        newline = "\n"
        raise commands.CommandError(
            f"I couldn't convert {argument} into a valid date time. Here's a list of valid format for date: \n"
            f"{newline.join(''.join(valid_replace(x)) for x in valid_conversion)}"
        )


class JumpValidator(commands.Converter):
    """Will get the jump_url of a message"""
    async def convert(self, ctx: StellaContext, argument: str) -> str:
        with contextlib.suppress(commands.MessageNotFound):
            message = await commands.MessageConverter().convert(ctx, argument)
            return message.jump_url
        raise commands.CommandError(f"I can't find {argument}. Is this even a real message?")


time_regex = re.compile(r"(\d{1,5}(?:[.,]?\d{1,5})?)([smhd])")
time_dict = {"h": 3600, "s": 1, "m": 60, "d": 86400}


class TimeConverter(commands.Converter):
    """Stole from discord.py because lazy, i'll make a better one later"""
    def __init__(self, minimum_time: Optional[datetime.datetime] = None,
                 maximum_time: Optional[datetime.datetime] = None,
                 backward=True):
        self.minimum_time = minimum_time
        self.maximum_time = maximum_time
        self.backward = backward

    async def __call__(self, argument: str) -> datetime.datetime:
        return await self.convert(0, argument)

    async def convert(self, ctx: StellaContext, argument: str) -> datetime.datetime:
        matches = time_regex.findall(argument.lower())
        time = 0
        for v, k in matches:
            try:
                time += time_dict[k]*float(v)
            except KeyError:
                raise commands.BadArgument(f"{k} is an invalid time-key! h/m/s/d are valid!")
            except ValueError:
                raise commands.BadArgument(f"{v} is not a number!")

        timedelta = datetime.timedelta(seconds=time)
        if self.backward:
            time_converted = datetime.datetime.utcnow() - timedelta
        else:
            time_converted = datetime.datetime.utcnow() + timedelta

        if self.minimum_time or self.maximum_time:
            if time_converted > datetime.datetime.utcnow() - self.minimum_time:
                raise commands.BadArgument(f"Time must be longer than `{humanize.precisedelta(self.minimum_time)}`")
            if time_converted < datetime.datetime.utcnow() - self.maximum_time:
                raise commands.BadArgument(f"Time must be shorter than `{humanize.precisedelta(self.maximum_time)}`")

        return time_converted


class AuthorMessage(commands.MessageConverter):
    """Only allows messages that belong to the context author"""
    async def convert(self, ctx: StellaContext, argument: str) -> discord.Message:
        message = await super().convert(ctx, argument)
        if message.author != ctx.author:
            raise commands.CommandError("The author of this message must be your own message.")
        return message


class AuthorJump_url(JumpValidator):
    """Yes i fetch message twice, I'm lazy to copy paste."""
    async def convert(self, ctx: StellaContext, argument: str) -> str:
        message = await AuthorMessage().convert(ctx, await super().convert(ctx, argument))
        return message.jump_url


class BooleanOwner(commands.Converter):
    async def convert(self, ctx: StellaContext, argument: str) -> bool:
        if await ctx.bot.is_owner(ctx.author):
            return commands.converter._convert_to_bool(argument)
        raise NotOwnerConvert('Boolean')


codeblock_re = re.compile(r'`{3}((?P<language>\w+)\n)?(\n)*(?P<code>((.|\n)+?(?=`{3})|(.|\n)*?(?=`{2})|(.|\n)+))(?P<end>`{1,3})?')


class CodeblockConverter(commands.Converter[Codeblock]):
    async def convert(self, ctx: StellaContext, argument: str) -> Codeblock:
        stringview = ctx.view
        stringview.undo()
        rest = stringview.read_rest()
        if codes := codeblock_re.search(rest):
            value = Codeblock(codes['language'], codes['code'])
            if value.content is None or value.content == "":
                raise commands.CommandError("Codeblock is empty")
            if codes["end"] is None or len(codes["end"]) <= 2:
                raise commands.CommandError("Codeblock was not properly ended")
            start, end = codes.span()
            stringview.previous += end
        else:
            value = codeblock_converter(argument)
            stringview.previous += len(argument)
        stringview.undo()
        return value


