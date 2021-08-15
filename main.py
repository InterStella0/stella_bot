import time
import re
import asyncpg
import asyncio
import aiohttp
import datetime
import os
import copy
import discord
import contextlib
import humanize
import json
import numpy as np
from aiogithub import GitHub
from typing import Union, List, Optional, Dict, Any
from utils.prefix_ai import PrefixNeuralNetwork, DerivativeNeuralNetwork
from utils.useful import StellaContext, ListCall, count_python
from utils.decorators import event_check, wait_ready, in_executor
from utils.ipc import StellaClient, StellaWebSocket
from discord.ext import commands
from discord.gateway import ReconnectWebSocket
from discord.backoff import ExponentialBackoff
from dotenv import load_dotenv
from os.path import join, dirname
from utils.useful import call, print_exception
from utils.buttons import PersistentRespondView
from os import environ

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
        self.socket_states = kwargs.pop("socket_states")
        self.websocket_IP = kwargs.pop("websocket_ip")
        self.ipc_key = kwargs.pop("ipc_key")
        self.ipc_port = kwargs.pop("ipc_port")
        self.ipc_client = StellaClient(host=self.websocket_IP, secret_key=self.ipc_key, port=self.ipc_port)
        self.git_token = kwargs.pop("git_token")
        self.git = GitHub(self.git_token)
        self.pool_pg = None
        self.uptime = None
        self.global_variable = None
        self.all_bot_prefixes = {}
        self.pending_bots = set()
        self.confirmed_bots = set()
        self.token = kwargs.pop("token", None)
        self.blacklist = set()
        self.cached_users = {}
        self.existing_prefix = {}
        super().__init__(self.get_prefix, **kwargs)

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

    async def invoke(self, ctx: StellaContext, **flags) -> None:
        dispatch = flags.pop("dispatch", True)
        if ctx.command is not None:
            if dispatch:
                self.dispatch('command', ctx)
            try:
                check = await self.can_run(ctx, call_once=flags.pop("call_once", True))

                if check or not flags.pop("call_check", True):
                    if ctx.command.name == "jishaku":
                        maximum = self._connection.max_messages
                        self._connection.max_messages = "<:uwuqueen:785765496393433129>"
                        await ctx.command.invoke(ctx)
                        self._connection.max_messages = maximum
                    else:
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
        elif ctx.invoked_with:
            exc = commands.CommandNotFound('Command "{}" is not found'.format(ctx.invoked_with))
            if dispatch:
                self.dispatch('command_error', ctx, exc)

            if flags.pop("redirect_error", False):
                raise exc

    @property
    def stella(self) -> Optional[discord.User]:
        """Returns discord.User of the owner"""
        return self.get_user(self.owner_id)

    @property
    def error_channel(self) -> discord.TextChannel:
        """Gets the error channel for the bot to log."""
        guild_id = int(environ.get("BOT_GUILD"))
        channel_id = int(environ.get("ERROR_CHANNEL"))
        return self.get_guild(guild_id).get_channel(channel_id)

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
    def loading_cog(self) -> None:
        """Loads the cog"""
        cogs = *(file[:-3] for file in os.listdir("cogs") if file.endswith(".py")), "jishaku"
        for cog in cogs:
            ext = "cogs." if cog != "jishaku" else ""
            if error := call(self.load_extension, f"{ext}{cog}", ret=True):
                print_exception('Ignoring exception while loading up {}:'.format(cog), error)
            else:
                print(f"cog {cog} is loaded")

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
        """Handles custom prefixes, this function is invoked every time process_command method is invoke thus returning
        the appropriate prefixes depending on the guild."""
        query = "SELECT prefix FROM internal_prefix WHERE snowflake_id=$1"
        snowflake_id = message.guild.id if message.guild else message.author.id
        if self.tester:
            return "+="

        if not (prefix := self.existing_prefix.get(snowflake_id)):
            data = await self.pool_pg.fetchrow(query, snowflake_id) or {}
            prefix = self.existing_prefix.setdefault(snowflake_id, data.get("prefix") or "uwu ")

        comp = re.compile(f"^({re.escape(prefix)}).*", flags=re.I)
        match = comp.match(message.content)
        if match is not None:
            return match.group(1)
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
            await ctx.trigger_typing()
        await self.invoke(ctx)

    async def connecting(self, ws_params: Dict[str, Any]) -> None:
        """Attempt connection and establish events"""
        coro = StellaWebSocket.from_client(self, **ws_params)
        self.ws = await asyncio.wait_for(coro, timeout=120)
        ws_params['initial'] = False
        while True:
            await self.ws.poll_event()

    async def handle_connect_errors(self, ws_params: Dict[str, Any], reconnect: bool, err: Any, backoff: ExponentialBackoff) -> bool:
        """Handles any connection errors if it is not a Reconnect Websocket error."""
        self.dispatch('disconnect')
        if not reconnect:
            await self.close()
            if isinstance(err, discord.ConnectionClosed) and err.code == 1000:
                return False
            raise

        if self.is_closed():
            return False

        if isinstance(err, OSError) and err.errno in (54, 10054):
            ws_params.update(sequence=self.ws.sequence, initial=False, resume=True, session=self.ws.session_id)
            return True

        if isinstance(err, discord.ConnectionClosed):
            if err.code == 4014:
                raise discord.PrivilegedIntentsRequired(err.shard_id) from None
            if err.code != 1000:
                await self.close()
                raise

        retry = backoff.delay()
        await asyncio.sleep(retry)
        ws_params.update(sequence=self.ws.sequence, resume=True, session=self.ws.session_id)
        return True

    async def connect(self, *, reconnect: bool = True) -> None:
        """Handles discord connections"""
        backoff = ExponentialBackoff()
        ws_params = {
            'initial': True,
            'shard_id': self.shard_id
        }
        while not self.is_closed():
            try:
                await self.connecting(ws_params)
            except ReconnectWebSocket as e:
                self.dispatch('disconnect')
                ws_params.update(sequence=self.ws.sequence, resume=e.resume, session=self.ws.session_id)
            except (OSError, discord.HTTPException, discord.GatewayNotFound, discord.ConnectionClosed,
                    aiohttp.ClientError, asyncio.TimeoutError) as exc:

                if not await self.handle_connect_errors(ws_params, reconnect, exc, backoff):
                    return

    def starter(self) -> None:
        """Starts the bot properly"""
        try:
            print("Connecting to database...")
            start = time.time()
            pool_pg = self.loop.run_until_complete(asyncpg.create_pool(
                database=self.db,
                user=self.user_db,
                password=self.pass_db)
            )
        except Exception as e:
            print_exception("Could not connect to database:", e)
        else:
            self.uptime = datetime.datetime.utcnow()
            self.pool_pg = pool_pg
            print(f"Connected to the database ({time.time() - start})s")
            self.loop.run_until_complete(self.after_db())
            self.loop.create_task(self.after_ready())
            self.run(self.token)


