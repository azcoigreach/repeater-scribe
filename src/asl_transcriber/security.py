from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from threading import Lock
from time import monotonic
from uuid import uuid4

from fastapi import HTTPException, Request
from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from asl_transcriber.auth import Principal, audit_event, authenticate_request
from asl_transcriber.config import settings


class RequestBodyTooLarge(Exception):
    pass


class SlidingWindowLimiter:
    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, key: str, limit: int, window_seconds: float = 60.0) -> bool:
        now = monotonic()
        with self._lock:
            events = self._events[key]
            while events and now - events[0] >= window_seconds:
                events.popleft()
            if len(events) >= limit:
                return False
            events.append(now)
            if not events:
                self._events.pop(key, None)
            return True


class SseConnectionLimiter:
    def __init__(self) -> None:
        self._counts: dict[str, int] = defaultdict(int)
        self._lock = asyncio.Lock()

    async def acquire(self, identity: str) -> None:
        async with self._lock:
            if self._counts[identity] >= settings.sse_connections_per_identity:
                raise HTTPException(status_code=429, detail="Too many live event connections")
            self._counts[identity] += 1

    async def release(self, identity: str) -> None:
        async with self._lock:
            self._counts[identity] = max(0, self._counts[identity] - 1)
            if self._counts[identity] == 0:
                self._counts.pop(identity, None)


sse_connections = SseConnectionLimiter()


class SecurityMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.limiter = SlidingWindowLimiter()

    @staticmethod
    def _client_ip(scope: Scope) -> str:
        client = scope.get("client")
        return str(client[0]) if client else "unknown"

    @staticmethod
    def _apply_headers(message: Message, path: str, request_id: str) -> None:
        headers = MutableHeaders(scope=message)
        headers["X-Content-Type-Options"] = "nosniff"
        headers["Referrer-Policy"] = "no-referrer"
        headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=(), usb=(), serial=()"
        )
        headers["X-Frame-Options"] = "DENY"
        headers["Cross-Origin-Opener-Policy"] = "same-origin"
        headers["Cross-Origin-Resource-Policy"] = "same-origin"
        headers["Content-Security-Policy"] = (
            "default-src 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'; "
            "form-action 'self'; script-src 'self'; style-src 'self'; img-src 'self' https:; "
            "media-src 'self'; connect-src 'self'"
        )
        headers["X-Request-ID"] = request_id
        if settings.deployment_mode == "internet":
            headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if path == "/" or path.startswith(("/api/", "/ui/", "/auth/")):
            headers["Cache-Control"] = "no-store"

    async def _reject(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        status: int,
        detail: str,
        path: str,
        request_id: str,
    ) -> None:
        response = JSONResponse({"detail": detail}, status_code=status)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                self._apply_headers(message, path, request_id)
            await send(message)

        await response(scope, receive, send_with_headers)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        path = request.url.path
        request_id = request.headers.get("x-request-id", str(uuid4()))[:128]
        content_length = request.headers.get("content-length")
        if (
            content_length
            and content_length.isdigit()
            and int(content_length) > settings.request_body_max_bytes
        ):
            await self._reject(
                scope,
                receive,
                send,
                413,
                "Request body is too large",
                path,
                request_id,
            )
            return

        has_credentials = bool(
            request.headers.get("authorization")
            or request.headers.get("x-api-key")
            or request.cookies.get(settings.session_cookie_name)
        )
        principal = (
            await asyncio.to_thread(authenticate_request, request)
            if has_credentials
            else authenticate_request(request)
        )
        identity = principal.subject if principal is not None else self._client_ip(scope)
        safe_method = request.method in {"GET", "HEAD", "OPTIONS"}
        limit: int | None = None
        bucket = "request"
        if path in {"/health", "/api/v1/health"}:
            pass
        elif path.startswith("/auth/"):
            limit = settings.anonymous_rate_per_minute
            bucket = "auth"
        elif path.startswith(
            ("/api/v1/node", "/api/v1/nodes", "/ui/node", "/ui/nodes")
        ) and not safe_method:
            limit = settings.control_rate_per_minute
            bucket = "control"
        elif principal is None:
            limit = settings.anonymous_rate_per_minute
            bucket = "anonymous"
        elif not safe_method:
            limit = settings.request_rate_per_minute
            bucket = "request"
        else:
            # Authenticated reads include dashboard refreshes and SSE handshakes.
            # Authorization still protects their data; throttling them makes a
            # busy local node look stale without reducing control-plane risk.
            pass
        if limit is not None and not self.limiter.allow(f"{bucket}:{identity}", limit):
            audit_event(
                actor=principal.identity if principal else "anonymous",
                auth_source=principal.auth_source if principal else "none",
                action="rate_limit",
                outcome="denied",
                request=request,
                detail=bucket,
            )
            await self._reject(
                scope,
                receive,
                send,
                429,
                "Request rate limit exceeded",
                path,
                request_id,
            )
            return

        status_code = 500

        async def send_with_headers(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                self._apply_headers(message, path, request_id)
            await send(message)

        buffered_messages: list[Message] = []
        received_bytes = 0
        while True:
            message = await receive()
            buffered_messages.append(message)
            if message["type"] != "http.request":
                break
            received_bytes += len(message.get("body", b""))
            if received_bytes > settings.request_body_max_bytes:
                await self._reject(
                    scope,
                    receive,
                    send,
                    413,
                    "Request body is too large",
                    path,
                    request_id,
                )
                return
            if not message.get("more_body", False):
                break

        async def receive_validated() -> Message:
            if buffered_messages:
                return buffered_messages.pop(0)
            return {"type": "http.disconnect"}

        try:
            await self.app(scope, receive_validated, send_with_headers)
        except RequestBodyTooLarge:
            await self._reject(
                scope,
                receive,
                send,
                413,
                "Request body is too large",
                path,
                request_id,
            )
        finally:
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                current = getattr(request.state, "principal", None)
                audit_event(
                    actor=current.identity if isinstance(current, Principal) else "anonymous",
                    auth_source=current.auth_source if isinstance(current, Principal) else "none",
                    action="http_write",
                    outcome="allowed" if status_code < 400 else "denied",
                    request=request,
                    detail=f"status={status_code}",
                )
