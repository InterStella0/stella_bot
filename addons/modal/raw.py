from __future__ import annotations
from typing import Dict, Any, Optional, List, Union, TYPE_CHECKING

import discord
from discord import Component
from discord.enums import try_enum

from .enums import ComponentType

if TYPE_CHECKING:
    from .ui import Modal, TextInput


class ResponseTextInput:
    __slots__ = (
        'custom_id',
        'value',
        'type',
        'original',
        'label',
    )

    def __init__(self, modal: Modal, data: PayloadTextInput):
        self.custom_id: str = data.custom_id
        self.value: str = data.value
        self.type: ComponentType = data.type
        self.original: Optional[TextInput] = discord.utils.get(modal.children, custom_id=self.custom_id)
        self.label = getattr(self.original, 'label', None)


class ResponseModal:
    __slots__ = (
        'data',
        'custom_id',
        'children',
    )

    def __init__(self, modal: Modal, data: Dict[str, Any]):
        self.data: Dict[str, Any] = data
        self.custom_id: str = self.data.get('custom_id')
        self.children: List[discord.ui.Item] = []

        for components in self.data.get("components"):
            # For some reason its wrapped in double [{'components': [{'type': 1, 'components': [data]}]}]
            for d in components['components']:
                to_append = d
                if d.get('type') == 4:
                    payload = PayloadTextInput(d)
                    to_append = ResponseTextInput(modal, payload)
                self.children.append(to_append)

    def __getitem__(self, item: Union[int, str]) -> Optional[ResponseTextInput]:
        if isinstance(item, int):
            return self.children[item]

        for child in self.children:
            if getattr(child, "custom_id", None) == item:
                return child
            if getattr(child, "label", None) == item:
                return child


class PayloadTextInput:
    __slots__ = (
        'custom_id',
        'value',
        'type',
    )

    def __init__(self, data: Dict[str, Any]):
        self.custom_id: str = data.get('custom_id')
        self.value: str = data.get('value')
        self.type: ComponentType = try_enum(ComponentType, data.get('type'))


class _RawTextInput(Component):
    __slots__ = (
        'style',
        'label',
        'custom_id',
        'min_length',
        'max_length',
        'required',
        'value',
        'type',
        'placeholder',
    )

    def __init__(self, payload: PayloadTextInput):
        self.custom_id = payload.custom_id
        self.value = payload.value
        self.type = payload.type

    def to_dict(self) -> Dict[str, Union[bool, int, str]]:
        payload = {
            'type': self.type.value,  # type: ignore
            'style': self.style.value,
            'label': self.label,
            'custom_id': self.custom_id
        }
        if self.min_length is not None:
            payload['min_length'] = self.min_length

        if self.max_length is not None:
            payload['max_length'] = self.max_length

        if self.value is not None:
            payload['value'] = self.value

        if self.required is not None:
            payload['required'] = self.required

        if self.placeholder:
            payload['placeholder'] = self.placeholder

        return payload
