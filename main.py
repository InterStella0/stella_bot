import asyncio
import collections
import contextlib
import copy
import datetime
import json
import os
import re
import time

from os.path import dirname, join
from typing import List, Optional, Union

import asyncpg
import discord
import humanize
import numpy as np

from aiogithub import GitHub
from discord.ext import commands
from dotenv import load_dotenv

from utils.buttons import PersistentRespondView
from utils.context_managers import UserLock
from utils.decorators import event_check, in_executor, wait_ready
from utils.ipc import IPCData, StellaClient, StellaAPI, StellaFile
from utils.prefix_ai import DerivativeNeuralNetwork, PrefixNeuralNetwork
from utils.useful import ListCall, StellaContext, count_source_lines, print_exception, except_retry

dotenv_path = join(dirname(__file__), 'bot_settings.env')
load_dotenv(dotenv_path)

import utils.library_override

to_call = ListCall()


class StellaBot(commands.Bot):
    def __init__(self, **kwargs):
        self.tester = kwargs.pop("tester", False)
        self.help_src = kwargs.pop("help_src", None)
        self.db = kwargs.pop("db", None)
        self.user_db = kwargs.pop("user_db", None)
        self.pass_db = kwargs.pop("pass_db", None)
        self.color = kwargs.pop("color", None)
        self.websocket_IP = kwargs.pop("websocket_ip")
        self.stella_api = StellaAPI(self)
        self.ipc_key = kwargs.pop("ipc_key")
        self.ipc_port = kwargs.pop("ipc_port")
        self.ipc_client = StellaClient(host=self.websocket_IP, secret_key=self.ipc_key, port=self.ipc_port)
        self.git_token = kwargs.pop("git_token")
        self.error_channel_id = kwargs.pop("error_channel")
        self.bot_guild_id = kwargs.pop("bot_guild")
        self.git = None
        self.pool_pg = None
        self.uptime = None
        self.global_variable = None
        self.all_bot_prefixes = {}
        self.pending_bots = set()
        self.confirmed_bots = set()
        self.token = kwargs.pop("token", None)
        self.blacklist = set()
        self.existing_prefix = {}
        self.cached_context = collections.deque(maxlen=100)
        self.command_running = {}
        self.user_lock = {}
        self._default_prefix = kwargs.pop("default_prefix")
        self._tester_prefix = kwargs.pop("tester_prefix")

        # main bot owner is kept separate
        owner_ids = kwargs.pop("owner_ids")
        self._stella_id, *_ = owner_ids

        super().__init__(
            self.get_prefix,
            owner_ids=set(owner_ids),
            strip_after_prefix=True,
            **kwargs,
        )

        kweights = kwargs.pop("prefix_weights")
        self.prefix_neural_network = PrefixNeuralNetwork.from_weight(*kweights.values())
        self.derivative_prefix_neural = DerivativeNeuralNetwork(kwargs.pop("prefix_derivative"))

    @in_executor()
    def get_prefixes_dataset(self, data: List[List[Union[int, str]]]) -> np.array:
        """Get a list of prefixes from database and calculated through Neural Network"""
        inputs = np.array(data)
        amounts, epoch_times = inputs[:, 1].astype(np.int32), inputs[:, 2].astype(np.float)

        # Normalize datasets into between 0 - 1 for ANN
        # This is done by getting the the current value divided by highest value
        normalized_amount, normalized_epoch = amounts / amounts.max(), epoch_times / epoch_times.max()
        normalized = np.dstack((normalized_amount, normalized_epoch))
        result = self.prefix_neural_network.fit(normalized) * 200
        predicted = np.column_stack((inputs, result.flat[::]))
        return predicted

    async def add_blacklist(self, snowflake_id, reason):
        timed = datetime.datetime.utcnow()
        values = (snowflake_id, reason, timed)
        await self.pool_pg.execute("INSERT INTO blacklist VALUES($1, $2, $3)", *values)
        self.blacklist.add(snowflake_id)
        payload = {
            "snowflake_id": snowflake_id,
            "reason": reason,
            "time": timed.timestamp()
        }
        await self.ipc_client.request("global_blacklist_id", **payload)

    async def remove_blacklist(self, snowflake_id):
        await self.pool_pg.execute("DELETE FROM blacklist WHERE snowflake_id=$1", snowflake_id)
        self.blacklist.remove(snowflake_id)
        await self.ipc_client.request("global_unblacklist_id", snowflake_id=snowflake_id)

    def get_command_signature(self, ctx: StellaContext, command_name: Union[commands.Command, str]) -> str:
        if isinstance(command_name, str):
            if not (command := self.get_command(command_name)):
                raise Exception("Command does not exist for signature.")
        else:
            command = command_name
        return self.help_command.get_command_signature(command, ctx)

    async def after_db(self) -> None:
        """Runs after the db is connected"""
        await to_call.call(self)

    def add_command(self, command: commands.Command) -> None:
        super().add_command(command)
        command.cooldown_after_parsing = True
        if not getattr(command._buckets, "_cooldown", None):
            command._buckets = commands.CooldownMapping.from_cooldown(1, 5, commands.BucketType.user)

    def add_user_lock(self, lock: UserLock):
        self.user_lock.update({lock.user.id: lock})

    async def check_user_lock(self, user: Union[discord.Member, discord.User]):
        if lock := self.user_lock.get(user.id):
            if lock.locked():
                if isinstance(lock, UserLock):
                    raise lock.error
                raise commands.CommandError("You can't invoke another command while another command is running.")
            else:
                self.user_lock.pop(user.id, None)

    async def running_command(self, ctx: StellaContext, **flags):
        dispatch = flags.pop("dispatch", True)
        self.cached_context.append(ctx)
        if dispatch:
            self.dispatch('command', ctx)
        try:
            await self.check_user_lock(ctx.author)
            check = await self.can_run(ctx, call_once=flags.pop("call_once", True))
            if check or not flags.pop("call_check", True):
                ctx.running = True
                await ctx.typing()
                await ctx.command.invoke(ctx)
            else:
                raise commands.CheckFailure('The global check once functions failed.')
        except commands.CommandError as exc:
            if dispatch:
                await ctx.command.dispatch_error(ctx, exc)
            if flags.pop("redirect_error", False):
                raise
        else:
            if dispatch:
                self.dispatch('command_completion', ctx)
        finally:
            ctx.running = False
            self.command_running.pop(ctx.message.id, None)

    async def invoke(self, ctx: StellaContext, **flags) -> None:
        dispatch = flags.get("dispatch", True)
        if ctx.command is not None:
            run_in_task = flags.pop("in_task", True)
            if run_in_task:
                command_task = self.loop.create_task(self.running_command(ctx, **flags))
                self.command_running.update({ctx.message.id: command_task})
            else:
                await self.running_command(ctx, **flags)
        elif ctx.invoked_with:
            exc = commands.CommandNotFound('Command "{}" is not found'.format(ctx.invoked_with))
            if dispatch:
                self.dispatch('command_error', ctx, exc)

            if flags.pop("redirect_error", False):
                raise exc

    def sync_is_owner(self, user: discord.User) -> bool:
        return user.id in self.owner_ids

    @property
    def stella(self) -> Optional[discord.User]:
        """Returns discord.User of the owner"""

        return self.get_user(self._stella_id)

    @property
    def error_channel(self) -> discord.TextChannel:
        """Gets the error channel for the bot to log."""
        return self.get_guild(self.bot_guild_id).get_channel(self.error_channel_id)

    async def setup_hook(self) -> None:
        await bot.stella_api.generate_token()
        self.git = GitHub(self.git_token)  # github uses aiohttp in init, need to put in async context
        await self.after_db()
        self.loop.create_task(self.after_ready())

    async def after_ready(self):
        await self.wait_until_ready()
        self.add_view(PersistentRespondView(self))
        await self.greet_server()

    async def greet_server(self):
        self.ipc_client(self.user.id)
        try:
            await self.ipc_client.subscribe()
        except Exception as e:
            print_exception("Failure to connect to server.", e)
        else:
            if data := await self.ipc_client.request("get_restart_data"):
                if (channel := self.get_channel(data["channel_id"])) and isinstance(channel, discord.abc.Messageable):
                    message = await channel.fetch_message(data["message_id"])
                    message_time = discord.utils.utcnow() - message.created_at
                    time_taken = humanize.precisedelta(message_time)
                    await message.edit(content=f"Restart lasted {time_taken}")
            print("Server connected.")

    @to_call.append
    async def loading_cog(self) -> None:
        """Loads the cog"""
        exclude = "_", "."

        cogs = [file for file in os.listdir("cogs") if not file.startswith(exclude)]
        for cog in cogs:
            name = cog[:-3] if cog.endswith(".py") else cog
            try:
                await self.load_extension(f"cogs.{name}")
            except Exception as e:
                print_exception('Ignoring exception while loading up {}:'.format(name), e)
            else:
                print(f"cog {name} is loaded")

        await bot.load_extension("jishaku")

    @to_call.append
    async def fill_bots(self) -> None:
        """Fills the pending/confirmed bots in discord.py"""
        for attr in "pending", "confirmed":
            record = await self.pool_pg.fetch(f"SELECT bot_id FROM {attr}_bots")
            setattr(self, f"{attr}_bots", set(x["bot_id"] for x in record))

        print("Bots list are now filled.")

    @to_call.append
    async def fill_blacklist(self) -> None:
        """Loading up the blacklisted users."""
        records = await self.pool_pg.fetch("SELECT snowflake_id FROM blacklist")
        self.blacklist = {r["snowflake_id"] for r in records}

    async def get_prefix(self, message: discord.Message) -> Union[List[str], str]:
        """A note to self: update this docstring each time i edit code.

        Check if bot is in woman mode. If true, return tester prefix.

        Set snowflake_id to id of guild if message originates in guild (guild object is present). Otherwise author id.

        Go to cached prefixes and try to get prefix using snowflake_id i created above. If found, skip next paragraph.

        If prefix is not present, select prefix field from internal_prefix postgres table using snowflake_id i created
        earlier as a key then try to get prefix from returned data. If nothing was returned, use default prefix, idrc.
        After doing that put resulting prefix back into in-memory cache because constant postgres lookups are no good.

        Escape special characters in prefix, then compile it as regular expression using case insensivity flag (yes, i
        know i could compile them in cache but has anyone asked?). Try matching the beginning of message content using
        regex. If match found, return match group 0 which will be just the prefix itself. Otherwise return the stored
        prefix/the default prefix.
        """
        if self.tester:
            return self._tester_prefix

        snowflake_id = message.guild.id if message.guild else message.author.id

        if (prefix := self.existing_prefix.get(snowflake_id)) is None:
            data = await self.pool_pg.fetchrow(
                "SELECT prefix FROM internal_prefix WHERE snowflake_id=$1",
                snowflake_id,
            )
            prefix = self._default_prefix if data is None else data["prefix"]
            self.existing_prefix[snowflake_id] = prefix

        if match := re.match(re.escape(prefix), message.content, flags=re.I):
            return match[0]
        return prefix

    def get_message(self, message_id: int) -> discord.Message:
        """Gets the message from the cache"""
        return self._connection._get_message(message_id)

    async def get_context(self, message: discord.Message, *,
                          cls: Optional[commands.Context] = StellaContext) -> Union[StellaContext, commands.Context]:
        """Override get_context to use a custom Context"""
        context = await super().get_context(message, cls=cls)
        context.view.update_values()
        return context

    async def process_commands(self, message: discord.Message) -> None:
        """Override process_commands to call typing every invoke"""
        if message.author.bot:
            return

        ctx = await self.get_context(message)
        if ctx.valid and getattr(ctx.cog, "qualified_name", None) != "Jishaku":
            await ctx.typing()
        await self.invoke(ctx)

    async def upload_file(self, *, byte: bytes, filename: str, retries: int = 4) -> StellaFile:
        return await self.stella_api.upload_file(file=byte, filename=filename, retries=retries)

    async def main(self) -> None:
        """Starts the bot properly"""
        try:
            print("Connecting to database...")
            start = time.time()
            pool_pg = await asyncpg.create_pool(
                database=self.db,
                user=self.user_db,
                password=self.pass_db
            )
            print(f"Connected to the database ({time.time() - start})s")
        except Exception as e:
            print_exception("Could not connect to database:", e)
            return

        async with self, pool_pg:
            self.uptime = datetime.datetime.utcnow()
            self.pool_pg = pool_pg
            await self.start(self.token)

    def starter(self):
        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(self.main())

    async def close(self) -> None:
        await super().close()
        await self.stella_api.close()


