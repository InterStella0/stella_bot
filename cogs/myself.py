from __future__ import annotations

import asyncio
import contextlib
import datetime
import io
import json
import textwrap
import time
import traceback
from typing import (TYPE_CHECKING, Any, Coroutine, Dict, Generator, List,
                    Optional, Tuple, Union, Literal)

import discord
import tabulate
from discord.ext import commands
from discord.ext.commands import Greedy
from jishaku.codeblocks import Codeblock, codeblock_converter
from utils import flags as flg
from utils import greedy_parser, menus
from utils.buttons import InteractionPages
from utils.decorators import event_check, pages
from utils.greedy_parser import GreedyParser, Separator, UntilFlag
from utils.new_converters import (CodeblockConverter, DatetimeConverter, IsBot,
                                  JumpValidator, ValidCog)
from utils.useful import (StellaContext, StellaEmbed, aware_utc, call,
                          empty_page_format, print_exception, text_chunker, try_call)

if TYPE_CHECKING:
    from main import StellaBot


@pages()
async def show_result(self, menu: menus.MenuBase, entry: str) -> str:
    return f"```py\n{entry}```"


class AddBotFlag(commands.FlagConverter):
    joined_at: Optional[DatetimeConverter]
    jump_url: Optional[JumpValidator]
    requested_at: Optional[DatetimeConverter]
    reason: Optional[str]
    message: Optional[discord.Message]
    author: Optional[discord.Member]


class ClearFlag(commands.FlagConverter):
    must: Optional[bool] = flg.flag(default=False)
    messages: Optional[Tuple[discord.Message, ...]] = flg.flag(default=None)


class SQLFlag(commands.FlagConverter):
    not_number: Optional[bool] = flg.flag(aliases=["NN"], default=False)
    max_row: Optional[int] = flg.flag(aliases=["MR"], default=12)


