from utils.errors import ErrorNoSignature


class NoPendingBots(ErrorNoSignature):
    def __init__(self) -> None:
        super().__init__("```\nThere are no pending bots at the moment.```")
