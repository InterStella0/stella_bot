import discord
import datetime
import contextlib
from typing import Union
from discord.ext import commands
from discord.ext.commands import Greedy
from utils.useful import call, BaseEmbed, AfterGreedy, event_check
from utils.new_converters import ValidCog, IsBot
from utils import flags as flg
from jishaku.codeblocks import codeblock_converter


class DatetimeConverter(commands.Converter):
    async def convert(self, ctx, argument):
        for _format in "%d/%m/%y %H:%M", "%d/%m/%y %H:%M:%S", "%d/%m/%y":
            with contextlib.suppress(ValueError):
                return datetime.datetime.strptime(argument, _format)
        raise commands.CommandError(f"I couldn't convert {argument} into a valid datetime.")


class JumpValidator(commands.Converter):
    async def convert(self, ctx, argument):
        with contextlib.suppress(commands.MessageNotFound):
            message = await commands.MessageConverter().convert(ctx, argument)
            return message.jump_url
        raise commands.CommandError(f"I can't find {argument}. Is this even a real message?")


class Myself(commands.Cog, command_attrs=dict(hidden=True)):
    def __init__(self, bot):
        self.bot = bot

    async def cog_check(self, ctx):
        return await commands.is_owner().predicate(ctx)

    @commands.command(cls=flg.SFlagCommand)
    @flg.add_flag("--joined_at", type=DatetimeConverter)
    @flg.add_flag("--jump_url", type=JumpValidator)
    @flg.add_flag("--requested_at", type=DatetimeConverter)
    @flg.add_flag("--reason")
    @flg.add_flag("--message", type=discord.Message)
    @flg.add_flag("--author", type=discord.Member)
    async def addbot(self, ctx, bot: IsBot, **flags):
        new_data = {'bot_id': bot.id}
        if message := flags.pop('message'):
            new_data['author_id'] = message.author.id
            new_data['reason'] = message.content
            new_data['requested_at'] = message.created_at
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
            new_data['joined_at'] = bot.joined_at
        values = [*new_data.values()]
        result = await self.bot.pool_pg.execute(query, *values)
        await ctx.maybe_reply(result)
        self.bot.confirmed_bots.add(bot.id)

    @commands.command()
    async def su(self, ctx, member: Union[discord.Member, discord.User], *, content):
        message = ctx.message
        message.author = member
        message.content = ctx.prefix + content
        self.bot.dispatch("message", message)

    @commands.command(cls=flg.SFlagCommand)
    @flg.add_flag("--uses", type=int, default=1)
    @flg.add_flag("--code", type=codeblock_converter)
    async def command(self, ctx, **flags):
        coding = {
            "_bot": self.bot,
            "commands": commands
        }
        content = flags["code"].content
        values = content.split("\n")
        values.pop()
        command = values.pop()
        values.append(f'_bot.add_command({command})')
        values.insert(1, f'@commands.is_owner()')
        exec("\n".join(values), coding)

        uses = flags["uses"]

        def check(ctx):
            return ctx.command.qualified_name == coding[command].qualified_name and self.bot.stella == ctx.author

        await ctx.message.add_reaction("<:next_check:754948796361736213>")
        while c := await self.bot.wait_for("command_completion", check=check):
            uses -= 1
            if uses <= 0:
                await ctx.message.add_reaction("<:checkmark:753619798021373974>")
                return self.bot.remove_command(c.command.qualified_name)

    @commands.Cog.listener()
    @event_check(lambda s, b, a: (b.content and a.content) or b.author.bot)
    async def on_message_edit(self, before, after):
        if await self.bot.is_owner(before.author) and not before.embeds and not after.embeds:
            await self.bot.process_commands(after)

    @commands.command(name="load", aliases=["cload", "loads", "lod"], cls=AfterGreedy)
    async def _cog_load(self, ctx, extension: Greedy[ValidCog]):
        await self.cogs_handler(ctx, "load", extension)

    @commands.command(name="reload", aliases=["creload", "reloads", "relod"], cls=AfterGreedy)
    async def _cog_reload(self, ctx, extension: Greedy[ValidCog]):
        await self.cogs_handler(ctx, "reload", extension)

    @commands.command(name="unload", aliases=["cunload", "unloads", "unlod"], cls=AfterGreedy)
    async def _cog_unload(self, ctx, extension: Greedy[ValidCog]):
        await self.cogs_handler(ctx, "unload", extension)

    @commands.command()
    async def dispatch(self, ctx, message: discord.Message):
        self.bot.dispatch('message', message)
        await ctx.message.add_reaction("<:checkmark:753619798021373974>")

    async def cogs_handler(self, ctx, method, extensions):
        def do_cog(exts):
            func = getattr(self.bot, f"{method}_extension")
            return func(f"cogs.{exts}")

        outputs = [call(do_cog, ext, ret=True) or f"cogs.{ext} is {method}ed"
                   for ext in extensions]
        await ctx.maybe_reply(embed=BaseEmbed.default(ctx, description="\n".join(str(x) for x in outputs)))


def setup(bot):
    bot.add_cog(Myself(bot))
