import inspect
from discord import ui
from utils.menus import ListPageInteractionBase, MenuViewInteractionBase

class BaseButton(ui.Button):
    def __init__(self, *, style, selected, group, label=None, **kwargs):
        super().__init__(style=style, label=label or selected, group=group, **kwargs)
        self.selected = selected

    async def callback(self, interaction):
        raise NotImplementedError

class ViewButtonIteration(ui.View):
    """A BaseView class that creates arrays of buttons, depending on the data type given on 'args',
        it will accept `mapper` as a dataset"""
    def __init__(self, *args, mapper=None, button=BaseButton, style=None):
        super().__init__()
        self.mapper=mapper
        for c, button_row in enumerate(args):
            for button_col in button_row:
                if isinstance(button_col, dict):
                    self.add_item(button(style=style, group=c, **button_col))
                elif isinstance(button_col, tuple):
                    selected, button_col = button_col
                    self.add_item(button(style=style, group=c, selected=selected, **button_col))
                else:
                    self.add_item(button(style=style, group=c, selected=button_col))

class MenuViewBase(ViewButtonIteration):
    """A Base Menu + View combination for all interaction that combines those two.
        It requires a page_source and an optional menu that must derived from MenuViewInteractionBase"""
    def __init__(self, ctx, page_source, *args, message=None, menu=MenuViewInteractionBase, **kwargs):
        super().__init__(*args, **kwargs)
        if not inspect.isclass(page_source):
            raise Exception(f"'page_source' must be a class")
        if not issubclass(page_source, ListPageInteractionBase):
            raise Exception(f"'page_source' must subclass ListPageInteractionBase, not '{page_source}'")
        if not inspect.isclass(menu):
            raise Exception("'menu' must a class")
        if not issubclass(menu, MenuViewInteractionBase):
            raise Exception(f"'menu' must subclass MenuViewInteractionBase, not '{menu}'")

        self.message = message
        self.context = ctx
        self._class_page_source = page_source
        self._class_menu = menu
        self.menu = None
        self.__prepare = False

    async def start(self, page_source):
        """Starts the menu if it has not yet started"""
        if not self.__prepare:
            message = self.message
            self.menu = self._class_menu(self, page_source, message=message)
            await self.menu.start(self.context)
            await self.menu.show_page(0)
            self.__prepare = True

    async def update(self, button, interaction, data):
        """Updates the view and menu, this method replace dataset that is bound to the menu,
            and changes it to a new page_source with a new dataset."""
        if self.message is None:
            self.message = interaction.message
        page_source = self._class_page_source(button, interaction, data, per_page=1)
        if not self.__prepare:
            await self.start(page_source)
        else:
            await self.menu.change_source(page_source)
        self.check_reactions(interaction)

    def check_reactions(self, interaction):
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

    async def interaction_check(self, interaction):
        """Only allowing the context author to interact with the view"""
        author = self.context.author
        if interaction.user != author:
            await interaction.response.send_message(content=f"Only {author} can use this.", ephemeral=True)
            raise Exception("no")
        return True
    async def on_timeout(self):
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