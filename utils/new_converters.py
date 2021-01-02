import discord
import inspect
import os
import re
import contextlib
from collections import namedtuple
from fuzzywuzzy import fuzz
from discord.ext import commands
from utils.errors import NotValidCog, ThisEmpty, NotBot, NotInDatabase, UserNotFound, MustMember
from discord.utils import _unique
from utils.useful import unpack


class FetchUser(commands.Converter):
    """Glorified fetch_user"""
    async def convert(self, ctx, argument):
        with contextlib.suppress(commands.UserNotFound):
            if argument.isdigit():
                return await ctx.bot.fetch_user(int(argument))
            return await commands.UserConverter().convert(ctx, argument)
        converter = self if inspect.isclass(self) else self.__class__
        raise UserNotFound(argument, converter=converter) from None


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
    name = "NONE"
    use = "NONE"

    def __init__(self, member):
        self.bot = member

    def __str__(self):
        return str(self.bot)

    @classmethod
    async def convert(cls, ctx, argument):
        member = await IsBot().convert(ctx, argument, cls=cls)
        if data := await ctx.bot.pool_pg.fetchrow(f"SELECT * FROM {cls.name} WHERE bot_id=$1", member.id):
            return member, data
        raise NotInDatabase(member, converter=cls)

    def __int__(self):
        return self.bot.id


class BotPrefix(BotData):
    """Bot data for prefix"""
    name = "bot_prefix"
    use = "prefix"
    __slots__ = ("prefix",)

    def __init__(self, member, prefix):
        super().__init__(member)
        self.prefix = prefix

    @classmethod
    async def convert(cls, ctx, argument):
        member, data = await super().convert(ctx, argument)
        return cls(member, data[cls.use])


class BotUsage(BotData):
    """Bot data for command counts"""
    __slots__ = ("count",)
    name = "bot_usage_count"
    use = "count"

    def __init__(self, member, count):
        super().__init__(member)
        self.count = count

    @classmethod
    async def convert(cls, ctx, argument):
        member, data = await super().convert(ctx, argument)
        return cls(member, data[cls.use])
