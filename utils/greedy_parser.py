"""
Copyright (C) by stella or something
if you copy this but make your repository private, ur weird
pls be nice to me if you do copy it that's all i want :pleading:
"""
from __future__ import annotations

import contextlib
import inspect
import itertools
import typing

from typing import TYPE_CHECKING, Any, Callable, List, Optional, Set, Tuple, Type, TypeVar, Union

from discord.ext import commands
from discord.ext.commands import ArgumentParsingError, CommandError
from discord.ext.commands.errors import BadUnionArgument

from utils.errors import ConsumerUnableToConvert
from utils.flags import SFlagCommand, find_flag
from utils.useful import StellaContext, isiterable

T = TypeVar('T')
if TYPE_CHECKING:
    from main import StellaBot


class WithCommaStringView(commands.view.StringView):
    """Custom StringView for Separator and Consumer class to use."""
    def __init__(self, view: Optional[commands.view.StringView]):
        super().__init__(view.buffer)
        self.old_view = view

    def update_values(self):
        """Update the current StringView value into this object"""
        self.__dict__.update({key: getattr(self.old_view, key) for key in ["previous", "index", "end"]})

    def get_parser(self, converter: BaseGreedy) -> Optional[int]:
        """Tries to get a separator within an argument, return None if it can't find any."""
        if not hasattr(converter, "separators"):
            return
        pos = previous = 0
        escaped = []
        with contextlib.suppress(IndexError):
            while not self.eof:
                current = self.buffer[self.index + pos]
                if current in converter.separators:
                    if previous not in converter.escapes:
                        break
                    else:
                        escaped.append(pos - 1)

                pos += 1
                previous = current

        for offset, escape in enumerate(escaped):
            maximum = self.index + escape - offset
            self.buffer = self.buffer[0: maximum] + self.buffer[maximum + 1: self.end]
            self.end -= 1
        pos -= len(escaped)
        if self.index + pos != self.end:
            return pos

    def get_arg_parser(self, end: int) -> str:
        """Gets a word that ends with ','"""
        self.previous = self.index
        offset = 0
        PARSER_SIZE = 1
        # Undo if there is a space, to not capture it
        while self.buffer[self.index + end - (1 + offset)].isspace():
            offset += 1
        result = self.buffer[self.index:self.index + (end - offset)]
        self.index += end + PARSER_SIZE
        return result


class GreedyAllowStr(commands.converter.Greedy):
    def __class_getitem__(cls, params: Union[Tuple[T], T]) -> "GreedyAllowStr":
        try:
            return super().__class_getitem__(params)
        except TypeError as e:
            if str(e) == "Greedy[str] is invalid.":
                return cls(converter=str)
            raise e from None


class BaseGreedy(GreedyAllowStr):
    """A Base class for all Greedy subclass, basic attribute such as separators
       and escapes."""
    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        self.separators = {','}
        self.escapes = {'\\'}

    @staticmethod
    def add_into_instance(instance: "BaseGreedy", separators: Set[str], escapes: Set[str]):
        if not hasattr(separators, "__iter__"):
            raise Exception("Separators passed must be an iterable.")
        if not hasattr(escapes, "__iter__"):
            raise Exception("Escapes passed must be an iterable.")
        for s, e in itertools.zip_longest(separators, escapes):
            if s and len(s) != 1:
                raise Exception("Separator must only be a single character.")
            if e and len(e) != 1:
                raise Exception("Escape must only be a single character.")
        instance.separators |= set(separators)
        instance.escapes |= set(escapes)
        return instance

    def __class_getitem__(cls, param: T) -> T:
        new_param = param
        if isiterable(param):
            new_param = new_param[0]
        instance = super().__class_getitem__(new_param) 
        if isiterable(param):
            separators, escapes = param[1:] if len(param) > 2 else (param[1], {})
            instance = cls.add_into_instance(instance, separators, escapes)
        return instance

    def __call__(self, *separators: Any, escapes: Optional[Set] = None):
        if escapes is None:
            escapes = set()
        instance = self.add_into_instance(self, separators, escapes)
        return instance
    
    async def actual_greedy_parsing(self, command: commands.Command, ctx: commands.Context, param: inspect.Parameter,
                                    required: bool, converter: T, optional: Optional[bool] = False) -> Union[List[T], T]:
        raise NotImplemented("Greedy subclass seems to not have this method. It dies.")


