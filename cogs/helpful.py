import contextlib
import inspect
import json
import re
import discord
import humanize
import datetime
import textwrap
import more_itertools
from discord.ext import commands, menus
from utils.useful import BaseEmbed, MenuBase, plural, print_exception
from utils.errors import CantRun
from utils import flags as flg
from collections import namedtuple
from discord.ext.menus import First, Last

CommandHelp = namedtuple("CommandHelp", 'command brief')


class HelpMenuBase(MenuBase):
    """This is a MenuPages class that is used every single paginator menus. All it does is replace the default emoji
       with a custom emoji, and keep the functionality."""

    def __init__(self, source, **kwargs):
        EmojiB = namedtuple("EmojiB", "emoji position explain")
        help_dict_emoji = {'\N{BLACK LEFT-POINTING DOUBLE TRIANGLE WITH VERTICAL BAR}\ufe0f':
                           EmojiB("<:before_fast_check:754948796139569224>", First(0),
                                  "Goes to the first page."),

                           '\N{BLACK LEFT-POINTING TRIANGLE}\ufe0f':
                           EmojiB("<:before_check:754948796487565332>", First(1),
                                  "Goes to the previous page."),

                           '\N{BLACK RIGHT-POINTING TRIANGLE}\ufe0f':
                           EmojiB("<:next_check:754948796361736213>", Last(1),
                                  "Goes to the next page."),

                           '\N{BLACK RIGHT-POINTING DOUBLE TRIANGLE WITH VERTICAL BAR}\ufe0f':
                           EmojiB("<:next_fast_check:754948796391227442>", Last(2),
                                  "Goes to the last page."),

                           '\N{BLACK SQUARE FOR STOP}\ufe0f':
                           EmojiB("<:stop_check:754948796365930517>", Last(0),
                                  "Remove this message."),

                           '<:information_pp:754948796454010900>':
                           EmojiB("<:information_pp:754948796454010900>", Last(4),
                                  "Shows this infomation message.")}
        super().__init__(source, dict_emoji=help_dict_emoji, **kwargs)

    async def show_page(self, page_number):
        self.info = False
        await super().show_page(page_number)

    @menus.button('<:information_pp:754948796454010900>', position=Last(4))
    async def on_information(self, payload):
        if info := not self.info:
            await self.on_information_show(payload)
        else:
            self.current_page = max(self.current_page, 0)
            await self.show_page(self.current_page)
        self.info = info

    async def on_information_show(self, payload):
        raise NotImplemented("Information is not implemented.")


class HelpMenu(HelpMenuBase):
    """This is a MenuPages class that is used only in help command. All it has is custom information and
       custom initial message."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._current_wait = None
        self.help_command = None

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
        await self.message.edit(embed=embed, allowed_mentions=discord.AllowedMentions(replied_user=False))

    async def background_update(self):
        """Makes a help menu responsive"""
        if not hasattr(self._source, "wait_for_update"):
            return
        while self._running:
            try:
                command = await self.ctx.bot.wait_for("command_remove")
                if not await self._source.wait_for_update(self, command):
                    continue
            except Exception as e:
                print_exception("background_update fucking dies", e)
            else:
                self._source._max_pages = len(self._source.entries)
                await self.show_page(min(self.current_page, self._source._max_pages - 1))

    async def start(self, ctx, **kwargs):
        self.help_command = ctx.bot.help_command
        self.help_command.context = ctx
        self._current_wait = ctx.bot.loop.create_task(self.background_update())
        await super().start(ctx, **kwargs)

    def stop(self):
        super().stop()
        self._current_wait.cancel()


class CogMenu(HelpMenu):
    """This is a MenuPages class that is used only in Cog help command. All it has is custom information and
       custom initial message."""
    async def on_information_show(self, payload):
        ctx = self.ctx
        exists = [str(emoji) for emoji in super().buttons]
        embed = BaseEmbed.default(ctx,
                                  title="Information",
                                  description="This shows each commands in this category. Each page is a command that shows "
                                              "what's the command is about and a demonstration of usage.")
        curr = self.current_page + 1 if (p := self.current_page > -1) else "cover page"
        pa = "page" if p else "the"
        embed.set_author(icon_url=ctx.bot.user.avatar_url,
                         name=f"You were on {pa} {curr}")
        nav = '\n'.join(f"{self.dict_emoji[e].emoji} {self.dict_emoji[e].explain}" for e in exists)
        embed.add_field(name="Navigation:", value=nav)
        await self.message.edit(embed=embed, allowed_mentions=discord.AllowedMentions(replied_user=False))


class HelpCogSource(menus.ListPageSource):
    """This is for help Cog ListPageSource"""
    def __init__(self, *args, cog=None):
        super().__init__(*args, per_page=1)
        self.cog = cog

    async def format_page(self, menu: CogMenu, entry):
        return menu.generate_page(entry, self._max_pages)

    async def wait_for_update(self, menu, command):
        """Makes cog menu responsive"""
        if getattr(command.cog, "qualified_name", None) != self.cog:
            return
        signature = menu.help_command.get_command_signature(command)
        i, _ = discord.utils.find(lambda entry: entry[1].title == signature, enumerate(self.entries))
        del self.entries[i]
        return True


class HelpSource(menus.ListPageSource):
    """This is for the help command ListPageSource"""
    async def format_page(self, menu: HelpMenu, entry):
        cog, list_commands = entry
        new_line = "\n"
        embed = discord.Embed(title=f"{getattr(cog, 'qualified_name', 'No')} Category",
                              description=new_line.join(f'{command_help.command}{new_line}{command_help.brief}'
                                                        for command_help in list_commands),
                              color=menu.bot.color)
        author = menu.ctx.author
        embed.set_footer(text=f"Requested by {author}", icon_url=author.avatar_url)

        return menu.generate_page(embed, self._max_pages)

    async def wait_for_update(self, menu, command):
        """Makes the help command responsive, I do this for the hell of it I'm bored."""
        cog_name = getattr(command.cog, "qualified_name", None)
        command = CommandHelp(menu.help_command.get_command_signature(command), "")

        def find_command(iterable):
            return discord.utils.find(lambda current: command.command == current.command, iterable)

        def find_cog_command(value):
            cogs, list_of_commands = value
            return getattr(cogs, "qualified_name", None) == cog_name and find_command(list_of_commands)
        _, list_commands = discord.utils.find(find_cog_command, self.entries)
        i, _ = discord.utils.find(lambda x: x[1].command == command.command, enumerate(list_commands))
        del list_commands[i]
        if not list_commands:
            index, _ = discord.utils.find(lambda x: not bool(x[1][1]), enumerate(self.entries))
            del self.entries[index]
        return True


