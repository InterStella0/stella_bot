from typing import Any, Union

import discord

from discord.ext import commands


class ArgumentBaseError(commands.UserInputError):
    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        # Base error, this used to have an extra attribute, but was removed due 2.0


class NotInDatabase(ArgumentBaseError):
    def __init__(self, _id: Union[discord.Member, discord.User, int], **kwargs: Any):
        super().__init__(message=f"It appears that {_id} is not in the database. Try someone else.", **kwargs)


class NotValidCog(ArgumentBaseError):
    def __init__(self, cog: str, **kwargs: Any):
        super().__init__(message=f"{cog} is not a valid cog.", **kwargs)


class BotNotFound(ArgumentBaseError):
    def __init__(self, _id: Union[discord.User, int, str], **kwargs: Any):
        super().__init__(message=f"{_id} not found.", **kwargs)


class NotBot(ArgumentBaseError):
    def __init__(self, _id: Union[discord.User, int], **kwargs: Any):
        if kwargs.pop("is_bot", True):
            m = f"{_id} is not a bot. Give me a bot please."
        else:
            m = f"{_id} is a bot. Give me a user please."
        super().__init__(message=m, **kwargs)


class MustMember(ArgumentBaseError):
    def __init__(self, _id: Union[discord.User, int], **kwargs: Any):
        super().__init__(message=f"{_id} must be in the server.", **kwargs)


class ThisEmpty(ArgumentBaseError):
    def __init__(self, arg: str, **kwargs: Any):
        super().__init__(message=f"No valid argument was converted. Which makes {arg} as empty.", **kwargs)


class UserNotFound(ArgumentBaseError):
    def __init__(self, arg: Union[discord.Member, discord.User], **kwargs: Any):
        super().__init__(message=f"I can't find {arg}, is this even a valid user?", **kwargs)


class CantRun(commands.CommandError):
    def __init__(self, message: str, *arg: Any):
        super().__init__(message=message, *arg)


class ConsumerUnableToConvert(ArgumentBaseError):
    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(message="Could not convert {} into {}".format(*args), **kwargs)


class ReplParserDies(ArgumentBaseError):
    def __init__(self, message: str, no: int, line: str, mode: bool):
        super().__init__(message=message)
        self.message = message
        self.line = line
        self.no = no
        self.mode = mode


class NotOwnerConvert(ArgumentBaseError):
    def __init__(self, converter: str):
        super().__init__(message=f"You're not the owner of this bot. You can't use {converter}")


class ErrorNoSignature(commands.CommandError):
    """Displays error in embed without generating signature hint"""


class UserLocked(ArgumentBaseError, ErrorNoSignature):
    pass


class BypassError(ArgumentBaseError):
    def __init__(self, error):
        super().__init__()
        self.original = error


class NotInDpy(ErrorNoSignature):
    def __init__(self) -> None:
        super().__init__(message="This command is only allowed in `discord.py` server.")