class RequiredGreedy(BaseGreedy):
    """All Required greedy must inherit this class so I can tell which greedy is required."""
    pass


class Separator(BaseGreedy):
    """Allow Greedy to be parse in a way that it will try to find ',' or any
       other passed separator in an argument, and will allow spaced argument to be
       passed given that there are a separator at the end of each argument.

       If a value failed to be converted, it will raise an error.

       Returns an empty list when none of the argument was valid."""

    async def actual_greedy_parsing(self, command: commands.Command, ctx: StellaContext, param: inspect.Parameter,
                                    required: bool, converter: T, optional: Optional[bool] = False) -> List[T]:
        view = ctx.view
        result = []
        _exit = False
        while not view.eof:
            view.skip_ws()
            try:
                if pos := view.get_parser(param.annotation):
                    argument = view.get_arg_parser(pos)
                else:
                    argument = view.get_quoted_word()
                    _exit = True

                value = await commands.run_converters(ctx, converter, argument, param)
                if _exit:
                    result.append(value)
                    break
            except (CommandError, ArgumentParsingError):
                raise  # allow raising for Seperator
            else:
                result.append(value)

        if not result and not required:
            return param.default
        return result


class Consumer(RequiredGreedy):
    """Allow a consume rest behaviour by trying to convert an argument into a valid
       conversion for each word it sees.
       Example: 'uwu argument1 argument2 argument3'

       If the Greedy is at argument1, it will try to first convert "argument1"
       when fails, it goes into "argument1 argument2" and so on.

       This Greedy raises an error if it can't find any valid conversion."""

    async def actual_greedy_parsing(self, command: commands.Command, ctx: commands.Context, param: inspect.Parameter,
                                    required: bool, converter: T, optional: Optional[bool] = False) -> T:
        view = ctx.view
        view.skip_ws()
        if pos := view.get_parser(param.annotation):
            current = view.get_arg_parser(pos)
            return await commands.run_converters(ctx, converter, current, param)

        previous = view.index
        once = 0
        while not view.eof:
            view.skip_ws()
            with contextlib.suppress(CommandError, ArgumentParsingError):
                if not once:
                    current = view.get_quoted_word()
                else:
                    while not view.eof:
                        if view.buffer[view.index].isspace():
                            break
                        view.index += 1
                    
                    current = view.buffer[previous: view.index]
                once |= 1
                return await commands.run_converters(ctx, converter, current, param)

        if getattr(converter, "__origin__", None) is typing.Union:
            raise BadUnionArgument(param, converter.__args__, [])
        name = (converter if inspect.isclass(converter) else type(converter)).__name__
        raise ConsumerUnableToConvert(view.buffer[previous: view.index], name)


class UntilFlag(RequiredGreedy):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.separators = {"-"}

    async def actual_greedy_parsing(self, command: commands.Command, ctx: commands.Context, param: inspect.Parameter,
                                    required: bool, converter: T) -> T:
        view = ctx.view
        view.skip_ws()
        argument = None
        if con := find_flag(command):
            regex = con.annotation.__commands_flag_regex__
            if match := regex.search(view.buffer):
                maximum = match.span()[0]
                while view.buffer[maximum - 1].isspace():  # Minus 1 to offset the extra space that it got
                    maximum -= 1
                argument = view.buffer[view.index: maximum]
            else:
                argument = view.read_rest()
        elif argument is None:
            if view.buffer[view.index] == '-':
                if required:
                    if self._is_typing_optional(param.annotation):
                        return None
                    raise commands.MissingRequiredArgument(param)
                else:
                    return param.default
            if pos := view.get_parser(param.annotation):
                # Undo until end of arg before separator
                while view.buffer[view.index + pos - 1].isspace():
                    pos -= 1
                argument = view.get_arg_parser(pos)
            else:
                argument = view.read_rest()
        return await commands.converter.run_converters(ctx, converter, argument, param)


