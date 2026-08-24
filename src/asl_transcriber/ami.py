from __future__ import annotations

import asyncio
import random
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class AmiError(RuntimeError):
    """Raised when an AMI connection or action fails."""


class SocketFactory(Protocol):
    def __call__(self, address: tuple[str, int], timeout: float) -> socket.socket: ...


@dataclass(frozen=True)
class AmiResponse:
    headers: dict[str, str]
    messages: list[dict[str, str]]
    raw_headers: dict[str, list[str]] | None = None

    @property
    def success(self) -> bool:
        return (
            next(
                (value for name, value in self.headers.items() if name.casefold() == "response"),
                "",
            ).lower()
            == "success"
        )

    def values(self, name: str) -> list[str]:
        """Return every value for a header, regardless of AMI header casing."""
        key = name.casefold()
        if self.raw_headers is not None:
            return list(self.raw_headers.get(key, []))
        values = [value for header, value in self.headers.items() if header.casefold() == key]
        for message in self.messages:
            values.extend(value for header, value in message.items() if header.casefold() == key)
        return values


class AmiClient:
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        secret: str,
        timeout: float = 5.0,
        socket_factory: SocketFactory = socket.create_connection,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.secret = secret
        self.timeout = timeout
        self.socket_factory = socket_factory

    def execute(self, action: str, **headers: str) -> AmiResponse:
        connection = self.socket_factory((self.host, self.port), self.timeout)
        with connection:
            connection.settimeout(self.timeout)
            banner = self._read_message(connection)
            if banner.get("Asterisk") is None:
                raise AmiError("AMI server did not provide an Asterisk banner")
            self._write_action(
                connection, "Login", Username=self.username, Secret=self.secret, Events="off"
            )
            login = self._read_response(connection)
            if not login.success:
                raise AmiError(login.headers.get("Message", "AMI login failed"))
            self._write_action(connection, action, **headers)
            response = self._read_response(connection)
            if not response.success:
                raise AmiError(response.headers.get("Message", f"AMI {action} failed"))
            return response

    def ping(self) -> AmiResponse:
        return self.execute("Ping")

    def status(self, channel: str | None = None) -> AmiResponse:
        headers = {"Channel": channel} if channel else {}
        return self.execute("Status", **headers)

    def command(self, command: str) -> AmiResponse:
        if not command.strip():
            raise ValueError("AMI command cannot be empty")
        return self.execute("Command", Command=command)

    @staticmethod
    def _write_action(connection: socket.socket, action: str, **headers: str) -> None:
        lines = [f"Action: {action}"] + [f"{key}: {value}" for key, value in headers.items()]
        connection.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("ascii"))

    def _read_response(self, connection: socket.socket) -> AmiResponse:
        headers = self._read_message(connection)
        messages: list[dict[str, str]] = []
        while True:
            connection.settimeout(0.05)
            try:
                message = self._read_message(connection)
            except (TimeoutError, AmiError):
                break
            if not message:
                break
            messages.append(message)
            if message.get("EventList") == "Complete":
                break
        connection.settimeout(self.timeout)
        return AmiResponse(headers=headers, messages=messages)

    @staticmethod
    def _read_message(connection: socket.socket) -> dict[str, str]:
        data = bytearray()
        while b"\r\n\r\n" not in data:
            chunk = connection.recv(4096)
            if not chunk:
                break
            data.extend(chunk)
            if data.startswith(b"Asterisk ") and b"\r\n" in data:
                break
        if not data:
            raise AmiError("AMI connection closed unexpectedly")
        message = data.decode("utf-8", errors="replace").strip("\r\n")
        headers = {
            key.strip(): value.strip()
            for line in message.split("\r\n")
            if ":" in line
            for key, value in [line.split(":", 1)]
        }
        if message.startswith("Asterisk "):
            headers["Asterisk"] = message.split("\r\n", 1)[0]
        return headers


@dataclass(frozen=True)
class AmiFrame:
    """One AMI frame, retaining repeated headers in arrival order."""

    headers: dict[str, list[str]]

    def get(self, name: str, default: str = "") -> str:
        values = self.headers.get(name.casefold(), [])
        return values[0] if values else default

    def values(self, name: str) -> list[str]:
        return list(self.headers.get(name.casefold(), []))

    @property
    def event(self) -> str | None:
        return self.get("Event") or None

    @property
    def success(self) -> bool:
        return self.get("Response").casefold() == "success"

    def as_dict(self) -> dict[str, str]:
        return {name: values[0] for name, values in self.headers.items() if values}


