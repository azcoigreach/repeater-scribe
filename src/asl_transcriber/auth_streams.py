"""Revalidate long-lived streams even when their underlying source is idle."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import aclosing, suppress

from asl_transcriber.auth import Principal, audit_event, refresh_principal

STREAM_AUTH_INTERVAL = 1.0


async def protected_stream[T](
    source: AsyncGenerator[T, None], principal: Principal
) -> AsyncGenerator[T, None]:
    # Keep the pending read alive through checks; cancelling it at each heartbeat
    # would close the underlying subscription and lose queued events.
    async with aclosing(source):
        pending = asyncio.ensure_future(anext(source))
        try:
            while True:
                done, _ = await asyncio.wait({pending}, timeout=STREAM_AUTH_INTERVAL)
                current = await asyncio.to_thread(refresh_principal, principal)
                if current is None or current.role != principal.role:
                    audit_event(
                        actor=principal.subject,
                        account_id=principal.account_id,
                        auth_source=principal.auth_source,
                        action="stream_authorization",
                        outcome="denied",
                        detail="Credential expired, revoked, or role changed",
                    )
                    return
                if done:
                    try:
                        item = pending.result()
                    except StopAsyncIteration:
                        return
                    yield item
                    pending = asyncio.ensure_future(anext(source))
        finally:
            pending.cancel()
            with suppress(asyncio.CancelledError, StopAsyncIteration):
                await pending
