"""
Copyright (C) by stella or something
if you copy this but make your repository private, ur weird
pls be nice to me if you do copy it that's all i want :pleading:
"""
import contextlib
import discord
import itertools
from discord.ext import commands
from discord.ext.commands import CommandError, ArgumentParsingError

class GreedyParser(commands.Command):
    """
    Allows the ability to process Greedy converter result before it is passed into the command parameter.
    Also allows for Greedy to have a new parsing method which is to split every ", " or any other passed
    separator into Greedy class.
    """
    async def _transform_greedy_pos(self, ctx, param, required, converter):
        result = await self.actual_greedy_parsing(ctx, param, required, converter)
        if hasattr(converter, 'after_greedy'):
            return await converter.after_greedy(ctx, result)
        return result

    async def actual_greedy_parsing(self, ctx, param, required, converter):
        view = ctx.view
        result = []
        while not view.eof:
            previous = view.index

            view.skip_ws()
            try:
                if pos := view.get_parser(param.annotation):
                    argument = view.get_arg_parser(pos)
                else:
                    argument = view.get_quoted_word()
                value = await self.do_conversion(ctx, converter, argument, param)
            except (CommandError, ArgumentParsingError):
                view.index = previous
                break
            else:
                result.append(value)

        if not result and not required:
            return param.default
        return result

    async def transform(self, ctx, param):
        required = param.default is param.empty
        converter = self._get_converter(param)
        if isinstance(converter, commands.converter._Greedy):
            if param.kind == param.POSITIONAL_OR_KEYWORD or param.kind == param.POSITIONAL_ONLY:
                return await self._transform_greedy_pos(ctx, param, required, converter.converter)

        return await super().transform(ctx, param)

class WithCommaStringView(commands.view.StringView):
    def __init__(self, view):
        super().__init__(view.buffer)
        self.old_view = view

    def update_values(self):
        self.__dict__.update({key: getattr(self.old_view, key) for key in ["previous", "index", "end"]})

    def get_parser(self, converter):
        if not hasattr(converter, "separators"):
            return
        pos = 0
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

    def get_arg_parser(self, end):
        self.previous = self.index
        offset = 0
        PARSERSIZE = 1
        # Undo if there is a space, to not capture it
        while self.buffer[self.index + end - (1 + offset)].isspace():
            offset += 1
        result = self.buffer[self.index:self.index + (end - offset)]
        self.index += end + PARSERSIZE
        return result


class _CustomGreedy(commands.converter._Greedy):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.separators = {','}
        self.escapes = {'\\'}

    def add_into_instance(self, instance, separators, escapes):
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

    def __getitem__(self, param):
        new_param = param
        if hasattr(param, "__iter__"):
            new_param = new_param[0]
        instance = super().__getitem__(new_param) 
        if hasattr(param, "__iter__"):
            separators, escapes = param[1:] if len(param) > 2 else (param[1], {})
            instance = self.add_into_instance(instance, separators, escapes)
        return instance

    def __call__(self, *separators, escapes={}):
        instance = self.add_into_instance(self, separators, escapes)
        return instance

Separator = _CustomGreedy()