class CodeBlockPage(menus.ListPageSource):
    """This is for Code Block ListPageSource"""
    async def format_page(self, menu: CogMenu, entry):
        return menu.generate_page(entry, self._max_pages)


class StellaBotHelp(commands.DefaultHelpCommand):
    def __init__(self, **options):
        super().__init__(**options)
        with open("d_json/help.json") as r:
            self.help_gif = json.load(r)

    def get_command_signature(self, command, ctx=None):
        """Method to return a commands name and signature"""
        if not ctx:
            if not command.signature and not command.parent:
                return f'`{self.clean_prefix}{command.name}`'
            if command.signature and not command.parent:
                return f'`{self.clean_prefix}{command.name}` `{command.signature}`'
            if not command.signature and command.parent:
                return f'`{self.clean_prefix}{command.parent}` `{command.name}`'
            else:
                return f'`{self.clean_prefix}{command.parent}` `{command.name}` `{command.signature}`'
        else:
            def get_invoke_with():
                msg = ctx.message.content
                escape = "\\"
                prefixmax = re.match(f'{escape}{escape.join(ctx.prefix)}', msg).regs[0][1]
                return msg[prefixmax:msg.rindex(ctx.invoked_with)]

            if not command.signature and not command.parent:
                return f'{ctx.prefix}{ctx.invoked_with}'
            if command.signature and not command.parent:
                return f'{ctx.prefix}{ctx.invoked_with} {command.signature}'
            if not command.signature and command.parent:
                return f'{ctx.prefix}{get_invoke_with()}{ctx.invoked_with}'
            else:
                return f'{ctx.prefix}{get_invoke_with()}{ctx.invoked_with} {command.signature}'

    def get_help(self, command, brief=True):
        """Gets the command short_doc if brief is True while getting the longer help if it is false"""
        real_help = command.help or "This command is not documented."
        return real_help if not brief else command.short_doc or real_help

    def get_demo(self, command):
        """Gets the gif demonstrating the command."""
        com = command.name
        if com not in self.help_gif:
            return ""
        return f"{self.context.bot.help_src}/{self.help_gif[com]}/{com}_help.gif"

    def get_aliases(self, command):
        """This isn't even needed jesus christ"""
        return command.aliases

    def get_flag_help(self, command):
        """Gets the flag help if there is any."""
        def c(x):
            return "_OPTIONAL" not in x.dest
        return ["**--{0.dest}:** {0.help}".format(action) for action in command.callback._def_parser._actions if c(action)]

    async def send_bot_help(self, mapping):
        """Gets called when `uwu help` is invoked"""
        def get_info(com):
            return (getattr(self, f"get_{x}")(com) for x in ("command_signature", "help"))

        command_data = []
        for cog, unfiltered_commands in mapping.items():
            list_commands = await self.filter_commands(unfiltered_commands, sort=True)
            for chunks in more_itertools.chunked(list_commands, 6):
                command_data.append((cog, [CommandHelp(*get_info(command)) for command in chunks]))

        pages = HelpMenu(source=HelpSource(command_data, per_page=1), delete_message_after=True)
        with contextlib.suppress(discord.NotFound, discord.Forbidden):
            await pages.start(self.context, wait=True)
            await self.context.message.add_reaction("<:checkmark:753619798021373974>")

    def get_command_help(self, command):
        """Returns an Embed version of the command object given."""
        embed = BaseEmbed.default(self.context)
        embed.title = self.get_command_signature(command)
        embed.description = self.get_help(command, brief=False)
        if demo := self.get_demo(command):
            embed.set_image(url=demo)
        if alias := self.get_aliases(command):
            embed.add_field(name="Aliases", value=f'[{" | ".join(f"`{x}`" for x in alias)}]', inline=False)
        if hasattr(command.callback, "_def_parser"):
            embed.add_field(name="Optional Flags", value="\n".join(self.get_flag_help(command)))
        if isinstance(command, commands.Group):
            subcommand = command.commands
            value = "\n".join(self.get_command_signature(c) for c in subcommand)
            embed.add_field(name=plural("Subcommand(s)", len(subcommand)), value=value)

        return embed

    async def handle_help(self, command):
        with contextlib.suppress(commands.CommandError):
            await command.can_run(self.context)
            return await self.context.reply(embed=self.get_command_help(command), mention_author=False)
        raise CantRun("You don't have enough permission to see this help.") from None

    async def send_command_help(self, command):
        """Gets invoke when `uwu help <command>` is invoked."""
        await self.handle_help(command)

    async def send_group_help(self, group):
        """Gets invoke when `uwu help <group>` is invoked."""
        await self.handle_help(group)

    async def send_cog_help(self, cog):
        """Gets invoke when `uwu help <cog>` is invoked."""
        cog_commands = [self.get_command_help(c) for c in await self.filter_commands(cog.walk_commands(), sort=True)]
        pages = CogMenu(source=HelpCogSource(cog_commands, cog=getattr(cog, "qualified_name", None)), delete_message_after=True)
        await pages.start(self.context)


