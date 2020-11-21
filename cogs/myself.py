import discord
from discord.ext import commands
from discord.ext.commands import Greedy
from utils.useful import call, BaseEmbed, AfterGreedy, event_check
from utils.new_converters import ValidCog


class Myself(commands.Cog, command_attrs=dict(hidden=True)):
    def __init__(self, bot):
        self.bot = bot

    async def cog_check(self, ctx):
        return await commands.is_owner().predicate(ctx)

    @commands.Cog.listener()
    @event_check(lambda s, b, a: (b.content and a.content) or b.author.bot)
    async def on_message_edit(self, before, after):
        if await self.bot.is_owner(before.author):
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
