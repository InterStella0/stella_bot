import shlex
import re
import inspect
import discord
import argparse
import sys
from dataclasses import dataclass
from typing import List, Optional, Union
from discord.ext import commands
from discord.ext.flags import FlagCommand, _parser
from discord.utils import MISSING
from utils.new_converters import AuthorJump_url, AuthorMessage, DatetimeConverter, BooleanOwner

class SFlagCommand(FlagCommand):
    """Legacy Flag parsing, only be used when i want to"""
    async def _parse_flag_arguments(self, ctx):
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

        async def do_conversion(value):
            # Would only call if a value is from _get_value else it is already a value.
            if type(value) is _parser.ParserResult:
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
    def signature(self):
        # Due to command.old_signature uses _Greedy, this caused error
        return commands.Command.signature.__get__(self)


class SFlagGroup(SFlagCommand, commands.Group):
    pass


def add_flag(*flag_names, **kwargs):
    def inner(func):
        if isinstance(func, commands.Command):
            nfunc = func.callback
        else:
            nfunc = func

        if any("_OPTIONAL" in flag for flag in flag_names):
            raise Exception("Flag with '_OPTIONAL' as it's name is not allowed.")

        if not hasattr(nfunc, '_def_parser'):
            nfunc._def_parser = _parser.DontExitArgumentParser()
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


def flag(*,name: str = MISSING, aliases: List[str] = MISSING, default=MISSING,
         max_args: int = MISSING, override: bool = MISSING, help=MISSING):
    return HelpFlag(name=name, aliases=aliases, default=default, max_args=max_args, 
                    override=override, help=help)

def find_flag(command):
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
    message: Optional[AuthorMessage] = flag(help="This flag will override 'reason', 'requested' and 'jump url' according to the target message.")


class ReinvokeFlag(commands.FlagConverter):
    redirect_error: Optional[bool] = flag(help="Redirecting error into the command, defaults to False", default=False)
    redirect: Optional[bool] = flag(help="Set redirect_error to True and setting dispatch to False. Defaults to True", default=True)
    dispatch: Optional[bool] = flag(help="Allowing to dispatch the events. Defaults to True", default=True)
    call_once: Optional[bool] = flag(help="Calling the check once. Defaults to True", default=True)
    call_check: Optional[bool] = flag(help="Calling the check during invocation. Defaults to True", default=True)
    user: Optional[Union[discord.Member, discord.User]] = flag(help="Calling the command using another user's object.")
    
class ReplFlag(commands.FlagConverter):
    counter: Optional[bool] = flag(help="Showing the counter for each line, defaults to False", default=False)
    exec_: Optional[BooleanOwner] = flag(name='exec', aliases=['execute'],
                                                 help="Allow execution of repl, defaults to True, unless a non owner.",
                                                 default=True)
    inner_func_check: Optional[bool] = flag(help="Check if return/yield is inside a function. Defaults to False for owner", default=False)
