from discord.ext import commands


class NotInDatabase(commands.UserInputError):
    def __init__(self, _id):
        super().__init__(f"{_id} is not in the database.")


class BotNotFound(commands.UserInputError):
    def __init__(self, _id):
        super().__init__(f"{_id} not found.")


class NotInDpy(commands.UserInputError):
    def __init__(self):
        super().__init__(f"This command is only allowed in discord.py")