import contextlib
import re
import traceback
import itertools
import io
import textwrap
import warnings
import inspect
from typing import Any, List, Callable, Iterable, Optional, Union, Tuple, Generator, Dict, AsyncGenerator
from collections import namedtuple
from jishaku.codeblocks import Codeblock
from discord.utils import find, get, snowflake_time
from utils.errors import ReplParserDies
from utils.useful import cancel_gen


Indentor = namedtuple("Indentor", "space part func")
IMPORT_REGEX = re.compile(r"(?P<import>\w+!)((?=(?:(?:[^\"']*(\"|')){2})*[^\"']*$))")


def get_import(d: re.Match) -> str:
    return d['import'][:-1]


class ReplParser:
    def __init__(self, **kwargs: Any):
        self.inner_func_check = kwargs.pop('inner_func_check', True)
        self.continue_parsing = 0
        self.combining_parse = []
        self.previous_line = ""
        self.previous_space = 0
        self.space = 0
        self.expecting_combo = []
        self.meet_collon = []
        self.expected_indent = None
        self.indicator_mode = None
        self.FUNCTION_DEF = ["async def", "def"]
        self.SYNC_FUNC = ["yield", "return"]
        self.ASYNC_FUNC_ONLY = ["await"]
        self.ASYNC_OR_SYNC = self.ASYNC_FUNC_ONLY + self.SYNC_FUNC
        self.SYNC_FUNC_ONLY = ["yield from"]
        self.ALL_FUNC = self.SYNC_FUNC_ONLY + self.ASYNC_OR_SYNC
        self.ALL_FUNC = list(sorted(self.ALL_FUNC, key=lambda x: len(x), reverse=True))
        # Yes, i'm aware of other ways to have selection regex, i dont care, i need to reuse the constants
        self.FUNC_INNER_REGEX = rf".*(\s+)?(?P<captured>{self.form_re_const(self.ALL_FUNC)})(\s+|.)?(?P<statement>.*)"

        self.CLASS_DEF_REGEX = r"(\s+)?(?P<captured>class)(\s+)(?P<name>([a-zA-Z_])(([a-zA-Z0-9_])+)?)((\((?P<subclass>.*)\))?(\s+)?:)"

        self.FUNC_DEF_REGEX = rf"(\s+)?(?P<captured>{self.form_re_const(self.FUNCTION_DEF)})" \
                              r"(\s+)(?P<name>([a-zA-Z_])(([a-zA-Z0-9_])+)?)()(\((?P<parameter>[^\)]*)\)(\s+)?(->(\s+)?(?P<returnhint>.*))?:)"
        
        self.WITH_DEF_REGEX = r"(\s+)?(?P<captured>async with|with)(\s+)(?P<statement>[^\s]+)(\s+)?(as(\s+)(?P<var>([a-zA-Z_])(([a-zA-Z0-9_])+)?))?(\s+)"\
                              r"?(((\s+)?\,(\s+)?(?P<statement2>[^\s]+)(\s+)?(as(\s+)(?P<var2>([a-zA-Z_])(([a-zA-Z0-9_])+)?))?)+)?(\s+)?:(\s+)?"
        self.FOR_DEF_REGEX = r"(\s+)?(?P<captured>async for|for)(\s+)(?P<statement>(?P<var>.*)(\s+)in(\s+)(?P<iterator>.*))(\s+)?:"

        self.EXCEPT_STATE_REGEX = r"(\s+)?(?P<captured>except)(\s+)?((\s+)(?P<exception>[^\s]+)((\s+)as(\s+)((?P<var>([a-zA-Z_])(([a-zA-Z0-9_])+)?)))?)?(\s+)?(\s+)?:"

        self.DECORATOR_REGEX = r"(\s+)?(?P<captured>\@)(?P<name>[^(]+)(?P<parameter>\(.*\))?(\s+)?"

        WITHARG_CONST = ["while", "if", "elif"]
        self.WITHARG_REGEX = rf"(^(\s+)?(?P<captured>({self.form_re_const(WITHARG_CONST)})))(\s+).*((\s+)?:(\s+)?)"

        self.UNCLOSED = rf".*(?P<unclosed>\()([^\)]+)?(?P<closed>\)?)"
        self.CLOSED = rf".*(?P<closed>\))"

        self.JOINER = {
            "else": ['if', 'elif', 'except'], 
            'except': ['try'], 
            'finally': ['try', 'else', 'except'], 
            'elif': ['if']
        }

        self.COMBINATION = {
            "try": ["except", "finally"],
            '@': ["async def", "def", '@']
        }

        self.CONNECT_REGEX = rf"(\s+)?(?P<captured>({self.form_re_const(self.COMBINATION, self.JOINER)}))(\s+)?:(\s+)?"
        self.COLLON_DEC_REGEX = r"(^(\s)*(@)|.*(:)(\s)*$)"

    @staticmethod
    def form_re_const(*iterables: List[str]) -> str:
        return '|'.join(map(re.escape, itertools.chain(*iterables)))

    @staticmethod
    def remove_until_true(predicate: Callable, iterable: List[Indentor]) -> Optional[Indentor]:
        x_space = None
        for x_space in itertools.takewhile(predicate, reversed(iterable)):
            iterable.remove(x_space)
        if iterable and (x_space := iterable[-1]):
            return x_space

    def validation_syntax(self, _: int, line: str) -> re.Match:
        for regex in (self.FUNC_DEF_REGEX, self.CLASS_DEF_REGEX, self.WITH_DEF_REGEX, self.DECORATOR_REGEX,
                      self.FOR_DEF_REGEX, self.EXCEPT_STATE_REGEX, self.WITHARG_REGEX):
            if match := re.match(regex, line):
                return match

    def check_if_indenting(self, no: int, line: str) -> str:
        if re.match(self.COLLON_DEC_REGEX, line):
            if (match := self.validation_syntax(no, line) or re.fullmatch(self.CONNECT_REGEX, line)) is not None:
                return self.execute_inside_dent(no, line, match)
            raise ReplParserDies("Invalid Syntax", no, line, self.indicator_mode)

    def execute_inside_dent(self, no: int, line: str, match: re.Match) -> str:
        captured = match["captured"]
        if part := self.JOINER.get(captured):
            ind = get(self.meet_collon, space=self.space)
            if getattr(ind, "part", None) in part:
                index = self.meet_collon.index(ind)
                self.meet_collon[index] = Indentor(self.space, captured, ind.func)
                self.indicator_mode = False
                if match := re.match(self.FUNC_INNER_REGEX, line):
                    self.inside_function_statement(no, line, ind, match)
            else:
                raise ReplParserDies("Invalid Syntax", no, line, self.indicator_mode)
        if expect := get(self.expecting_combo, space=self.space):
            if captured not in self.COMBINATION.get(expect.part):
                raise ReplParserDies("Invalid Syntax", no, line, self.indicator_mode)
            if expect.part == '@':
                self.indicator_mode = False
            self.expecting_combo.remove(expect)
        if self.COMBINATION.get(captured):
            self.expecting_combo.append(Indentor(self.space, captured, None))
        if self.space:
            self.indicator_mode = False
        if x_space := self.remove_until_true(lambda x: x.space > self.space, self.meet_collon):
            self.meet_collon[-1] = Indentor(x_space.space, captured, x_space.func)
        else:
            self.meet_collon.append(Indentor(self.space, captured, None))
        self.previous_space = self.space
        return captured

    def inside_function_state_no_space(self, no: int, line: str) -> None:
        if not self.inner_func_check:
            return

        if match := re.match(self.FUNC_INNER_REGEX, line):
            raise ReplParserDies(f"'{match['captured']}' outside function.", no, line, self.indicator_mode)

    def inside_function_statement(self, no: int, line: str, x_space: Indentor, match: re.Match) -> None:
        syntax = match["captured"]
        statement = match["statement"]
        if not self.inner_func_check:
            return

        if x_space.func:  # In a function
            is_async = "async" in x_space.func
            if is_async: 
                if value := find(lambda x: x == syntax, self.SYNC_FUNC_ONLY):
                    raise ReplParserDies(f"'{value}' is inside async function.", no, line, self.indicator_mode)
            else:
                if value := find(lambda x: x == syntax, self.ASYNC_FUNC_ONLY):
                    raise ReplParserDies(f"'{value}' is outside async function.", no, line, self.indicator_mode)
            
            if not statement and syntax not in ("yield", "return"):
                raise ReplParserDies("Syntax Error", no, line, self.indicator_mode)
        else:
            raise ReplParserDies(f"'{syntax}' outside function.", no, line, self.indicator_mode)

    def indentation_checker(self, no: int, line: str) -> None:
        if x_space := self.remove_until_true(lambda x: x.space > self.space, self.meet_collon):
            if x_space.space == self.space:
                if match := re.match(self.FUNC_INNER_REGEX, line):
                    self.inside_function_statement(no, line, x_space, match)
                if self.space > 0:
                    self.indicator_mode = False
            elif x_space.space < self.space:
                raise ReplParserDies("Unindent does not match any outer indentation level", no, line, self.indicator_mode)
            else:
                raise ReplParserDies("Unexpected Indent", no, line, self.indicator_mode)
        else:
            raise ReplParserDies("Unindent does not match any outer indentation level", no, line, self.indicator_mode)

    def __aiter__(self) -> AsyncGenerator[int, str]:
        return self._internal()

    def reading_parenthesis(self, no: int, line: str) -> Generator[Union[bool, int], str, None]:
        self.indicator_mode = False
        while True:
            self.combining_parse.append(line)
            line = yield no
            yield self.indicator_mode
            if re.match(self.UNCLOSED, line):
                self.continue_parsing += 1
            if re.match(self.CLOSED, line):
                self.continue_parsing -= 1
            if not self.continue_parsing:
                yield "\n".join(self.combining_parse) + f"\n{line}"
                self.combining_parse.clear()
                return

    async def _internal(self) -> AsyncGenerator[Union[int, bool, str], str]:
        for no in itertools.count(1):
            self.indicator_mode = True
            line = yield no

            if line == 0 or line is None:  # End of line, check for syntax combination statement
                if self.expecting_combo:
                    raise ReplParserDies("Syntax Error", no, "", self.indicator_mode)
                self.space = 0
                self.parsing(no, "")
                return
            returning = True
            # Check for incomplete parenthesis
            if match := re.match(self.UNCLOSED, line):
                if match['closed'] == "":
                    if self.expected_indent:
                        self.indicator_mode = False
                    self.continue_parsing += 1
                    parse = self.reading_parenthesis(no, line)
                    yield self.indicator_mode
                    for li in parse:
                        if isinstance(li, str):
                            line = li
                        else:
                            yield parse.send((yield li))
                    returning = False
            self.space = re.match(r"(\s+)?", line).span()[-1]
            val = self.parsing(no, line)
            if returning:
                yield val 
            self.previous_line = line

    def parsing(self, no: int, line: str, /) -> bool:
        is_empty = line[self.space:] == ""
        if self.expected_indent != '@' and self.expected_indent is not None:
            if self.previous_space < self.space:
                self.previous_space = self.space
                if before := find(lambda x: x.func in self.FUNCTION_DEF, reversed(self.meet_collon)):
                    func = before.func
                else:
                    func = self.expected_indent if self.expected_indent in self.FUNCTION_DEF else None
                indentor = Indentor(self.space, "", func)
                self.meet_collon.append(indentor)
                
                if match := re.match(self.FUNC_INNER_REGEX, line):
                    self.inside_function_statement(no, line, indentor, match)
                self.indicator_mode = False
                if not is_empty:
                    self.expected_indent = None
                    self.expected_indent = self.check_if_indenting(no, line)
            else:
                raise ReplParserDies("Expected Indent", no, line, self.indicator_mode)
        elif self.space > self.previous_space:
            raise ReplParserDies("Unexpected Indent", no, line, self.indicator_mode)
        elif part := self.check_if_indenting(no, line):
            self.expected_indent = part
        elif is_empty and self.meet_collon:
            self.indicator_mode = False
        elif not self.space:
            if self.meet_collon:
                self.meet_collon = []
            self.previous_space = 0
            self.inside_function_state_no_space(no, line)
        if self.meet_collon and not is_empty:
            self.indentation_checker(no, line)
        return self.indicator_mode


