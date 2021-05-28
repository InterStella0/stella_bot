from discord import ui


class BaseButton(ui.Button):
    def __init__(self, *, style, selected, group):
        super().__init__(style=style, label=selected, group=group)
        self.selected = selected

    async def callback(self, interaction):
        raise NotImplemented("Implement this please")

class ViewButtonIteration(ui.View):
    def __init__(self, *args, button=BaseButton, style=None):
        super().__init__()
        for c, button_row in enumerate(args):
            for button_col in button_row:
                self.add_item(button(style=style, selected=button_col, group=c))
