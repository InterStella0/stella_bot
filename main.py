import time
import re
import discord
from discord.ext import commands
from dotenv import load_dotenv
from os.path import join, dirname
from os import environ
import asyncpg
import json
import datetime

dotenv_path = join(dirname(__file__), 'bot_settings.env')
load_dotenv(dotenv_path)


class StellaBot(commands.Bot):
    def __init__(self, color, token, db, user_db, pass_db, tester, **kwargs):
        super().__init__(self, **kwargs)
        self.tester = tester
        self.command_prefix = self.get_prefix
        self.db = db
        self.user_db = user_db
        self.pass_db = pass_db
        self.color = color
        self.pg_con = None
        self.uptime = None
        self.pending_bots = set()
        self.confirmed_bots = set()
        self.token = token
        self.existing_prefix = self.fill_prefix()
        self.loading_cog()

    @property
    def stella(self):
        return self.get_user(self.owner_id)

    def loading_cog(self):
        cogs = ("find_bot", "useful", "helpful", "myself", "jishaku")
        for cog in cogs:
            ext = "cogs." if cog != "jishaku" else ""
            try:
                self.load_extension(f"{ext}{cog}")
            except Exception as e:
                print(e)
            else:
                print(f"cog {cog} is loaded")

    def fill_prefix(self):
        with open("d_json/prefix.json", "r") as read:
            return json.load(read)

    async def fill_bots(self):
        record_pending = await self.pg_con.fetch("SELECT bot_id FROM pending_bots;")
        self.pending_bots = set(x["bot_id"] for x in record_pending)

        record_confirmed = await self.pg_con.fetch("SELECT bot_id FROM confirmed_bots;")
        self.confirmed_bots = set(x["bot_id"] for x in record_confirmed)
        print("Bots list are now filled.")

    async def get_prefix(self, message):
        """Handles custom prefixes, this function is invoked every time process_command method is invoke thus returning
        the appropriate prefixes depending on the guild."""
        if message.guild is None or str(message.guild.id) not in self.existing_prefix:
            return "."
        if self.tester:
            return "+="
        return self.existing_prefix[str(message.guild.id)]

    async def get_message(self, message_id):
        return self._connection._get_message(message_id)

    def starter(self):
        try:
            print("Connecting to database...")
            start = time.time()
            loop_pg = self.loop.run_until_complete(asyncpg.create_pool(database=self.db,
                                                                       user=self.user_db,
                                                                       password=self.pass_db))
        except Exception as e:
            print("Could not connect to database.")
            print(e)
            return
        else:
            self.uptime = datetime.datetime.utcnow()
            self.pg_con = loop_pg
            print(f"Connected to the database ({time.time() - start})s")
            self.loop.run_until_complete(self.fill_bots())
            self.run(self.token)


intents = discord.Intents.default()
intents.members = True
intents.dm_typing = False

bot_data = {"token": environ.get("TOKEN"),
            "color": 0xffcccb,
            "db": environ.get("DATABASE"),
            "user_db": environ.get("USER"),
            "pass_db": environ.get("PASSWORD"),
            "tester": bool(environ.get("TEST")),
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
        await bot.process_commands(message)

bot.starter()