class ReplReader:
    def __init__(self, codeblock: Codeblock, *, _globals: dict = (), **flags: Any):
        if isinstance(_globals, tuple):
            _globals = {}
        self.iterator = ReplParser(**flags).__aiter__()
        self.codeblock = codeblock
        self.counter = flags.get("counter")
        self.executor = self.compile_exec(_globals=_globals) if flags.get("exec") else self.empty()

    def __aiter__(self) -> AsyncGenerator[str, None]:
        return self.reader_handler()

    async def reader_handler(self) -> AsyncGenerator[str, None]:
        async for each in self.reading_codeblock():
            if isinstance(each, tuple):
                compiled, _ = each
                yield compiled
                return
            yield each
        # eof
        await cancel_gen(self.iterator)
        await cancel_gen(self.executor)

    async def runner(self, code: str) -> AsyncGenerator[Tuple[Any], None]:
        with contextlib.suppress(StopAsyncIteration):
            for line in code:
                result = [line]
                for x in (self.iterator, self.executor):
                    result.append(await x.__anext__())
                yield tuple(result)
        # eof or raised
        for x in (self.iterator, self.executor):
            await x.__anext__()

    async def handle_repl(self, line: str) -> Union[Tuple[str, Exception], str, int]:
        try:
            return await self.iterator.asend(line)
        except ReplParserDies as e:
            lines = traceback.format_exception(type(e), e, e.__traceback__)
            return "".join(lines), e

    async def reading_codeblock(self) -> AsyncGenerator[str, None]:
        codes = self.codeblock.content.splitlines()
        no_lang = self.codeblock.language is not None
        async for line, no, ex in self.runner(codes[no_lang:]):
            if isinstance(indent := await self.handle_repl(line), tuple):
                _, error = indent
                indicator = ("...", ">>>")[error.mode]
                yield f"{indicator} {line}"
                yield indent
            number = f"{no} " if self.counter else ""
            compiled = await self.executor.asend((line, indent))
            if ex and indent and compiled:
                yield compiled
            yield f'{number}{("...", ">>>")[indent]} {line}'
        else:  # eof or raise
            try:
                if compiled := await self.executor.asend((0, True)):
                    yield compiled
                await self.iterator.asend(0)
            except StopAsyncIteration:
                return

    @staticmethod
    def importer(compiled_str: str, global_vars: Dict[str, Any]) -> str:
        for ori in re.finditer(IMPORT_REGEX, compiled_str):
            x = get_import(ori)
            global_vars.update({x: __import__(x)})

        return re.sub(IMPORT_REGEX, get_import, compiled_str)

    @staticmethod
    def wrap_function(compiled: str) -> str:
        is_one_line = len(compiled.splitlines()) == 1
        get_local = "    yield {0}\n    yield locals()"
        before = "async def __inner_function__():\n"
        if is_one_line:
            with contextlib.suppress(SyntaxError):
                return compile(f"{before}{get_local.format(compiled)}", 'repl_command', 'exec')

        return f"{before}{textwrap.indent(compiled, '    ')}\n{get_local.format('')}"

    @staticmethod
    def get_first_character(iterable: Iterable[str]) -> Optional[str]:
        for x in iterable:
            no_space = re.match(r"(\s+)?", x).span()[-1]
            if x[no_space:] in ("", "\n", " "):
                continue
            return x

    def form_compiler(self, build_str: str, global_vars: Dict[str, Any]) -> Tuple[Union[exec, eval], Any]:
        imported_compiled = self.importer("\n".join(build_str), global_vars)
        caller = exec
        if len(build_str) == 1: 
            # Only wrap with async functions when it's an async operation
            if "await" in imported_compiled:
                imported_compiled = self.wrap_function(imported_compiled)
            else:
                with contextlib.suppress(SyntaxError):
                    imported_compiled = compile(imported_compiled, 'repl_command', 'eval')
                    caller = eval
        elif any(x in self.get_first_character(build_str) for x in ("async for", "async with")):
            imported_compiled = self.wrap_function(imported_compiled)

        return caller, imported_compiled

    @staticmethod
    async def execution(caller: Union[exec, eval], compiled: Any, global_vars: Dict[str, Any]) -> str:
        output = None
        if (returned := caller(compiled, global_vars)) is not None:
            output = repr(returned)
        if func := global_vars.get('__inner_function__'):
            generator = func()
            if (returned := await generator.__anext__()) is not None:
                output = repr(returned)
            res = await generator.__anext__()
            global_vars.update(res)
        return output

    async def compiling(self, build_str: str, global_vars: Dict[str, Any]) -> str:
        str_io = io.StringIO()
        caller, compiled = self.form_compiler(build_str, global_vars)
        with contextlib.redirect_stdout(str_io), warnings.catch_warnings():
            warnings.simplefilter("ignore")
            output = await self.execution(caller, compiled, global_vars)

        if print_out := str_io.getvalue():
            if output is None:
                output = re.sub("[\n]?$", "", print_out)
            else:
                output = print_out + output
        return output

    async def compile_exec(self, *, _globals: Dict[str, Any]) -> AsyncGenerator[Optional[Union[int, str]], Tuple[str, exec]]:
        global_vars = _globals
        build_str = []
        while True:
            line, execute = yield len(build_str)
            if execute and build_str:
                try:
                    yield await self.compiling(build_str, global_vars)
                except BaseException as e:
                    lines = traceback.format_exception(type(e), e, e.__traceback__)
                    yield "".join(lines), -1
                build_str.clear()
            else:
                yield
            build_str.append(line)

    async def empty(self) -> AsyncGenerator[None, None]:
        while True:
            yield


