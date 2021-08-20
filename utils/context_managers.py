import asyncio
import itertools
from discord.context_managers import Typing


class BreakableTyping(Typing):
    def __init__(self, messageable, /, *, limit=None):
        self.loop = messageable._state.loop
        self.messageable = messageable
        self.limit = limit

    async def cancel_typing(self):
        self.limit -= 5
        values = await asyncio.wait(
            {self.task, asyncio.sleep(self.limit)},
            return_when=asyncio.FIRST_COMPLETED
        )
        for t in itertools.chain.from_iterable(values):
            t.cancel()

    def __enter__(self):
        super().__enter__()
        if self.limit is not None:
            self.loop.create_task(self.cancel_typing())
        return self
