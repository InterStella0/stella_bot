from __future__ import annotations
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
from pygit2 import Repository, GIT_SORT_TOPOLOGICAL
from fuzzywuzzy import process
from discord.ext import commands
from utils.useful import StellaEmbed, plural, empty_page_format, unpack, StellaContext, aware_utc, text_chunker, \
    in_executor
from utils.decorators import pages, event_check
from utils.errors import CantRun, BypassError
from utils.parser import ReplReader, repl_wrap
from utils.greedy_parser import UntilFlag, command, GreedyParser
from utils.buttons import BaseButton, InteractionPages, MenuViewBase, ViewButtonIteration, PersistentRespondView, \
    ButtonView
from utils.new_converters import CodeblockConverter
from utils.menus import ListPageInteractionBase, MenuViewInteractionBase, HelpMenuBase
from utils import flags as flg
from collections import namedtuple
from jishaku.codeblocks import Codeblock
from typing import Any, Tuple, List, Union, Optional, Dict, TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from main import StellaBot

CommandGroup = Union[commands.Command, commands.Group, GreedyParser]
CogHelp = namedtuple("CogAmount", 'name commands emoji description')
CommandHelp = namedtuple("CommandHelp", 'command brief command_obj')
emoji_dict = {"Bots": '<:robot_mark:848257366587211798>',
              "Useful": '<:useful:848258928772776037>',
              "Helpful": '<:helpful:848260729916227645>',
              "Statistic": '<:statis_mark:848262218554408988>',
              "Myself": '<:me:848262873783205888>',
              None: '<:question:848263403604934729>'}
home_emoji = '<:house_mark:848227746378809354>'


def message_getter(message_id: int) -> Callable[[StellaContext], Optional[discord.Message]]:
    def inner(context: StellaContext) -> Optional[discord.Message]:
        return context.get_message(message_id)
    return inner


def is_message_older_context(bot: StellaBot, message_id: int) -> bool:
    if not (cached_context := bot.cached_context):
        return False

    return message_id < cached_context[0].message.id


def is_command_message():
    def inner(self, payload):
        bot = self.bot
        if is_message_older_context(bot, payload.message_id):
            return False

        return discord.utils.get(bot.cached_context, message__id=payload.message_id) is not None
    return event_check(inner)


def is_message_context():
    async def inner(self, payload):
        bot = self.bot
        if is_message_older_context(bot, payload.message_id):
            return False

        return discord.utils.find(message_getter(payload.message_id), bot.cached_context)
    return event_check(inner)




@pages()
async def formatter(self, menu, entry):
    return f"```py\n{entry}```"


class HelpSource(ListPageInteractionBase):
    """This ListPageSource is meant to be used with view, format_page method is called first
       after that would be the format_view method which must return a View, or None to remove."""

    async def format_page(self, menu: HelpMenu, entry: Tuple[commands.Cog, List[CommandHelp]]) -> discord.Embed:
        """This is for the help command ListPageSource"""
        cog, list_commands = entry
        new_line = "\n"
        embed = discord.Embed(title=f"{getattr(cog, 'qualified_name', 'No')} Category",
                              description=new_line.join(f'{command_help.command}{new_line}{command_help.brief}'
                                                        for command_help in list_commands),
                              color=menu.bot.color)
        author = menu.ctx.author
        return embed.set_footer(text=f"Requested by {author}", icon_url=author.display_avatar)

    async def format_view(self, menu: HelpMenu, entry: Tuple[Optional[commands.Cog], List[CommandHelp]]) -> HelpMenuView:
        if not menu._running:
            return
        _, list_commands = entry
        commands = [c.command_obj.name for c in list_commands]
        menu.view.clear_items()
        menu.view.add_item(HomeButton(style=discord.ButtonStyle.success, selected="Home", row=None, emoji=home_emoji))
        for c in commands:
            menu.view.add_item(HelpSearchButton(style=discord.ButtonStyle.secondary, selected=c, row=None))

        return menu.view