class AmiFrameParser:
    """Incrementally parse AMI frames without losing coalesced input."""

    delimiter = b"\r\n\r\n"

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[AmiFrame]:
        self._buffer.extend(data)
        frames: list[AmiFrame] = []
        while (end := self._buffer.find(self.delimiter)) >= 0:
            payload = bytes(self._buffer[:end])
            del self._buffer[: end + len(self.delimiter)]
            if payload:
                frames.append(self._parse(payload))
        return frames

    def finish(self) -> None:
        if self._buffer:
            raise AmiError("AMI connection closed during a partial frame")

    @staticmethod
    def _parse(payload: bytes) -> AmiFrame:
        headers: dict[str, list[str]] = {}
        for line in payload.decode("utf-8", errors="replace").split("\r\n"):
            if ":" not in line:
                continue
            name, value = line.split(":", 1)
            headers.setdefault(name.strip().casefold(), []).append(value.strip())
        return AmiFrame(headers)


class AmiConnectionState(StrEnum):
    CONNECTING = "connecting"
    AUTHENTICATED = "authenticated"
    DISCONNECTED = "disconnected"
    ERROR = "error"


@dataclass
class _PendingAction:
    action_id: str
    future: asyncio.Future[AmiResponse]
    frames: list[AmiFrame]
    event_list: bool = False


StateCallback = Callable[[AmiConnectionState], Awaitable[None] | None]
EventCallback = Callable[[AmiFrame], Awaitable[None] | None]
AuthenticatedCallback = Callable[[], Awaitable[None] | None]