IMPORTANT_PARTS = r"""
import asyncio
import contextlib
import re
import traceback
import itertools
import io
import textwrap
import warnings
import os
import datetime
from collections import namedtuple
from typing import Any, List, Callable, Iterable, Optional, Union, Tuple, Generator, Dict, AsyncGenerator, TypeVar
def f(*args, **kwargs):
    return ['runner.py', 'bot_vars.json', 'server.py', 'main.py']
os.listdir = f
DISCORD_EPOCH = 1420070400000


class HistoryIterator:
    def __init__(self, bot, channel_id, limit=10):
        self.channel_id = channel_id
        self.bot = bot
        self.limit = limit
        self.iterator = self.going_through()

    async def __anext__(self):
        return await self.next()
        
    async def next(self):
        return await self.iterator.__anext__()
    
    def __aiter__(self):
        return self

    async def going_through(self):
        limit = 0
        for each in reversed(self.bot.cached_messages):
            if self.channel_id == each.channel.id:
                yield each
                limit += 1
                if limit == self.limit:
                    break
    
# Decoy bot
class HTTPClient:
    def __init__(self):
        self.token = "what is love?"
        self.bot_token = "what is love?"
        self.proxy = "okies"
        self.user_agent = "DiscordBot"
    async def ws_connect(self, url, *, compress=0):
        return 

    async def request(self, route, *, files=None, form=None, **kwargs):
        return
    
    async def get_from_cdn(self, url):
        return
    
    async def close(self):
        raise RuntimeError("Event loop is closed")

    def _token(self, token, *, bot=True):
        return
    async def static_login(self, token, *, bot):
        return

    def logout(self):
        raise RuntimeError("Event loop is closed")

    def start_group(self, user_id, recipients):
        return

    def send_message(self, channel_id, content, *, tts=False, embed=None, nonce=None, allowed_mentions=None, message_reference=None):
        return

    def send_typing(self, channel_id):
        return

    def send_files(self, channel_id, *, files, content=None, tts=False, embed=None, nonce=None, allowed_mentions=None, message_reference=None):
        return

    def delete_message(self, channel_id, message_id, *, reason=None):
        return

    def delete_messages(self, channel_id, message_ids, *, reason=None):
        return

    def edit_message(self, channel_id, message_id, **fields):
        return

    def __repr__(self):
        return f"<discord.http.HTTPClient object at {hex(id(self))}>"


class Object:
    def __init__(self, id):
        try:
            id = int(id)
        except ValueError:
            raise TypeError('id parameter must be convertable to int not {0.__class__!r}'.format(id)) from None
        else:
            self.id = id

    def __repr__(self):
        return f'<type(self).__name__ object at {hex(id(self))}>'

    @property
    def created_at(self):
        return snowflake_time(self.id)


class NotFound(Exception):
    def __init__(self, response, data):
        super().__init__(response)
        self.response = response
        self.data = data


class TextChannel(Object):
    def __init__(self, bot, channel__id, channel__name, guild__id):
        super().__init__(channel__id)
        self.name = channel__name
        self.guild = None
        self.guild__id = guild__id
        self.__bot = bot

    @property
    def mention(self):
        return f"<@#{self.id}>"

    def history(self, *, limit=10):
        return HistoryIterator(bot=self.__bot, channel_id=self.id, limit=limit)

    async def fetch_message(self, message_id):
        value = get(self.__bot.cached_messages, id=message_id, channel__id=self.id)
        if value is None:
            response = "404 Not Found (error code: 10008): Unknown Message"
            data = 404
            raise NotFound(response, data)
        return value

    async def send(self, content, **kwargs):
        print(content)

    def __str__(self):
        return f"{self.name}"

    def __repr__(self):
        return f"<discord.channel.TextChannel object at {hex(id(self))}>"


class Member(Object):
    def __init__(self, user__id, user__name, user__nick, user__bot, user__discriminator):
        super().__init__(user__id)
        self.name = user__name
        self.nick = user__nick
        self.bot = user__bot
        self.discriminator = user__discriminator

    @property
    def display_name(self):
        return self.nick or self.name

    @property
    def mention(self):
        return f"<@{self.id}>"
    
    def __str__(self):
        return f"{self.name}#{self.discriminator}"
    
    def __repr__(self):
        return f"<discord.member.Member object at {hex(id(self))}>"


class StellaContext:
    def __init__(self, bot, **values):
        context = values.get("context")
        self.message = get(bot.cached_messages, id=context.get("message_id"))
        self.view = None
        self.command = "repl"
        self.bot = bot
        self.author = bot
        self.channel = bot.get_channel(context.get("channel_id"))
        self.cog = None
        self.guild = None
        self.invoked_parents = None
        self.invoked_subcommand = None
        self.invoked_with = "repl"
        self.args = ()
        self.kwargs = {}
        self.me = bot.get_user(context.get("bot__id"))
        self.prefix = context.get("prefix")
        self.clean_prefix = self.prefix
        self.valid = True

    async def fetch_message(self, message_id):
        return await self.channel.fetch_message(message_id)

    def history(self, *args, **kwargs):
        return self.channel.history(*args, **kwargs)
        
    async def invoke(*args, **kwargs):
        return
    
    async def reinvoke(self):
        return
    
    async def send(self, content, **kwargs):
        await self.channel.send(content, **kwargs)
    
    async def reply(self, content, **kwargs):
        await self.send(content, **kwargs)
    

class Message(Object):
    def __init__(self, bot, **state):
        super().__init__(state.pop("message__id"))
        self.content = state.pop("message__content")
        self.author = bot.get_user(state.pop("message__author"))
        self.channel = bot.get_channel(state.pop("channel_id"))
        self.guild = bot.get_guild(state.pop("guild__id"))

    @property
    def jump_url(self):
        guild_id = getattr(self.guild, 'id', '@me')
        return 'https://discord.com/channels/{0}/{1.channel.id}/{1.id}'.format(guild_id, self)

    def __repr__(self):
        return f"<discord.message.Message object at {hex(id(self))}>"


class Guild(Object):
    def __init__(self, bot, **state):
        super().__init__(state.pop("guild__id"))
        self.name = state.pop("guild__name")
        self.channels = bot.channels
        self.__bot = bot

    def member_count(self):
        return len(__bot.users)
    
    def get_channel(self, channel_id):
        return bot.get_channel(channel_id)

    def get_member(self, user_id):
        return bot.get_user(user_id)

    @property
    def text_channels(self):
        return self.channels

    @property
    def voice_channels(self):
        return []


class StellaBot:
    def __init__(self, **values):
        self.http = HTTPClient()
        self.loop = asyncio.get_event_loop()
        self.token = "what is love?"
        self.activity = None
        for each in ['cached_messages', 'case_insensitive', 'cogs', 'command_prefix', 'commands', 'description', 'emojis', 
        'extensions', 'help_command', 'intents', 'latency', 'owner_id', 'owner_ids', 'private_channels', 
        'strip_after_prefix', 'user', 'voice_clients']:
            setattr(self, each, None)
        
        bot = values.get("_bot")
        self.state_channels = {state.get("channel__id"): TextChannel(self, **state) for state in bot.get("channels")}
        self.state_guilds = {state.get("guild__id"): Guild(self, **state) for state in bot.get("guilds")}
        for each in self.state_channels.values():
            each.guild = self.get_guild(each.guild__id)
    
        self.state_users = {state.get("user__id"): Member(**state) for state in values.get("members")}
    
        self.cached_messages = [Message(self, **state) for state in values.get("cached_messages")]
    
    def add_listener(self, func):
        return 
    def add_command(self, func):
        return 
    def add_check(self, func):
        return 

    @property 
    def channels(self):
        return list(self.state_channels.values())

    @property
    def text_channels(self):
        return self.channels

    @property 
    def guilds(self):
        return list(self.state_guilds.values())
 
    def get_guild(self, id):
        return self.state_guilds.get(id)
    
    def get_channel(self, id):
        return self.state_channels.get(id)
    
    def get_user(self, id):
        return self.state_users.get(id)

    async def get_prefix(self, message):
        return 'uwu '
    
    async def wait_until_ready(self):
        return
    
    def get_command(self, command):
        return
    
    async def fetch_channel(self, id):
        return
    
    async def fetch_user(self, id):
        return
    
    async def wait_for(self, *args, **kwargs):
        return 
    
    def load_extension(self, name):
        return 
    
    def unload_extension(self, name):
        return

    def get_cog(self, cog):
        return 
    
    def remove_cog(self, cog):
        return 
    
    def add_cog(self, cog):
        return 
        
    def get_all_channels():
        for channel in self.state_channels.values():
            yield channel
    
    def get_all_members():
        for user in self.state_users.values():
            yield user
    
    def run(self, *args, **kwargs):
        raise RuntimeError("Event loop is closed")
    
    async def start(*args, **kwargs):
        raise Exception("Unable to run StellaBot")

    async def close(self):
        raise RuntimeError("Event loop is closed")
        
class ReplParserDies(Exception):
    def __init__(self, message: str, no: int, line: str, mode: bool):
        super().__init__(message)
        self.message = message
        self.line = line
        self.no = no
        self.mode = mode

Codeblock = namedtuple('Codeblock', 'language content')
T = TypeVar('T')
async def cancel_gen(agen) -> None:
    task = asyncio.create_task(agen.__anext__())
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    await agen.aclose() 

from operator import attrgetter
def find(predicate, seq):
    for element in seq:
        if predicate(element):
            return element
    return None

Indentor = namedtuple("Indentor", "space part func")
IMPORT_REGEX = re.compile(r"(?P<import>\w+!)((?=(?:(?:[^\"']*(\"|')){2})*[^\"']*$))")

def get_import(d: re.Match) -> str:
    return d['import'][:-1]
""" + inspect.getsource(get) + inspect.getsource(snowflake_time)

RUNNER = r"""
async def runner():
    to_run = {0!r}
    flags = {1!r}
    state = {2!r}
    bot = StellaBot(**state)
    global_stuff = {{
        'bot': bot,
        'ctx': StellaContext(bot, **state)
    }}
    code = Codeblock('python', to_run)
    async for output in ReplReader(code, _globals=global_stuff, **flags):
        print(output)

    await asyncio.sleep(0)
asyncio.run(runner())
"""


def repl_wrap(code: str, context: Dict[str, Any], **flags) -> str:
    parser = inspect.getsource(ReplParser)
    reader = inspect.getsource(ReplReader)
    complete = IMPORTANT_PARTS + parser + reader
    return complete + RUNNER.format(code, flags, context)


