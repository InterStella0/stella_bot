import contextlib
import inspect
import json
import re
import discord
import copy
import humanize
import datetime
import textwrap
import itertools
import more_itertools
import typing
from fuzzywuzzy import process
from discord.ext import commands, menus
from utils.useful import BaseEmbed, MenuBase, plural, empty_page_format, unpack
from utils.decorators import pages
from utils.errors import CantRun
from utils.parser import ReplReader
from utils.greedy_parser import UntilFlag, command
from utils.buttons import BaseButton, ViewButtonIteration
from utils import flags as flg
from collections import namedtuple
from discord.ext.menus import First, Last, button
from jishaku.codeblocks import codeblock_converter

CommandHelp = namedtuple("CommandHelp", 'command brief command_obj')
emoji_dict = {"Bots": '<:robot_mark:848257366587211798>',
              "Useful": '<:useful:848258928772776037>',
              "Helpful": '<:helpful:848260729916227645>',
              "Statistic": '<:statis_mark:848262218554408988>',
              "Myself": '<:me:848262873783205888>',
              None: '<:question:848263403604934729>'}

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
        raise NotImplementedError

    async def start(self, ctx, **kwargs):
        self.help_command = ctx.bot.help_command
        self.help_command.context = ctx
        await super().start(ctx, **kwargs)


class HelpSource(menus.ListPageSource):
    def __init__(self, button, interaction, entries):
        super().__init__(entries, per_page=1)
        self.button = button
        self.interaction = interaction

    async def help_source_format(self, menu, entry):
        """This is for the help command ListPageSource"""
        cog, list_commands = entry
        new_line = "\n"
        embed = discord.Embed(title=f"{getattr(cog, 'qualified_name', 'No')} Category",
                            description=new_line.join(f'{command_help.command}{new_line}{command_help.brief}'
                                                        for command_help in list_commands),
                            color=menu.bot.color)
        author = menu.ctx.author
        await self.button.during_menu(self.interaction, list_commands)
        return embed.set_footer(text=f"Requested by {author}", icon_url=author.avatar.url)

    async def format_page(self, menu, entry):
        result = await self.help_source_format(menu, entry)
        return menu.generate_page(result, self._max_pages)

