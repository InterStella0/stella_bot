import shlex
import re
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
        values = vars(namespace)
        for x in values.copy():
            if hasattr(self.callback._def_parser, "optional"):
                for val, y in self.callback._def_parser.optional:
                    y = re.sub("-", "", y)
                    if y == x and values[y]:
                        values.update({re.sub("-", "", val): True})
        ctx.kwargs.update(values)

    @property
    def signature(self):
        result = self.old_signature
        to_append = [result]
        parser = self.callback._def_parser  # type: _parser.DontExitArgumentParser

        for action in parser._actions:
            if action.option_strings:
                flag = action.option_strings[0].lstrip('-').replace('-', '_')
                k = '-' if len(flag) == 1 else '--'
                should_print = action.default is not None and action.default != ''
                if "_OPTIONAL" in flag:
                    continue
                if action.required:
                    if should_print:
                        to_append.append('<%s%s %s>' % (k, flag, action.default))
                    else:
                        to_append.append('<%s%s>' % (k, flag))
                else:
                    if should_print:
                        to_append.append('[%s%s %s]' % (k, flag, action.default))
                    else:
                        to_append.append('[%s%s]' % (k, flag))

        for action in parser._actions:
            if not action.option_strings:
                name = action.dest
                should_print = action.default is not None and action.default != ''
                if action.nargs in ('*', '?'):
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

        if all(x in kwargs for x in ("type", "action")):
            _without = kwargs.copy()
            if _type := _without.pop("type"):
                if _type is not bool:
                    raise Exception(f"Combination of type and action must be a bool not {type(_type)}")
            kwargs.pop("action")
            optional = [f'{x}_OPTIONAL' for x in flag_names]
            nfunc._def_parser.optional = [(x, f'{x}_OPTIONAL') for x in flag_names]
            nfunc._def_parser.add_argument(*optional, **_without)

        nfunc._def_parser.add_argument(*flag_names, **kwargs)
        return func
    return inner
