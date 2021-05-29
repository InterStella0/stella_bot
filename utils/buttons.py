from discord import ui


class BaseButton(ui.Button):
    def __init__(self, *, style, selected, group, label=None, **kwargs):
        super().__init__(style=style, label=label or selected, group=group, **kwargs)
        self.selected = selected

    async def callback(self, interaction):
        raise NotImplementedError

class ViewButtonIteration(ui.View):
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


