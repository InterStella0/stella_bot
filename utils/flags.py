import functools
import shlex
import re
import inspect
from collections import namedtuple

import discord
import argparse
import sys
from dataclasses import dataclass
from typing import List, Optional, Union, Awaitable, Callable, Any
from discord.ext import commands
from discord.utils import MISSING
from utils.new_converters import AuthorJump_url, AuthorMessage, DatetimeConverter, BooleanOwner


ParserResult = namedtuple("ParserResult", "result action arg_string")


class ArgumentParsingError(commands.CommandError):
    def __init__(self, message):
        super().__init__(discord.utils.escape_mentions(message))


class DontExitArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        self.ctx = None
        kwargs.pop('add_help', False)
        super().__init__(*args, add_help=False, **kwargs)

    def error(self, message):
        raise ArgumentParsingError(message)

    def _get_value(self, action, arg_string):
        type_func = self._registry_get('type', action.type, action.type)
        param = [arg_string]

        if hasattr(type_func, '__module__') and type_func.__module__ is not None:
            module = type_func.__module__
            if module.startswith('discord') and not module.endswith('converter'):
                # gets the default discord.py converter
                try:
                    type_func = getattr(commands.converter, type_func.__name__ + 'Converter')
                except AttributeError:
                    pass

        # for custom converter compatibility
        if inspect.isclass(type_func):
            if issubclass(type_func, commands.Converter):
                type_func = type_func().convert
                param.insert(0, self.ctx)

        if not callable(type_func):
            msg = '%r is not callable'
            raise argparse.ArgumentError(action, msg % type_func)

        # if type is bool, use the discord.py's bool converter
        if type_func is bool:
            type_func = commands.converter._convert_to_bool

        # convert into a partial function
        result = functools.partial(type_func, *param)
        # return the function, with it's action and arg_string in a namedtuple.
        return ParserResult(result, action, arg_string)

    # noinspection PyMethodOverriding
    def parse_args(self, args, namespace=None, *, ctx):
        self.ctx = ctx
        return super().parse_args(args, namespace)


