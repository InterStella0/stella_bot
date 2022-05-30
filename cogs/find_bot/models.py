from __future__ import annotations

import contextlib
import datetime
import http
import random
from dataclasses import dataclass
from typing import Optional, Union

import discord
from aiogithub.exceptions import HttpException
from aiogithub.objects import Repo
from discord.ext import commands

from utils.errors import NotInDatabase, BotNotFound
from utils.new_converters import IsBot
from utils.useful import StellaContext


@dataclass
class BotRepo:
    bot: discord.User = None
    repo: Repo = None

    @classmethod
    async def from_db(cls, stellabot, bot, data):
        repo = await stellabot.git.get_repo(data["owner_repo"], data["bot_name"])
        return cls(bot=bot, repo=repo)

    @classmethod
    async def convert(cls, ctx: StellaContext, argument: str) -> BotRepo:
        user = await IsBot().convert(ctx, argument)
        data = await ctx.bot.pool_pg.fetchrow("SELECT * FROM bot_repo WHERE bot_id=$1", user.id)
        if data:
            try:
                return await cls.from_db(ctx.bot, user, data)
            except HttpException as e:
                if e.status == 404:
                    raise commands.CommandError("Bot has an invalid github link. Sorry.")
                status = http.client.responses.get(e.status) or f"Invalid Code: ({e.status})"
                raise commands.CommandError(f"Error: {status}\n{e.url}")
        raise NotInDatabase(user)

    def __str__(self) -> str:
        return str(self.bot)


@dataclass
class BotGitHubLink:
    repo_owner: str = None
    repo_name: str = None

    @classmethod
    async def convert(cls, ctx: StellaContext, argument: str) -> BotGitHubLink:
        from .github import GithubHandler
        find = ctx.bot.get_cog("Bots")
        if find is None or not isinstance(find, GithubHandler):
            raise commands.CommandError("This converter is disabled until Bots cog is loaded")

        regex = find.re_github
        if found := regex.search(argument):
            repo_owner = found['repo_owner']
            repo_bot = found['repo_name']
            content = f"**Owner repository:** `{repo_owner}`\n**Bot repository:** `{repo_bot}`\n\n **Is this correct?**"
            if not await ctx.confirmation(content, delete_after=True):
                raise commands.CommandNotFound()
            return cls(repo_owner=repo_owner, repo_name=repo_bot)
        raise commands.CommandError("Unable to resolve repository owner and repository bot")


@dataclass
class BotAdded:
    """BotAdded information for discord.py that is used in whoadd and whatadd command."""
    author: discord.Member = None
    bot: discord.Member = None
    reason: str = None
    requested_at: datetime.datetime = None
    jump_url: str = None
    joined_at: datetime.datetime = None

    @classmethod
    def from_json(cls, bot: Optional[Union[discord.Member, discord.User]] = None, *, bot_id: Optional[int] = None,
                  **data: Union[discord.Member, datetime.datetime, str]) -> BotAdded:
        """factory method on data from a dictionary like object into BotAdded."""
        author = data.pop("author_id", None)
        join = data.pop("joined_at", None)
        bot = bot or bot_id
        if isinstance(bot, discord.Member):
            join = bot.joined_at
            author = bot.guild.get_member(author) or author
        if join is not None:
            join = join.replace(tzinfo=None)
        return cls(author=author, bot=bot, joined_at=join, **data)

    @classmethod
    async def convert(cls, ctx: StellaContext, argument: str) -> BotAdded:
        """Invokes when the BotAdded is use as a typehint."""
        with contextlib.suppress(commands.BadArgument):
            if user := await IsBot().convert(ctx, argument):
                for attribute in ("pending", "confirmed")[isinstance(user, discord.Member):]:
                    attribute += "_bots"
                    if user.id in getattr(ctx.bot, attribute):
                        data = await ctx.bot.pool_pg.fetchrow(f"SELECT * FROM {attribute} WHERE bot_id = $1", user.id)
                        return cls.from_json(user, **data)
                raise NotInDatabase(user)
        raise BotNotFound(argument)

    def __str__(self) -> str:
        return str(self.bot or "")


class BotOwner(BotAdded):
    """Raises an error if the bot does not belong to the context author"""

    @classmethod
    async def convert(cls, ctx: StellaContext, argument: str) -> BotOwner:
        botdata = await super().convert(ctx, argument)
        if botdata.author != ctx.author:
            raise commands.CommandError(
                f"Sorry you can only change your own bot's information. This bot belongs to {botdata.author}.")

        if not ctx.guild.get_member(botdata.bot.id):
            raise commands.CommandError("This bot must be in the server.")
        return botdata


class BotPending(BotAdded):
    @classmethod
    async def convert(cls, ctx: StellaContext, argument: str) -> BotPending:
        botdata = await super().convert(ctx, argument)
        if isinstance(botdata.bot, discord.Member):
            suggest = ctx.bot.get_command_signature(ctx, "botinfo")
            raise commands.CommandError(f"Sorry `{botdata.bot}` is already in the server. Use `{suggest}` instead.")

        return botdata


class DeletedUser:
    """A placeholder for cases when fetch_user fails"""

    __slots__ = (
        "id",
        "created_at",
        "display_avatar",
    )

    def __init__(self, user_id: int):
        self.id = user_id
        self.created_at = discord.utils.snowflake_time(user_id)
        self.display_avatar = f"{discord.Asset.BASE}/embed/avatars/" \
                              f"{random.randrange(len(discord.enums.DefaultAvatar))}.png"

    def __str__(self) -> str:
        return "Deleted User#0000"

    def __repr__(self) -> str:
        return f"<{type(self).__name__} id={self.id}>"