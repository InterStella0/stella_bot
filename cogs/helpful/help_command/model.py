from __future__ import annotations

from typing import List, Union, Mapping, Optional

from discord.ext import commands

from utils.cog import StellaCog
from utils.greedy_parser import GreedyParser


StellaCommands = Union[commands.Command, commands.Group, GreedyParser]
BotHelpMap = Mapping[Optional[StellaCog], List[StellaCommands]]


class CogEmoji:
    Bots: str = '<:robot_mark:848257366587211798>'
    Useful: str = '<:useful:848258928772776037>'
    Helpful: str = '<:helpful:848260729916227645>'
    Statistic: str = '<:statis_mark:848262218554408988>'
    Myself: str = '<:me:848262873783205888>'
    Home: str = '<:house_mark:848227746378809354>'

    @classmethod
    def get(cls, name: str) -> str:
        return getattr(cls, name, '<:question:848263403604934729>')
