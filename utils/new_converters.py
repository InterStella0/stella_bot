from discord.ext import commands
from utils.errors import NotValidCog, ThisEmpty


class FetchUser(commands.Converter):
    async def convert(self, ctx, argument):
        return await ctx.bot.fetch_user(int(argument))


class CleanListGreedy:
    @classmethod
    async def after_greedy(cls, ctx, argument):
        """
        This method will be called after greedy was processed. This will remove any duplicates of a list, putting list
        within a list into the current list. Set was not used to keep the original order.
        """
        final = []
        # unable to use set, to preserve order
        for value in argument:
            if isinstance(value, list):
                for subvalue in value:
                    if subvalue not in final:
                        final.append(subvalue)
                continue
            if value not in final:
                final.append(value)
        if not final:
            raise ThisEmpty(cls.__name__)
        final.reverse()
        return final


class ValidCog(CleanListGreedy):
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
        raise NotValidCog(argument)