class FlagCommand(commands.Command):
    async def _parse_flag_arguments(self, ctx):
        if not hasattr(self.callback, '_def_parser'):
            return
        arg = ctx.view.read_rest()
        namespace = self.callback._def_parser.parse_args(shlex.split(arg), ctx=ctx)
        flags = vars(namespace)

        async def do_convertion(value):
            # Would only call if a value is from _get_value else it is already a value.
            if type(value) is ParserResult:
                try:
                    value = await discord.utils.maybe_coroutine(value.result)

                # ArgumentTypeErrors indicate errors
                except argparse.ArgumentTypeError:
                    msg = str(sys.exc_info()[1])
                    raise argparse.ArgumentError(value.action, msg)

                # TypeErrors or ValueErrors also indicate errors
                except (TypeError, ValueError):
                    name = getattr(value.action.type, '__name__', repr(value.action.type))
                    args = {'type': name, 'value': value.arg_string}
                    msg = 'invalid %(type)s value: %(value)r'
                    raise argparse.ArgumentError(value.action, msg % args)
            return value

        for flag, value in flags.items():
            # iterate if value is a list, this happens when nargs = '+'
            if type(value) is list:
                value = [await do_convertion(v) for v in value]
            else:
                value = await do_convertion(value)
            ctx.kwargs.update({flag: value})

    @property
    def old_signature(self):
        if self.usage is not None:
            return self.usage

        params = self.clean_params
        if not params:
            return ''

        result = []
        for name, param in params.items():
            greedy = isinstance(param.annotation, discord.ext.commands.converter.Greedy)

            if param.default is not param.empty:
                # We don't want None or '' to trigger the [name=value] case and instead it should
                # do [name] since [name=None] or [name=] are not exactly useful for the user.
                should_print = param.default if isinstance(param.default, str) else param.default is not None
                if should_print:
                    result.append('[%s=%s]' % (name, param.default) if not greedy else
                                  '[%s=%s]...' % (name, param.default))
                    continue
                else:
                    result.append('[%s]' % name)

            elif param.kind == param.VAR_POSITIONAL:
                result.append('[%s...]' % name)
            elif greedy:
                result.append('[%s]...' % name)
            elif self._is_typing_optional(param.annotation):
                result.append('[%s]' % name)
            elif param.kind == param.VAR_KEYWORD:
                pass
            else:
                result.append('<%s>' % name)

        return ' '.join(result)

    @property
    def signature(self):
        result = self.old_signature
        to_append = [result]
        parser = self.callback._def_parser  # type: _parser.DontExitArgumentParser

        for action in parser._actions:
            # in argparse, options are done before positionals
            #  so we need to loop over it twice unfortunately
            if action.option_strings:
                name = action.dest.upper()
                flag = action.option_strings[0].lstrip('-').replace('-', '_')
                k = '-' if len(flag) == 1 else '--'
                should_print = action.default is not None and action.default != ''
                if action.required:
                    if should_print:
                        to_append.append('<%s%s %s=%s>' % (k, flag, name, action.default))
                    else:
                        to_append.append('<%s%s %s>' % (k, flag, name))
                else:
                    if should_print:
                        to_append.append('[%s%s %s=%s]' % (k, flag, name, action.default))
                    else:
                        to_append.append('[%s%s %s]' % (k, flag, name))

        for action in parser._actions:
            # here we do the positionals
            if not action.option_strings:
                name = action.dest
                should_print = action.default is not None and action.default != ''
                if action.nargs in ('*', '?'):  # optional narg types
                    if should_print:
                        to_append.append('[%s=%s]' % (name, action.default))
                    else:
                        to_append.append('[%s]' % name)
                else:
                    if should_print:
                        to_append.append('<%s=%s>' % (name, action.default))
                    else:
                        to_append.append('<%s>' % name)

        return ' '.join(to_append)

    async def _parse_arguments(self, ctx):
        ctx.args = [ctx] if self.cog is None else [self.cog, ctx]
        ctx.kwargs = {}
        args = ctx.args
        kwargs = ctx.kwargs

        view = ctx.view
        iterator = iter(self.params.items())

        if self.cog is not None:
            # we have 'self' as the first parameter so just advance
            # the iterator and resume parsing
            try:
                next(iterator)
            except StopIteration:
                fmt = 'Callback for {0.name} command is missing "self" parameter.'
                raise discord.ClientException(fmt.format(self))

        # next we have the 'ctx' as the next parameter
        try:
            next(iterator)
        except StopIteration:
            fmt = 'Callback for {0.name} command is missing "ctx" parameter.'
            raise discord.ClientException(fmt.format(self))

        for name, param in iterator:
            if param.kind == param.POSITIONAL_OR_KEYWORD:
                transformed = await self.transform(ctx, param)
                args.append(transformed)
            elif param.kind == param.KEYWORD_ONLY:
                # kwarg only param denotes "consume rest" semantics
                if self.rest_is_raw:
                    converter = self._get_converter(param)
                    argument = view.read_rest()
                    kwargs[name] = await self.do_conversion(ctx, converter, argument, param)
                else:
                    kwargs[name] = await self.transform(ctx, param)
                break
            elif param.kind == param.VAR_POSITIONAL:
                while not view.eof:
                    try:
                        transformed = await self.transform(ctx, param)
                        args.append(transformed)
                    except RuntimeError:
                        break
            elif param.kind == param.VAR_KEYWORD:
                await self._parse_flag_arguments(ctx)
                break

        if not self.ignore_extra:
            if not view.eof:
                raise commands.TooManyArguments('Too many arguments passed to ' + self.qualified_name)



class SFlagCommand(FlagCommand):
    """Legacy Flag parsing, only be used when i want to"""
    async def _parse_flag_arguments(self, ctx: commands.Context) -> None:
        if not hasattr(self.callback, '_def_parser'):
            return
        arg = ctx.view.read_rest()
        arguments = shlex.split(arg)
        if hasattr(self.callback._def_parser, "optional"):
            for x, y in enumerate(arguments):
                if "--" in y and "--" in arguments[min(len(arguments) - 1, x+1)]:
                    for p, q in self.callback._def_parser.optional:
                        y = y.replace(p, q)
                    arguments[x] = y
        namespace = self.callback._def_parser.parse_args(arguments, ctx=ctx)
        flags = vars(namespace)

        async def do_conversion(value: ParserResult) -> Any:
            # Would only call if a value is from _get_value else it is already a value.
            if type(value) is ParserResult:
                try:
                    value = await discord.utils.maybe_coroutine(value.result)

                # ArgumentTypeErrors indicate errors
                except argparse.ArgumentTypeError:
                    msg = str(sys.exc_info()[1])
                    raise argparse.ArgumentError(value.action, msg)

                # TypeErrors or ValueErrors also indicate errors
                except (TypeError, ValueError):
                    name = getattr(value.action.type, '__name__', repr(value.action.type))
                    args = {'type': name, 'value': value.arg_string}
                    msg = 'invalid %(type)s value: %(value)r'
                    raise argparse.ArgumentError(value.action, msg % args)
            return value

        for flag, value in flags.items():
            # iterate if value is a list, this happens when nargs = '+'
            if type(value) is list:
                values = [await do_conversion(v) for v in value]
                value = " ".join(values) if all(isinstance(v, str) for v in values) else values
            else:
                value = await do_conversion(value)
            flags.update({flag: value})

        for x in flags.copy():
            if hasattr(self.callback._def_parser, "optional"):
                for val, y in self.callback._def_parser.optional:
                    y = re.sub("-", "", y)
                    if y == x and flags[y]:
                        flags.update({re.sub("-", "", val): True})
        ctx.kwargs.update(flags)

    @property
    def signature(self) -> str:
        # Due to command.old_signature uses _Greedy, this caused error
        return commands.Command.signature.__get__(self)


