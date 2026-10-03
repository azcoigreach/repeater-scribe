"""Closing/reloading dashboards must release idle SSE reads, not exhaust read workers."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from queue import Queue
from types import SimpleNamespace

from asl_transcriber import auth_streams, main
from asl_transcriber.auth import Principal
from asl_transcriber.security import sse_connections


def test_dashboard_reload_releases_idle_event_worker(monkeypatch):
    principal = Principal("fixture-dashboard-reload", "fixture", "viewer", "local")
    monkeypatch.setattr(auth_streams, "refresh_principal", lambda _: principal)

    async def reload_once():
        loop = asyncio.get_running_loop()
        waiting = asyncio.Event()
        released = []

        class IdleQueue(Queue):
            def get(self, *args, **kwargs):
                loop.call_soon_threadsafe(waiting.set)
                return super().get(*args, **kwargs)

        queue = IdleQueue()
        monkeypatch.setattr(main, "current_runtime", lambda: SimpleNamespace(
            subscribe=lambda: queue, unsubscribe=lambda q: released.append(q)
        ))

        async def connected():
            return False

        response = await main.events(SimpleNamespace(is_disconnected=connected), principal)
        stream = response.body_iterator
        assert "event: ready" in await anext(stream)
        read = asyncio.create_task(anext(stream))
        await asyncio.wait_for(waiting.wait(), 1)
        read.cancel()
        with suppress(asyncio.CancelledError):
            await read
        await stream.aclose()
        assert released == [queue]
        assert principal.subject not in sse_connections._counts
        # Authentication for subsequent Apply/refresh requests uses this pool.
        assert await asyncio.wait_for(asyncio.to_thread(lambda: True), 1)

    async def exercise():
        loop = asyncio.get_running_loop()
        # A single worker makes starvation deterministic, as opposed to relying
        # on rapid page reloads exceeding a machine-dependent pool size.
        loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
        for _ in range(3):
            await reload_once()

    asyncio.run(exercise())
