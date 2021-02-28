import discord
import os
import re
import contextlib
import datetime
import humanize
from collections import namedtuple, Counter
from fuzzywuzzy import fuzz
from discord.ext import commands
from utils.errors import NotValidCog, ThisEmpty, NotBot, NotInDatabase, UserNotFound, MustMember
from discord.utils import _unique
from utils.useful import unpack, RenameClass


class CleanListGreedy:
    @classmethod
    async def after_greedy(cls, ctx, greedy_list):
        """
        This method will be called after greedy was processed. This will remove any duplicates of a list, putting list
        within a list into the current list. Set was not used to keep the original order.
        """
        unclean = [*unpack(greedy_list)]
        final = _unique(unclean)
        if not final:
            raise ThisEmpty(cls.__name__, converter=cls)
        return final


class ValidCog(CleanListGreedy):
    """Tries to convert into a valid cog"""
    @classmethod
    async def convert(cls, ctx, argument):
        Valid = namedtuple("Valid", "ratio key")
        loaded_cog = {re.sub("(cogs)|\.|(cog)", "", x.__module__) for _, x in ctx.bot.cogs.items()}
        valid_cog = {x[:-3] for x in os.listdir("cogs") if x[-3:] == ".py"} | loaded_cog

        if any(argument == x for x in ("all", "*", "al", "everything", "every", "ever")):
            return list(valid_cog)
        maximum = max((Valid(fuzz.ratio(key, argument), key) for key in valid_cog), key=lambda v: v.ratio)
        if maximum.ratio >= 50:
            return maximum.key
        raise NotValidCog(argument, converter=cls)


class IsBot(commands.Converter, metaclass=RenameClass, name="Bot"):
    """Raises an error if the member is not a bot"""
    def __init__(self, is_bot=True, user_check=True, dont_fetch=False):
        self.is_bot = is_bot
        self.user_check = user_check
        self.dont_fetch = dont_fetch

    async def convert(self, ctx, argument, cls=None):
        for converter in ("Member", "User")[:(not self.dont_fetch) + 1]:
            with contextlib.suppress(commands.BadArgument):
                user = await getattr(commands, f"{converter}Converter")().convert(ctx, argument)
                if user.bot is not self.is_bot:
                    raise NotBot(user, is_bot=self.is_bot, converter=cls or self)
                if isinstance(user, discord.User) and not self.user_check:
                    raise MustMember(user, converter=cls or self)
                return user
        raise UserNotFound(argument, converter=cls or self) from None


class BotData:
    """BotData Base for Bot data that was fetch from the database. It checks if it's a member and gets it's data."""
    __slots__ = ("bot",)

    def __init__(self, member):
        self.bot = member

    def __str__(self):
        return str(self.bot)

    @classmethod
    async def convert(cls, ctx, argument):
        member = await IsBot().convert(ctx, argument, cls=cls)
        table = cls.__name__.replace("Bot", "").lower()
        query = f"SELECT * FROM {table}_list WHERE guild_id=$1 AND bot_id=$2"
        if data := await ctx.bot.pool_pg.fetch(query, ctx.guild.id, member.id):
            return member, data
        raise NotInDatabase(member, converter=cls)

    def __int__(self):
        return self.bot.id


class BotPrefixes(BotData):
    """Bot data for prefix"""
    __slots__ = ("prefixes",)

    def __init__(self, member, prefixes):
        super().__init__(member)
        self.prefixes = prefixes

    @classmethod
    async def convert(cls, ctx, argument):
        member, data = await super().convert(ctx, argument)
        prefixes = {payload['prefix']: payload['usage'] for payload in data}
        return cls(member, prefixes)

    @property
    def prefix(self):
        return max(self.prefixes, key=lambda x: self.prefixes[x])

    @property
    def aliases(self):
        alias = {x: y for x, y in self.prefixes.items() if x != self.prefix}
        total = sum(alias.values())
        highest = self.prefixes[self.prefix]
        return [p for p, v in alias.items() if v / total > 0.5 and v / highest > 0.05]

    @property
    def allprefixes(self):
        return ", ".join(map("`{0}`".format, [self.prefix, *self.aliases]))


class BotCommands(BotData):
    """Bot data for command counts"""
    __slots__ = ("_commands", "total_usage", "command_usages")

    def __init__(self, member, commands, command_usages, total_usage):
        super().__init__(member)
        self._commands = commands
        self.command_usages = command_usages
        self.total_usage = total_usage

    @classmethod
    async def convert(cls, ctx, argument):
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

    def get_command(self, command):
        return self._commands.get(command)

    @property
    def commands(self):
        total = sum(self._commands.values())
        pair = [(c, v) for c, v in self._commands.items() if v / total > 0.001]
        return [c[0] for c in sorted(pair, key=lambda x: x[1], reverse=True)]

    @property
    def highest_command(self):
        return max(self._commands, key=lambda x: self._commands[x])

class DatetimeConverter(commands.Converter):
    """Will try to convert into a valid datetime object based on a specific format"""
    async def convert(self, ctx, argument):
        def valid_replace(argument):
            multiple = {x: 1 for x in (" ", ":", "/")}
            multiple.update({"Y": 4})
            for x in argument.replace("%", ""):
                yield x * multiple.get(x, 2)
        valid_conversion = ("%d/%m/%Y %H", "%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y", "%Y/%m/%d", "%Y/%d/%m", "%m/%d/%Y")
        for _format in valid_conversion:
            with contextlib.suppress(ValueError):
                return datetime.datetime.strptime(argument, _format)
        newline = "\n"
        raise commands.CommandError(
            f"I couldn't convert {argument} into a valid date time. Here's a list of valid format for date: \n"\
            f"{newline.join(''.join(valid_replace(x)) for x in valid_conversion)}"
        )


class JumpValidator(commands.Converter):
    """Will get the jump_url of a message"""
    async def convert(self, ctx, argument):
        with contextlib.suppress(commands.MessageNotFound):
            message = await commands.MessageConverter().convert(ctx, argument)
            return message.jump_url
        raise commands.CommandError(f"I can't find {argument}. Is this even a real message?")


time_regex = re.compile(r"(\d{1,5}(?:[.,]?\d{1,5})?)([smhd])")
time_dict = {"h":3600, "s":1, "m":60, "d":86400}
class TimeConverter(commands.Converter):
    """Stole from discord.py because lazy, i'll make a better one later"""
    def __init__(self, minimum_time=None, maximum_time=None):
        self.minimum_time = minimum_time
        self.maximum_time = maximum_time

    async def __call__(self, argument):
        return await self.convert(0, argument)

    async def convert(self, ctx, argument):
        matches = time_regex.findall(argument.lower())
        time = 0
        for v, k in matches:
            try:
                time += time_dict[k]*float(v)
            except KeyError:
                raise commands.BadArgument(f"{k} is an invalid time-key! h/m/s/d are valid!")
            except ValueError:
                raise commands.BadArgument(f"{v} is not a number!")
        
        time_converted = datetime.datetime.utcnow() - datetime.timedelta(seconds=time)
        if self.minimum_time or self.maximum_time:
            if time_converted > datetime.datetime.utcnow() - self.minimum_time:
                raise commands.BadArgument(f"Time must be longer than `{humanize.precisedelta(self.minimum_time)}`")
            if time_converted < datetime.datetime.utcnow() - self.maximum_time:
                raise commands.BadArgument(f"Time must be shorter than `{humanize.precisedelta(self.maximum_time)}`")

        return time_converted
