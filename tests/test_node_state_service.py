from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

from asl_transcriber.ami import AmiConnectionState, AmiFrame, AmiResponse
from asl_transcriber.config import Settings
from asl_transcriber.node_control import RemoteKeyTransition, parse_alinks, parse_xstat_snapshot
from asl_transcriber.node_service import NodeStateService


def response(status: str = "Success", **headers: list[str]) -> AmiResponse:
    raw = {"response": [status], **{key.casefold(): value for key, value in headers.items()}}
    return AmiResponse({"Response": status}, [], raw_headers=raw)


class FakeClient:
    def __init__(self, replies: Iterator[AmiResponse]) -> None:
        self.state = AmiConnectionState.AUTHENTICATED
        self.replies = replies
        self.actions: list[tuple[str, dict[str, str]]] = []

    async def execute(self, action: str, **headers: str) -> AmiResponse:
        self.actions.append((action, headers))
        return next(self.replies)


def service_with(*replies: AmiResponse) -> tuple[NodeStateService, FakeClient]:
    service = NodeStateService(
        Settings(
            ami_node_id="668390",
            ami_secret="secret",
            ami_event_debounce_seconds=0.01,
            ami_reconcile_seconds=60,
        )
    )
    client = FakeClient(iter(replies))
    service.clients["668390"] = client  # type: ignore[assignment]
    return service, client


def test_service_owns_one_persistent_client_per_configured_home_node() -> None:
    service = NodeStateService(Settings(ami_node_id="668390,674982", ami_secret="secret"))

    assert service.home_nodes == ["668390", "674982"]
    assert set(service.clients) == {"668390", "674982"}


def test_successful_empty_snapshot_clears_cached_connections() -> None:
    async def scenario() -> None:
        service, _ = service_with(response(), response())
        state = service.state("668390")
        state.links = {link.identifier: link for link in parse_alinks("1,674982TU")}

        assert await service.reconcile("668390") is True
        assert service.state("668390").links == {}
        assert service.state("668390").stale is False

    asyncio.run(scenario())


def test_stable_connected_at_survives_consecutive_snapshots() -> None:
    async def scenario() -> None:
        first_x = response(Conn=["674982 192.0.2.4 0 OUT 00:00:20 ESTABLISHED"])
        first_saw = response(Conn=["674982 0 8 1"])
        second_x = response(Conn=["674982 192.0.2.4 0 OUT 00:00:25 ESTABLISHED"])
        second_saw = response(Conn=["674982 0 13 1"])
        service, _ = service_with(first_x, first_saw, second_x, second_saw)

        await service.reconcile("668390")
        connected_at = service.state("668390").links["674982"].connected_at
        await asyncio.sleep(0)
        await service.reconcile("668390")

        assert service.state("668390").links["674982"].connected_at == connected_at

    asyncio.run(scenario())


def test_sparse_alinks_key_event_preserves_xstat_connection_metadata() -> None:
    async def scenario() -> None:
        service, _ = service_with()
        links, _ = parse_xstat_snapshot(
            response(Conn=["674982 192.0.2.4 0 OUT 00:00:20 ESTABLISHED"]),
            response(Conn=["674982 0 8 1"]),
        )
        service.state("668390").links = links
        service.state("668390").stale = False

        await service._on_event(
            "668390",
            AmiFrame(
                {
                    "event": ["RPT_ALINKS"],
                    "node": ["668390"],
                    "eventvalue": ["1,674982RK"],
                }
            ),
        )

        link = service.state("668390").links["674982"]
        assert link.keyed is True
        assert link.peer == "192.0.2.4"
        assert link.direction == "out"

    asyncio.run(scenario())


def test_keyed_transitions_are_structured_with_duration() -> None:
    async def scenario() -> None:
        service, _ = service_with()
        unkeyed, _ = parse_xstat_snapshot(
            response(Conn=["KM7GHS (none) 0 IN 00:00:10 ESTABLISHED"]),
            response(Conn=["KM7GHS 0 4 1"]),
        )
        keyed, _ = parse_xstat_snapshot(
            response(Conn=["KM7GHS (none) 0 IN 00:00:11 ESTABLISHED"]),
            response(Conn=["KM7GHS 1 0 2"]),
        )
        await service._apply_snapshot("668390", unkeyed, [])
        await service._apply_snapshot("668390", keyed, [])
        service._keyed_started[("668390", "KM7GHS")] = datetime.now(UTC) - timedelta(seconds=3)
        await service._apply_snapshot("668390", unkeyed, [])

        assert [event.event for event in service.transitions] == [
            "remote_keyed_started",
            "remote_keyed_ended",
        ]
        assert service.transitions[-1].home_node == "668390"
        assert service.transitions[-1].remote_identifier == "KM7GHS"
        assert service.transitions[-1].duration_seconds == 3

    asyncio.run(scenario())


