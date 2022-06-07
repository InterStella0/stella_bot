from __future__ import annotations

import datetime
import json
from typing import Dict, List, Optional, Union, Literal

import discord
import tabulate
from discord.ext import commands
from discord.ext.commands import Greedy
from jishaku.codeblocks import Codeblock

from .baseclass import BaseMyselfCog
from .flags import AddBotFlag, SQLFlag, ClearFlag
from utils import flags as flg
from utils import greedy_parser
from utils.buttons import InteractionPages
from utils.decorators import pages
from utils.greedy_parser import UntilFlag
from utils.new_converters import (CodeblockConverter, IsBot)
from utils.useful import (StellaContext, StellaEmbed, aware_utc)
from .interaction import InteractionServers, show_server


class Miscellaneous(BaseMyselfCog):
    @greedy_parser.command()
    async def addbot(self, ctx: StellaContext, bot: IsBot, *, flags: AddBotFlag):
        flags = dict(flags)
        new_data = {'bot_id': bot.id}
        if message := flags.pop('message'):
            new_data['author_id'] = message.author.id
            new_data['reason'] = message.content
            new_data['requested_at'] = message.created_at.replace(tzinfo=None)
            new_data['jump_url'] = message.jump_url

        if auth := flags.pop('author'):
            new_data['author_id'] = auth.id
        for flag, item in flags.items():
            if item:
                new_data.update({flag: item})

        if exist := await self.bot.pool_pg.fetchrow("SELECT * FROM confirmed_bots WHERE bot_id=$1", bot.id):
            existing_data = dict(exist)
            existing_data.update(new_data)
            query = "UPDATE confirmed_bots SET "
            queries = [f"{k}=${i}" for i, k in enumerate(list(existing_data)[1:], start=2)]
            query += ", ".join(queries)
            query += " WHERE bot_id=$1"
            new_data = existing_data
        else:
            query = "INSERT INTO confirmed_bots VALUES($1, $2, $3, $4, $5, $6)"
            for x in "author_id", "reason", "requested_at", "jump_url":
                if new_data.get(x) is None:
                    new_data[x] = None
            new_data['joined_at'] = bot.joined_at.replace(tzinfo=None)
        values = [*new_data.values()]
        result = await self.bot.pool_pg.execute(query, *values)
        await ctx.maybe_reply(result)
        self.bot.confirmed_bots.add(bot.id)

    @greedy_parser.command()
    @commands.bot_has_permissions(read_message_history=True)
    async def clear(self, ctx: StellaContext, amount: Optional[int] = 50, *, flags: ClearFlag):
        def check(m: discord.Message) -> bool:
            return m.author == ctx.me

        def less_two_weeks(message: discord.Message) -> bool:
            return message.created_at > datetime.datetime.utcnow() - datetime.timedelta(days=14)

        flag = dict(flags)
        must = flag["must"]
        purge_enable = ctx.channel.permissions_for(ctx.me).manage_messages
        if messages := flag.get("messages"):
            if purge_enable:
                await ctx.channel.delete_messages(messages)
            else:
                for m in messages:
                    await m.delete()
        elif not must and purge_enable:
            await ctx.channel.purge(limit=amount, check=check)
        else:
            counter = 0
            to_delete = []
            async for m in ctx.history(limit=(None, amount)[not must]):
                if check(m):
                    counter += 1
                    if purge_enable and less_two_weeks(m):
                        to_delete.append(m)
                    else:
                        await m.delete()

                if counter == amount:
                    break
            if purge_enable and to_delete:
                for bulk in discord.utils.as_chunks(to_delete, 100):
                    await ctx.channel.delete_messages(bulk)
        
        await ctx.confirmed()

    @commands.command()
    async def dispatch(self, ctx: StellaContext, message: discord.Message):
        self.bot.dispatch('message', message)
        await ctx.confirmed()

    @greedy_parser.command()
    async def sql(self, ctx: StellaContext, query: UntilFlag[CodeblockConverter], *, flags: SQLFlag):
        flags = dict(flags)
        MR = flags.get("max_row")
        to_run = query.content
        method = fetch = self.bot.pool_pg.fetch
        if to_run.lower().startswith(("insert", "update", "delete", "create", "drop")):
            if "returning" not in to_run.lower():
                method = self.bot.pool_pg.execute

        @pages(per_page=MR)
        async def tabulation(self, menu, entries):
            if not isinstance(entries, list):
                entries = [entries]
            offset = menu.current_page * self.per_page + 1
            to_pass = {"no": [*range(offset, offset + len(entries))]} if not nn else {}
            for d in entries:
                for k, v in d.items():
                    value = to_pass.setdefault(k, [])
                    value.append(v)
            table = tabulate.tabulate(to_pass, 'keys', 'pretty')
            return f"```py\n{table}```"

        try:
            rows = await method(to_run)
            nn = flags.pop("not_number")
            if method is fetch:
                menu = InteractionPages(tabulation(rows))
                await menu.start(ctx)
            else:
                await ctx.maybe_reply(rows)
        except Exception as e:
            raise commands.CommandError(str(e))

    @commands.group(invoke_without_command=True)
    async def blacklist(self, ctx: StellaContext, snowflake_id: Optional[Union[discord.Guild, discord.User]]):
        E = Union[discord.User, discord.Guild, int]

        def user_guild(data: Dict[str, Union[int, str]]) -> E:
            uid = data["snowflake_id"]
            if not (ob := ctx.bot.get_user(uid)):
                if not (ob := ctx.bot.get_guild(uid)):
                    return uid

            return ob

        @pages(per_page=10)
        async def blacklist_result(self, menu: InteractionPages, entries: List[E]) -> discord.Embed:
            s = menu.current_page * self.per_page + 1
            content = "\n".join(f"{i}. {uid}" for i, uid in enumerate(entries, start=s))
            return discord.Embed(title="blacklist", description=content)

        query = "SELECT * FROM blacklist"
        if snowflake_id is None:
            data = await self.bot.pool_pg.fetch(query)
            ip = InteractionPages(blacklist_result([*map(user_guild, data)]))
            await ip.start(ctx)
        else:
            if data := await self.bot.pool_pg.fetchrow(query + " WHERE snowflake_id=$1", snowflake_id.id):
                uid = user_guild(data)
                reason = data["reason"]
                embed = StellaEmbed.default(ctx, title=f"Blacklist for {uid}", description=f"**Reason:**\n{reason}")
                embed.add_field(name="Time of blacklist", value=aware_utc(data["timestamp"]))
                await ctx.maybe_reply(embed=embed)
            else:
                await ctx.maybe_reply(f"`{snowflake_id}` is not blacklisted.")

    @blacklist.command(name="add")
    async def blaclist_add(self, ctx: StellaContext, snowflake_ids: Greedy[Union[discord.Guild, discord.User]], *, reason: str):
        for uid in snowflake_ids:
            await self.bot.add_blacklist(uid.id, reason)
        names = ", ".join(map(str, snowflake_ids))
        await ctx.maybe_reply(f"{names} are now blacklisted.")

    @blacklist.command(name="remove")
    async def blaclist_remove(self, ctx: StellaContext, snowflake_ids: Greedy[Union[discord.Guild, discord.User]]):
        for uid in snowflake_ids:
            await self.bot.remove_blacklist(uid.id)
        names = ", ".join(map(str, snowflake_ids))
        await ctx.maybe_reply(f"{names} are no longer blacklisted.")

    @commands.command()
    async def restart(self, ctx: StellaContext, *, reason: Optional[str] = "No reason"):
        m = await ctx.maybe_reply("Restarting...")
        payload = {
            "reason": reason,
            "channel_id": ctx.channel.id,
            "message_id": m.id
        }
        await self.bot.ipc_client.request("restart_connection", **payload)
        await self.bot.close()

    @commands.command()
    async def report_end(self, ctx: StellaContext, message: discord.Message):
        query = """SELECT report_id, user_id 
                   FROM reports WHERE report_id=(
                    SELECT report_id 
                    FROM report_respond 
                    WHERE message_id=$1
                    LIMIT 1
                   )"""
        data = await self.bot.pool_pg.fetchrow(query, message.id)
        report_id = data["report_id"]
        user_id = data["user_id"]
        await self.bot.pool_pg.execute("UPDATE reports SET finish='t' WHERE report_id=$1", report_id)

        # Remove the view from the other user
        query_interface = """SELECT user_id, MAX(interface_id) "recent_interface_id"
                             FROM report_respond WHERE report_id=$1
                             GROUP BY user_id
                             HAVING user_id=$2"""
        values_interface = (report_id, self.bot.stella.id)
        interface_id = await self.bot.pool_pg.fetchval(query_interface, *values_interface, column="recent_interface_id")
        user = self.bot.get_user(user_id)
        channel = await user.create_dm()
        msg = channel.get_partial_message(interface_id)
        await msg.edit(view=None)

        # Send to myself
        desc_opposite = f"{ctx.author} has ended the report."
        embed = StellaEmbed.to_error(title="End of Report", description=desc_opposite)
        await msg.reply(embed=embed)
        await message.reply(f"You've forcefully ended the report. (`{report_id}`)")

    @commands.command()
    async def botupdate(self, ctx: StellaContext):
        jsk = self.bot.get_command("jsk git")
        await jsk(ctx, argument=Codeblock("me", "pull"))

    @greedy_parser.command()
    async def changebotvar(self, ctx: StellaContext, key: str, value: UntilFlag[str], *, type: flg.BotVarFlag):
        with open("d_json/bot_var.json") as b:
            bot_var = json.load(b)

        converter = eval(type.type)
        new_val = converter(value)
        bot_var[key] = new_val
        with open("d_json/bot_var.json", "w") as w:
            json.dump(bot_var, w, indent=4)
        await ctx.confirmed()

    @commands.command()
    async def servers(self, ctx: StellaContext):
        values = ctx.bot.guilds
        values.sort(key=lambda x: x.me.joined_at)
        await InteractionServers(show_server(values)).start(ctx)

    @commands.command()
    async def sync(self, ctx: StellaContext, guild: Union[Literal["all"], discord.Guild] = commands.param(
            converter=Union[Literal["all"], discord.Guild],
            default=lambda x: x.guild,
            displayed_default="Current Guild"
        )):
        guild = guild if guild != "all" else None
        await self.bot.tree.sync(guild=guild)
        desc = f"{guild} guild" if guild is not None else f"{len(self.bot.guilds)} guilds"
        await ctx.embed(title="Synced", description=desc)
