import shlex
import re
import discord
import argparse
import sys
from discord.ext import commands
from discord.ext.flags import FlagCommand, _parser


class SFlagCommand(FlagCommand):
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
        for flag, value in flags.items():
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
        return self.old_signature


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
