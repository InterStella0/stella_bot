from __future__ import annotations

import re
from typing import TYPE_CHECKING

from discord.ext import commands

if TYPE_CHECKING:
    from main import StellaBot


class FindBotCog(commands.Cog):
    def __init__(self, bot: StellaBot):
        self.bot = bot
        valid_prefix = ("!", "?", "？", "<@(!?)80528701850124288> ")
        re_command = "(\{}|\{}|\{}|({}))addbot".format(*valid_prefix)
        re_bot = "[\s|\n]+(?P<id>[0-9]{17,19})[\s|\n]"
        re_reason = "+(?P<reason>.[\s\S\r]+)"
        self.re_addbot = re_command + re_bot + re_reason
        self.cached_bots = {}
        self.re_github = re.compile(r'https?://(?:www\.)?github.com/(?P<repo_owner>(\w|-)+)/(?P<repo_name>(\w|-)+)?')
        self.all_bot_prefixes = {}
        self.all_bot_commands = {}
        self.compiled_prefixes = None
        self.compiled_commands = None
