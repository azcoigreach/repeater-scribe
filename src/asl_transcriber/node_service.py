from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence

from asl_transcriber.ami import (
    AmiConnectionState,
    AmiError,
    AmiFrame,
    AmiResponse,
    PersistentAmiClient,
)
from asl_transcriber.config import Settings
from asl_transcriber.node_control import NodeState, normalize_app_rpt_event


class NodeMonitor:
    """Own the persistent AMI client and publish normalized node-state changes."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = PersistentAmiClient(
            settings.ami_host,
            settings.ami_port,
            settings.ami_username,
            settings.ami_secret,
            timeout=settings.ami_timeout_seconds,
            state_callback=self._on_connection_state,
            event_callback=self._on_event,
            authenticated_callback=self.refresh_baseline,
            reconnect_max_seconds=settings.ami_reconnect_max_seconds,
        )
        self.states: dict[str, NodeState] = {}
        self._subscribers: set[asyncio.Queue[dict[str, object]]] = set()
        self._baseline_lock = asyncio.Lock()

    async def start(self) -> None:
        await self.client.start()

    async def stop(self) -> None:
        await self.client.stop()
        self._subscribers.clear()

    def subscribe(self) -> asyncio.Queue[dict[str, object]]:
        queue: asyncio.Queue[dict[str, object]] = asyncio.Queue(maxsize=100)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, object]]) -> None:
        self._subscribers.discard(queue)

    def state(self, home: str) -> NodeState:
        return self.states.setdefault(home, NodeState(home_node=home, ami_state=self.client.state.value))

    async def _on_connection_state(self, state: AmiConnectionState) -> None:
        for node in self.states.values():
            node.ami_state = state.value
            await self._publish("ami_state", node)

    async def _on_event(self, frame: AmiFrame) -> None:
        home = frame.get("Node")
        if not home:
            return
        current = self.state(home)
        self.states[home] = normalize_app_rpt_event(current, frame)
        await self._publish((frame.event or "event").lower(), self.states[home])

    async def refresh_baseline(self) -> None:
        async with self._baseline_lock:
            try:
                response = await self.client.execute("RptStatus", Command="RptStat")
            except (AmiError, ConnectionError, OSError, TimeoutError):
                return
            nodes = self._node_ids(response)
            for home in nodes:
                self.states.setdefault(home, NodeState(home_node=home))
                responses = await asyncio.gather(
                    self.client.execute("RptStatus", Command="XStat", Node=home),
                    self.client.execute("RptStatus", Command="SawStat", Node=home),
                    self.client.execute("RptStatus", Command="NodeStat", Node=home),
                    self.client.execute("Command", Command=f"rpt show variables {home}"),
                    return_exceptions=True,
                )
                self._apply_baseline(home, responses)
                await self._publish("baseline", self.states[home])

    @staticmethod
    def _node_ids(response: AmiResponse) -> list[str]:
        values = [response.headers.get("Node", "")]
        values.extend(message.get("Node", "") for message in response.messages)
        return sorted({value for value in values if value.isdigit()})

    def _apply_baseline(self, home: str, responses: Sequence[object]) -> None:
        state = self.state(home)
        for response in responses:
            if not isinstance(response, AmiResponse):
                continue
            values: list[tuple[str, str]] = []
            for key in ("RPT_ALINKS", "RPT_RXKEYED", "RPT_TXKEYED"):
                value = response.headers.get(key)
                if value:
                    values.append((key, value))
                values.extend((key, message[key]) for message in response.messages if key in message)
            for name, value in values:
                frame = AmiFrame({"event": [name], "eventvalue": [value], "node": [home]})
                state = normalize_app_rpt_event(state, frame)
        self.states[home] = state

    async def _publish(self, event: str, state: NodeState) -> None:
        payload: dict[str, object] = {
            "event": event,
            "home_node": state.home_node,
            "state": self.serialize(state),
        }
        for queue in tuple(self._subscribers):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                    queue.put_nowait(payload)
                except asyncio.QueueEmpty:
                    pass

    @staticmethod
    def serialize(state: NodeState) -> dict[str, object]:
        return {
            "home_node": state.home_node,
            "links": [
                {"identifier": link.identifier, "mode": link.mode, "keyed": link.keyed}
                for link in state.links.values()
            ],
            "local_rx_keyed": state.local_rx_keyed,
            "transmitter_keyed": state.transmitter_keyed,
            "ami_state": state.ami_state,
            "updated_at": state.updated_at.isoformat(),
            "keyed": bool(state.keyed_links),
            "collision": state.collision,
        }

    async def events(self, queue: asyncio.Queue[dict[str, object]]) -> AsyncIterator[str]:
        yield "event: ready\ndata: {}\n\n"
        try:
            while True:
                payload = await queue.get()
                yield f"event: node\ndata: {json.dumps(payload)}\n\n"
        finally:
            self.unsubscribe(queue)
