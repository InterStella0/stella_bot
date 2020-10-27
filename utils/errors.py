from discord.ext import commands


class NotInDatabase(commands.UserInputError):
    def __init__(self, _id):
        super().__init__(f"{_id} is not in the database.")


class NotValidCog(commands.UserInputError):
    def __init__(self, cog):
        super().__init__(f"{cog} is not a valid cog")


class BotNotFound(commands.UserInputError):
    def __init__(self, _id):
        super().__init__(f"{_id} not found.")


class NotInDpy(commands.UserInputError):
    def __init__(self):
        super().__init__(f"This command is only allowed in discord.py")


class ThisEmpty(commands.UserInputError):
    def __init__(self, arg):
        super().__init__(f"{arg} is empty.")