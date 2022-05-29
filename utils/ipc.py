from __future__ import annotations

import asyncio
import datetime
import io
import mimetypes
import os
from dataclasses import dataclass

from typing import Any, AsyncIterator, Callable, Coroutine, Dict, List, Optional, TypedDict

import aiohttp
import discord

from discord.ext import ipc
from starlette import status
from typing_extensions import Self

from utils.errors import TokenInvalid, StellaAPIError
from utils.useful import print_exception, except_retry


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
        self._server_request_handlers: Dict[str, _HandlerType] = {}
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

    def server_request(self) -> Callable[[_HandlerType], _HandlerType]:
        def inner(handler: _HandlerType) -> _HandlerType:
            name = handler.__name__
            if self._server_request_handlers.get(name):
                raise Exception(f"Handler '{name}' has already been registered.")

            self._server_request_handlers[name] = handler
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
        listen = timeout != 0
        future = None
        if listen:
            # register before sending message to avoid data race
            future = self._register_callback(endpoint, request_id)
        await self.websocket.send_json(
            self._make_payload(endpoint=endpoint, data=data, request_id=request_id)
        )

        if listen:
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
            elif recv.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE):
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

        if callback := self._server_request_handlers.get(event):
            value = await callback(response)
            request_id = response.get('listen_id')
            asyncio.create_task(self.request("bot_response", request_id=request_id, data=value))
            return

        if handlers := self._event_handlers.get(event):
            await asyncio.gather(*[handler(response) for handler in handlers])


@dataclass
class TokenPayload:
    access_token: str
    token_type: str


@dataclass
class StellaFile:
    id: str
    name: str
    byte: bytes
    created_at: datetime.datetime

    @classmethod
    def from_api(cls, data: Dict[str, Any], byte) -> Self:
        date_data = discord.utils.parse_time(data['created_at'])
        return cls(data['file_id'], data['file_name'], byte, date_data)

    @property
    def url(self):
        return f"{StellaAPI.BASE}/files/{self.id}"

    def __str__(self):
        return self.url


class StellaAPI:
    BASE = "http://api.interstella.online"

    def __init__(self, bot):
        self.bot = bot
        self.username = bot.user_db
        self.password = bot.pass_db
        self.http: Optional[aiohttp.ClientSession] = None
        self.headers = None
        self.access_token = None

    async def generate_token(self):
        data = {
            "username": self.username,
            "password": self.password
        }
        http = self.http or aiohttp.ClientSession()
        async with http:
            self.http = http
            values = await self._request("POST", "/token", data=data)
        token = TokenPayload(values.get("access_token"), values.get("token_type"))
        headers = {
            'Authorization': f'Bearer {token.access_token}'
        }
        self.http = aiohttp.ClientSession(headers=headers)
        self.access_token = token.access_token
        return token

    async def _request(self, method: str, url: str, **kwargs) -> Dict[str, Any]:
        async with self.http.request(method, self.BASE + url, **kwargs) as resp:
            if resp.status == status.HTTP_401_UNAUTHORIZED:
                raise TokenInvalid("Invalid token given.")
            if resp.content_type == "application/json":
                return await resp.json()
            raise StellaAPIError(await resp.text())

    async def upload_file(self, *, file: bytes, filename: str, retries=4) -> StellaFile:
        async def callback():
            try:
                return await self._upload_file(file, filename)
            except TokenInvalid:
                await self.generate_token()
                return await self._upload_file(file, filename)

        return await except_retry(callback, retries=retries)

    async def _upload_file(self, file: bytes, filename: str) -> StellaFile:
        data = aiohttp.FormData()
        extension, _ = mimetypes.guess_type(filename)
        data.add_field('file', io.BytesIO(file), filename=filename, content_type=extension)
        data.add_field('name', filename)

        values = await self._request("POST", "/files/", data=data)
        return StellaFile.from_api(values, file)

    async def is_nsfw(self, query: str):
        return await self._request("POST", "/simple_nsfw_detection/", data={"query": query})

    async def execute_python(self, code: str):
        return await self._request("POST", "/execute_python/", data={"code": code})

    async def close(self):
        await self.http.close()




