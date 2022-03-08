import inspect

import discord
from dataclasses import dataclass
from typing import List, Optional, Union
from discord.ext import commands
from discord.utils import MISSING
from utils.new_converters import AuthorJump_url, AuthorMessage, DatetimeConverter


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
