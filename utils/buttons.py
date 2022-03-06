from __future__ import annotations

import asyncio
import contextlib
import inspect
import os
import time

from copy import copy
from enum import Enum
from functools import partial
from typing import (TYPE_CHECKING, Any, AsyncGenerator, Callable, Coroutine, Dict, Iterable, List, Optional, Tuple, Type,
                    Union)

import discord

from discord import ui
from discord.ext import commands
from discord.ui import TextInput

from utils.context_managers import UserLock
from utils.menus import ListPageInteractionBase, MenuBase, MenuViewInteractionBase
from utils.useful import StellaEmbed

if TYPE_CHECKING:
    from utils.useful import StellaContext


class BaseButton(ui.Button):
    def __init__(self, *, style: discord.ButtonStyle, selected: Union[int, str], row: int,
                 label: Optional[str] = None, **kwargs: Any):
        super().__init__(style=style, label=label or selected, row=row, **kwargs)
        self.selected = selected

    async def callback(self, interaction: discord.Interaction) -> None:
        raise NotImplementedError


class BaseView(ui.View):
    def reset_timeout(self):
        self.set_timeout(time.monotonic() + self.timeout)

    def set_timeout(self, new_time):
        self._View__timeout_expiry = new_time


class CallbackView(BaseView):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for b in self.children:
            self.wrap(b)

    def wrap(self, b):
        callback = b.callback
        b.callback = partial(self.handle_callback, callback, b)

    async def handle_callback(self, callback, item, interaction):
        pass

    def add_item(self, item: ui.Item) -> None:
        self.wrap(item)
        super().add_item(item)


class ViewButtonIteration(BaseView):
    """A BaseView class that creates arrays of buttons, depending on the data type given on 'args',
        it will accept `mapper` as a dataset"""
    def __init__(self, *args: Any, mapper: Optional[Dict[str, Any]] = None,
                 button: Optional[Type[BaseButton]] = BaseButton, style: Optional[discord.ButtonStyle] = None):
        super().__init__()
        self.mapper = mapper
        for c, button_row in enumerate(args):
            for button_col in button_row:
                if isinstance(button_col, button):
                    self.add_item(button_col)
                elif isinstance(button_col, dict):
                    self.add_item(button(style=style, row=c, **button_col))
                elif isinstance(button_col, tuple):
                    selected, button_col = button_col
                    self.add_item(button(style=style, row=c, selected=selected, **button_col))
                else:
                    self.add_item(button(style=style, row=c, selected=button_col))