intent_data = {x: True for x in ('guilds', 'members', 'emojis', 'messages', 'reactions')}
intents = discord.Intents(**intent_data)
with open("d_json/bot_var.json") as states_bytes:
    states = json.load(states_bytes)
bot_data = {
    "token": states.get("TOKEN"),
    "color": 0xffcccb,
    "db": states.get("DATABASE"),
    "user_db": states.get("USER"),
    "pass_db": states.get("PASSWORD"),
    "tester": states.get("TEST"),
    "help_src": states.get("HELP_SRC"),
    "ipc_port": states.get("IPC_PORT"),
    "ipc_key": states.get("IPC_KEY"),
    "intents": intents,
    "owner_id": 591135329117798400,
    "websocket_ip": states.get("WEBSOCKET_IP"),
    "socket_states": states.get("WEBSOCKET_STATES"),
    "prefix_weights": states.get("PREFIX_WEIGHT"),
    "prefix_derivative": states.get("PREFIX_DERIVATIVE_PATH"),
    "git_token": states.get("GIT_TOKEN"),
    "activity": discord.Activity(type=discord.ActivityType.listening, name="phone. who dis doe"),
    "description": "{}'s personal bot that is partially for the public. "
                   f"Written with only `{count_python('.'):,}` lines. plz be nice"
}

bot = StellaBot(**bot_data)


@bot.event
async def on_ready():
    print("bot is ready")


@bot.event
async def on_disconnect():
    print("bot disconnected")


@bot.event
async def on_connect():
    print("bot connected")


@bot.event
@wait_ready(bot=bot)
@event_check(lambda m: not bot.tester or m.author == bot.stella)
async def on_message(message):
    if re.fullmatch("<@(!)?661466532605460530>", message.content):
        await message.channel.send(f"My prefix is `{await bot.get_prefix(message)}`")
        return

    if message.author.id in bot.blacklist or getattr(message.guild, "id", None) in bot.blacklist:
        return

    if message.author == bot.stella and message.attachments:
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
async def on_restarting_server(data):
    print("Server restarting...")
    server = bot.ipc_client
    await server.session.close()
    print("Server waiting for server respond.")
    await asyncio.sleep(10)
    print("Server re-establishing connection")
    await server.init_sock()
    print("Server Connection Successful.")


@bot.ipc_client.listen()
async def on_kill(data):
    print("Kill has been ordered", data)
    await bot.close()


bot.starter()
