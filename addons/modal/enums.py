from discord import ComponentType as OldComponentType, InteractionType as OldInteractionType, Enum


__all__ = (
    'ComponentType',
    'InteractionType',
    'InputStyle',
)


class ComponentType(OldComponentType):
    text_input = 4


class InteractionType(OldInteractionType):
    modal_submit = 5


class InputStyle(Enum):
    short = 1
    paragraph = 2