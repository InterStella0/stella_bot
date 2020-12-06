import time
import re
import discord
import asyncpg
import datetime
import utils.useful
import utils.library_override
from utils.useful import StellaContext
from discord.ext import commands
from dotenv import load_dotenv
from os.path import join, dirname
from utils.useful import call, print_exception
from os import environ
dotenv_path = join(dirname(__file__), 'bot_settings.env')
load_dotenv(dotenv_path)


class StellaBot(commands.Bot):
    def __init__(self, help_src, color, token, db, user_db, pass_db, tester, **kwargs):
        super().__init__(self, **kwargs)
        self.tester = tester
        self.help_src = help_src
        self.command_prefix = self.get_prefix
        self.decorator_store = utils.useful.decorator_store
        self.db = db
        self.user_db = user_db
        self.pass_db = pass_db
        self.color = color
        self.pool_pg = None
        self.uptime = None
        self.all_bot_prefixes = {}
        self.pending_bots = set()
        self.confirmed_bots = set()
        self.token = token
        self.existing_prefix = None
        self.blacklist = set()

    async def after_db(self):
        """Runs after the db is connected"""
        self.loading_cog()
        for name in "prefix", "bots", "blacklist":
            await getattr(self, "fill_" + name)()
        for command in bot.commands:
            command._buckets = commands.CooldownMapping.from_cooldown(1, 5, commands.BucketType.user)
            command.cooldown_after_parsing = True

    @property
    def stella(self):
        """Returns discord.User of the owner"""
        return self.get_user(self.owner_id)

    @property
    def error_channel(self):
        """Gets the error channel for the bot to log."""
        return self.get_guild(int(environ.get("BOT_GUILD"))).get_channel(int(environ.get("ERROR_CHANNEL")))

    def loading_cog(self):
        """Loads the cog"""
        cogs = ("error_handler", "find_bot", "useful", "helpful", "myself", "eros", "jishaku")
        for cog in cogs:
            ext = "cogs." if cog != "jishaku" else ""
            if error := call(self.load_extension, f"{ext}{cog}", ret=True):
                print_exception('Ignoring exception while loading up {}:'.format(cog), error)
            else:
                print(f"cog {cog} is loaded")

    async def fill_prefix(self):
        """Fills the bot actual prefix"""
        prefixes = await self.pool_pg.fetch("SELECT * FROM internal_prefix")
        self.existing_prefix = {data["snowflake_id"]: data["prefix"] for data in prefixes}

    async def fill_bots(self):
        """Fills the pending/confirmed bots in discord.py"""
        record_pending = await self.pool_pg.fetch("SELECT bot_id FROM pending_bots;")
        self.pending_bots = set(x["bot_id"] for x in record_pending)

        record_confirmed = await self.pool_pg.fetch("SELECT bot_id FROM confirmed_bots;")
        self.confirmed_bots = set(x["bot_id"] for x in record_confirmed)
        print("Bots list are now filled.")

    async def fill_blacklist(self):
        """Loading up the blacklisted users."""
        records = await self.pool_pg.fetch("SELECT snowflake_id FROM blacklist")
        self.blacklist = {r["snowflake_id"] for r in records}

    async def get_prefix(self, message):
        """Handles custom prefixes, this function is invoked every time process_command method is invoke thus returning
        the appropriate prefixes depending on the guild."""
        query = "INSERT INTO internal_prefix VALUES($1, $2) ON CONFLICT(snowflake_id) DO NOTHING"
        snowflake_id = message.guild.id if message.guild else message.author.id
        default = "uwu "
        if self.tester:
            return "+="
        if snowflake_id not in self.existing_prefix:
            self.existing_prefix.update({snowflake_id: default})
            await self.pool_pg.fetch(query, snowflake_id, default)
            return default
        return self.existing_prefix.get(snowflake_id)

    def get_message(self, message_id):
        """Gets the message from the cache"""
        return self._connection._get_message(message_id)

    async def get_context(self, message, *, cls=None):
        return await super().get_context(message, cls=StellaContext)

    def starter(self):
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
            "owner_id": 591135329117798400}

bot = StellaBot(**bot_data)


@bot.event
async def on_ready():
    print("bot is ready")


@bot.event
async def on_message(message):
    if re.fullmatch("<@(!)?661466532605460530>", message.content):
        await message.channel.send(f"My prefix is `{await bot.get_prefix(message)}`")
        return

    if not bot.tester or message.author == bot.stella:
        if message.author.id in bot.blacklist or getattr(message.guild, "id", None) in bot.blacklist:
            return
        await bot.process_commands(message)

bot.starter()
