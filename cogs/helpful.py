import inspect
import os
import discord
import humanize
import datetime
from discord.ext import commands, menus
from utils.useful import BaseEmbed
from collections import namedtuple
from discord.ext.menus import First, Last, Button, MenuPages

class CommandHelp:
    def __init__(self, command, brief):
        self.command = command
        self.brief = brief


class HelpMenuBase(MenuPages):
    """This is a MenuPages class that is used every single paginator menus. All it does is replace the default emoji
       with a custom emoji, and keep the functionality."""

    def __init__(self, source, **kwargs):
        super().__init__(source, **kwargs)
        self.info = False

        EmojiB = namedtuple("EmojiB", "emoji position explain")
        self.dict_emoji = {'\N{BLACK LEFT-POINTING DOUBLE TRIANGLE WITH VERTICAL BAR}\ufe0f':
                               EmojiB("<:before_fast_check:754948796139569224>", First(0),
                                      "Goes to the first page."),

                           '\N{BLACK LEFT-POINTING TRIANGLE}\ufe0f':
                               EmojiB("<:before_check:754948796487565332>", First(1), "Goes to the previous page."),

                           '\N{BLACK RIGHT-POINTING TRIANGLE}\ufe0f':
                               EmojiB("<:next_check:754948796361736213>", Last(1), "Goes to the next page."),

                           '\N{BLACK RIGHT-POINTING DOUBLE TRIANGLE WITH VERTICAL BAR}\ufe0f':
                               EmojiB("<:next_fast_check:754948796391227442>", Last(2), "Goes to the last page."),

                           '\N{BLACK SQUARE FOR STOP}\ufe0f':
                               EmojiB("<:stop_check:754948796365930517>", Last(0), "Remove this message."),

                           '<:information_pp:754948796454010900>':
                               EmojiB("<:information_pp:754948796454010900>", Last(4),
                                      "Shows this infomation message.")}

        for emoji in super().buttons:
            callback = super().buttons[emoji].action  # gets the function that would be called for that button
            if emoji.name not in self.dict_emoji:
                continue
            new_butO = self.dict_emoji[emoji.name]
            new_button = Button(new_butO.emoji, callback, position=new_butO.position)
            del self.dict_emoji[emoji.name]
            self.dict_emoji[new_butO.emoji] = new_butO
            super().add_button(new_button)
            super().remove_button(emoji)

    async def show_page(self, page_number):
        self.info = False
        await super().show_page(page_number)

    @menus.button('<:information_pp:754948796454010900>', position=Last(4))
    async def on_information(self, payload):
        if info := not self.info:
            await self.on_information_show(payload)
        else:
            self.current_page = self.current_page if self.current_page > 0 else 0
            await self.show_page(self.current_page)
        self.info = info

    async def on_information_show(self, payload):
        raise NotImplemented("Information is not implemented.")


class HelpMenu(HelpMenuBase):
    """This is a MenuPages class that is used only in record server command. All it has is custom information and
       custom initial message."""
    async def on_information_show(self, payload):
        ctx = self.ctx
        exists = [str(emoji) for emoji in super().buttons]
        embed = BaseEmbed.default(ctx,
                                  title="Information",
                                  description="This shows each commands in this bot. Each page is a category that shows "
                                              "what commands that the category have.")
        curr = self.current_page + 1 if (p := self.current_page > -1) else "cover page"
        pa = "page" if p else "the"
        embed.set_author(icon_url=ctx.bot.user.avatar_url,
                         name=f"You were on {pa} {curr}")
        nav = '\n'.join(f"{self.dict_emoji[e].emoji} {self.dict_emoji[e].explain}" for e in exists)
        embed.add_field(name="Navigation:", value=nav)
        await self.message.edit(embed=embed)


