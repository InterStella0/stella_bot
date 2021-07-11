import time
import re
import asyncpg
import datetime
import os
import copy
import discord
import contextlib
from typing import Union, List
from utils.useful import StellaContext, ListCall, count_python
from utils.decorators import event_check, wait_ready 
from discord.ext import commands
from dotenv import load_dotenv
from os.path import join, dirname
from utils.useful import call, print_exception
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
        self.pool_pg = None
        self.uptime = None
        self.all_bot_prefixes = {}
        self.pending_bots = set()
        self.confirmed_bots = set()
        self.token = kwargs.pop("token", None)
        self.blacklist = set()
        self.cached_users = {}
        self.existing_prefix = {}
        super().__init__(self.get_prefix, **kwargs)

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
    def stella(self) -> str:
        """Returns discord.User of the owner"""
        return self.get_user(self.owner_id)

    @property
    def error_channel(self) -> discord.TextChannel:
        """Gets the error channel for the bot to log."""
        return self.get_guild(int(environ.get("BOT_GUILD"))).get_channel(int(environ.get("ERROR_CHANNEL")))

    @to_call.append
    def loading_cog(self) -> None:
        """Loads the cog"""
        cogs = ()
        for file in os.listdir("cogs"):
            if file.endswith(".py"):
                cogs += (file[:-3],)

        cogs += ("jishaku",)
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
                          cls: commands.Context = None) -> Union[StellaContext, commands.Context]:
        """Override get_context to use a custom Context"""
        context = await super().get_context(message, cls=StellaContext)
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
            pool_pg = self.loop.run_until_complete(asyncpg.create_pool(database=self.db,
                                                                       user=self.user_db,
                                                                       password=self.pass_db))
        except Exception as e:
            print_exception("Could not connect to database:", e)
        else:
            self.uptime = datetime.datetime.utcnow()
            self.pool_pg = pool_pg
            print(f"Connected to the database ({time.time() - start})s")
            self.loop.run_until_complete(self.after_db())
            self.run(self.token)


intent_data = {x: True for x in ('guilds', 'members', 'emojis', 'messages', 'reactions')}
intents = discord.Intents(**intent_data)
bot_data = {"token": environ.get("TOKEN"),
            "color": 0xffcccb,
            "db": environ.get("DATABASE"),
            "user_db": environ.get("USER"),
            "pass_db": environ.get("PASSWORD"),
            "tester": bool(environ.get("TEST")),
            "help_src": environ.get("HELP_SRC"),
            "intents": intents,
            "owner_id": 591135329117798400,
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

bot.starter()