def test_keyed_transition_callback_runs_before_the_event_is_published() -> None:
    async def scenario() -> None:
        calls: list[str] = []

        async def persist(transition: RemoteKeyTransition) -> None:
            calls.append(f"persist:{transition.event}")

        service = NodeStateService(
            Settings(ami_node_id="668390", ami_secret="secret"),
            transition_callback=persist,
        )
        queue = service.subscribe("668390")
        transition = RemoteKeyTransition(
            "remote_keyed_started", "668390", "KM7GHS", datetime.now(UTC)
        )

        await service._publish_transition(transition)
        payload = await queue.get()

        assert calls == ["persist:remote_keyed_started"]
        assert payload["event"] == "remote_keyed_started"

    asyncio.run(scenario())


def test_initial_keyed_snapshot_counts_as_an_observed_keyup() -> None:
    async def scenario() -> None:
        service, _ = service_with()
        keyed, _ = parse_xstat_snapshot(
            response(Conn=["674982 192.0.2.4 0 OUT 00:00:11 ESTABLISHED"]),
            response(Conn=["674982 1 3 0"]),
        )

        await service._apply_snapshot("668390", keyed, [])

        assert [event.event for event in service.transitions] == ["remote_keyed_started"]
        assert service.transitions[0].remote_identifier == "674982"

    asyncio.run(scenario())


def test_rpt_alinks_fast_path_publishes_key_transitions() -> None:
    async def scenario() -> None:
        persisted: list[RemoteKeyTransition] = []

        def persist(transition: RemoteKeyTransition) -> None:
            persisted.append(transition)

        service = NodeStateService(
            Settings(ami_node_id="668390", ami_secret="secret"),
            transition_callback=persist,
        )
        service.state("668390").links = {
            link.identifier: link for link in parse_alinks("1,674982TU")
        }

        await service._on_event(
            "668390",
            AmiFrame({"event": ["RPT_ALINKS"], "node": ["668390"], "eventvalue": ["1,674982TK"]}),
        )
        await service._on_event(
            "668390",
            AmiFrame({"event": ["RPT_ALINKS"], "node": ["668390"], "eventvalue": ["1,674982TU"]}),
        )

        assert [event.event for event in persisted] == [
            "remote_keyed_started",
            "remote_keyed_ended",
        ]

    asyncio.run(scenario())


def test_event_storm_is_coalesced_into_one_reconciliation() -> None:
    async def scenario() -> None:
        replies = (item for _ in range(10) for item in (response(), response()))
        service, client = service_with(*list(replies))
        worker = asyncio.create_task(service._reconciliation_worker("668390"))
        try:
            for _ in range(20):
                service.request_reconcile("668390")
            await asyncio.sleep(0.05)
            assert len(client.actions) == 2
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    asyncio.run(scenario())


def test_authentication_callback_forces_refresh_and_disconnect_marks_stale() -> None:
    async def scenario() -> None:
        service = NodeStateService(Settings(ami_node_id="668390", ami_secret="secret"))
        state = service.state("668390")
        state.links = {link.identifier: link for link in parse_alinks("1,KM7GHSTU")}
        state.stale = False

        callback = service.client.authenticated_callback
        assert callback is not None
        callback()
        assert service._triggers["668390"].is_set()

        await service._on_connection_state("668390", AmiConnectionState.DISCONNECTED)
        assert service.state("668390").stale is True
        assert service.state("668390").links["KM7GHS"].stale is True

    asyncio.run(scenario())


def test_sse_sends_initial_snapshot_then_changed_state() -> None:
    async def scenario() -> None:
        service, _ = service_with()
        queue = service.subscribe("668390")
        stream = service.events("668390", queue)

        initial = await anext(stream)
        await service._publish_state("snapshot", service.state("668390"))
        changed = await anext(stream)
        await stream.aclose()

        assert initial.startswith("event: node-state\n")
        assert '"event": "snapshot"' in initial
        assert changed.startswith("event: node-state\n")
        assert queue not in service._subscribers

    asyncio.run(scenario())


def test_xstat_unavailable_falls_back_to_rpt_alinks() -> None:
    async def scenario() -> None:
        fallback = response("Follows", Output=["RPT_ALINKS=2,674982TU,KM7GHSTK", "--END COMMAND--"])
        service, client = service_with(response("Error"), fallback)

        assert await service.reconcile("668390") is True
        assert list(service.state("668390").links) == ["674982", "KM7GHS"]
        assert service.state("668390").links["KM7GHS"].keyed is True
        assert [action for action, _ in client.actions] == ["RptStatus", "Command"]

    asyncio.run(scenario())
