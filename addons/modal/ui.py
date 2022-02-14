from __future__ import annotations

import asyncio
import functools
import os
from typing import Any, Optional, Dict, Union, List, TypeVar

import discord
from discord.enums import InteractionResponseType
from discord.webhook.async_ import async_context

# if you're looking at here, its not done yet hoes dont copy yet
from .enums import ComponentType, InputStyle, InteractionType
from .raw import _RawTextInput, ResponseModal

class Modal:
    def __init__(self, title: str, *, timeout: Optional[int] = 180, custom_id: Optional[str] = None):
        self.timeout = timeout
        if self.timeout is None and custom_id is None:
            raise ValueError("'Custom_id' must be filled on persistent modal.")

        self.custom_id = custom_id or os.urandom(16).hex()
        self.title = title
        self.children: List[discord.ui.Item] = []
        self.__waiter = None

    def stop(self):
        if self.__remove_listening is not None:
            self.__remove_listening()
        if self.__waiter:
            if not self.__waiter.done():
                self.__waiter.set_result(None)

            self.__waiter = None

    async def defer(self, interaction: discord.Interaction, *, ephemeral: bool = False) -> None:
        response = interaction.response
        parent = response._parent
        adapter = async_context.get()
        type_defer: int = 0
        if parent.type is InteractionType.modal_submit or parent.type.value == InteractionType.modal_submit.value:
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

    def __start_timer(self):
        self.__waiter = asyncio.Future()

        async def stop_modal():
            nonlocal self
            try:
                await asyncio.wait_for(self.__waiter, timeout=self.timeout)
            except asyncio.TimeoutError:
                await self.on_timeout()
            finally:
                self.stop()

        asyncio.create_task(stop_modal())

    def wait(self):
        return self.__waiter

    def _setup_listener(self, interaction: discord.Interaction) -> None:
        def remove_modal(modal, modal_store):
            modal_store.remove_modal(modal)

        if self.timeout is not None:
            self.__start_timer()

        if hasattr(interaction._state, '_modal_store'):
            interaction._state._modal_store.add_modal(self)
            self.__remove_listening = functools.partial(remove_modal, self, interaction._state._modal_store)

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
        self._setup_listener(interaction)

        response._responded = True

    async def on_timeout(self):
        pass

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
            self.__waiter.set_result(None)

    def add_item(self, item: discord.ui.Item) -> Modal:
        if not isinstance(item, discord.ui.Item):
            raise TypeError(f"item must derived from discord.ui.Item not {type(item)!r}")

        if len(self.children) >= 5:
            raise ValueError("maximum amount of components exceeded")

        self.children.append(item)
        return self

    def remove_item(self, item: discord.ui.Item) -> Modal:
        self.children.pop(item)
        return self

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "custom_id": self.custom_id,
            "components": [{
                "type": 1,
                "components": [item.to_component_dict()]
            } for item in self.children]
        }


V = TypeVar('V', bound='View', covariant=True)


class TextInput(discord.ui.Item[V]):
    __slots__ = (
        '_underlying',
    )

    def __init__(self, *, label, style=InputStyle.short, custom_id=None, min_length=None, max_length=None, required=None, value=None,
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
            type=ComponentType.text_input,
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
    def type(self) -> Optional[ComponentType]:
        return self._underlying.type

    @type.setter
    def type(self, value: ComponentType):
        self._underlying.type = value

    def to_component_dict(self) -> Dict[str, Union[bool, int, str]]:
        return self._underlying.to_dict()