intent_data = {x: True for x in ('guilds', 'members', 'emojis', 'messages', 'reactions', 'message_content')}
intents = discord.Intents(**intent_data)
with open("d_json/bot_var.json") as states_bytes:
    states = json.load(states_bytes)
bot_data = {
    "token": states.get("TOKEN"),
    "default_prefix": states.get("DEFAULT_PREFIX", "uwu "),
    "tester_prefix": states.get("TESTER_PREFIX", "?uwu "),
    "bot_guild": states.get("BOT_GUILD"),
    "error_channel": states.get("ERROR_CHANNEL"),
    "color": 0xffcccb,
    "db": states.get("DATABASE"),
    "user_db": states.get("USER"),
    "pass_db": states.get("PASSWORD"),
    "tester": states.get("TEST"),
    "help_src": states.get("HELP_SRC"),
    "ipc_port": states.get("IPC_PORT"),
    "ipc_key": states.get("IPC_KEY"),
    "intents": intents,
    "owner_ids": states.get("OWNER_IDS"),
    "websocket_ip": states.get("WEBSOCKET_IP"),
    "prefix_weights": states.get("PREFIX_WEIGHT"),
    "prefix_derivative": states.get("PREFIX_DERIVATIVE_PATH"),
    "git_token": states.get("GIT_TOKEN"),
    "activity": discord.Activity(type=discord.ActivityType.listening, name="logged to my pc."),
    "description": "{}'s personal bot that is partially for the public. "
                   f"Written with only `{count_source_lines('.'):,}` lines. plz be nice"
}

