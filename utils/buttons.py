from __future__ import annotations
import discord
import inspect
from copy import copy
from functools import partial
from discord import ui
from discord.ext import commands
from typing import Optional, Any, Dict, Iterable, Union, Type, TYPE_CHECKING, Coroutine, Callable
from utils.useful import BaseEmbed
from utils.menus import ListPageInteractionBase, MenuViewInteractionBase, MenuBase

if TYPE_CHECKING:
    from utils.useful import StellaContext


class BaseButton(ui.Button):
    def __init__(self, *, style: discord.ButtonStyle, selected: Union[int, str], row: int,
                 label: Optional[str] = None, **kwargs: Any):
        super().__init__(style=style, label=label or selected, row=row, **kwargs)
        self.selected = selected

    async def callback(self, interaction: discord.Interaction) -> None:
        raise NotImplementedError


class ViewButtonIteration(ui.View):
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


class ViewIterationAuthor(ViewButtonIteration):
    def __init__(self, ctx: StellaContext, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.context = ctx
        self.cooldown = commands.CooldownMapping.from_cooldown(1, 10, commands.BucketType.user)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Only allowing the context author to interact with the view"""
        ctx = self.context
        author = ctx.author
        if interaction.user != author:
            bucket = self.cooldown.get_bucket(ctx.message)
            if not bucket.update_rate_limit():
                h = ctx.bot.help_command
                command = h.get_command_signature(ctx.command, ctx)
                content = f"Only `{author}` can use this menu. If you want to use it, use `{command}`"
                embed = BaseEmbed.to_error(description=content)
                await interaction.response.send_message(embed=embed, ephemeral=True)
            return False
        return True


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


class InteractionPages(ui.View, MenuBase):
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

    async def start(self, ctx: StellaContext, /) -> None:
        self.ctx = ctx
        self.message = await self.send_initial_message(ctx, ctx.channel)

    def add_item(self, item: discord.ui.Item) -> None:
        coro = copy(item.callback)
        item.callback = partial(self.handle_callback, coro)
        super().add_item(item)

    async def handle_callback(self, coro: Callable[[discord.ui.Button, discord.Interaction], Coroutine[None, None, None]],
                              button: discord.ui.Button, interaction: discord.Interaction, /) -> None:
        self.current_button = button
        self.current_interaction = interaction
        await coro(button, interaction)

    @ui.button(emoji='<:before_fast_check:754948796139569224>')
    async def first_page(self, *_: Union[discord.ui.Button, discord.Interaction]):
        await self.show_page(0)

    @ui.button(emoji='<:before_check:754948796487565332>')
    async def before_page(self, *_: Union[discord.ui.Button, discord.Interaction]):
        await self.show_checked_page(self.current_page - 1)

    @ui.button(emoji='<:stop_check:754948796365930517>')
    async def stop_page(self, *_: Union[discord.ui.Button, discord.Interaction]):
        self.stop()
        await self.message.delete()

    @ui.button(emoji='<:next_check:754948796361736213>')
    async def next_page(self, *_: Union[discord.ui.Button, discord.Interaction]):
        await self.show_checked_page(self.current_page + 1)

    @ui.button(emoji='<:next_fast_check:754948796391227442>')
    async def last_page(self, *_: Union[discord.ui.Button, discord.Interaction]):
        await self.show_page(self._source.get_max_pages() - 1)

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
        if interaction.user != author:
            bucket = self.cooldown.get_bucket(ctx.message)
            if not bucket.update_rate_limit():
                h = ctx.bot.help_command
                command = h.get_command_signature(ctx.command, ctx)
                content = f"Only `{author}` can use this menu. If you want to use it, use `{command}`"
                embed = BaseEmbed.to_error(description=content)
                await interaction.response.send_message(embed=embed, ephemeral=True)
            return False
        return True

    async def on_timeout(self) -> None:
        await self.message.delete()
