from __future__ import annotations

import asyncio
import os

from typing import Any, AsyncIterator, Callable, Coroutine, Dict, List, Optional, TypedDict

import aiohttp

from discord.ext import ipc

from utils.useful import print_exception


class IPCData(TypedDict, total=False):
    # requires python 3.11: https://www.python.org/dev/peps/pep-0655
    # error: Dict[str, Any]
    ...


class _RecvMessagePayload(TypedDict):
    endpoint: str
    # requires python 3.11: https://www.python.org/dev/peps/pep-0655
    # request_id: NotRequired[str]
    response: IPCData


class _SendMessagePayload(TypedDict):
    endpoint: str
    request_id: str
    headers: Dict[str, Any]
    data: Dict[str, Any]


_HandlerType = Callable[[IPCData], Coroutine[Any, Any, Any]]


class StellaClient(ipc.Client):
    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.bot_id = kwargs.pop("bot_id", None)
        # callbacks are subscribed to event name and message id
        self._callbacks: Dict[str, Dict[str, asyncio.Future[IPCData]]] = {}
        # event handlers are subscribed to event name
        self._event_handlers: Dict[str, List[_HandlerType]] = {}
        self._stream_reader_task: Optional[asyncio.Task[None]] = None

    def __call__(self, bot_id: int) -> None:
        self.bot_id = bot_id

    async def check_init(self) -> None:
        if not self.session:
            await self.init_sock()

        self.start_reading_messages()

    @staticmethod
    def _print_exception_callback(task: asyncio.Task[Any]) -> None:
        if task.cancelled():
            return
        if task.exception():
            task.print_stack()

    def start_reading_messages(self) -> None:
        if self._stream_reader_task is None:
            self._stream_reader_task = asyncio.create_task(self._read_message_stream())
            self._stream_reader_task.add_done_callback(self._print_exception_callback)

    def stop_reading_messages(self) -> None:
        if self._stream_reader_task is not None:
            self._stream_reader_task.cancel()
            self._stream_reader_task = None

    def listen(self) -> Callable[[_HandlerType], _HandlerType]:
        def inner(handler: _HandlerType) -> _HandlerType:
            event_handlers = self._event_handlers.setdefault(handler.__name__, [])
            event_handlers.append(handler)

            return handler
        return inner

    async def subscribe(self) -> IPCData:
        data = await self.request("start_connection")
        if (error := data.get("error")) is not None:
            self.stop_reading_messages()
            raise Exception(f"Unable to get event from server: {error}")
        return data

    async def request(self, endpoint: str, *, timeout: Optional[float] = None,
                      **data: Any) -> IPCData:
        await self.check_init()

        request_id = self._new_request_id()
        # register before sending message to avoid data race
        future = self._register_callback(endpoint, request_id)

        await self.websocket.send_json(
            self._make_payload(endpoint=endpoint, data=data, request_id=request_id)
        )

        return await asyncio.wait_for(future, timeout)

    @staticmethod
    def _new_request_id() -> str:
        return os.urandom(32).hex()

    def _register_callback(self, endpoint: str, request_id: str) -> asyncio.Future[IPCData]:
        future = asyncio.get_event_loop().create_future()
        callbacks = self._callbacks.setdefault(f"on_{endpoint}", {})
        callbacks[request_id] = future

        return future

    def _make_payload(self, *, endpoint: str, data: Dict[str, Any], request_id: str) -> _SendMessagePayload:
        return {
            "endpoint": endpoint,
            "request_id": request_id,
            "headers": {"Authorization": self.secret_key, "Bot_id": self.bot_id},
            "data": data,
        }

    async def _message_stream(self) -> AsyncIterator[aiohttp.WSMessage]:
        while True:
            recv = await self.websocket.receive()
            if recv.type == aiohttp.WSMsgType.PING:
                await self.websocket.ping()
                continue
            elif recv.type == aiohttp.WSMsgType.PONG:
                continue
            elif recv.type == aiohttp.WSMsgType.CLOSED:
                print("IPC websocket session closed, reconnecting...")
                await self.session.close()
                await asyncio.sleep(5)
                await self.init_sock()
                continue
            else:
                yield recv

    async def _read_message_stream(self) -> None:
        async for ws_message in self._message_stream():
            try:
                await self._process_message(ws_message.json())
            except Exception as e:
                print_exception("Ignoring error in gateway:", e)

    async def _process_message(self, message: _RecvMessagePayload) -> None:
        event = f"on_{message['endpoint']}"
        response = message["response"]

        if request_id := message.get("request_id"):
            callbacks = self._callbacks.get(event, {})

            if future := callbacks.pop(request_id, None):  # type: ignore[call-overload]
                future.set_result(response)
            else:
                print(f"unregistered request id {request_id} for IPC event {event}, ignoring")

        if handlers := self._event_handlers.get(event):
            await asyncio.gather(*[handler(response) for handler in handlers])