class ViewAuthor(BaseView):
    def __init__(self, ctx: StellaContext, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.context = ctx
        self.is_command = ctx.command is not None
        self.cooldown = commands.CooldownMapping.from_cooldown(1, 10, commands.BucketType.user)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Only allowing the context author to interact with the view"""
        ctx = self.context
        author = ctx.author
        if await ctx.bot.is_owner(interaction.user):
            return True
        if interaction.user != author:
            bucket = self.cooldown.get_bucket(ctx.message)
            if not bucket.update_rate_limit():
                if self.is_command:
                    command = ctx.bot.get_command_signature(ctx, ctx.command)
                    content = f"Only `{author}` can use this. If you want to use it, use `{command}`"
                else:
                    content = f"Only `{author}` can use this."
                embed = StellaEmbed.to_error(description=content)
                await interaction.response.send_message(embed=embed, ephemeral=True)
            return False
        return True


class ViewIterationAuthor(ViewAuthor, ViewButtonIteration):
    pass


class MenuViewBase(ViewIterationAuthor):
    """A Base Menu + View combination for all interaction that combines those two.
        It requires a page_source and an optional menu that must derived from MenuViewInteractionBase"""
    def __init__(self, ctx: StellaContext, page_source: Type[ListPageInteractionBase], *args: Any,
                 message: Optional[discord.Message] = None,
                 menu: Optional[Type[MenuViewInteractionBase]] = MenuViewInteractionBase, **kwargs: Any):
        super().__init__(ctx, *args, **kwargs)
        if not inspect.isclass(page_source):
            raise Exception(f"'page_source' must be a class")
        if not issubclass(page_source, ListPageInteractionBase):
            raise Exception(f"'page_source' must subclass ListPageInteractionBase, not '{page_source}'")
        if not inspect.isclass(menu):
            raise Exception("'menu' must a class")
        if not issubclass(menu, MenuViewInteractionBase):
            raise Exception(f"'menu' must subclass MenuViewInteractionBase, not '{menu}'")

        self.message = message
        self._class_page_source = page_source
        self._class_menu = menu
        self.menu = None
        self.__prepare = False

    async def start(self, page_source: ListPageInteractionBase) -> None:
        """Starts the menu if it has not yet started"""
        if not self.__prepare:
            message = self.message
            self.menu = self._class_menu(self, page_source, message=message)
            await self.menu.start(self.context)
            await self.menu.show_page(0)
            self.__prepare = True

    async def update(self, button: discord.Button, interaction: discord.Interaction, data: Iterable[Any]) -> None:
        """Updates the view and menu, this method replace dataset that is bound to the menu,
            and changes it to a new page_source with a new dataset."""
        if self.message is None:
            self.message = interaction.message
        page_source = self._class_page_source(button, data, per_page=1)
        if not self.__prepare:
            await self.start(page_source)
        else:
            await self.menu.change_source(page_source)
        self.check_reactions(interaction)

    def check_reactions(self, interaction: discord.Interaction) -> None:
        """This method is responsible for adding reactions to the button for the menu to
            operate. This should only trigger once."""
        menu = self.menu
        if not menu._Menu__tasks:
            loop = self.menu.ctx.bot.loop
            menu._Menu__tasks.append(loop.create_task(menu._internal_loop()))
            current_react = [*map(str, interaction.message.reactions)]

            async def add_reactions_task():
                for emoji in menu.buttons:
                    if emoji not in current_react:
                        await interaction.message.add_reaction(emoji)
            menu._Menu__tasks.append(loop.create_task(add_reactions_task()))

    async def on_timeout(self) -> None:
        """After a timeout it should disable all the buttons"""
        bot = self.context.bot
        if self.message:
            return

        message = None
        for m_id, view in bot._connection._view_store._synced_message_views.items():
            if view is self:
                if m := bot.get_message(m_id):
                    message = m

        if message is None:
            return

        for b in self.children:
            b.disabled = True
        await message.edit(view=self)


class QueueView(CallbackView):
    class State(Enum):
        confirmed = "CONFIRMED"
        denied = "DENIED"

    def __init__(self, ctx: StellaContext, *respondents: Union[discord.Member, discord.User], delete_after: bool = False):
        super().__init__()
        self.ctx = ctx
        self.respondents = respondents
        self.delete_after = delete_after
        self.message = None
        self.accepted_respondents = []
        self.denied_respondents = []

    async def send(self, content: str, **kwargs: Any) -> List[Optional[Union[discord.Member, discord.User]]]:
        return await self.start(content=content, **kwargs)

    async def start(self, **kwargs) -> List[Optional[Union[discord.Member, discord.User]]]:
        self.message = await self.ctx.maybe_reply(view=self, **kwargs)
        await self.wait()
        return self.accepted_respondents

    async def on_member_respond(self, member: Union[discord.Member, discord.User],
                                interaction: discord.Interaction, response: State):
        pass

    async def handle_callback(self, callback: Callable, item: discord.ui.Button, interaction: discord.Interaction):
        await callback(interaction)
        summation = len(self.accepted_respondents) + len(self.denied_respondents)
        if summation == len(self.respondents):
            self.stop()

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.green)
    async def on_confirm(self, button: discord.ui.Button, interaction: discord.Interaction) -> None:
        for member in self.respondents:
            if member.id == getattr(interaction.user, "id", None):
                self.accepted_respondents.append(member)
                await self.on_member_respond(member, interaction, self.State.confirmed)
                break

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.red)
    async def on_denied(self, button: discord.ui.Button, interaction: discord.Interaction) -> None:
        for member in self.respondents:
            if member.id == getattr(interaction.user, "id", None):
                self.denied_respondents.append(member)
                await self.on_member_respond(member, interaction, self.State.denied)
                break

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        uid = getattr(interaction.user, "id", None)
        if uid in [u.id for u in self.respondents]:
            return True

        users = ", ".join(map(str, self.respondents))
        await interaction.response.send_message(f"Sorry, only {users} can respond to this prompt.", ephemeral=True)

    async def on_stop(self):
        if self.message is None:
            return

        if self.delete_after:
            await self.message.delete(delay=0)
        else:
            for item in self.children:
                item.disabled = True

            await self.message.edit(view=self)


    def stop(self):
        self.ctx.bot.loop.create_task(self.on_stop())
        super().stop()



class ConfirmView(CallbackView):
    """ConfirmView literally handles confirmation where it asks the user at start() and returns a Tribool"""
    def __init__(self, ctx: StellaContext, *, to_respond: Optional[Union[discord.User, discord.Member]] = None,
                 delete_after: bool = False, message_error: Optional[str] = None):
        super().__init__()
        self.result = None
        self.message = None
        self.to_respond = to_respond or ctx.author
        self.context = ctx
        self.delete_after = delete_after
        self.message_error = message_error or "I'm waiting for your confirm response. You can't run another command."

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.to_respond.id == getattr(interaction.user, "id", None):
            return True

        await interaction.response.send_message(f"Sorry, only {self.to_respond} can respond to this prompt.", ephemeral=True)

    async def handle_callback(self, callback, item, interaction):
        self.result = await callback(interaction)
        if not interaction.response.is_done():
            await interaction.response.defer()
        self.stop()

    async def send(self, content: str, **kwargs: Any) -> Optional[bool]:
        return await self.start(content=content, **kwargs)

    async def start(self, message: Optional[discord.Message] = None, **kwargs: Any) -> Optional[bool]:
        self.message = message or await self.context.reply(view=self, **kwargs)

        lock = UserLock(self.context.author, self.message_error)
        async with lock(self.context.bot):
            await self.wait()

        if not self.delete_after:
            for x in self.children:
                x.disabled = True
            coro = self.message.edit(view=self)
        else:
            coro = self.message.delete()

        with contextlib.suppress(discord.HTTPException):
            await coro
        return self.result

    async def confirmed(self, button: ui.Button, interaction: discord.Interaction):
        pass

    async def denied(self, button: ui.Button, interaction: discord.Interaction):
        pass

    @ui.button(emoji="<:checkmark:753619798021373974>", label="Confirm", style=discord.ButtonStyle.green)
    async def confirmed_action(self, button: ui.Button, interaction: discord.Interaction):
        await self.confirmed(button, interaction)
        return True

    @ui.button(emoji="<:crossmark:753620331851284480>", label="Cancel", style=discord.ButtonStyle.danger)
    async def denied_action(self, button: ui.Button, interaction: discord.Interaction):
        await self.denied(button, interaction)
        return False


class PromptView(ViewAuthor):
    """PromptView literally handles prompting where it asks the user at start() and returns a Tribool or a discord.Message"""
    def __init__(self, ctx: StellaContext, *, delete_after: Optional[bool] = False,
                 ori_interaction: Optional[discord.Interaction] = None, accept_values: Optional[Tuple[str, ...]] = (),
                 message_error=None, **kwargs: Any):
        super().__init__(ctx, **kwargs)
        self.result = None
        self.message = None
        self.delete_after = delete_after
        self.ori_interaction = ori_interaction
        self.author_respond = self.wait_for_message()
        self.accept_values = accept_values
        self.message_error = message_error or "I'm waiting for your response right now. Don't run another command."

    async def send(self, content: str, **kwargs: Any) -> Optional[Union[discord.Message, bool]]:
        return await self.start(content=content, **kwargs)

    async def start(self, message: Optional[discord.Message] = None, **kwargs: Any) -> Optional[Union[discord.Message, bool]]:
        self.message = message
        if self.message is None:
            if self.ori_interaction and kwargs.get("ephemeral"):
                await self.ori_interaction.response.send_message(**kwargs)
            else:
                reference = kwargs.pop("reference", self.context.message.to_reference())
                self.message = await self.context.send(view=self, reference=reference, **kwargs)

        task = asyncio.create_task(self.handle_message())
        lock = UserLock(self.context.author, self.message_error)
        async with lock(self.context.bot):
            await self.wait()
        task.cancel()
        if self.message is None:
            coro = discord.utils.maybe_coroutine(lambda: True)
        elif not self.delete_after:
            for x in self.children:
                x.disabled = True

            if self.result is None:
                coro = self.message.edit(content=f"{self.context.author} failed to response within {self.timeout:.0f} seconds.!",
                                         view=self)
            else:
                coro = self.message.edit(view=self)
        else:
            coro = self.message.delete()

        with contextlib.suppress(discord.HTTPException):
            await coro
        return self.result

    async def handle_message(self) -> None:
        bot = self.context.bot
        async for message in self.author_respond:
            value = None
            check_context = await bot.get_context(message)
            if not check_context.valid:
                value = await self.message_respond(message)
            await self.author_respond.asend(value)

    def invalid_response(self) -> str:
        return "Invalid Input. These are the accepted inputs: " + ", ".join(self.accept_values)

    async def message_respond(self, message: discord.Message) -> bool:
        """Actual interaction with user, override this method for a different behaviour."""
        result = True
        if self.accept_values:
            if message.content.casefold() not in self.accept_values:
                result = False
        return result

    async def denied(self, button: ui.Button, interaction: discord.Interaction) -> None:
        pass

    def predicate(self, message: discord.Message) -> bool:
        """Override this method to modify wait_for check behaviour."""
        context = self.context
        return message.author == context.author and message.channel == context.channel

    async def wait_for_message(self) -> AsyncGenerator[bool, discord.Message]:
        while True:
            try:
                message = await self.context.bot.wait_for("message", check=self.predicate, timeout=self.timeout)
                value = yield message
                self.reset_timeout()
                if value:
                    self.result = message
                    self.stop()
                    break
                if value is False:
                    error = self.invalid_response()
                    await message.reply(error, delete_after=60)
                yield
            except asyncio.TimeoutError:
                self.stop()

    @ui.button(emoji="<:crossmark:753620331851284480>", label="Cancel", style=discord.ButtonStyle.danger)
    async def denied_action(self, button: ui.Button, interaction: discord.Interaction):
        await self.denied(button, interaction)
        self.result = False
        self.stop()


class InteractionPages(BaseView, MenuBase):
    def __init__(self, source: ListPageInteractionBase, generate_page: Optional[bool] = False):
        super().__init__(timeout=120)
        self._source = source
        self._generate_page = generate_page
        self.ctx = None
        self.message = None
        self.current_page = 0
        self.current_button = None
        self.current_interaction = None
        self.cooldown = commands.CooldownMapping.from_cooldown(1, 10, commands.BucketType.user)
        self.prompter = None

    class PagePrompt(ui.Modal):
        page_number = TextInput(label="Page Number", min_length=1, required=True)

        def __init__(self, view: InteractionPages):
            max_pages = view._source.get_max_pages()
            super().__init__(title=f"Pick a page from 1 to {max_pages}")
            self.page_number.max_length = len(str(max_pages))
            self.view = view
            self.max_pages = max_pages
            self.valid = False
            self.ctx = view.ctx

        async def interaction_check(self, interaction: discord.Interaction) -> Optional[bool]:
            # extra measures, there isn't a way for this to trigger.
            if interaction.user == self.ctx.author:
                return True

            await interaction.response.send_message("You can't fill up this modal.", ephemeral=True)

        async def on_submit(self, interaction: discord.Interaction):
            value = self.page_number.value.strip()
            if value.isdigit() and 0 < (page := int(value)) <= self.max_pages:
                await self.view.show_checked_page(page - 1)
                self.view.reset_timeout()
                return

            def send(content):
                return interaction.response.send_message(content, ephemeral=True)

            if not value.isdigit():
                if value.lower() == "cancel":
                    return

                await send(f"{value} is not a page number")
            else:
                await send(f"Please pick a number between 1 and {self.max_pages}. Not {value}")

    def stop(self):
        if self.prompter:
            self.prompter.stop()

        super().stop()

    def selecting_page(self, interaction):
        if self.prompter is None:
            self.prompter = self.PagePrompt(self)

        return interaction.response.send_modal(self.prompter)

    async def start(self, ctx: StellaContext, /) -> None:
        self.ctx = ctx
        self.message = await self.send_initial_message(ctx, ctx.channel)

    def add_item(self, item: ui.Item) -> None:
        coro = copy(item.callback)
        item.callback = partial(self.handle_callback, coro)
        super().add_item(item)

    async def handle_callback(self, coro: Callable[[ui.Button, discord.Interaction], Coroutine[None, None, None]],
                              button: ui.Button, interaction: discord.Interaction, /) -> None:
        self.current_button = button
        self.current_interaction = interaction
        await coro(button, interaction)

    @ui.button(emoji='<:before_fast_check:754948796139569224>', style=discord.ButtonStyle.blurple)
    async def first_page(self, *_: Union[ui.Button, discord.Interaction]):
        await self.show_page(0)

    @ui.button(emoji='<:before_check:754948796487565332>', style=discord.ButtonStyle.blurple)
    async def before_page(self, *_: Union[ui.Button, discord.Interaction]):
        await self.show_checked_page(self.current_page - 1)

    @ui.button(emoji='<:stop_check:754948796365930517>', style=discord.ButtonStyle.blurple)
    async def stop_page(self, *_: Union[ui.Button, discord.Interaction]):
        self.stop()
        await self.message.delete(delay=0)

    @ui.button(emoji='<:next_check:754948796361736213>', style=discord.ButtonStyle.blurple)
    async def next_page(self, *_: Union[ui.Button, discord.Interaction]):
        await self.show_checked_page(self.current_page + 1)

    @ui.button(emoji='<:next_fast_check:754948796391227442>', style=discord.ButtonStyle.blurple)
    async def last_page(self, *_: Union[ui.Button, discord.Interaction]):
        await self.show_page(self._source.get_max_pages() - 1)

    @ui.button(label="Select Page", style=discord.ButtonStyle.gray)
    async def select_page(self, button, interaction):
        await self.selecting_page(interaction)

    async def _get_kwargs_from_page(self, page: Any) -> Dict[str, Any]:
        value = await super()._get_kwargs_from_page(page)
        self.format_view()
        if 'view' not in value:
            value.update({'view': self})
        value.update({'allowed_mentions': discord.AllowedMentions(replied_user=False)})
        return value

    def format_view(self) -> None:
        for i, b in enumerate(self.children):
            b.disabled = any(
                [self.current_page == 0 and i < 2, self.current_page == self._source.get_max_pages() - 1 and not i < 3]
            )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Only allowing the context author to interact with the view"""
        ctx = self.ctx
        author = ctx.author
        if await ctx.bot.is_owner(interaction.user):
            return True
        if interaction.user != author:
            bucket = self.cooldown.get_bucket(ctx.message)
            if not bucket.update_rate_limit():
                command = ctx.bot.get_command_signature(ctx, ctx.command)
                content = f"Only `{author}` can use this menu. If you want to use it, use `{command}`"
                embed = StellaEmbed.to_error(description=content)
                await interaction.response.send_message(embed=embed, ephemeral=True)
            return False
        return True

    async def on_timeout(self) -> None:
        await self.message.delete(delay=0)


class PersistentRespondView(ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    class ConfirmationView(ConfirmView):
        def __init__(self, ctx):
            super().__init__(ctx, delete_after=True)

        async def confirmed(self, button: ui.Button, interaction: discord.Interaction):
            await interaction.response.send_message("Message has been sent.", ephemeral=True)

        async def denied(self, button: ui.Button, interaction: discord.Interaction):
            msg = "Message was not sent, please click on Respond button again to respond."
            await interaction.response.send_message(msg, ephemeral=True)

    @ui.button(label="Respond", style=discord.ButtonStyle.primary, custom_id="persistent_report_reply")
    async def res_action(self, button: ui.Button, interaction: discord.Interaction):
        message = interaction.message
        bot = self.bot
        if bot.tester:
            return

        msg = await interaction.user.send("Please enter your message to respond. You have 60 seconds.")
        await self.clean_up(message)
        try:
            respond = await bot.wait_for("message", check=lambda m: m.channel.id == msg.channel.id, timeout=60)
        except asyncio.TimeoutError:
            await msg.edit(content="Timeout. Please click Respond if you want to respond again.", delete_after=60)
            return await message.edit(view=self)
        else:
            await msg.delete()
        ctx = await bot.get_context(respond)
        data = await self.get_interface_data(interaction)
        report_id = data["report_id"]
        destination = await self.get_destination(interaction, report_id)

        usure = f"Are you sure, you want to send this message to `{destination}`?"
        if await self.ConfirmationView(ctx).send(usure):
            # Send to the opposite person
            dm = await destination.create_dm()
            msg = dm.get_partial_message(data["message_id"])
            embed = StellaEmbed.default(ctx, title=f"Respond from {ctx.author}", description=respond.content)
            interface_msg = await msg.reply(embed=embed, view=self)

            query_insert = "INSERT INTO report_respond VALUES($1, $2, $3, $4, $5)"
            values = (report_id, respond.author.id, interface_msg.id, respond.id, respond.content)
            await bot.pool_pg.execute(query_insert, *values)
            await self.clean_up(message)
        else:
            await message.edit(view=self)

    @ui.button(label="End Report", style=discord.ButtonStyle.danger, custom_id="persistent_end_report")
    async def end_action(self, button: ui.Button, interaction: discord.Interaction):
        message = interaction.message
        bot = self.bot
        if bot.tester:
            return

        interaction_data = await self.get_interface_data(interaction)
        report_id = interaction_data["report_id"]
        # Update to database
        query = "UPDATE reports SET finish=True WHERE report_id=$1"
        await bot.pool_pg.execute(query, report_id)

        # Send to author
        desc_user = "You will no longer receive any respond nor able to respond."
        embed = StellaEmbed.to_error(title="End of Report", description=desc_user)
        channel = await interaction.user.create_dm()
        pmessage = channel.get_partial_message(message.id)
        await pmessage.reply(embed=embed)
        destination = await self.get_destination(interaction, report_id)

        # Send to the opposite person
        query_m = "SELECT message_id FROM report_respond WHERE interface_id=$1"
        data = await bot.pool_pg.fetchval(query_m, message.id, column='message_id')
        desc_opposite = f"{interaction.user} has ended the report."
        embed = StellaEmbed.to_error(title="End of Report", description=desc_opposite)

        dm = await destination.create_dm()
        msg = dm.get_partial_message(data)
        await msg.reply(embed=embed)
        await self.clean_up(message)

    async def get_destination(self, interaction, report_id):
        bot = self.bot
        stella = bot.stella
        if interaction.user == stella:
            report = await bot.pool_pg.fetchrow("SELECT user_id FROM reports WHERE report_id=$1", report_id)
            return bot.get_user(report["user_id"])
        return stella

    async def get_interface_data(self, interaction: discord.Interaction) -> Dict[str, int]:
        old_query = "SELECT report_id, interface_id, message_id FROM report_respond WHERE interface_id=$1"
        return await self.bot.pool_pg.fetchrow(old_query, interaction.message.id)

    async def clean_up(self, message: discord.Message) -> None:
        await message.edit(view=None)


command_cooldown = commands.CooldownMapping.from_cooldown(1, 5, commands.BucketType.user)


class ButtonView(ViewAuthor, CallbackView):
    @ui.button(label='Re-run', style=discord.ButtonStyle.blurple)
    async def on_run(self, button: ui.Button, interaction: discord.Interaction):
        if not (retry := command_cooldown.update_rate_limit(self.context.message)):
            await interaction.response.edit_message(view=None)
            new_message = await self.context.fetch_message(self.context.message.id)
            new_message._edited_timestamp = discord.utils.utcnow() # take account cooldown
            await self.context.reinvoke(message=new_message)
        else:
            raise commands.CommandOnCooldown(command_cooldown._cooldown, retry, command_cooldown._type)

    @ui.button(label='Delete', style=discord.ButtonStyle.danger)
    async def on_delete(self, button: ui.Button, interaction: discord.Interaction):
        await interaction.message.delete(delay=0)

    async def handle_callback(self, callback, button: ui.Button, interaction: discord.Interaction):
        try:
            await callback(interaction)
        except commands.CommandOnCooldown as cooldown:
            await interaction.response.send_message(
                content=f"Don't spam the button. You're on cooldown. Retry after: `{cooldown.retry_after:.2f}`",
                ephemeral=True
            )
        else:
            self.stop()
