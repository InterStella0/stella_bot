import discord
import re
import itertools
from collections import namedtuple
from utils.errors import ReplParserDies


Indentor = namedtuple("Indentor", "space part func")


class ReplParser:
    def __init__(self):
        self.previous_line = ""
        self.previous_space = 0
        self.space = 0
        self.expecting_combo = []
        self.meet_collon = []
        self.expected_indent = ()
        self.FUNCTION_DEF = ["async def", "def"]
        self.SYNC_FUNC = ["yield", "return"]
        self.ASYNC_FUNC_ONLY = ["await"]
        self.ASYNC_OR_SYNC = self.ASYNC_FUNC_ONLY + self.SYNC_FUNC
        self.SYNC_FUNC_ONLY = ["yield from"]
        self.ALL_FUNC = self.SYNC_FUNC_ONLY + self.ASYNC_OR_SYNC
        self.ALL_FUNC = list(sorted(self.ALL_FUNC, key=lambda x: len(x), reverse=True))
        self.FUNC_INNER_REGEX = rf".*(\s+)?(?P<captured>{self.form_re_const(self.ALL_FUNC)})(\s+|.)?(?P<statement>.*)"
        WITHARG_CONST = ["except", "class", "async with", "with", "async for", "for", "while", "if", "elif"]
        self.WITHARG_REGEX = rf"(^(\s+)?(?P<captured>({self.form_re_const(WITHARG_CONST, self.FUNCTION_DEF)})))(\s+).*((\s+)?:(\s+)?$)"

        self.JOINER = {
            "else": ['if', 'elif', 'except'], 
            'except': ['try'], 
            'finally': ['try', 'else', 'except'], 
            'elif': ['if']
        }

        self.COMBINATION = {
            "try": ["except", "finally"]
        }

        self.CONNECT_REGEX = rf"(\s+)?(?P<captured>({self.form_re_const(self.COMBINATION, self.JOINER)}))(\s+)?:(\s+)?"
        self.COLLON_REGEX = r".*(:)(\s+)?$"

    @staticmethod
    def form_re_const(*iterables):
        return '|'.join(map(re.escape, itertools.chain(*iterables)))

    @staticmethod
    def remove_until_true(predicate, iterable):
        for x_space in itertools.takewhile(predicate, reversed(iterable)):
            iterable.remove(x_space)
        if iterable and (x_space := iterable[-1]):
            return x_space

    def check_if_indenting(self, no, line):
        if re.match(self.COLLON_REGEX, line):
            if (match := re.match(self.WITHARG_REGEX, line) or re.fullmatch(self.CONNECT_REGEX, line)) is not None:
                return self.execute_inside_dent(no, line, match)
            else:
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

    def inside_function_statement(self, no, line, x_space, match):
        syntax = match["captured"]
        statement = match["statement"]
        if x_space.func: # In a function
            is_async = "async" in x_space.func
            print(syntax, "|", statement, "|", is_async, x_space.func)
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

    def __iter__(self):
        return self._internal()

    def _internal(self):
        for no in itertools.count(1):
            self.indicator_mode = True
            line = yield no

            if line == 0: # End of line, check for syntax combination statement
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
        if self.expected_indent:
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
                self.expected_indent = ()
                self.indicator_mode = False
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
            if match := re.match(self.FUNC_INNER_REGEX, line):
                raise ReplParserDies(f"'{match['captured']}' outside function.", no, line)
        elif self.meet_collon:
            if x_space := self.remove_until_true(lambda x_space: x_space.space > self.space, self.meet_collon):
                if x_space.space == self.space:
                    if match := re.match(self.FUNC_INNER_REGEX, line):
                        self.inside_function_statement(no, line, x_space, match)
                    self.indicator_mode = False
                elif x_space.space < self.space:
                    raise ReplParserDies("Unindent does not match any outer indentation level", no, line)
                else:
                    raise ReplParserDies("Unexpected Indent", no, line)
            else:
                raise ReplParserDies("Unindent does not match any outer indentation level", no, line)
        return self.indicator_mode


class ReplReader:
    def __init__(self, codeblock, **flags):
        self.iterator = iter(ReplParser())
        self.codeblock = codeblock
        self.counter = flags.get("counter")

    def __iter__(self):
        return self.reading_codeblock()

    def reading_codeblock(self):
        codes = self.codeblock.content.splitlines()
        no_lang = self.codeblock.language is not None
        for line, no in zip(codes[no_lang:], self.iterator):
            indent = self.iterator.send(line)
            number = f"{no} " if self.counter else ""
            yield f'{number}{(">>>", "...")[not indent]} {line}'

        try:
            # End of line
            next(self.iterator)
            self.iterator.send(0)
        except StopIteration:
            return
