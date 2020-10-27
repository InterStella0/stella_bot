import discord
from discord.ext import commands
from discord.ext.commands import NotOwner
from utils.useful import try_call, BaseEmbed


class Myself(commands.Cog, command_attrs=dict(hidden=True)):
    def __init__(self, bot):
        self.bot = bot

    async def cog_check(self, ctx):
        if not await ctx.bot.is_owner(ctx.author):
            raise NotOwner('You do not own this bot.')
        return True

    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        if not (before.content and after.content) or before.author.bot:
            return

        if await self.bot.is_owner(before.author):
            await self.bot.process_commands(after)

    @commands.command(name="load", aliases=["cload", "loads", "lod"])
    async def _cog_load(self, ctx, extension):
        await self.cogs_handler(ctx, "load", extension)

    @commands.command(name="reload", aliases=["creload", "reloads", "relod"])
    async def _cog_reload(self, ctx, extension):
        await self.cogs_handler(ctx, "reload", extension)

    @commands.command(name="unload", aliases=["cunload", "unloads", "unlod"])
    async def _cog_unload(self, ctx, extension):
        await self.cogs_handler(ctx, "unload", extension)

    @commands.command()
    async def dispatch(self, ctx, message: discord.Message):
        self.bot.dispatch('message', message)
        await ctx.message.add_reaction("<:checkmark:753619798021373974>")

    async def cogs_handler(self, ctx, method, extension):
        async def do_cog(method):
            method = getattr(self.bot, f"{method}_extension")
            return method(f"cogs.{extension}")

        output = await try_call(do_cog(method), Exception, ret=True) or f"cogs.{extension} is {method}ed"
        await ctx.send(embed=BaseEmbed.default(ctx, description=str(output)))


def setup(bot):
    bot.add_cog(Myself(bot))
