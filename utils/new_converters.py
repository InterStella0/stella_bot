import discord
import os
import re
import contextlib
import datetime
from collections import namedtuple
from fuzzywuzzy import fuzz
from discord.ext import commands
from utils.errors import NotValidCog, ThisEmpty, NotBot, NotInDatabase, UserNotFound, MustMember
from discord.utils import _unique
from utils.useful import unpack


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


class IsBot(commands.Converter):
    """Raises an error if the member is not a bot"""
    def __init__(self, is_bot=True, user_check=True):
        self.is_bot = is_bot
        self.user_check = user_check

    async def convert(self, ctx, argument, cls=None):
        for converter in ("Member", "User"):
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
    name = None
    use = None
    method = None

    def __init__(self, member):
        self.bot = member

    def __str__(self):
        return str(self.bot)

    @classmethod
    async def convert(cls, ctx, argument):
        member = await IsBot().convert(ctx, argument, cls=cls)
        method = getattr(ctx.bot.pool_pg, cls.method)
        if data := await method(f"SELECT * FROM {cls.name} WHERE bot_id=$1", member.id):
            return member, data
        raise NotInDatabase(member, converter=cls)

    def __int__(self):
        return self.bot.id


class BotPrefix(BotData):
    """Bot data for prefix"""
    name = "bot_prefix_list"
    use = "prefixes"
    method = "fetch"
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


class BotUsage(BotData):
    """Bot data for command counts"""
    __slots__ = ("count",)
    name = "bot_usage_count"
    use = "count"
    method = "fetchrow"

    def __init__(self, member, count):
        super().__init__(member)
        self.count = count

    @classmethod
    async def convert(cls, ctx, argument):
        member, data = await super().convert(ctx, argument)
        return cls(member, data[cls.use])


class BotCommand(BotData):
    """Bot data for command counts"""
    __slots__ = ("_commands",)
    name = "bot_commands_list"
    use = "commands"
    method = "fetch"

    def __init__(self, member, commands):
        super().__init__(member)
        self._commands = commands

    @classmethod
    async def convert(cls, ctx, argument):
        member, data = await super().convert(ctx, argument)
        commands = {payload['command']: payload['usage'] for payload in data}
        return cls(member, commands)

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
