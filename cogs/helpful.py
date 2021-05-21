import contextlib
import inspect
import json
import re
import discord
import humanize
import datetime
import textwrap
import itertools
import more_itertools
from discord.ext import commands, menus
from utils.useful import BaseEmbed, MenuBase, plural, empty_page_format
from utils.decorators import pages
from utils.errors import CantRun, ReplParserDies
from utils.greedy_parser import UntilFlag, command
from utils import flags as flg
from collections import namedtuple
from discord.ext.menus import First, Last
from jishaku.codeblocks import codeblock_converter

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
        embed.set_author(icon_url=ctx.bot.user.avatar.url,
                         name=f"You were on {pa} {curr}")
        nav = '\n'.join(f"{self.dict_emoji[e].emoji} {self.dict_emoji[e].explain}" for e in exists)
        embed.add_field(name="Navigation:", value=nav)
        await self.message.edit(embed=embed, allowed_mentions=discord.AllowedMentions(replied_user=False))

    async def start(self, ctx, **kwargs):
        self.help_command = ctx.bot.help_command
        self.help_command.context = ctx
        await super().start(ctx, **kwargs)


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
        embed.set_author(icon_url=ctx.bot.user.avatar.url,
                         name=f"You were on {pa} {curr}")
        nav = '\n'.join(f"{self.dict_emoji[e].emoji} {self.dict_emoji[e].explain}" for e in exists)
        embed.add_field(name="Navigation:", value=nav)
        await self.message.edit(embed=embed, allowed_mentions=discord.AllowedMentions(replied_user=False))


@pages()
async def help_source_format(self, menu: HelpMenu, entry):
    """This is for the help command ListPageSource"""
    cog, list_commands = entry
    new_line = "\n"
    embed = discord.Embed(title=f"{getattr(cog, 'qualified_name', 'No')} Category",
                          description=new_line.join(f'{command_help.command}{new_line}{command_help.brief}'
                                                    for command_help in list_commands),
                          color=menu.bot.color)
    author = menu.ctx.author
    return embed.set_footer(text=f"Requested by {author}", icon_url=author.avatar.url)


class StellaBotHelp(commands.DefaultHelpCommand):
    def __init__(self, **options):
        super().__init__(**options)
        with open("d_json/help.json") as r:
            self.help_gif = json.load(r)

    def get_command_signature(self, command, ctx=None):
        """Method to return a commands name and signature"""
        if not ctx:
            prefix = self.context.clean_prefix
            if not command.signature and not command.parent:
                return f'`{prefix}{command.name}`'
            if command.signature and not command.parent:
                return f'`{prefix}{command.name}` `{command.signature}`'
            if not command.signature and command.parent:
                return f'`{prefix}{command.parent}` `{command.name}`'
            else:
                return f'`{prefix}{command.parent}` `{command.name}` `{command.signature}`'
        else:
            def get_invoke_with():
                msg = ctx.message.content
                prefixmax = re.match(f'{re.escape(ctx.prefix)}', ctx.message.content).regs[0][1]
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

    def get_old_flag_help(self, command):
        """Gets the flag help if there is any."""
        def c(x):
            return "_OPTIONAL" not in x.dest
        return ["**--{0.dest} |** {0.help}".format(action) for action in command.callback._def_parser._actions if c(action)]

    def get_flag_help(self, command):
        required_flags = []
        optional_flags = []
        if (param := flg.find_flag(command)):
            for name, flags in param.annotation.__commands_flags__.items():
                not_documented = "This flag is not documented."
                description = getattr(flags, "help", not_documented) or not_documented
                formatted = f"**{':** | **'.join(itertools.chain([name], flags.aliases))}:** **|** {description}"
                list_append = (required_flags, optional_flags)[command._is_typing_optional(flags.annotation)]
                list_append.append(formatted)
        return required_flags, optional_flags

    async def send_bot_help(self, mapping):
        """Gets called when `uwu help` is invoked"""
        def get_info(com):
            return (getattr(self, f"get_{x}")(com) for x in ("command_signature", "help"))

        command_data = []
        for cog, unfiltered_commands in mapping.items():
            list_commands = await self.filter_commands(unfiltered_commands, sort=True)
            for chunks in more_itertools.chunked(list_commands, 6):
                command_data.append((cog, [CommandHelp(*get_info(command)) for command in chunks]))

        pages = HelpMenu(source=help_source_format(command_data))
        with contextlib.suppress(discord.NotFound, discord.Forbidden):
            await pages.start(self.context, wait=True)
            await self.context.confirmed()

    def get_command_help(self, command):
        """Returns an Embed version of the command object given."""
        embed = BaseEmbed.default(self.context)
        embed.title = self.get_command_signature(command)
        embed.description = self.get_help(command, brief=False)
        if demo := self.get_demo(command):
            embed.set_image(url=demo)
        if alias := self.get_aliases(command):
            embed.add_field(name="Aliases", value=f'[{" | ".join(f"`{x}`" for x in alias)}]', inline=False)
        
        required_flags, optional_flags = self.get_flag_help(command)
        if hasattr(command.callback, "_def_parser"):
            optional_flags.extend(self.get_old_flag_help(command))

        if required_flags:
            embed.add_field(name="Required Flags", value="\n".join(required_flags), inline=False)

        if optional_flags:
            embed.add_field(name="Optional Flags", value="\n".join(optional_flags), inline=False)
    
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
        pages = CogMenu(source=empty_page_format(cog_commands))
        with contextlib.suppress(discord.NotFound, discord.Forbidden):
            await pages.start(self.context, wait=True)
            await self.context.confirmed()


