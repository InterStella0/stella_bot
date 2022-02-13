from __future__ import annotations

import asyncio
import os
from typing import Any, Optional, Dict, Union, List, TypeVar

import discord
from discord import Enum, Component, ComponentType
from discord.enums import try_enum, InteractionType, InteractionResponseType
from discord.webhook.async_ import async_context

# if you're looking at here, its not done yet hoes dont copy yet

class UpComponentType(ComponentType):
    text_input = 4


class UpInteractionType(InteractionType):
    modal_submit = 5


class Modal:
    def __init__(self, title: str, *, custom_id: Optional[str] = None):
        self.custom_id = custom_id or os.urandom(16).hex()
        self.title = title
        self.children: List[discord.ui.Item] = []
        self.done = asyncio.Event()

    async def defer(self, interaction: discord.Interaction, *, ephemeral: bool = False) -> None:
        response = interaction.response
        parent = response._parent
        adapter = async_context.get()
        type_defer: int = 0
        if parent.type is UpInteractionType.modal_submit or parent.type.value == UpInteractionType.modal_submit.value:  # type: ignore
            type_defer = InteractionResponseType.deferred_message_update.value

        if type_defer:
            await adapter.create_interaction_response(
                parent.id,
                parent.token,
                session=parent._session,
                type=type_defer
            )
            response._responded = True
        else:
            await interaction.response.defer(ephemeral=ephemeral)

    async def prompt(self, interaction: discord.Interaction) -> None:
        response = interaction.response
        parent = response._parent
        adapter = async_context.get()
        await adapter.create_interaction_response(
            parent.id,
            parent.token,
            session=parent._session,
            type=9,
            data=self.to_dict()
        )
        if hasattr(interaction._state, '_modal_store'):
            interaction._state._modal_store.add_modal(self)

        response._responded = True

    async def on_invoke_error(self, error):
        pass

    async def callback(self, modal: ResponseModal, interaction: discord.Interaction):
        pass

    async def invoke(self, interaction):
        modal = ResponseModal(self, interaction.data)
        try:
            await self.callback(modal, interaction)
            if not interaction.response._responded:
                await self.defer(interaction)

        except Exception as e:
            await self.on_invoke_error(e)
        finally:
            self.done.set()

    def add_item(self, item):
        if len(self.children) > 5:
            raise ValueError("maximum amount of components exceeded")

        if not isinstance(item, discord.ui.Item):
            raise TypeError(f"item must derived from discord.ui.Item not {type(item)!r}")

        self.children.append(item)

    def to_dict(self):
        return {
            "title": self.title,
            "custom_id": self.custom_id,
            "components": [{
                "type": 1,
                "components": [item.to_component_dict()]
            } for item in self.children]
        }


class InputStyle(Enum):
    short = 1
    paragraph = 2


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
        self.type: UpComponentType = data.type
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
        self.type: UpComponentType = try_enum(UpComponentType, data.get('type'))


V = TypeVar('V', bound='View', covariant=True)


class TextInput(discord.ui.Item[V]):
    __slots__ = (
        '_underlying',
    )

    def __init__(self, *, label, style, custom_id=None, min_length=None, max_length=None, required=None, value=None,
                 placeholder=None):
        super().__init__()
        self._provided_custom_id = custom_id is not None
        self._underlying = _RawTextInput._raw_construct(
            label=label,
            style=style,
            custom_id=custom_id or os.urandom(16).hex(),
            min_length=min_length,
            max_length=max_length,
            required=required,
            value=value,
            placeholder=placeholder,
            type=UpComponentType.text_input,
        )

    @property
    def label(self) -> str:
        return self._underlying.label

    @label.setter
    def label(self, value: str):
        self._underlying.label = value

    @property
    def style(self) -> InputStyle:
        return self._underlying.style

    @style.setter
    def style(self, value: InputStyle):
        self._underlying.style = value

    @property
    def custom_id(self) -> InputStyle:
        return self._underlying.custom_id

    @custom_id.setter
    def custom_id(self, value: str):
        self._underlying.custom_id = value

    @property
    def min_length(self) -> Optional[int]:
        return self._underlying.min_length

    @min_length.setter
    def min_length(self, value: Optional[int]):
        self._underlying.min_length = value

    @property
    def max_length(self) -> Optional[int]:
        return self._underlying.max_length

    @max_length.setter
    def max_length(self, value: Optional[int]):
        self._underlying.max_length = value

    @property
    def required(self) -> Optional[bool]:
        return self._underlying.required

    @required.setter
    def required(self, value: Optional[bool]):
        self._underlying.required = value

    @property
    def placeholder(self) -> Optional[str]:
        return self._underlying.placeholder

    @placeholder.setter
    def placeholder(self, value: Optional[str]):
        self._underlying.placeholder = value

    @property
    def type(self) -> Optional[UpComponentType]:
        return self._underlying.type

    @type.setter
    def type(self, value: UpComponentType):
        self._underlying.type = value

    def to_component_dict(self) -> Dict[str, Union[bool, int, str]]:
        return self._underlying.to_dict()


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
            'type': self.type.value,
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