bot = StellaBot(**bot_data)


@bot.event
async def on_ready() -> None:
    print("bot is ready")


@bot.event
async def on_disconnect() -> None:
    print("bot disconnected")


@bot.event
async def on_connect() -> None:
    print("bot connected")


@bot.event
@wait_ready(bot=bot)
@event_check(lambda m: not m.author.bot and not bot.tester or bot.sync_is_owner(m.author))
async def on_message(message: discord.Message) -> None:
    if re.fullmatch(rf"<@!?{bot.user.id}>", message.content):
        await message.channel.send(f"My prefix is `{await bot.get_prefix(message)}`")
        return

    if message.author.id in bot.blacklist or getattr(message.guild, "id", None) in bot.blacklist:
        return

    if await bot.is_owner(message.author) and message.attachments:
        ctx = await bot.get_context(message)
        if ctx.valid:
            return await bot.invoke(ctx)

        text_command = ["text/plain", "text/x-python"]
        for a in message.attachments:
            with contextlib.suppress(ValueError):
                index = text_command.index(a.content_type)
                attachment = await a.read()
                new_message = copy.copy(message)
                # Yes, i'm extremely lazy to get the command, and call the codeblock converter
                # Instead, i make a new message, and make it a command.
                if index:
                    prefix = await bot.get_prefix(message)
                    new_message.content = f"{prefix}jsk py ```py\n{attachment.decode('utf-8')}```"
                else:
                    new_message.content = attachment.decode('utf-8')
                await bot.process_commands(new_message)

    await bot.process_commands(message)


@bot.ipc_client.listen()
async def on_restarting_server(_: IPCData) -> None:
    print("Server restarting...")
    server = bot.ipc_client
    await server.session.close()
    print("Server waiting for server respond.")
    await asyncio.sleep(10)
    print("Server re-establishing connection")
    await server.init_sock()
    print("Server Connection Successful.")


@bot.ipc_client.server_request()
async def on_get_info(data: IPCData) -> None:
    return {
        "guild_amount": len(bot.guilds),
        "user_amount": len(bot.users),
        "latency": bot.latency,
        "launch_time": bot.uptime.isoformat(),
        "codelines": count_source_lines('.'),
        "last_commands": [
            {
                "author": str(ctx.author),
                "command": ctx.command.qualified_name,
                "created_at": ctx.message.created_at.isoformat()
            }
            for ctx in [*bot.cached_context][:-10:-1]
        ]
    }


@bot.ipc_client.server_request()
async def on_get_invite(data: IPCData) -> None:
    return {"invite": discord.utils.oauth_url(bot.user.id)}


@bot.ipc_client.listen()
async def on_kill(data: IPCData) -> None:
    print("Kill has been ordered", data)
    await bot.close()


bot.starter()
