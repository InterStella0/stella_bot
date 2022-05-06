from __future__ import annotations
import datetime
from dataclasses import dataclass
from typing import Union, Dict, List, Tuple

import discord
from discord.ext import commands

from .baseclass import FindBotCog
from .converters import BotListReverse
from utils import greedy_parser
from utils.buttons import InteractionPages
from utils.decorators import pages, DISCORD_PY
from utils.errors import NotInDatabase
from utils.new_converters import IsBot, BotCommands
from utils.useful import StellaContext, StellaEmbed, realign, aware_utc, try_call
from .models import BotAdded


@dataclass
class BotCommandActivity:
    bot: discord.User
    data: List[Tuple[str, datetime.datetime]]

    @classmethod
    async def convert(cls, ctx: StellaContext, argument: str) -> BotCommandActivity:
        user = await IsBot().convert(ctx, argument)
        query = "SELECT command, time_used " \
                "FROM commands_list " \
                "WHERE bot_id=$1 AND guild_id=$2 " \
                "ORDER BY time_used DESC " \
                "LIMIT 100"
        if not (data := await ctx.bot.pool_pg.fetch(query, user.id, ctx.guild.id)):
            raise NotInDatabase(user)

        return cls(user, [(d['command'], d['time_used']) for d in data])


class CommandHandler(FindBotCog):
    @commands.command(aliases=['findcommands', 'fc', 'fuck'], help="Finds all bots that has a particular command")
    @commands.guild_only()
    async def findcommand(self, ctx: StellaContext, *, command: str):
        sql = 'SELECT bot_id, COUNT(command) "counter" ' \
              'FROM commands_list ' \
              'WHERE command LIKE $1 AND guild_id=$2 ' \
              'GROUP BY bot_id ' \
              'ORDER BY counter DESC'
        data = await self.bot.pool_pg.fetch(sql, command, ctx.guild.id)
        if not data:
            raise commands.CommandError("Looks like i have no data to analyze maaf.")

        @pages(per_page=6)
        async def each_member_list(instance, menu_inter: InteractionPages,
                                   entries: List[Dict[str, Union[str, int]]]) -> discord.Embed:
            offset = menu_inter.current_page * instance.per_page
            embed = StellaEmbed(title=f"All Bots that has `{command}`")
            key = "(\u200b|\u200b)"

            def getter(d):
                bot_id = d['bot_id']
                member = ctx.guild.get_member(bot_id)
                return member.display_name if member else bot_id

            contents = ["`{i}. {bot_name}{k}{counter}`".format(i=i, bot_name=getter(d), k=key, **d)
                        for i, d in enumerate(entries, start=offset + 1)]
            embed.description = "\n".join(realign(contents, key))
            return embed

        menu = InteractionPages(each_member_list(data), generate_page=True)
        await menu.start(ctx)

    @commands.command(aliases=['lastcommands', 'lastbotcommand', 'lastcommand'],
                      help="Showing the first 100 commands of a bot.")
    @commands.guild_only()
    async def lastbotcommands(self, ctx, *, bot: BotCommandActivity):
        @pages(per_page=10)
        async def each_commands_list(instance, menu_interact: InteractionPages,
                                     entries: List[Tuple[str, datetime.datetime]]) -> discord.Embed:
            number = menu_interact.current_page * instance.per_page + 1
            key = "(\u200b|\u200b)"
            list_commands = [f"`{x}. {c} {key} `[{aware_utc(d, mode='R')}]"
                             for x, (c, d) in enumerate(entries, start=number)]
            content = "\n".join(realign(list_commands, key))
            return StellaEmbed(title=f"{bot.bot}'s command activities", description=content)

        menu = InteractionPages(each_commands_list(bot.data), generate_page=True)
        await menu.start(ctx)

    @greedy_parser.command(
        brief="Get all unique command for all bot in a server.",
        help="Get all unique command for all bot in a server that are shown in an "
             "descending order for the unique.",
        aliases=["ac", "acc", "allcommand", "acktually", "act"]
    )
    @commands.guild_only()
    async def allcommands(self, ctx: StellaContext, *, flags: BotListReverse):
        reverse = flags.reverse
        query = "SELECT * FROM " \
                "   (SELECT command, COUNT(command) AS command_count FROM " \
                "       (SELECT DISTINCT bot_id, command FROM commands_list " \
                "       WHERE guild_id=$1 " \
                "       GROUP BY bot_id, command) AS _ " \
                "   GROUP BY command) AS _ " \
                f"ORDER BY command_count {('DESC', '')[reverse]}"

        data = await self.bot.pool_pg.fetch(query, ctx.guild.id)

        @pages(per_page=6)
        async def each_commands_list(instance, menu_inter: InteractionPages,
                                     entries: List[Dict[str, Union[str, int]]]) -> discord.Embed:
            offset = menu_inter.current_page * instance.per_page
            embed = StellaEmbed(title=f"All Commands")
            key = "(\u200b|\u200b)"
            contents = ["`{i}. {command}{k}{command_count}`".format(i=i, k=key, **b)
                        for i, b in enumerate(entries, start=offset + 1)]
            embed.description = "\n".join(realign(contents, key))
            return embed

        menu = InteractionPages(each_commands_list(data))
        await menu.start(ctx)

    @commands.command(aliases=["botcommand", "bc", "bcs"],
                      help="Predicting the bot's command based on the message history.")
    @commands.guild_only()
    async def botcommands(self, ctx: StellaContext, *, bot: BotCommands):
        owner_info = None
        if ctx.guild.id == DISCORD_PY:
            owner_info = await try_call(BotAdded.convert, ctx, str(int(bot)))

        @pages(per_page=6)
        def each_page(instance, menu_inter: InteractionPages, entries: List[str]) -> discord.Embed:
            number = menu_inter.current_page * instance.per_page + 1
            list_commands = "\n".join(f"{x}. {c}[`{bot.get_command(c)}`]" for x, c in enumerate(entries, start=number))
            embed = StellaEmbed.default(ctx, title=f"{bot} Commands[`{bot.total_usage}`]", description=list_commands)
            if owner_info and owner_info.author:
                embed.set_author(icon_url=owner_info.author.display_avatar, name=f"Owner {owner_info.author}")

            return embed.set_thumbnail(url=bot.bot.display_avatar)
        menu = InteractionPages(each_page(bot.commands))
        await menu.start(ctx)
