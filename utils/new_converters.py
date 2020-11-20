import discord
from discord.ext import commands
from utils.errors import NotValidCog, ThisEmpty, NotBot, NotInDatabase
from discord.utils import _unique
from itertools import chain
from utils.useful import unpack


class FetchUser(commands.Converter):
    """Glorified fetch_user"""
    async def convert(self, ctx, argument):
        try:
            if argument.isdigit():
                return await ctx.bot.fetch_user(int(argument))
            return await commands.UserConverter().convert(ctx, argument)
        except Exception as e:
            e.converter = self.__class__
            raise e


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
        valid_cog = {"useful": ["use", "u"],
                     "helpful": ["help", "h"],
                     "myself": ["stella", "my", "self", "m"],
                     "find_bot": ["find", "f", "bot"],
                     "error_handler": ["error", "e", "err", "error_handlers"],
                     "all": ["al", "a", "*"]}

        for key in valid_cog:
            if key == argument or argument in valid_cog[key]:
                if key == "all":
                    return [x for x in valid_cog if key != x]
                return key
        raise NotValidCog(argument, converter=cls)


class IsBot(commands.Converter):
    """Raises an error if the member is not a bot"""
    async def convert(self, ctx, argument):
        member = await commands.MemberConverter().convert(ctx, argument)
        if not member.bot:
            raise NotBot(member, converter=self.__class__)
        return member


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
        member = await IsBot().convert(ctx, argument)

        if data := await ctx.bot.pool_pg.fetchrow(f"SELECT * FROM {cls.name} WHERE bot_id=$1", member.id):
            return member, data
        raise NotInDatabase(member, converter=cls)


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
