from discord.ext import commands


class ArgumentBaseError(commands.UserInputError):
    def __init__(self, converter=None, **kwargs):
        super().__init__(**kwargs)
        self.converter = converter


class NotInDatabase(ArgumentBaseError):
    def __init__(self, _id, **kwargs):
        super().__init__(message=f"It appears that {_id} is not in the database. Try someone else.", **kwargs)


class NotValidCog(ArgumentBaseError):
    def __init__(self, cog, **kwargs):
        super().__init__(message=f"{cog} is not a valid cog.", **kwargs)


class BotNotFound(ArgumentBaseError):
    def __init__(self, _id, **kwargs):
        super().__init__(message=f"{_id} not found.", **kwargs)


class NotBot(ArgumentBaseError):
    def __init__(self, _id, **kwargs):
        super().__init__(message=f"{_id} is not a bot. Give me a bot please.", **kwargs)


class NotInDpy(commands.UserInputError):
    def __init__(self):
        super().__init__(message=f"This command is only allowed in `discord.py` server.")


class ThisEmpty(ArgumentBaseError):
    def __init__(self, arg, **kwargs):
        super().__init__(message=f"No valid argument was converted. Which makes {arg} as empty.", **kwargs)


class UserNotFound(ArgumentBaseError):
    def __init__(self, arg, **kwargs):
        super().__init__(message=f"I can't find {arg}, is this even a valid user?", **kwargs)


class CantRun(commands.CommandError):
    def __init__(self, message, *arg):
        super().__init__(message=message, *arg)