class HelpMenuView(MenuViewBase):
    """This class is responsible for starting the view + menus activity for the help command.
       This accepts embed, help_command, context, page_source, dataset and optionally Menu.
       """
    def __init__(self, *data: Any, embed: discord.Embed, help_object: StellaBotHelp, context: StellaContext,
                 **kwargs: Any):
        super().__init__(context, HelpSource, *data,
                         button=HelpButton,
                         menu=HelpMenu,
                         style=discord.ButtonStyle.primary,
                         **kwargs)
        self.original_embed = embed
        self.help_command = help_object


class HomeButton(BaseButton):
    """This button redirects the view from the menu, into the category section, which
       adds the old buttons back."""

    async def callback(self, interaction: discord.Interaction) -> None:
        self.view.clear_items()
        for b in self.view.old_items:
            self.view.add_item(b)
        await interaction.message.edit(view=self.view, embed=self.view.original_embed)


class HelpButton(BaseButton):
    """This Button update the menu, and shows a list of commands for the cog.
       This saves the category buttons as old_items and adds relevant buttons that
       consist of HomeButton, and HelpSearchButton."""

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        bot = view.help_command.context.bot
        select = self.selected or "No Category"
        cog = bot.get_cog(select)
        data = [(cog, commands_list) for commands_list in view.mapper.get(cog)]
        self.view.old_items = copy.copy(self.view.children)
        await view.update(self, interaction, data)


