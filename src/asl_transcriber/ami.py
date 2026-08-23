from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import Protocol


class AmiError(RuntimeError):
    """Raised when an AMI connection or action fails."""


class SocketFactory(Protocol):
    def __call__(self, address: tuple[str, int], timeout: float) -> socket.socket: ...


@dataclass(frozen=True)
class AmiResponse:
    headers: dict[str, str]
    messages: list[dict[str, str]]

    @property
    def success(self) -> bool:
        return self.headers.get("Response", "").lower() == "success"


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
            self._write_action(connection, "Login", Username=self.username, Secret=self.secret, Events="off")
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