class Helpful(commands.Cog):
    def __init__(self, bot):
        self._default_help_command = bot.help_command
        bot.help_command = StellaBotHelp()
        bot.help_command.cog = self
        self.bot = bot

    @commands.command(aliases=["ping", "p"],
                      help="Shows the bot latency from the discord websocket.")
    async def pping(self, ctx):
        await ctx.embed(
            title="PP",
            description=f"Your pp lasted `{self.bot.latency * 1000:.2f}ms`"
        )

    @commands.command(aliases=["up"],
                      help="Shows the bot uptime from when it was started.")
    async def uptime(self, ctx):
        c_uptime = datetime.datetime.utcnow() - self.bot.uptime
        await ctx.embed(
            title="Uptime",
            description=f"Current uptime: `{humanize.precisedelta(c_uptime)}`"
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
            return await ctx.embed(title="here's the entire repo", description=source_url)
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
            menu = MenuBase(empty_page_format(list_codeblock))
            await menu.start(ctx)
        else:
            lines, firstlineno = inspect.getsourcelines(src)
            location = module.replace('.', '/') + '.py'
            url = f'<{source_url}/blob/master/{location}#L{firstlineno}-L{firstlineno + len(lines) - 1}>'
            await ctx.embed(title=f"Here's uh, {content}", description=f"[Click Here]({url})")

    @commands.command(help="Gives you the invite link")
    async def invite(self, ctx):
        await ctx.maybe_reply(f"Thx\n<{discord.utils.oauth_url(ctx.me.id)}>")

    @command(help="Simulate a live python interpreter interface when given a python code.")
    async def repl(self, ctx, content: UntilFlag[codeblock_converter], *, flags: flg.ReplFlag):
        Indentor = namedtuple("Indentor", "space part")
        witharg_regex = r"(^(\s+)?(?P<captured>(except|class|async def|def|async with|with|async for|for|while|if|elif)))(\s+).*((\s+)?:(\s+)?$)"
        connect_regex = r"(\s+)?(?P<captured>(try|else|except|finally))(\s+)?:(\s+)?"
        joiner = {"else": ['if', 'try'], 'except': ['try'], 'finally': ['try', 'else', 'except']}

        collon_regex = r".*(:)(\s+)?$"
        def remove_until_true(predicate, iterable):
            for x_space in itertools.takewhile(predicate, reversed(iterable)):
                iterable.remove(x_space)

        def parsing():
            previous_space = 0
            meet_collon = []
            expected_indent = ()
            for no in itertools.count(1):
                line = yield no
                _, space = re.match(r"(\s+)?", line).span()
                is_empty = line[space:] == ""
                indicator_mode = True
                def check_if_indenting(line):
                    nonlocal meet_collon, previous_space, space, indicator_mode
                    if re.match(collon_regex, line):
                        if (match := re.match(witharg_regex, line) or re.fullmatch(connect_regex, line)) is not None:
                            if part := joiner.get(match["captured"]):
                                ind = discord.utils.get(meet_collon, space=space)
                                if getattr(ind, "part", None) in part:
                                    indicator_mode = False
                                else:
                                    raise ReplParserDies("Invalid Syntax", no, line)

                            if space:
                                indicator_mode = False
                            if meet_collon:
                                remove_until_true(lambda x_space: x_space.space > space, meet_collon)
                            else:
                                meet_collon.append(Indentor(space, match["captured"]))
                            previous_space = space
                            return match["captured"]
                        else:
                            raise ReplParserDies(f"Invalid Syntax", no, line)
                if expected_indent:
                    if previous_space < (previous_space := space):
                        meet_collon.append(Indentor(space, expected_indent))
                        expected_indent = ()
                        indicator_mode = False
                        expected_indent = check_if_indenting(line)
                    else:
                        raise ReplParserDies(f"Expected Indent", no, line)
                elif space > previous_space:
                    raise ReplParserDies(f"Unexpected Indent", no, line)
                elif part := check_if_indenting(line):
                    expected_indent = part
                elif is_empty:
                    indicator_mode = False
                elif not space:
                    if meet_collon:
                        meet_collon = []
                    previous_space = 0
                elif meet_collon:
                    remove_until_true(lambda x_space: x_space.space > space, meet_collon)
                    if meet_collon and (x_space := meet_collon[-1]):
                        if x_space.space == space:
                            indicator_mode = False
                        elif x_space.space < space:
                            raise ReplParserDies(f"Unindent does not match any outer indentation level", no, line)
                        else:
                            raise ReplParserDies(f"Unexpected Indent", no, line)
                    else:
                        raise ReplParserDies(f"Unindent does not match any outer indentation level", no, line)
                yield indicator_mode
    
        def code_reader(codeblock):
            codes = codeblock.content.splitlines()
            parser = parsing()
            no_lang = codeblock.language is not None
            for line, no in zip(codes[no_lang:], parser):
                indent = parser.send(line)
                number = f"{no} " if flags.counter else ""
                yield f'{number}{(">>>", "...")[not indent]} {line}'

        newline = "\n"
        await ctx.send(f"```py{newline}{newline.join(code_reader(content))}\n```")

    def cog_unload(self):
        self.bot.help_command = self._default_help_command


def setup(bot):
    bot.add_cog(Helpful(bot))
