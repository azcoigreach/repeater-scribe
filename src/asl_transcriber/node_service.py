from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from functools import partial

from asl_transcriber.ami import AmiConnectionState, AmiError, AmiFrame, PersistentAmiClient
from asl_transcriber.config import Settings
from asl_transcriber.node_control import (
    AdjacentLink,
    NodeState,
    RemoteKeyTransition,
    normalize_app_rpt_event,
    parse_alinks,
    parse_xstat_snapshot,
)

REFRESH_EVENTS = {"RPT_ALINKS", "NODECONN", "NODEDISCONN", "HANGUP", "FULLYBOOTED"}


class NodeStateService:
    """Backend-owned authority for app_rpt direct-link state."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.home_nodes = [item.strip() for item in settings.ami_node_id.split(",") if item.strip()]
        if not self.home_nodes:
            self.home_nodes = [settings.ami_node_id]
        self.states = {home: NodeState(home_node=home) for home in self.home_nodes}
        self.clients: dict[str, PersistentAmiClient] = {}
        for home in self.home_nodes:
            self.clients[home] = PersistentAmiClient(
                settings.ami_host,
                settings.ami_port,
                settings.ami_username,
                settings.ami_secret,
                timeout=settings.ami_timeout_seconds,
                state_callback=partial(self._on_connection_state, home),
                event_callback=partial(self._on_event, home),
                authenticated_callback=partial(self.request_reconcile, home, immediate=True),
                reconnect_max_seconds=settings.ami_reconnect_max_seconds,
            )
        self._subscribers: dict[asyncio.Queue[dict[str, object]], str | None] = {}
        self._triggers = {home: asyncio.Event() for home in self.home_nodes}
        self._immediate = {home: False for home in self.home_nodes}
        self._locks = {home: asyncio.Lock() for home in self.home_nodes}
        self._workers: dict[str, asyncio.Task[None]] = {}
        self._repair_task: asyncio.Task[None] | None = None
        self._keyed_started: dict[tuple[str, str], datetime] = {}
        self.transitions: list[RemoteKeyTransition] = []
        self._running = False

    @property
    def client(self) -> PersistentAmiClient:
        """Compatibility access to the configured primary node's persistent client."""
        return self.clients[self.home_nodes[0]]

    def client_for(self, home: str) -> PersistentAmiClient:
        try:
            return self.clients[home]
        except KeyError as error:
            raise AmiError(f"Home node {home} is not configured") from error

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._workers = {
            home: asyncio.create_task(self._reconciliation_worker(home)) for home in self.home_nodes
        }
        self._repair_task = asyncio.create_task(self._repair_loop())
        await asyncio.gather(*(client.start() for client in self.clients.values()))

    async def stop(self) -> None:
        self._running = False
        for task in self._workers.values():
            task.cancel()
        if self._repair_task is not None:
            self._repair_task.cancel()
        await asyncio.gather(*self._workers.values(), return_exceptions=True)
        if self._repair_task is not None:
            await asyncio.gather(self._repair_task, return_exceptions=True)
            self._repair_task = None
        self._workers.clear()
        await asyncio.gather(*(client.stop() for client in self.clients.values()))
        self._subscribers.clear()

    def state(self, home: str) -> NodeState:
        return self.states.setdefault(home, NodeState(home_node=home))

    def subscribe(self, home: str | None = None) -> asyncio.Queue[dict[str, object]]:
        queue: asyncio.Queue[dict[str, object]] = asyncio.Queue(maxsize=100)
        self._subscribers[queue] = home
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, object]]) -> None:
        self._subscribers.pop(queue, None)

    async def _on_connection_state(self, home: str, state: AmiConnectionState) -> None:
        current = self.state(home)
        stale = state is not AmiConnectionState.AUTHENTICATED or current.stale
        links = {
            identifier: replace(link, stale=stale or link.stale)
            for identifier, link in current.links.items()
        }
        next_state = replace(current, links=links, ami_state=state.value, stale=stale)
        if self._visible(next_state) != self._visible(current):
            self.states[home] = next_state
            await self._publish_state("ami_state", next_state)
        else:
            self.states[home] = next_state

    async def _on_event(self, connection_home: str, frame: AmiFrame) -> None:
        event = (frame.event or "").upper()
        frame_home = frame.get("Node")
        if frame_home and frame_home != connection_home:
            return
        current = self.state(connection_home)
        if event in {"RPT_ALINKS", "RPT_RXKEYED", "RPT_TXKEYED"}:
            next_state = normalize_app_rpt_event(current, frame)
            next_state.ami_state = current.ami_state
            if event == "RPT_ALINKS":
                next_state.links = self._preserve_link_times(current.links, next_state.links)
                next_state.links = {
                    identifier: replace(link, stale=current.stale)
                    for identifier, link in next_state.links.items()
                }
            if self._visible(next_state) != self._visible(current):
                self.states[connection_home] = next_state
                await self._publish_state(event.casefold(), next_state)
        if event in REFRESH_EVENTS:
            self.request_reconcile(connection_home)

    def request_reconcile(self, home: str, *, immediate: bool = False) -> None:
        if home not in self._triggers:
            return
        self._immediate[home] = self._immediate[home] or immediate
        self._triggers[home].set()

    async def _repair_loop(self) -> None:
        while True:
            await asyncio.sleep(self.settings.ami_reconcile_seconds)
            for home in self.home_nodes:
                self.request_reconcile(home, immediate=True)

    async def _reconciliation_worker(self, home: str) -> None:
        trigger = self._triggers[home]
        while True:
            await trigger.wait()
            trigger.clear()
            immediate = self._immediate[home]
            self._immediate[home] = False
            if not immediate:
                await asyncio.sleep(self.settings.ami_event_debounce_seconds)
                trigger.clear()
            await self.reconcile(home)

    async def reconcile(self, home: str) -> bool:
        """Refresh one node once; failures retain last-known links and mark them stale."""
        async with self._locks[home]:
            client = self.client_for(home)
            if client.state is not AmiConnectionState.AUTHENTICATED:
                await self._mark_stale(home)
                return False
            try:
                xstat = await client.execute("RptStatus", Command="XStat", Node=home)
                if not xstat.success:
                    links = await self._fallback_alinks(client, home)
                    topology: list[str] = []
                else:
                    sawstat = await client.execute("RptStatus", Command="SawStat", Node=home)
                    links, topology = parse_xstat_snapshot(xstat, sawstat)
                await self._apply_snapshot(home, links, topology)
                return True
            except (AmiError, ConnectionError, OSError, TimeoutError, ValueError):
                await self._mark_stale(home)
                return False

    async def _fallback_alinks(
        self, client: PersistentAmiClient, home: str
    ) -> dict[str, AdjacentLink]:
        response = await client.execute("Command", Command=f"rpt show variables {home}")
        if not response.success and response.headers.get("Response", "").casefold() != "follows":
            raise AmiError("rpt show variables fallback failed")
        value: str | None = None
        for line in response.values("Output") + response.values("RPT_ALINKS"):
            match = re.search(r"\bRPT_ALINKS\s*[=:]\s*(.*)$", line, re.IGNORECASE)
            if match:
                value = match.group(1).strip()
                break
            if line and not response.values("Output"):
                value = line.strip()
        if value is None:
            raise ValueError("RPT_ALINKS was absent from fallback response")
        return {link.identifier: link for link in parse_alinks(value)}

    async def _apply_snapshot(
        self, home: str, links: dict[str, AdjacentLink], topology: list[str]
    ) -> None:
        current = self.state(home)
        links = self._preserve_link_times(current.links, links)
        timestamp = datetime.now(UTC)
        transitions = self._key_transitions(home, current.links, links, timestamp)
        next_state = NodeState(
            home_node=home,
            links=links,
            topology=topology,
            local_rx_keyed=current.local_rx_keyed,
            transmitter_keyed=current.transmitter_keyed,
            ami_state=self.client_for(home).state.value,
            stale=False,
            updated_at=timestamp,
        )
        changed = self._visible(next_state) != self._visible(current)
        self.states[home] = next_state
        if changed:
            await self._publish_state("snapshot", next_state)
        for transition in transitions:
            self.transitions.append(transition)
            await self._publish_transition(transition)

    @staticmethod
    def _preserve_link_times(
        previous: dict[str, AdjacentLink], current: dict[str, AdjacentLink]
    ) -> dict[str, AdjacentLink]:
        result: dict[str, AdjacentLink] = {}
        for identifier, link in current.items():
            old = previous.get(identifier)
            result[identifier] = replace(
                link,
                connected_at=old.connected_at if old else link.connected_at,
                link_mode=link.link_mode or (old.link_mode if old else None),
            )
        return result

    def _key_transitions(
        self,
        home: str,
        previous: dict[str, AdjacentLink],
        current: dict[str, AdjacentLink],
        timestamp: datetime,
    ) -> list[RemoteKeyTransition]:
        transitions: list[RemoteKeyTransition] = []
        identifiers = previous.keys() | current.keys()
        for identifier in identifiers:
            old = previous.get(identifier)
            new = current.get(identifier)
            key = (home, identifier)
            if old is not None and old.keyed is False and new is not None and new.keyed is True:
                self._keyed_started[key] = timestamp
                transitions.append(
                    RemoteKeyTransition("remote_keyed_started", home, identifier, timestamp)
                )
            elif old is not None and old.keyed is True and (new is None or new.keyed is False):
                started = self._keyed_started.pop(key, None)
                duration = int((timestamp - started).total_seconds()) if started else None
                transitions.append(
                    RemoteKeyTransition("remote_keyed_ended", home, identifier, timestamp, duration)
                )
            elif new is not None and new.keyed is True and key not in self._keyed_started:
                seconds = new.seconds_since_keyed or 0
                self._keyed_started[key] = timestamp.replace(microsecond=0) - timedelta(
                    seconds=seconds
                )
        return transitions

    async def _mark_stale(self, home: str) -> None:
        current = self.state(home)
        links = {
            identifier: replace(link, stale=True) for identifier, link in current.links.items()
        }
        next_state = replace(current, links=links, stale=True, updated_at=datetime.now(UTC))
        if self._visible(next_state) != self._visible(current):
            self.states[home] = next_state
            await self._publish_state("stale", next_state)
        else:
            self.states[home] = next_state

    @staticmethod
    def _visible(state: NodeState) -> tuple[object, ...]:
        return (
            state.home_node,
            state.ami_state,
            state.stale,
            state.local_rx_keyed,
            state.transmitter_keyed,
            tuple(state.topology),
            tuple(
                (
                    link.identifier,
                    link.node_number,
                    link.callsign,
                    link.display_name,
                    link.peer,
                    link.direction,
                    link.link_mode,
                    link.connection_state,
                    link.keyed,
                    link.seconds_since_keyed,
                    link.seconds_since_unkeyed,
                    link.connected_at,
                    link.stale,
                    link.source,
                )
                for link in sorted(state.links.values(), key=lambda item: item.identifier)
            ),
        )

    async def _publish_state(self, event: str, state: NodeState) -> None:
        await self._publish(
            {"event": event, "home_node": state.home_node, "state": self.serialize(state)}
        )

    async def _publish_transition(self, transition: RemoteKeyTransition) -> None:
        await self._publish(
            {
                "event": transition.event,
                "home_node": transition.home_node,
                "remote_identifier": transition.remote_identifier,
                "timestamp": transition.timestamp.isoformat(),
                "duration_seconds": transition.duration_seconds,
            }
        )

    async def _publish(self, payload: dict[str, object]) -> None:
        home = str(payload.get("home_node", ""))
        for queue, subscribed_home in tuple(self._subscribers.items()):
            if subscribed_home is not None and subscribed_home != home:
                continue
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                    queue.put_nowait(payload)
                except asyncio.QueueEmpty:
                    pass

    @staticmethod
    def serialize_link(link: AdjacentLink) -> dict[str, object]:
        return {
            "identifier": link.identifier,
            "node_number": link.node_number,
            "callsign": link.callsign,
            "display_name": link.display_name,
            "peer": link.peer,
            "direction": link.direction,
            "link_mode": link.link_mode,
            "connection_state": link.connection_state,
            "keyed": link.keyed,
            "seconds_since_keyed": link.seconds_since_keyed,
            "seconds_since_unkeyed": link.seconds_since_unkeyed,
            "connected_at": link.connected_at.isoformat(),
            "updated_at": link.updated_at.isoformat(),
            "stale": link.stale,
            "source": link.source,
        }

    @classmethod
    def serialize(cls, state: NodeState) -> dict[str, object]:
        ordered_links = sorted(state.links.values(), key=lambda item: item.identifier)
        links = [cls.serialize_link(link) for link in ordered_links]
        connected_nodes = [link.identifier for link in ordered_links]
        talkers = [link.identifier for link in state.links.values() if link.keyed is True]
        return {
            "home_node": state.home_node,
            "links": links,
            "connections": links,
            "topology": state.topology,
            "local_rx_keyed": state.local_rx_keyed,
            "transmitter_keyed": state.transmitter_keyed,
            "ami_state": state.ami_state,
            "ami_connected": state.ami_state == AmiConnectionState.AUTHENTICATED.value,
            "stale": state.stale,
            "updated_at": state.updated_at.isoformat(),
            "keyed": bool(state.keyed_links),
            "collision": state.collision,
            "connected_nodes": connected_nodes,
            "connected_stations": [
                {
                    "id": link.identifier,
                    "name": link.display_name,
                    "channel": link.peer or "",
                    "state": link.connection_state,
                }
                for link in state.links.values()
            ],
            "talkers": talkers,
            "active_channels": [],
        }

    async def events(
        self, home: str, queue: asyncio.Queue[dict[str, object]]
    ) -> AsyncIterator[str]:
        initial = {
            "event": "snapshot",
            "home_node": home,
            "state": self.serialize(self.state(home)),
        }
        yield f"event: node-state\ndata: {json.dumps(initial)}\n\n"
        try:
            while True:
                try:
                    payload = await asyncio.wait_for(
                        queue.get(), timeout=self.settings.ami_sse_heartbeat_seconds
                    )
                except TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                transition = str(payload.get("event", "")).startswith("remote_keyed_")
                event_name = "node-transition" if transition else "node-state"
                yield f"event: {event_name}\ndata: {json.dumps(payload)}\n\n"
        finally:
            self.unsubscribe(queue)


# Compatibility for code built against the foundation branch name.
NodeMonitor = NodeStateService