class Myself(commands.Cog):
    """Commands for stella"""
    def __init__(self, bot: StellaBot):
        self.bot = bot

    async def cog_check(self, ctx: StellaContext) -> bool:
        return await commands.is_owner().predicate(ctx)  # type: ignore

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

    @commands.command()
    async def su(self, ctx: StellaContext, member: Union[discord.Member, discord.User], *, content: str):
        message = ctx.message
        message.author = member
        message.content = ctx.prefix + content
        self.bot.dispatch("message", message)
        await ctx.confirmed()

    @commands.command(name="eval", help="Eval for input/print feature", aliases=["e", "ev", "eva"])
    async def _eval(self, ctx: StellaContext, *, code: codeblock_converter):
        loop = ctx.bot.loop
        stdout = io.StringIO()

        def sending_print() -> None:
            nonlocal stdout
            content = stdout.getvalue()
            if content:
                printing(content, now=True)
                stdout.truncate(0)
                stdout.seek(0)

        # Shittiest code I've ever wrote remind me to think of another way
        def run_async(coro: Coroutine[Any, Any, Any], wait_for_value: Optional[bool] = True) -> Any:
            if wait_for_value:
                sending_print()
                ctx.waiting = datetime.datetime.utcnow() + datetime.timedelta(seconds=60)
                ctx.result = None

                async def getting_result() -> None:
                    ctx.result = await coro

                run = run_async(getting_result(), wait_for_value=False)
                while ctx.waiting > datetime.datetime.utcnow() and not run.done():
                    time.sleep(1)
                if not run.done():
                    raise asyncio.TimeoutError(f"{coro} took to long to give a result")
                return ctx.result

            task = loop.create_task(coro)

            def coroutine_dies(target_task: asyncio.Task) -> None:
                ctx.failed = target_task.exception()

            task.add_done_callback(coroutine_dies)
            return task

        def printing(*content: str, now: Optional[bool] = False, channel: Optional[discord.abc.Messageable] = ctx,
                     reply: Optional[bool] = True, mention: Optional[bool]=False, **kwargs: Any) -> None:
            async def sending(cont: str) -> None:
                nonlocal channel, reply, mention
                if c := channel is not ctx:
                    channel = await commands.TextChannelConverter().convert(ctx, str(channel))

                attr = ("send", "reply")[reply is not c]
                sent = getattr(channel, attr)
                text = textwrap.wrap(cont, 1000, replace_whitespace=False)
                ctx.channel_used = channel if channel is not ctx else ctx.channel
                if len(text) == 1:
                    kwargs = {"content": cont}
                    if attr == "reply":
                        kwargs.update({"mention_author": mention})
                    await sent(**kwargs)
                else:
                    menu = InteractionPages(empty_page_format([*map("```{}```".format, text)]))
                    await menu.start(ctx)

            if now:
                showing = " ".join(map(lambda x: (str(x), '\u200b')[x == ''], content if content else ('\u200b',)))
                run_async(sending(showing), wait_for_value=False)
            else:
                print(*content, **kwargs)

        def inputting(*content: str, channel: Optional[discord.abc.Messageable] = ctx,
                      target: Tuple[int, ...] = (ctx.author.id,), **kwargs: Any) -> Optional[str]:
            target = discord.utils.SnowflakeList(target, is_sorted=True)

            async def waiting_respond() -> discord.Message:
                return await ctx.bot.wait_for("message", check=waiting, timeout=60)

            def waiting(m: discord.Message) -> bool:
                return target.has(m.author.id) and m.channel == ctx.channel_used

            printing(*content, channel=channel, **kwargs)
            if result := run_async(waiting_respond()):
                return result.content

        async def giving_emote(e: str) -> None:
            if ctx.channel.permissions_for(ctx.me).external_emojis:
                await ctx.message.add_reaction(e)

        async def starting(startup: datetime.datetime) -> None:
            ctx.running = True
            while ctx.running:
                if datetime.datetime.utcnow() > startup + datetime.timedelta(seconds=5):
                    await giving_emote("<:next_check:754948796361736213>")
                    break
                await asyncio.sleep(1)

        variables = {
            "discord": discord,
            "commands": commands,
            "_channel": ctx.channel,
            "_bot": self.bot,
            "_ctx": ctx,
            "print": printing,
            "input": inputting,
            "_message": ctx.message,
            "_author": ctx.author,
            "_await": run_async
        }

        values = code.content.splitlines()
        if not values[-1].startswith(("return", "raise", " ", "yield")):
            values[-1] = f"return {values[-1]}"
        values.insert(0, "yield")
        values = [f"{'':>4}{v}" for v in values]
        values.insert(0, "def _to_run():")

        def running() -> Generator[Any, None, None]:
            yield (yield from variables['_to_run']())

        def in_exec() -> None:
            loop.create_task(starting(datetime.datetime.utcnow()))
            with contextlib.redirect_stdout(stdout):
                for result in running():
                    sending_print()
                    if result is not None:
                        loop.create_task(ctx.send(result))
        try:
            exec("\n".join(values), variables)
            await loop.run_in_executor(None, in_exec)
            if ctx.failed:
                raise ctx.failed from None
        except Exception as e:
            ctx.running = False
            await giving_emote("<:crossmark:753620331851284480>")
            lines = traceback.format_exception(type(e), e, e.__traceback__)
            error_trace = f"Evaluation failed:\n{''.join(lines)}"
            await ctx.reply(f"{stdout.getvalue()}```py\n{error_trace}```", delete_after=60)
        else:
            ctx.running = False
            await ctx.confirmed()

    @commands.Cog.listener()
    @event_check(lambda s, b, a: (b.content and a.content) or b.author.bot)
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if await self.bot.is_owner(before.author) and not before.embeds and not after.embeds:
            if context := discord.utils.find(lambda ctx: ctx.message == after, self.bot.cached_context):
                await context.reinvoke(message=after)
            else:
                await self.bot.process_commands(after)

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

    async def cogs_handler(self, ctx: StellaContext, extensions: ValidCog,
                           method: Literal["load", "unload", "reload"]) -> None:
        async def do_cog(exts: str) -> str:
            try:
                func = getattr(self.bot, f"{method}_extension")
                await func(f"cogs.{exts}")
            except Exception as e:
                return f"cogs.{exts} failed to {method}: {e}"
            else:
                return f"cogs.{exts} is {method}ed"

        outputs = await asyncio.gather(*map(do_cog, extensions))
        await ctx.embed(description="\n".join(map(str, outputs)))

    @greedy_parser.command()
    async def sql(self, ctx: StellaContext, query: UntilFlag[CodeblockConverter], *, flags: SQLFlag):
        flags = dict(flags)
        MR = flags.get("max_row")
        to_run = query.content
        method = fetch = self.bot.pool_pg.fetch
        if to_run.lower().startswith(("insert", "update", "delete", "create", "drop")):
            if "returning" not in to_run.lower():
                method = self.bot.pool_pg.execute

        rows = await method(to_run)
        nn = flags.pop("not_number")

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
        if method is fetch:
            menu = InteractionPages(tabulation(rows))
            await menu.start(ctx)
        else:
            await ctx.maybe_reply(rows)

    @greedy_parser.command()
    async def reinvoke(self, ctx: StellaContext, command: greedy_parser.UntilFlag[str], *, flags: flg.ReinvokeFlag):
        message = ctx.message
        message.author = flags.user or ctx.author
        message.content = ctx.prefix + command
        context = await self.bot.get_context(message)
        try:
            c_flags = dict(flags)
            if c_flags.pop("redirect", True):
                c_flags["redirect_error"] = True
                c_flags["dispatch"] = False
            await self.bot.invoke(context, in_task=False, **c_flags)
            await ctx.confirmed()
        except commands.CommandError as e:
            error = print_exception(f'Exception raised while reinvoking {context.command}:', e, _print=False)
            chunked = text_chunker(error, max_newline=10)
            await InteractionPages(show_result(chunked)).start(ctx)

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
    async def cancel(self, ctx: StellaContext, message: Union[discord.Message, discord.Object]):
        with contextlib.suppress(KeyError):
            task = self.bot.command_running.pop(message.id)
            if task is not None and not task.done():
                task.cancel()
                await message.reply("This command was cancelled.")
            else:
                await ctx.maybe_reply("This command was already done.")
            return await ctx.confirmed()
        await ctx.maybe_reply("Unable to find a running command from this message.")

    @commands.Cog.listener()
    async def on_command(self, ctx: StellaContext):
        ctx.done = False

    @commands.Cog.listener()
    async def on_command_completion(self, ctx: StellaContext):
        if not ctx.done:
            ctx.done = True

    @commands.command(name="load", aliases=["cload", "loads"], cls=GreedyParser)
    async def _cog_load(self, ctx, extension: Separator[ValidCog]):
        await self.cogs_handler(ctx, extension, "load")

    @commands.command(name="unload", aliases=["cunload", "unloads"], cls=GreedyParser)
    async def _cog_unload(self, ctx, extension: Separator[ValidCog]):
        await self.cogs_handler(ctx, extension, "unload")

    @commands.command(name="reload", aliases=["creload", "reloads"], cls=GreedyParser)
    async def _cog_reload(self, ctx, extension: Separator[ValidCog]):
        await self.cogs_handler(ctx, extension, "reload")


async def setup(bot: StellaBot) -> None:
    await bot.add_cog(Myself(bot))
