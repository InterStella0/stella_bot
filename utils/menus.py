import discord
from discord.ext import menus
from discord.ext.menus import First, Last, Button
from collections import namedtuple

class MenuBase(menus.MenuPages):
    """This is a MenuPages class that is used every single paginator menus. All it does is replace the default emoji
       with a custom emoji, and keep the functionality."""
    def __init__(self, source, *, generate_page=True, **kwargs):
        super().__init__(source, delete_message_after=kwargs.pop('delete_message_after', True), **kwargs)
        self.info = False
        self._generate_page = generate_page
        for x in list(self._buttons):
            if ":" not in str(x): # I dont care
                self._buttons.pop(x)

    @menus.button("<:before_check:754948796487565332>", position=First(1))
    async def go_before(self, payload):
        """Goes to the previous page."""
        await self.show_checked_page(self.current_page - 1)

    @menus.button("<:next_check:754948796361736213>", position=Last(0))
    async def go_after(self, payload):
        """Goes to the next page."""
        await self.show_checked_page(self.current_page + 1)

    @menus.button("<:before_fast_check:754948796139569224>", position=First(0))
    async def go_first(self, payload):
        """Goes to the first page."""
        await self.show_page(0)

    @menus.button("<:next_fast_check:754948796391227442>", position=Last(1))
    async def go_last(self, payload):
        """Goes to the last page."""
        await self.show_page(self._source.get_max_pages() - 1)
    
    @menus.button("<:stop_check:754948796365930517>", position=First(2))
    async def go_stop(self, payload):
        """Remove this message."""
        self.stop()

    async def _get_kwargs_format_page(self, page):
        value = await discord.utils.maybe_coroutine(self._source.format_page, self, page)
        if self._generate_page:
            value = self.generate_page(value, self._source.get_max_pages())
        if isinstance(value, dict):
            return value
        elif isinstance(value, str):
            return { 'content': value, 'embed': None }
        elif isinstance(value, discord.Embed):
            return { 'embed': value, 'content': None }

    async def _get_kwargs_from_page(self, page):
        dicts = await self._get_kwargs_format_page(page)
        dicts.update({'allowed_mentions': discord.AllowedMentions(replied_user=False)})
        return dicts

    def generate_page(self, content, maximum):
        if maximum > 0:
            page = f"Page {self.current_page + 1}/{maximum}"
            if isinstance(content, discord.Embed):
                if embed_dict := getattr(content, "_author", None):
                    if "Page" not in embed_dict["name"]:
                        embed_dict["name"] += f"[{page.replace('Page ', '')}]"
                    return content
                return content.set_author(name=page)
            elif isinstance(content, str):
                return f"{page}\n{content}"
        return content

    async def send_initial_message(self, ctx, channel):
        page = await self._source.get_page(0)
        kwargs = await self._get_kwargs_from_page(page)
        return await ctx.reply(**kwargs)

# Remind me to rewrite this trash
class HelpMenuBase(MenuBase, inherit_buttons=False):
    """Menu that has information implementation"""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.help_command = None

    async def show_page(self, page_number):
        self.info = False
        await super().show_page(page_number)

    @menus.button('<:information_pp:754948796454010900>', position=Last(4))
    async def on_information(self, payload):
        """Shows this help information"""
        if info := not self.info:
            await self.on_information_show(payload)
        else:
            self.current_page = max(self.current_page, 0)
            await self.show_page(self.current_page)
        self.info = info

    async def on_information_show(self, payload):
        raise NotImplementedError

    async def start(self, ctx, **kwargs):
        self.help_command = ctx.bot.help_command
        self.help_command.context = ctx
        await super().start(ctx, **kwargs)


class MenuViewInteractionBase(HelpMenuBase):
    """MenuPages class that is specifically for the help command."""
    def __init__(self, view, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.view = view

    def stop(self):
        self.view.stop()
        super().stop()

    async def _get_kwargs_from_page(self, page):
        kwargs = await super()._get_kwargs_from_page(page)
        kwargs.update({"view": await self._source.format_view(self, page)})
        return kwargs

class ListPageInteractionBase(menus.ListPageSource):
    def __init__(self, button, interaction, entries, **kwargs):
        super().__init__(entries, **kwargs)
        self.button = button
        self.interaction = interaction

    async def format_view(self, menu, entry):
        """Method that handles views, it must return a View"""
        raise NotImplementedError
