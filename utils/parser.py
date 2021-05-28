import contextlib
import discord
import re
import traceback
import itertools
import io
from collections import namedtuple
from utils.errors import ReplParserDies
from utils.useful import cancel_gen


Indentor = namedtuple("Indentor", "space part func")


class ReplParser:
    def __init__(self, **kwargs):
        self.inner_func_check = kwargs.pop('inner_func_check', True)
        self.previous_line = ""
        self.previous_space = 0
        self.space = 0
        self.expecting_combo = []
        self.meet_collon = []
        self.expected_indent = None
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
                              r"(\s+)(?P<name>([a-zA-Z_])(([a-zA-Z0-9_])+)?)()(\((?P<parameter>.*)\)(\s+)?(->(\s+)?(?P<returnhint>.*))?:)"
        
        self.WITH_DEF_REGEX = r"(\s+)?(?P<captured>async with|with)(\s+)(?P<statement>[^\s]+)(\s+)?(as(\s+)(?P<var>([a-zA-Z_])(([a-zA-Z0-9_])+)?))?(\s+)"\
                              r"?(((\s+)?\,(\s+)?(?P<statement2>[^\s]+)(\s+)?(as(\s+)(?P<var2>([a-zA-Z_])(([a-zA-Z0-9_])+)?))?)+)?(\s+)?:(\s+)?"
        self.FOR_DEF_REGEX = r"(\s+)?(?P<captured>async for|for)(\s+)(?P<statement>(?P<var>([a-zA-Z_])(([a-zA-Z0-9_])+)?))(\s+)in(\s+)(?P<iterator>[^\s]+)(\s+)?:"

        self.EXCEPT_STATE_REGEX = r"(\s+)?(?P<captured>except)(\s+)?((\s+)(?P<exception>[^\s]+)((\s+)as(\s+)((?P<var>([a-zA-Z_])(([a-zA-Z0-9_])+)?)))?)?(\s+)?(\s+)?:"

        self.DECORATOR_REGEX = r"(\s+)?(?P<captured>\@)(?P<name>[^(]+)(?P<parameter>\(.*\))?(\s+)?"

        WITHARG_CONST = ["while", "if", "elif"]
        self.WITHARG_REGEX = rf"(^(\s+)?(?P<captured>({self.form_re_const(WITHARG_CONST)})))(\s+).*((\s+)?:(\s+)?$)"

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
        self.COLLON_DEC_REGEX = r"(^(@)|.*(:)(\s+)?$)"

    @staticmethod
    def form_re_const(*iterables):
        return '|'.join(map(re.escape, itertools.chain(*iterables)))

    @staticmethod
    def remove_until_true(predicate, iterable):
        for x_space in itertools.takewhile(predicate, reversed(iterable)):
            iterable.remove(x_space)
        if iterable and (x_space := iterable[-1]):
            return x_space

    def validation_syntax(self, no, line):
        for regex in (self.FUNC_DEF_REGEX, self.CLASS_DEF_REGEX, self.WITH_DEF_REGEX, self.DECORATOR_REGEX,
                      self.FOR_DEF_REGEX, self.EXCEPT_STATE_REGEX, self.WITHARG_REGEX):
            if match := re.match(regex, line):
                return match

    def check_if_indenting(self, no, line):
        if re.match(self.COLLON_DEC_REGEX, line):
            if (match := self.validation_syntax(no, line) or re.fullmatch(self.CONNECT_REGEX, line)) is not None:
                return self.execute_inside_dent(no, line, match)
            raise ReplParserDies("Invalid Syntax", no, line)

    def execute_inside_dent(self, no, line, match):
        captured = match["captured"]
        if part := self.JOINER.get(captured):
            ind = discord.utils.get(self.meet_collon, space=self.space)
            if getattr(ind, "part", None) in part:
                index = self.meet_collon.index(ind)
                self.meet_collon[index] = Indentor(self.space, captured, ind.func)
                self.indicator_mode = False
                if match := re.match(self.FUNC_INNER_REGEX, line):
                    self.inside_function_statement(no, line, ind, match)
            else:
                raise ReplParserDies("Invalid Syntax", no, line)
        if expect := discord.utils.get(self.expecting_combo, space=self.space):
            if captured not in self.COMBINATION.get(expect.part):
                raise ReplParserDies("Invalid Syntax", no, line)
            if expect.part == '@':
                self.indicator_mode = False
            self.expecting_combo.remove(expect)
        if self.COMBINATION.get(captured):
            self.expecting_combo.append(Indentor(self.space, captured, None))
        if self.space:
            self.indicator_mode = False
        if x_space := self.remove_until_true(lambda x_space: x_space.space > self.space, self.meet_collon):
            self.meet_collon[-1] = Indentor(x_space.space, captured, x_space.func)
        else:
            self.meet_collon.append(Indentor(self.space, captured, None))
        self.previous_space = self.space
        return captured

    def inside_function_state_no_space(self, no, line):
        if not self.inner_func_check:
            return

        if match := re.match(self.FUNC_INNER_REGEX, line):
            raise ReplParserDies(f"'{match['captured']}' outside function.", no, line)

    def inside_function_statement(self, no, line, x_space, match):
        syntax = match["captured"]
        statement = match["statement"]
        if not self.inner_func_check:
            return

        if x_space.func: # In a function
            is_async = "async" in x_space.func
            if is_async: 
                if value := discord.utils.find(lambda x: x == syntax, self.SYNC_FUNC_ONLY):
                    raise ReplParserDies(f"'{value}' is inside async function.", no, line)
            else:
                if value := discord.utils.find(lambda x: x == syntax, self.ASYNC_FUNC_ONLY):
                    raise ReplParserDies(f"'{value}' is outside async function.", no, line)
            
            if not statement and syntax not in ("yield", "return"):
                raise ReplParserDies("Syntax Error", no, line)
        else:
            raise ReplParserDies(f"'{syntax}' outside function.", no, line)

    def indentation_checker(self, no, line):
        if x_space := self.remove_until_true(lambda x_space: x_space.space > self.space, self.meet_collon):
            if x_space.space == self.space:
                if match := re.match(self.FUNC_INNER_REGEX, line):
                    self.inside_function_statement(no, line, x_space, match)
                if self.space > 0:
                    self.indicator_mode = False
            elif x_space.space < self.space:
                raise ReplParserDies("Unindent does not match any outer indentation level", no, line)
            else:
                raise ReplParserDies("Unexpected Indent", no, line)
        else:
            raise ReplParserDies("Unindent does not match any outer indentation level", no, line)

    def __aiter__(self):
        return self._internal()

    async def _internal(self):
        for no in itertools.count(1):
            self.indicator_mode = True
            line = yield no

            if line == 0 or line is None: # End of line, check for syntax combination statement
                if self.expecting_combo:
                    raise ReplParserDies("Syntax Error", no, "")
                self.space = 0
                self.parsing(no, "")
                return
            self.space = re.match(r"(\s+)?", line).span()[-1]
            yield self.parsing(no, line)
            self.previous_line = line 

    def parsing(self, no, line, /):
        is_empty = line[self.space:] == ""
        if self.expected_indent != '@' and self.expected_indent is not None:
            if self.previous_space < self.space:
                self.previous_space = self.space
                if before := discord.utils.find(lambda x: x.func in self.FUNCTION_DEF, reversed(self.meet_collon)):
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
                raise ReplParserDies("Expected Indent", no, line)
        elif self.space > self.previous_space:
            raise ReplParserDies("Unexpected Indent", no, line)
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
    def __init__(self, codeblock, *, _globals={}, **flags):
        self.iterator = ReplParser(**flags).__aiter__()
        self.codeblock = codeblock
        self.counter = flags.get("counter")
        self.executor = self.compile_exec(_globals=_globals) if flags.get("exec") else self.empty()

    def __aiter__(self):
        return self.reader_handler()

    async def reader_handler(self):
        async for each in self.reading_codeblock():
            if isinstance(each, tuple):
                compiled, _ = each
                yield compiled
                return
            yield each
        
        await cancel_gen(self.iterator)
        await cancel_gen(self.executor)

    async def runner(self, code):
        with contextlib.suppress(StopAsyncIteration):
            for line in code:
                result = [line]
                for x in (self.iterator, self.executor):
                    result.append(await x.__anext__())
                yield tuple(result)
        for x in (self.iterator, self.executor):
            await x.__anext__()
            

    async def reading_codeblock(self):
        codes = self.codeblock.content.splitlines()
        no_lang = self.codeblock.language is not None
        async for line, no, ex in self.runner(codes[no_lang:]):
            indent = await self.iterator.asend(line)
            number = f"{no} " if self.counter else ""
            compiled = await self.executor.asend((line, indent))
            if ex and indent and compiled:
                yield compiled
            yield f'{number}{("...", ">>>")[indent]} {line}'
        else:
            try:
                if compiled := await self.executor.asend((0, True)):
                    yield compiled
                await self.iterator.asend(0)
            except StopAsyncIteration:
                return

    async def compile_exec(self, *, _globals):
        global_vars = _globals
        build_str = []
        while True:
            line, execute = yield len(build_str)
            if execute and build_str:
                compiled = "\n".join(build_str)
                str_io = io.StringIO()
                try:
                    caller = exec
                    if len(build_str) == 1: 
                        # Only wrap with async functions when it's an async operation
                        if 'await' in compiled:
                            before = "async def __inner_function__():\n    "
                            try:
                                compiled = compile(f"{before}yield {compiled}\n    yield locals()", 'repl_command', 'exec')
                            except SyntaxError:
                                compiled = f"{before}{compiled}\n    yield\n    yield locals()"
                        else:
                            with contextlib.suppress(SyntaxError):
                                compiled = compile(compiled, 'repl_command', 'eval')
                                caller = eval

                    output = None
                    with contextlib.redirect_stdout(str_io):
                        if (returned := caller(compiled, global_vars)) is not None:
                            output = repr(returned)
                        if func := global_vars.get('__inner_function__'):
                            generator = func()
                            if (returned := await generator.__anext__()) is not None:
                                output = repr(returned)
                            res = await generator.__anext__()
                            global_vars.update(res)
                    if print_out := str_io.getvalue():
                        if output is None:
                            output = re.sub("[\n]{0,1}$", "", print_out)
                        else:
                            output = print_out + output

                    yield output
                except BaseException as e:
                    lines = traceback.format_exception(type(e), e, e.__traceback__)
                    yield "".join(lines), -1
                build_str.clear()
            else:
                yield
            build_str.append(line)

    async def empty(self):
        while True:
            yield
        