class HelpSource(menus.ListPageSource):
    async def format_page(self, menu, entry):
        cog, list_commands = entry
        new_line = "\n"
        embed = discord.Embed(title=f"{cog.qualified_name} Category",
                              description=new_line.join(f'{command_help.command}{new_line}{command_help.brief}'
                                                        for command_help in list_commands),
                              color=menu.bot.color)
        author = menu.ctx.author
        embed.set_footer(text=f"Requested by {author}", icon_url=author.avatar_url)

        return embed


class StellaBotHelp(commands.DefaultHelpCommand):
    def get_bot_mapping(self):
        """Retrieves the bot mapping passed to :meth:`send_bot_help`."""
        mapping = super().get_bot_mapping()
        filtered_mapping = {cog: self.filter_commands(mapping[cog], sort=True) for cog in mapping}
        return filtered_mapping

    def get_command_signature(self, command, simplified=False):
        """Method to return a commands name and signature"""
        if not command.signature and not command.parent:  # checking if it has no args and isn't a subcommand
            return f'`{self.clean_prefix}{command.name}`'
        if command.signature and not command.parent:  # checking if it has args and isn't a subcommand
            return f'`{self.clean_prefix}{command.name}` `{command.signature}`'
        if not command.signature and command.parent:  # checking if it has no args and is a subcommand
            return f'`{self.clean_prefix}{command.parent}` `{command.name}`'
        else:  # else assume it has args a signature and is a subcommand
            return f'`{self.clean_prefix}{command.parent}` `{command.name}` `{command.signature}`'

    async def send_bot_help(self, mapping):
        command_data = {}
        for cog in mapping:
            command_data[cog] = []
            for command in await mapping[cog]:
                command_data[cog].append(CommandHelp(self.get_command_signature(command), command.help))

        command_data = tuple((cog, command_data[cog]) for cog in mapping if command_data[cog])
        pages = HelpMenu(source=HelpSource(command_data, per_page=1), delete_message_after=True)
        await pages.start(self.context)
        await self.context.message.add_reaction("<:checkmark:753619798021373974>")


class Helpful(commands.Cog):
    def __init__(self, bot):
        self._default_help_command = bot.help_command
        bot.help_command = StellaBotHelp()
        bot.help_command.cog = self
        self.bot = bot

    @commands.command(aliases=["ping", "p"], help="Shows the bot latency")
    async def pping(self, ctx):
        await ctx.send(embed=BaseEmbed.default(ctx,
                                               title="PP",
                                               description=f"Your pp lasted `{self.bot.latency * 1000:.2f}ms`"))

    @commands.command()
    async def uptime(self, ctx):
        c_uptime = datetime.datetime.utcnow() - self.bot.uptime
        await ctx.send(embed=BaseEmbed.default(ctx,
                                               title="Uptime",
                                               description=f"Current uptime: `{humanize.precisedelta(c_uptime)}`"))

    @commands.command(help="shows the source code")
    async def source(self, ctx, *, content=None):
        source_url = 'https://github.com/InterStella0/stella_bot'
        if not content:
            return await ctx.send(f"<{source_url}>")
        src, module = None, None

        def command_check(command):
            nonlocal src, module
            if command == 'help':
                src = type(self.bot.help_command)
                module = src.__module__
            else:
                obj = self.bot.get_command(command.replace('.', ' '))
                if obj:
                    src = obj.callback.__code__
                    module = obj.callback.__module__

        def cog_check(cog):
            nonlocal src, module
            if "." not in cog:
                return
            cog, _, method = cog.partition(".")
            cog = self.bot.get_cog(cog)
            if method := getattr(cog, method, None):
                src = method.__code__
                module = method.__module__

        functions = (command_check, cog_check)
        for func in functions:
            if not src:
                func(content)
        if module is None:
            return await ctx.send(f"Method {content} not found.")
        lines, firstlineno = inspect.getsourcelines(src)
        location = module.replace('.', '/') + '.py'

        url = f'<{source_url}/blob/master/{location}#L{firstlineno}-L{firstlineno + len(lines) - 1}>'
        await ctx.send(url)

    def cog_unload(self):
        self.bot.help_command = self._default_help_command


def setup(bot):
    bot.add_cog(Helpful(bot))