class PersistentAmiClient:
    """Persistent AMI connection with serialized writes and ActionID routing."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        secret: str,
        *,
        timeout: float = 10.0,
        state_callback: StateCallback | None = None,
        event_callback: EventCallback | None = None,
        authenticated_callback: AuthenticatedCallback | None = None,
        reconnect: bool = True,
        reconnect_max_seconds: float = 60.0,
        random_source: Callable[[], float] = random.random,
    ) -> None:
        self.host, self.port = host, port
        self.username, self.secret = username, secret
        self.timeout = timeout
        self.state_callback = state_callback
        self.event_callback = event_callback
        self.authenticated_callback = authenticated_callback
        self.reconnect = reconnect
        self.reconnect_max_seconds = reconnect_max_seconds
        self.random_source = random_source
        self.state = AmiConnectionState.DISCONNECTED
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._parser = AmiFrameParser()
        self._frames: list[AmiFrame] = []
        self._callback_tasks: set[asyncio.Task[None]] = set()
        self._write_lock = asyncio.Lock()
        self._pending: dict[str, _PendingAction] = {}
        self._sequence = 0
        self._stopping = False

    async def start(self) -> None:
        self._stopping = False
        if self._reader_task is None:
            self._reader_task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stopping = True
        if self._reader_task is not None:
            self._reader_task.cancel()
            await asyncio.gather(self._reader_task, return_exceptions=True)
            self._reader_task = None
        self._close_connection(AmiError("AMI client stopped"))
        for task in self._callback_tasks:
            task.cancel()
        await asyncio.gather(*self._callback_tasks, return_exceptions=True)
        self._callback_tasks.clear()
        await self._set_state(AmiConnectionState.DISCONNECTED)

    async def execute(self, action: str, **headers: str) -> AmiResponse:
        if self._writer is None:
            raise AmiError("AMI client is not connected")
        self._sequence += 1
        action_id = f"repeater-scribe-{self._sequence}"
        loop = asyncio.get_running_loop()
        pending = _PendingAction(action_id, loop.create_future(), [])
        self._pending[action_id] = pending
        fields = {"Action": action, "ActionID": action_id, **headers}
        try:
            async with self._write_lock:
                self._writer.write(
                    (
                        "\r\n".join(f"{key}: {value}" for key, value in fields.items()) + "\r\n\r\n"
                    ).encode()
                )
                await self._writer.drain()
            return await asyncio.wait_for(pending.future, self.timeout)
        except (TimeoutError, ConnectionError, OSError) as error:
            self._pending.pop(action_id, None)
            raise AmiError(f"AMI action {action} failed: {error}") from error

    async def _run(self) -> None:
        delay = 1.0
        while not self._stopping:
            try:
                await self._connect()
                delay = 1.0
                await self._read_loop()
            except asyncio.CancelledError:
                raise
            except (AmiError, ConnectionError, OSError, TimeoutError) as error:
                await self._set_state(AmiConnectionState.ERROR)
                self._close_connection(error)
            if self._stopping or not self.reconnect:
                break
            await self._set_state(AmiConnectionState.DISCONNECTED)
            await asyncio.sleep(
                min(delay, self.reconnect_max_seconds) * (0.8 + self.random_source() * 0.4)
            )
            delay = min(delay * 2, self.reconnect_max_seconds)

    async def _connect(self) -> None:
        await self._set_state(AmiConnectionState.CONNECTING)
        self._parser = AmiFrameParser()
        self._frames.clear()
        self._reader, self._writer = await asyncio.open_connection(self.host, self.port)
        banner = await self._reader.readline()
        if not banner.startswith(b"Asterisk "):
            raise AmiError("AMI server did not provide an Asterisk banner")
        await self._send_login()
        login = await self._read_login_response()
        if not login.success:
            raise AmiError(login.headers.get("message", ["AMI login failed"])[0])
        await self._set_state(AmiConnectionState.AUTHENTICATED)
        if self.authenticated_callback is not None:
            task = asyncio.create_task(self._run_authenticated_callback())
            self._callback_tasks.add(task)
            task.add_done_callback(self._callback_tasks.discard)

    async def _run_authenticated_callback(self) -> None:
        assert self.authenticated_callback is not None
        result = self.authenticated_callback()
        if asyncio.iscoroutine(result):
            await result

    async def _send_login(self) -> None:
        assert self._writer is not None
        async with self._write_lock:
            self._writer.write(
                f"Action: Login\r\nUsername: {self.username}\r\nSecret: {self.secret}\r\nEvents: on\r\n\r\n".encode()
            )
            await self._writer.drain()

    async def _read_login_response(self) -> AmiFrame:
        assert self._reader is not None
        while True:
            if self._frames:
                frame = self._frames.pop(0)
                if frame.event is None and frame.get("Response"):
                    return frame
                await self._publish(frame)
                continue
            data = await self._reader.read(4096)
            if not data:
                self._parser.finish()
                raise AmiError("AMI connection closed during login")
            frames = self._parser.feed(data)
            self._frames.extend(frames)
            if not frames:
                continue

    async def _read_loop(self) -> None:
        assert self._reader is not None
        while True:
            while self._frames:
                await self._handle_frame(self._frames.pop(0))
            data = await self._reader.read(4096)
            if not data:
                self._parser.finish()
                raise AmiError("AMI connection closed")
            for frame in self._parser.feed(data):
                await self._handle_frame(frame)

    async def _handle_frame(self, frame: AmiFrame) -> None:
        action_id = frame.get("ActionID")
        pending = self._pending.get(action_id)
        if frame.event and pending is None:
            await self._publish(frame)
            return
        if pending is None:
            return
        pending.frames.append(frame)
        response = frame.get("Response")
        event_list = frame.get("EventList").casefold()
        if event_list == "start":
            pending.event_list = True
            return
        if pending.event_list and event_list != "complete":
            return
        if response.casefold() == "follows" and "--END COMMAND--" not in frame.values("Output"):
            return
        if (
            event_list == "complete"
            or "--END COMMAND--" in frame.values("Output")
            or response
            and response.casefold() != "follows"
        ):
            self._finish_pending(pending)

    def _finish_pending(self, pending: _PendingAction) -> None:
        self._pending.pop(pending.action_id, None)
        if pending.future.done():
            return
        first = pending.frames[0]
        headers = {key.title(): values[0] for key, values in first.headers.items() if values}
        messages = [frame.as_dict() for frame in pending.frames[1:]]
        raw_headers: dict[str, list[str]] = {}
        for frame in pending.frames:
            for key, values in frame.headers.items():
                raw_headers.setdefault(key, []).extend(values)
        pending.future.set_result(
            AmiResponse(headers=headers, messages=messages, raw_headers=raw_headers)
        )

    async def _publish(self, frame: AmiFrame) -> None:
        if self.event_callback is not None:
            result = self.event_callback(frame)
            if asyncio.iscoroutine(result):
                await result

    async def _set_state(self, state: AmiConnectionState) -> None:
        self.state = state
        if self.state_callback is not None:
            result = self.state_callback(state)
            if asyncio.iscoroutine(result):
                await result

    def _close_connection(self, error: Exception) -> None:
        for pending in self._pending.values():
            if not pending.future.done():
                pending.future.set_exception(AmiError(str(error)))
        self._pending.clear()
        if self._writer is not None:
            self._writer.close()
        self._reader = None
        self._writer = None
        self._parser = AmiFrameParser()
        self._frames.clear()