class HelpView(ViewButtonIteration):
    def __init__(self, help_object, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.help_object = help_object
        self.ctx = help_object.context
        self.bot = help_object.context.bot

class HelpMenuView(HelpView):
    def __init__(self, embed, page_source, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.page_source = page_source
        self.original_embed = embed
        self.menu = None
        self.__prepare = False

    async def start(self, button, interaction, data):
        if not self.__prepare:
            self.menu = HelpMenu(self.page_source(button, interaction, data), message=interaction.message)
            await self.menu.start(self.help_object.context)
            await self.menu.show_page(0)
            self.__prepare = True

    async def update(self, button, interaction, data):
        if not self.__prepare:
            await self.start(button, interaction, data)
        else:
            await self.menu.change_source(self.page_source(button, interaction, data))
        menu = self.menu # I declared it here because before `self.start`, `self.menu` is None, 4 hours wasted
        if not menu._Menu__tasks:
            loop = self.bot.loop
            menu._Menu__tasks.append(loop.create_task(menu._internal_loop()))
            current_react = [*map(str, interaction.message.reactions)]
            async def add_reactions_task():
                for emoji in menu.buttons:
                    if emoji not in current_react:
                        await interaction.message.add_reaction(emoji)
            menu._Menu__tasks.append(loop.create_task(add_reactions_task()))

    async def interaction_check(self, interaction):
        if interaction.user != self.ctx.author:
            await interaction.response.send_message(content=f"Only {self.ctx.author} can use this.", ephemeral=True)
            raise Exception("no")
        return True

class HelpSearchButton(BaseButton):
    async def callback(self, interaction):
        help_obj = self.view.help_object
        bot = help_obj.context.bot
        command = bot.get_command(self.selected)
        embed = help_obj.get_command_help(command)
        await interaction.response.send_message(content=f"Help for **{self.selected}**", embed=embed, ephemeral=True)

class HomeButton(BaseButton):
    async def callback(self, interaction):
        self.view.clear_items()
        for b in self.view.old_items:
            self.view.add_item(b)
        await interaction.message.edit(view=self.view, embed=self.view.original_embed)


class HelpButton(BaseButton):
    async def callback(self, interaction):
        view = self.view
        select = self.selected or "No Category"
        cog = view.bot.get_cog(select)
        data = [(cog, x) for x in view.mapper.get(cog)]
        self.view.old_items = copy.copy(self.view.children)
        await view.update(self, interaction, data)
    
    async def during_menu(self, interaction, list_commands):
        if not self.view.menu._running:
            return
        commands = [c.command_obj.name for c in list_commands]
        self.view.clear_items()
        self.view.add_item(HomeButton(style=discord.ButtonStyle.success, selected="Home", group=None, emoji='<:house_mark:848227746378809354>'))
        for c in commands:
            self.view.add_item(HelpSearchButton(style=discord.ButtonStyle.secondary, selected=c, group=None))

        await interaction.message.edit(view=self.view)

class HelpMenu(HelpMenuBase, inherit_buttons=False):
    """This is a MenuPages class that is used every single paginator menus. All it does is replace the default emoji
       with a custom emoji, and keep the functionality."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, form_buttons=False, **kwargs)
        self.help_command = None

    @menus.button("<:before_check:754948796487565332>", position=First(0))
    async def go_before(self, payload):
        """Goes to the previous page."""
        await self.show_checked_page(self.current_page - 1)

    @menus.button("<:next_check:754948796361736213>", position=Last(0))
    async def go_after(self, payload):
        """Goes to the next page."""
        await self.show_checked_page(self.current_page + 1)

    @menus.button("<:stop_check:754948796365930517>", position=First(1))
    async def dies(self, payload):
        """Deletes the message."""
        self._source.button.view.stop()
        self.stop()

    @menus.button('<:information_pp:754948796454010900>', position=Last(1))
    async def on_information(self, payload):
        """Shows this message"""
        await super().on_information(payload)

    async def on_information_show(self, payload):
        ctx = self.ctx
        embed = BaseEmbed.default(ctx,
                                  title="Information",
                                  description="This shows each commands in this bot. Each page is a category that shows "
                                              "what commands that the category have.")
        curr = self.current_page + 1 if (p := self.current_page > -1) else "cover page"
        pa = "page" if p else "the"
        embed.set_author(icon_url=ctx.bot.user.avatar.url,
                         name=f"You were on {pa} {curr}")
        nav = '\n'.join(f"{e} {b.action.__doc__}" for e, b in super().buttons.items())
        embed.add_field(name="Navigation:", value=nav)
        await self.message.edit(embed=embed, allowed_mentions=discord.AllowedMentions(replied_user=False))


class CogMenu(HelpMenuBase):
    """This is a MenuPages class that is used only in Cog help command. All it has is custom information and
       custom initial message."""
    async def on_information_show(self, payload):
        ctx = self.ctx
        embed = BaseEmbed.default(ctx,
                                  title="Information",
                                  description="This shows each commands in this category. Each page is a command that shows "
                                              "what's the command is about and a demonstration of usage.")
        curr = self.current_page + 1 if (p := self.current_page > -1) else "cover page"
        pa = "page" if p else "the"
        embed.set_author(icon_url=ctx.bot.user.avatar.url,
                         name=f"You were on {pa} {curr}")
        nav = '\n'.join(f"{self.dict_emoji[e].emoji} {self.dict_emoji[e].explain}" for e in map(emoji, super().buttons))
        embed.add_field(name="Navigation:", value=nav)
        await self.message.edit(embed=embed, allowed_mentions=discord.AllowedMentions(replied_user=False))


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
            return self.get_command_signature(com), self.get_help(com), com

        command_data = {}
        for cog, unfiltered_commands in mapping.items():
            if list_commands := await self.filter_commands(unfiltered_commands, sort=True):
                lists = command_data.setdefault(cog, [])
                for chunks in more_itertools.chunked(list_commands, 4):
                    lists.append([*map(lambda c: CommandHelp(*get_info(c)), chunks)])

        cog_names = [dict(selected=getattr(c, "qualified_name", "No Category"),
                          emoji=emoji_dict.get(getattr(c, "qualified_name", None))) for c in command_data]
        fields = ((f"{emoji_dict[getattr(c, 'qualified_name', None)]} {getattr(c, 'qualified_name', 'No')} Category [`{len([*unpack(i)])}`]", 
                      getattr(c, 'description', "Not documented"))
                      for c, i in command_data.items())

        embed = BaseEmbed.default(
            self.context,
            title="<:house_mark:848227746378809354> Help Command", 
            description=f"{self.context.bot.stella}'s personal bot\n**Select a Category:**",
            fields=fields
        )
        embed.set_thumbnail(url=self.context.bot.user.avatar)
        loads = {
            "style": discord.ButtonStyle.primary,
            "button": HelpButton,
            "mapper": command_data
        }
        menu_view = HelpMenuView(embed, HelpSource, self, cog_names, **loads)
        await self.context.reply(embed=embed, view=menu_view)
        with contextlib.suppress(discord.NotFound, discord.Forbidden):
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

    async def command_callback(self, ctx, search: typing.Optional[typing.Literal['search', 'select']], *, command=None):
        if search:
            bot = ctx.bot
            if command is not None:
                iterator = filter(lambda x: x[1] > 50, process.extract(command, [x.name for x in bot.commands], limit=5))
                result = [*more_itertools.chunked(map(lambda x: x[0], iterator), 2)]
                if result:
                    button_view = HelpView(self, *result, button=HelpSearchButton, style=discord.ButtonStyle.secondary)
                    await ctx.send("**Searched Command:**", view=button_view, delete_after=180)
                else:
                    await self.send_error_message(f'Unable to find any command that is even close to "{command}"')
            else:
                param = bot.get_command('help').params['command']
                ctx.current_parameter = param
                raise commands.MissingRequiredArgument(param)

        else:
            return await super().command_callback(ctx, command=command)


class Helpful(commands.Cog):
    """Commands that I think are helpful for users"""
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
    async def repl(self, ctx, code: UntilFlag[codeblock_converter], *, flags: flg.ReplFlag):
        newline = "\n"
        globals_ = {'ctx': ctx, 'author': ctx.author, 'guild': ctx.guild, 'bot': self.bot, 'discord': discord, 'commands': commands}
        flags = dict(flags)
        if flags.get('exec') and not await self.bot.is_owner(ctx.author):
            flags.update({"exec": False, "inner_func_check": True})
        await ctx.maybe_reply(f"```py{newline}{newline.join([o async for o in ReplReader(code, _globals=globals_, **flags)])}```")

    def cog_unload(self):
        self.bot.help_command = self._default_help_command


def setup(bot):
    bot.add_cog(Helpful(bot))
