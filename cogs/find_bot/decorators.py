from typing import Callable

from utils.decorators import event_check


deco_event = Callable[[Callable], Callable]


def is_user() -> deco_event:
    """Event check for returning true if it's a bot."""
    return event_check(lambda _, m: not m.author.bot)