class HelpSearchView(ViewButtonIteration):
    """This view class is specifically for command_callback method"""

    def __init__(self, help_object: StellaBotHelp, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.help_command = help_object
        self.ctx = help_object.context
        self.bot = help_object.context.bot


class HelpSearchButton(BaseButton):
    """This class is used inside a help command that shows a help for a specific command.
       This is also used inside help search command."""

    async def callback(self, interaction: discord.Interaction) -> None:
        help_obj = self.view.help_command
        bot = help_obj.context.bot
        command = bot.get_command(self.selected)
        embed = help_obj.get_command_help(command)
        await interaction.response.send_message(content=f"Help for **{self.selected}**", embed=embed, ephemeral=True)


class Information(HelpMenuBase):
    async def on_information_show(self, payload: discord.RawReactionActionEvent) -> None:
        ctx = self.ctx
        embed = StellaEmbed.default(ctx, title="Information", description=self.description)
        curr = self.current_page + 1 if (p := self.current_page > -1) else "cover page"
        pa = ("page", "the")[not p]
        embed.set_author(icon_url=ctx.bot.user.display_avatar, name=f"You were on {pa} {curr}")
        nav = '\n'.join(f"{e} {b.action.__doc__}" for e, b in super().buttons.items())
        embed.add_field(name="Navigation:", value=nav)
        await self.message.edit(embed=embed, allowed_mentions=discord.AllowedMentions(replied_user=False))


class HelpMenu(MenuViewInteractionBase, Information):
    """MenuPages class that is specifically for the help command."""
    def __init__(self, *args: Any, description: Optional[str] = None, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.description = description or """This shows each commands in this bot. Each page is a category that shows 
                                             what commands that the category have."""


class CogMenu(Information):
    def __init__(self, *args: Any, description: Optional[str] = None, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.description = description


class StellaBotHelp(commands.DefaultHelpCommand):
    def __init__(self, **options: Any):
        super().__init__(**options)
        with open("d_json/help.json") as r:
            self.help_gif = json.load(r)

    def get_command_signature(self, command: CommandGroup, ctx: Optional[StellaContext] = None) -> str:
        """Method to return a commands name and signature"""
        def get_invoke_with():
            msg = ctx.message.content
            prefixmax = re.match(f'{re.escape(ctx.prefix)}', ctx.message.content).regs[0][1]
            return msg[prefixmax:msg.rindex(ctx.invoked_with)]

        parent = command.parent
        with contextlib.suppress(ValueError):
            parent = get_invoke_with() if ctx else command.parent
        command_name = ctx.invoked_with if ctx else command.name
        prefix = (ctx or self.context).clean_prefix

        if not command.signature and not command.parent:
            return f'{prefix}{command_name}'
        if command.signature and not command.parent:
            return f'{prefix}{command_name} {command.signature}'
        if not command.signature and command.parent:
            return f'{prefix}{parent} {command_name}'
        else:
            return f'{prefix}{parent} {command_name} {command.signature}'

    def get_help(self, command: CommandGroup, brief: Optional[bool] = True) -> str:
        """Gets the command short_doc if brief is True while getting the longer help if it is false"""
        real_help = command.help or "This command is not documented."
        return real_help if not brief else command.short_doc or real_help

    def get_demo(self, command: CommandGroup) -> str:
        """Gets the gif demonstrating the command."""
        com = command.name
        if com not in self.help_gif:
            return ""
        return f"{self.context.bot.help_src}/{self.help_gif[com]}/{com}_help.gif"

    def get_aliases(self, command: CommandGroup) -> List[str]:
        """This isn't even needed jesus christ"""
        return command.aliases

    def get_old_flag_help(self, command: CommandGroup) -> List[str]:
        """Gets the flag help if there is any."""

        def c(x):
            return "_OPTIONAL" not in x.dest

        return ["**--{0.dest} |** {0.help}".format(action) for action in command.callback._def_parser._actions if
                c(action)]

    def get_flag_help(self, command: CommandGroup) -> Tuple[List[str], List[str]]:
        required_flags = []
        optional_flags = []
        if param := flg.find_flag(command):
            for name, flags in param.annotation.__commands_flags__.items():
                not_documented = "This flag is not documented."
                description = getattr(flags, "help", not_documented) or not_documented
                formatted = f"**{':** | **'.join(itertools.chain([name], flags.aliases))}:** **|** {description}"
                list_append = (required_flags, optional_flags)[command._is_typing_optional(flags.annotation)]
                list_append.append(formatted)
        return required_flags, optional_flags

    async def send_bot_help(self, mapping: Dict[Optional[commands.Cog], CommandGroup]) -> None:
        """Gets called when `uwu help` is invoked"""

        def get_command_help(com: CommandGroup) -> CommandHelp:
            signature = self.get_command_signature(com)
            desc = self.get_help(com)
            return CommandHelp(signature, desc, com)

        def get_cog_help(cog: Optional[commands.Cog],
                         cog_commands: List[List[CommandGroup]]) -> CogHelp:
            cog_name_none = getattr(cog, "qualified_name", None)
            cog_name = cog_name_none or "No Category"
            cog_description = getattr(cog, 'description', "Not documented")
            cog_emoji = emoji_dict.get(cog_name_none) or emoji_dict[None]
            cog_amount = len([*unpack(cog_commands)])
            return CogHelp(cog_name, cog_amount, cog_emoji, cog_description)

        ctx = self.context
        bot = ctx.bot
        EACH_PAGE = 4
        command_data = {}
        for cog, unfiltered in mapping.items():
            if list_commands := await self.filter_commands(unfiltered, sort=True):
                lists = command_data.setdefault(cog, [])
                for chunks in discord.utils.as_chunks(list_commands, EACH_PAGE):
                    lists.append([*map(get_command_help, chunks)])

        mapped = itertools.starmap(get_cog_help, command_data.items())
        sort_cog = [*sorted(mapped, key=lambda c: c.commands, reverse=True)]
        stella = bot.stella
        embed = StellaEmbed.default(
            ctx,
            title=f"{home_emoji} Help Command",
            description=f"{bot.description.format(stella)}\n\n**Select a Category:**",
            fields=map(lambda ch: ("{0.emoji} {0.name} [`{0.commands}`]".format(ch), ch.description), sort_cog)
        )
        payload = {
            "bot_name": str(bot.user),
            "name": str(bot.stella),
            "author_avatar": ctx.author.display_avatar.url,
            "author_avatar_hash": ctx.author.display_avatar.key,
            "author_name": str(ctx.author)
        }
        banner = await bot.ipc_client.request("generate_banner", **payload)
        if isinstance(banner, str):
            embed.set_image(url=banner)
        embed.set_author(name=f"By {stella}", icon_url=stella.display_avatar)

        loads = {
            "embed": embed,
            "help_object": self,
            "context": ctx,
            "mapper": command_data
        }
        cog_names = [dict(selected=ch.name, emoji=ch.emoji) for ch in sort_cog]
        buttons = discord.utils.as_chunks(cog_names, 5)
        menu_view = HelpMenuView(*buttons, **loads)
        await ctx.reply(embed=embed, view=menu_view)

    def get_command_help(self, command: commands.Command) -> discord.Embed:
        """Returns an Embed version of the command object given."""
        embed = StellaEmbed.default(self.context)
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

    async def handle_help(self, command: commands.Command) -> discord.Message:
        with contextlib.suppress(commands.CommandError):
            await command.can_run(self.context)
            return await self.context.reply(embed=self.get_command_help(command), mention_author=False)
        raise CantRun("You don't have enough permission to see this help.") from None

    async def send_command_help(self, command: commands.Command) -> None:
        """Gets invoke when `uwu help <command>` is invoked."""
        await self.handle_help(command)

    async def send_group_help(self, group: commands.Group) -> None:
        """Gets invoke when `uwu help <group>` is invoked."""
        await self.handle_help(group)

    async def send_cog_help(self, cog: commands.Cog) -> None:
        """Gets invoke when `uwu help <cog>` is invoked."""
        cog_commands = [self.get_command_help(c) for c in await self.filter_commands(cog.walk_commands(), sort=True)]
        description = """This shows each commands in this category. Each page is a command 
                         that shows what's the command is about and a demonstration of usage."""
        pages = CogMenu(source=empty_page_format(cog_commands), description=description)
        with contextlib.suppress(discord.NotFound, discord.Forbidden):
            await pages.start(self.context, wait=True)
            await self.context.confirmed()

    def command_not_found(self, string: str) -> Tuple[str, str]:
        return super().command_not_found(string), string

    def subcommand_not_found(self, command: commands.Group, string: str) -> Tuple[str, str, commands.Group]:
        return super().subcommand_not_found(command, string), string, command

    async def send_error_message(self, error: Tuple[str, str, Optional[commands.Group]]) -> None:
        await self.handle_error_message(*error)

    async def handle_error_message(self, error: str, command: str, group: Optional[commands.Group] = None) -> None:
        ctx = self.context
        to_search = group.commands if group else ctx.bot.commands
        filtered = filter(lambda x: x[1] > 50, process.extract(command, [x.name for x in to_search], limit=5))
        mapped = itertools.starmap(lambda x, *_: f"{group} {x}" if group else x, filtered)
        if result := list(discord.utils.as_chunks(mapped, 2)):
            button_view = HelpSearchView(self, *result, button=HelpSearchButton, style=discord.ButtonStyle.secondary)
            message = f"{error}.\nShowing results for the closest command to `{command}`:"
            await ctx.reply(message, view=button_view, delete_after=180)
        else:
            await super().send_error_message(error)


class Helpful(commands.Cog):
    """Commands that I think are helpful for users"""

    def __init__(self, bot: StellaBot):
        self._default_help_command = bot.help_command
        bot.help_command = StellaBotHelp()
        bot.help_command.cog = self
        self.bot = bot
        self.cooldown_report = commands.CooldownMapping.from_cooldown(5, 30, commands.BucketType.user)

    @commands.command(aliases=["ping", "p"],
                      help="Shows the bot latency from the discord websocket.")
    async def pping(self, ctx: StellaContext):
        await ctx.embed(
            title="PP",
            description=f"Your pp lasted `{self.bot.latency * 1000:.2f}ms`"
        )

    @commands.command(aliases=["up"],
                      help="Shows the bot uptime from when it was started.")
    async def uptime(self, ctx: StellaContext):
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
    async def source(self, ctx: StellaContext, content: str = None, **flags: bool):
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
            menu = InteractionPages(empty_page_format(list_codeblock))
            await menu.start(ctx)
        else:
            lines, firstlineno = inspect.getsourcelines(src)
            location = module.replace('.', '/') + '.py'
            url = f'<{source_url}/blob/master/{location}#L{firstlineno}-L{firstlineno + len(lines) - 1}>'
            await ctx.embed(title=f"Here's uh, {content}", description=f"[Click Here]({url})")

    @commands.command(help="Gives you the invite link")
    async def invite(self, ctx: StellaContext):
        await ctx.maybe_reply(f"Thx\n<{discord.utils.oauth_url(ctx.me.id)}>")

    @in_executor()
    def get_wrapped(self, ctx, code, **flags):
        if ctx.guild:
            guild_values = [{"channel__id": c.id, "channel__name": c.name, "guild__id": c.guild.id}
                            for c in ctx.guild.text_channels]
            user_values = [{"user__id": u.id, "user__name": u.name, "user__nick": u.nick, "user__bot": u.bot,
                            "user__discriminator": u.discriminator}
                           for u in ctx.guild.members[:100]]
            message_values = [{"message__id": m.id, "message__content": m.content,
                               "message__author": m.author.id, "channel_id": m.channel.id,
                               "guild__id": m.guild.id}
                              for m in self.bot.cached_messages if m.guild == ctx.guild][:100]
        else:
            c = ctx.channel
            u = ctx.author
            guild_values = [{"channel__id": c.id, "channel__name": None, "guild__id": None}]
            user_values = [{"user__id": u.id, "user__name": u.name, "user__nick": u.nick, "user__bot": u.bot,
                            "user__discriminator": u.discriminator}]
            message_values = [{"message__id": m.id, "message__content": m.content, "message__author": u.id,
                               "channel_id": m.channel.id, "guild__id": m.guild}
                              for m in self.bot.cached_messages if m.channel.id == ctx.channel.id]
        context = {
            "context": {
                "channel_id": ctx.channel.id,
                "message_id": ctx.message.id,
                "bot__id": ctx.me.id,
                "prefix": ctx.clean_prefix
            },
            "_bot": {
                "channels": guild_values,
                "guilds": [{"guild__id": ctx.guild.id, "guild__name": ctx.guild.name}] if ctx.guild else []
            },
            "members": user_values,
            "cached_messages": message_values
        }  # Allowed variables to be passed

        return repl_wrap(code.content, context, **flags)

    @command(help="Simulate a live python interpreter interface when given a python code.")
    @commands.max_concurrency(1, commands.BucketType.user)
    async def repl(self, ctx: StellaContext, code: UntilFlag[CodeblockConverter], *, flags: flg.ReplFlag):
        globals_ = {
            'ctx': ctx,
            'author': ctx.author,
            'guild': ctx.guild,
            'bot': self.bot,
            'discord': discord,
            'commands': commands
        }
        flags = dict(flags)
        if flags.get('exec') and not await self.bot.is_owner(ctx.author):
            if code.language is None:
                content = code.content
                code = Codeblock("py", f"\n{content}\n")

            coded = await self.get_wrapped(ctx, code, **flags)
            accepted = await self.bot.ipc_client.request("execute_python", code=coded)
            if output := accepted.get("output"):
                code = output
            else:
                raise commands.CommandError(f"It died sorry dan maaf")

        else:
            code = "\n".join([o async for o in ReplReader(code, _globals=globals_, **flags)])

        text = text_chunker(code, width=1900, max_newline=20)

        concurrent = ctx.command._max_concurrency
        await concurrent.release(ctx.message)

        if len(text) > 1:
            menu = InteractionPages(formatter(text))
            await menu.start(ctx)
        elif len(text) == 1:
            code, = text
            view = ButtonView(ctx)
            await ctx.maybe_reply(f"```py\n{code}```", view=view, allowed_mentions=discord.AllowedMentions.none())
        else:
            await ctx.maybe_reply(f"```py\nNo Output```")

    @commands.command(help="Reports to the owner through the bot. Automatic blacklist if abuse.")
    @commands.cooldown(1, 60, commands.BucketType.user)
    async def report(self, ctx: StellaContext, *, message: str):
        usure = f"Are you sure you wanna send this message to `{self.bot.stella}`?"
        if not await ctx.confirmation(usure, delete_after=True):
            await ctx.confirmed()
            return

        try:
            embed = StellaEmbed.default(
                ctx,
                title=f"Report sent to {self.bot.stella}",
                description=f"**You sent:** {message}"
            )
            embed.set_author(name=f"Any respond from {self.bot.stella} will be through DM.")
            interface = await ctx.author.send(embed=embed)
        except discord.Forbidden:
            died = "Unable to send a DM, please enable DM as it is crucial for the report."
            raise commands.CommandError(died)
        else:
            query = "INSERT INTO reports VALUES(DEFAULT, $1, False, $2) RETURNING report_id"
            created_at = ctx.message.created_at.replace(tzinfo=None)
            report_id = await self.bot.pool_pg.fetchval(query, ctx.author.id, created_at, column='report_id')

            embed = StellaEmbed.default(ctx, title=f"Reported from {ctx.author} ({report_id})", description=message)
            msg = await self.bot.stella.send(embed=embed, view=PersistentRespondView(self.bot))
            await ctx.confirmed()

            query_msg = "INSERT INTO report_respond VALUES($1, $2, $3, $4, $5)"
            msg_values = (report_id, ctx.author.id, msg.id, interface.id, message)
            await self.bot.pool_pg.execute(query_msg, *msg_values)

    @report.error
    async def report_error(self, ctx: StellaContext, error: commands.CommandError):
        if isinstance(error, commands.CommandOnCooldown):
            if self.cooldown_report.update_rate_limit(ctx.message):
                await self.bot.add_blacklist(ctx.author.id, "Spamming cooldown report message.")
        self.bot.dispatch("command_error", ctx, BypassError(error))

    @commands.command(aliases=["aboutme"], help="Shows what the bot is about. It also shows recent changes and stuff.")
    async def about(self, ctx: StellaContext):
        REPO_URL = "https://github.com/InterStella0/stella_bot"
        embed = StellaEmbed.default(
            ctx, 
            title=f"About {self.bot.user}", 
            description=self.bot.description.format(self.bot.stella),
            url=REPO_URL
        )
        payload = {
            "bot_name": str(self.bot.user),
            "name": str(self.bot.stella),
            "author_avatar": ctx.author.display_avatar.url,
            "author_avatar_hash": ctx.author.display_avatar.key,
            "author_name": str(ctx.author)
        }
        banner = await self.bot.ipc_client.request("generate_banner", **payload)
        if isinstance(banner, str):
            embed.set_image(url=banner)
        repo = Repository('.git')
        HEAD = repo.head.target
        COMMIT_AMOUNT = 4
        iterator = itertools.islice(repo.walk(HEAD, GIT_SORT_TOPOLOGICAL), COMMIT_AMOUNT)

        def format_commit(c):
            time = datetime.datetime.fromtimestamp(c.commit_time)
            repo_link = f"{REPO_URL}/commit/{c.hex}"
            message, *_ = c.message.partition("\n")
            return f"[`{c.hex[:6]}`] [{message}]({repo_link}) ({aware_utc(time, mode='R')})"

        embed.add_field(name="Recent Changes", value="\n".join(map(format_commit, iterator)), inline=False)
        embed.add_field(name="Launch Time", value=f"{aware_utc(self.bot.uptime, mode='R')}")
        embed.add_field(name="Bot Ping", value=f"{self.bot.latency * 1000:.2f}ms")
        bots = sum(u.bot for u in self.bot.users)
        content = f"`{len(self.bot.guilds):,}` servers, `{len(self.bot.users) - bots:,}` users, `{bots:,}` bots"
        embed.add_field(name="Users", value=content)
        await ctx.embed(embed=embed)

    @commands.Cog.listener("on_raw_bulk_message_delete")
    async def remove_context_messages(self, payload: discord.RawBulkMessageDeleteEvent):
        bot = self.bot
        for message_id in payload.message_ids:
            if is_message_older_context(bot, message_id):
                continue

            if ctx := discord.utils.find(message_getter(message_id), bot.cached_context):
                ctx.remove_message(message_id)

    @commands.Cog.listener("on_raw_message_delete")
    @is_message_context()
    async def remove_context_message(self, payload: discord.RawMessageDeleteEvent):
        target = payload.message_id
        if ctx := discord.utils.find(message_getter(target), self.bot.cached_context):
            ctx.remove_message(target)

    @commands.Cog.listener("on_raw_message_delete")
    @is_command_message()
    async def on_command_delete(self, payload: discord.RawMessageDeleteEvent):
        context = discord.utils.get(self.bot.cached_context, message__id=payload.message_id)
        await context.delete_all()

    def cog_unload(self) -> None:
        self.bot.help_command = self._default_help_command


def setup(bot: StellaBot) -> None:
    bot.add_cog(Helpful(bot))