class Helpful(commands.Cog):
    def __init__(self, bot):
        self._default_help_command = bot.help_command
        bot.help_command = StellaBotHelp()
        bot.help_command.cog = self
        self.bot = bot

    @commands.command(aliases=["ping", "p"],
                      help="Shows the bot latency from the discord websocket.")
    async def pping(self, ctx):
        await ctx.maybe_reply(embed=BaseEmbed.default(
            ctx,
            title="PP",
            description=f"Your pp lasted `{self.bot.latency * 1000:.2f}ms`"))

    @commands.command(aliases=["up"],
                      help="Shows the bot uptime from when it was started.")
    async def uptime(self, ctx):
        c_uptime = datetime.datetime.utcnow() - self.bot.uptime
        await ctx.maybe_reply(embed=BaseEmbed.default(
            ctx,
            title="Uptime",
            description=f"Current uptime: `{humanize.precisedelta(c_uptime)}`")
        )

    @commands.command(aliases=["src", "sources"],
                      brief="Shows the source code link in github.",
                      help="Shows the source code in github given the cog/command name. "
                           "Defaults to the stella_bot source code link if not given any argument. "
                           "It accepts 2 types of content, the command name, or the Cog method name. "
                           "Cog method must specify it's Cog name separate by a period and it's method.",
                      cls=flg.SFlagCommand)
    @flg.add_flag("--code", type=bool, action="store_true", default=False,
                  help="Shows the code block instead of the link. Accepts True or False, defaults to False if not stated.")
    async def source(self, ctx, content=None, **flags):
        source_url = 'https://github.com/InterStella0/stella_bot'
        if not content:
            return await ctx.maybe_reply(f"<{source_url}>")
        src, module = None, None

        def command_check(command):
            nonlocal src, module
            if command == 'help':
                src = type(self.bot.help_command)
                module = src.__module__
            else:
                obj = self.bot.get_command(command.replace('.', ' '))
                if obj and obj.cog_name != "Jishaku":
                    src = obj.callback.__code__
                    module = obj.callback.__module__

        def cog_check(content):
            nonlocal src, module
            if "." not in content:
                return
            cog, _, method = content.partition(".")
            cog = self.bot.get_cog(cog)
            if method_func := getattr(cog, method, None):
                module = method_func.__module__
                target = getattr(method_func, "callback", method_func)
                src = target.__code__

        for func in (command_check, cog_check):
            if not src:
                func(content)
        if module is None:
            return await ctx.maybe_reply(f"Method {content} not found.")
        show_code = flags.pop("code", False)
        if show_code:
            param = {"text": inspect.getsource(src), "width": 1900, "replace_whitespace": False}
            list_codeblock = [f"```py\n{cb}\n```" for cb in textwrap.wrap(**param)]
            menu = MenuBase(CodeBlockPage(list_codeblock, per_page=1))
            await menu.start(ctx)
        else:
            lines, firstlineno = inspect.getsourcelines(src)
            location = module.replace('.', '/') + '.py'
            content = f'<{source_url}/blob/master/{location}#L{firstlineno}-L{firstlineno + len(lines) - 1}>'
            await ctx.maybe_reply(content)

    def cog_unload(self):
        self.bot.help_command = self._default_help_command


def setup(bot):
    bot.add_cog(Helpful(bot))