# TODO: Drop support for old flag
class GreedyParser(commands.Command):
    async def _transform_greedy_pos(self, ctx: StellaContext, param: inspect.Parameter, required: bool,
                                    greedy: commands.converter.Greedy, converter: T,
                                    normal_greedy: Optional[bool] = False) -> List[T]:
        """Allow Greedy subclass to have their own method of conversion by checking "actual_greedy_parsing"
           method, and invoking that method when it is available, else it will call the normal greedy method
           conversion."""

        ctx.current_parameter = param
        if hasattr(greedy, "actual_greedy_parsing") and not normal_greedy:
            result = await greedy.actual_greedy_parsing(self, ctx, param, required, converter)
        else:
            result = await super()._transform_greedy_pos(ctx, param, required, converter)
        if hasattr(converter, 'after_greedy'):
            return await converter.after_greedy(ctx, result)
        return result

    @staticmethod
    def is_greedy_required(x: Any) -> bool:
        return isinstance(x, RequiredGreedy)

    def get_optional_converter(self, converter: Any) -> Type:
        if getattr(converter, "__args__", []):
            stored_converter = converter.__args__[0]
            if self.is_greedy_required(stored_converter):
                return stored_converter
        return converter

    async def transform(self, ctx: StellaContext, param: inspect.Parameter) -> List[Any]:
        """Because Danny literally only allow commands.converter._Greedy class to be pass here using
           'is' comparison, I have to override it to allow any other Greedy subclass.
           
           It's obvious that Danny doesn't want people to subclass it smh."""

        required = param.default is param.empty
        converter = commands.converter.get_converter(param)
        optional_converter = self._is_typing_optional(param.annotation)

        if optional_converter:
            converter = self.get_optional_converter(converter)

        if isinstance(converter, commands.converter.Greedy):
            if param.kind == param.POSITIONAL_OR_KEYWORD or param.kind == param.POSITIONAL_ONLY:
                if self.is_greedy_required(converter) and ctx.view.eof:
                    if required:
                        if optional_converter:
                            return None
                        raise commands.MissingRequiredArgument(param)
                    else:
                        return param.default
                return await self._transform_greedy_pos(ctx, param, required, converter, converter.converter)

        return await super().transform(ctx, param)

    @property
    def signature(self) -> str:
        if self.usage is not None:
            return self.usage

        params = self.clean_params
        if not params:
            return ''

        result = []
        for name, param in params.items():
            converter = commands.converter.get_converter(param)
            converter = self.get_optional_converter(converter)
            greedy = isinstance(converter, commands.converter.Greedy)
            if param.kind == param.VAR_KEYWORD:
                result.append('[%s...]' % name)
                continue

            if param.default is not param.empty:
                # We don't want None or '' to trigger the [name=value] case and instead it should
                # do [name] since [name=None] or [name=] are not exactly useful for the user.
                should_print = param.default if isinstance(param.default, str) else param.default is not None
                if should_print:
                    result.append('[%s=%s]' % (name, param.default) if not greedy else
                                  '[%s=%s]...' % (name, param.default))
                    continue
                else:
                    if not isinstance(converter, commands.converter.Greedy):
                        result.append('[%s]' % name)
                    else:
                        result.append('[%s]...' % name)

            elif param.kind == param.VAR_POSITIONAL:
                if self.require_var_positional:
                    result.append('<%s...>' % name)
                else:
                    result.append('[%s...]' % name)
            elif greedy:
                if isinstance(converter, RequiredGreedy) and not self._is_typing_optional(param.annotation):
                    result.append('<%s>...' % name)
                else:
                    result.append('[%s]...' % name)
            elif self._is_typing_optional(param.annotation):
                result.append('[%s]' % name)
            else:
                result.append('<%s>' % name)

        return ' '.join(result)


def command(name: Optional[str] = None, *, bot: StellaBot = None, **attrs: Any) -> Callable:
    def decorator(func):
        if isinstance(func, commands.Command):
            raise TypeError('Callback is already a command.')

        command = GreedyParser(func, name=name, **attrs)
        if bot:
            bot.add_command(command)
        return command
    return decorator
