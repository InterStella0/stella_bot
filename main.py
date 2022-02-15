import asyncio
import collections
import contextlib
import copy
import datetime
import json
import os
import re
import time
from os import environ
from os.path import dirname, join
from typing import List, Optional, Union, Sequence

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
from utils.ipc import StellaClient
from utils.prefix_ai import DerivativeNeuralNetwork, PrefixNeuralNetwork
from utils.useful import (ListCall, StellaContext, call, count_python,
                          print_exception)

dotenv_path = join(dirname(__file__), 'bot_settings.env')
load_dotenv(dotenv_path)

import utils.library_override

to_call = ListCall()


class StellaBot(commands.Bot):
    def __init__(self, *, owner_ids: Sequence[int], **kwargs):
        self.tester = kwargs.pop("tester", False)
        self.help_src = kwargs.pop("help_src", None)
        self.db = kwargs.pop("db", None)
        self.user_db = kwargs.pop("user_db", None)
        self.pass_db = kwargs.pop("pass_db", None)
        self.color = kwargs.pop("color", None)
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
        self.cached_context = collections.deque(maxlen=100)
        self.command_running = {}
        self.user_lock = {}

        # main bot owner is kept separate
        self._stella_id, *_ = owner_ids

        super().__init__(self.get_prefix, owner_ids=set(owner_ids), **kwargs)

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
                await ctx.trigger_typing()
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
        self.load_extension("addons.modal")

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
    "owner_ids": (591135329117798400, ),
    "websocket_ip": states.get("WEBSOCKET_IP"),
    "prefix_weights": states.get("PREFIX_WEIGHT"),
    "prefix_derivative": states.get("PREFIX_DERIVATIVE_PATH"),
    "git_token": states.get("GIT_TOKEN"),
    "activity": discord.Activity(type=discord.ActivityType.listening, name="logged to my pc."),
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
@event_check(lambda m: not bot.tester or bot.sync_is_owner(m.author))
async def on_message(message):
    if re.fullmatch("<@(!)?661466532605460530>", message.content):
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