class SFlagGroup(SFlagCommand, commands.Group):
    pass


def add_flag(*flag_names: Any, **kwargs: Any):
    def inner(func: Union[Awaitable, commands.Command]) -> Callable:
        if isinstance(func, commands.Command):
            nfunc = func.callback
        else:
            nfunc = func

        if any("_OPTIONAL" in flag for flag in flag_names):
            raise Exception("Flag with '_OPTIONAL' as it's name is not allowed.")

        if not hasattr(nfunc, '_def_parser'):
            nfunc._def_parser = DontExitArgumentParser()
            nfunc._def_parser.optional = []

        if all(x in kwargs for x in ("type", "action")):
            _without = kwargs.copy()
            if _type := _without.pop("type"):
                if _type is not bool:
                    raise Exception(f"Combination of type and action must be a bool not {type(_type)}")
            kwargs.pop("action")
            optional = [f'{x}_OPTIONAL' for x in flag_names]
            nfunc._def_parser.optional += [(x, f'{x}_OPTIONAL') for x in flag_names]
            nfunc._def_parser.add_argument(*optional, **_without)

        nfunc._def_parser.add_argument(*flag_names, **kwargs)
        return func
    return inner


@dataclass
class HelpFlag(commands.Flag):
    help: str = MISSING


def flag(*, name: str = MISSING, aliases: List[str] = MISSING, default=MISSING,
         max_args: int = MISSING, override: bool = MISSING, help=MISSING) -> HelpFlag:
    return HelpFlag(name=name, aliases=aliases, default=default, max_args=max_args, 
                    override=override, help=help)


def find_flag(command: commands.Command) -> Optional[commands.FlagConverter]:
    """Helper function to find the flag that is in a command"""
    last = [*command.params.values()][-1]
    if last.kind is last.KEYWORD_ONLY:
        ann = last.annotation
        if inspect.isclass(ann):
            if issubclass(ann, commands.FlagConverter):
                return last


class InfoFlag(commands.FlagConverter):
    jump_url: Optional[AuthorJump_url] = flag(help="The jump url that will be displayed under 'Message Request'.")
    requested_at: Optional[DatetimeConverter] = flag(help="The date that is displayed under 'Requested'.")
    reason: Optional[str] = flag(help="The text that are displayed under 'Reason'.")
    message: Optional[AuthorMessage] = flag(help="This flag will override 'reason', 'requested' and 'jump url'"
                                                 " according to the target message.")


class ReinvokeFlag(commands.FlagConverter):
    redirect_error: Optional[bool] = flag(help="Redirecting error into the command, defaults to False", default=False)
    redirect: Optional[bool] = flag(help="Set redirect_error to True and setting dispatch to False. Defaults to True",
                                    default=True)
    dispatch: Optional[bool] = flag(help="Allowing to dispatch the events. Defaults to True", default=True)
    call_once: Optional[bool] = flag(help="Calling the check once. Defaults to True", default=True)
    call_check: Optional[bool] = flag(help="Calling the check during invocation. Defaults to True", default=True)
    user: Optional[Union[discord.Member, discord.User]] = flag(help="Calling the command using another user's object.")


class ReplFlag(commands.FlagConverter):
    counter: Optional[bool] = flag(help="Showing the counter for each line, defaults to False", default=False)
    exec_: Optional[bool] = flag(name='exec', aliases=['execute'],
                                 help="Allow execution of repl, defaults to True.",
                                 default=True)
    inner_func_check: Optional[bool] = flag(help="Check if return/yield is inside a function. Defaults to False for owner",
                                            default=False)
    exec_timer: Optional[bool] = flag(help="Shows the execution time for each line. Defaults to False.",
                                      default=False)


class BotVarFlag(commands.FlagConverter):
    type: Optional[str] = flag(help="The type of variable to be converted to. Defaults to str", default="str")
