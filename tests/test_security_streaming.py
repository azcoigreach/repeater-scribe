from __future__ import annotations

import asyncio

import pytest

from asl_transcriber.config import settings
from asl_transcriber.security import SecurityMiddleware


def invoke(messages: list[dict], headers: list[tuple[bytes, bytes]] | None = None) -> tuple[list[dict], int]:
    consumed = 0

    async def handler(_scope, receive, send) -> None:
        nonlocal consumed
        while True:
            message = await receive()
            if message["type"] != "http.request":
                return
            consumed += len(message.get("body", b""))
            if not message.get("more_body", False):
                await send({"type": "http.response.start", "status": 204, "headers": []})
                await send({"type": "http.response.body", "body": b""})
                return

    sent: list[dict] = []
    iterator = iter(messages)

    async def receive() -> dict:
        return next(iterator, {"type": "http.disconnect"})

    async def send(message: dict) -> None:
        sent.append(message)

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "POST", "scheme": "http", "path": "/health", "raw_path": b"/health",
        "query_string": b"", "headers": headers or [], "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
    }
    asyncio.run(SecurityMiddleware(handler)(scope, receive, send))
    return sent, consumed


@pytest.mark.parametrize(
    ("chunks", "expected_status", "expected_consumed"),
    [
        ([b"x" * settings.request_body_max_bytes], 204, settings.request_body_max_bytes),
        ([b"x" * (settings.request_body_max_bytes + 1)], 413, 0),
        ([b"x" * (settings.request_body_max_bytes - 1), b"xx"], 413, 0),
        ([b"x" * (settings.request_body_max_bytes - 1), b"x", b"x"], 413, 0),
    ],
)
def test_streamed_body_limit_is_enforced_while_consuming(chunks, expected_status, expected_consumed) -> None:
    messages = [
        {"type": "http.request", "body": chunk, "more_body": index < len(chunks) - 1}
        for index, chunk in enumerate(chunks)
    ]
    sent, consumed = invoke(messages)
    assert sent[0]["status"] == expected_status
    assert consumed == expected_consumed


def test_content_length_rejection_happens_before_handler() -> None:
    sent, consumed = invoke(
        [{"type": "http.request", "body": b"x", "more_body": False}],
        [(b"content-length", str(settings.request_body_max_bytes + 10).encode())],
    )
    assert sent[0]["status"] == 413
    assert consumed == 0


def test_understated_content_length_does_not_bypass_stream_limit() -> None:
    sent, consumed = invoke(
        [{"type": "http.request", "body": b"x" * (settings.request_body_max_bytes + 1), "more_body": False}],
        [(b"content-length", b"1")],
    )
    assert sent[0]["status"] == 413
    assert consumed == 0


def test_incomplete_stream_is_not_treated_as_an_overflow() -> None:
    sent, consumed = invoke([{"type": "http.request", "body": b"partial", "more_body": True}])
    assert sent == []
    assert consumed == len(b"partial")
