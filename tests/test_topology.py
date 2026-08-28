from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from asl_transcriber.database import Base
from asl_transcriber.favorites import create_favorite
from asl_transcriber.topology import (
    TopologyService,
    apply_topology_snapshot,
    cached_topology_values,
    ensure_topology_crawl,
    next_crawl_work,
    serialize_topology,
)


def snapshot(
    identifier: str, links: list[tuple[str, str]], fetched_at: datetime
) -> dict[str, object]:
    return {
        "remote_identifier": identifier,
        "callsign": f"N{identifier}",
        "description": "Hub",
        "location": "Arizona",
        "active": True,
        "keyed": False,
        "total_keyups": 12,
        "total_tx_seconds": 34,
        "total_kerchunks": 1,
        "uptime_seconds": 100,
        "link_count": len(links),
        "source_reported_at": fetched_at,
        "fetched_at": fetched_at,
        "topology_json": json.dumps(
            {
                "root": {"identifier": identifier, "callsign": f"N{identifier}"},
                "links": [
                    {"identifier": target, "callsign": callsign, "mode": "transceive"}
                    for target, callsign in links
                ],
            }
        ),
    }


def test_persistent_breadth_first_topology_confirms_reciprocal_edges(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'topology.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)
    timestamp = datetime(2026, 8, 24, 3, tzinfo=UTC)

    with sessions() as session:
        ensure_topology_crawl(session, "100000", "674982", now=timestamp)
        root_work = next_crawl_work(session, refresh_seconds=900, now=timestamp)
        assert root_work is not None
        apply_topology_snapshot(
            session,
            root_work,
            snapshot("674982", [("63573", "KI5KUD"), ("KI5KUD", "KI5KUD")], timestamp),
            refresh_seconds=900,
            now=timestamp,
        )
        first = serialize_topology(session, "100000", "674982", now=timestamp)

        assert first["progress"] == {
            "discovered": 3,
            "queried": 1,
            "queued": 1,
            "max_nodes": 200,
            "max_depth": 12,
        }
        assert len(first["edges"]) == 2
        assert all(edge["provisional"] for edge in first["edges"])

        neighbor_work = next_crawl_work(session, refresh_seconds=900, now=timestamp)
        assert neighbor_work is not None
        assert neighbor_work.identifier == "63573"
        apply_topology_snapshot(
            session,
            neighbor_work,
            snapshot("63573", [("674982", "N674982"), ("55553", "W5XYZ")], timestamp),
            refresh_seconds=900,
            now=timestamp,
        )

    # A new session proves that graph and crawl state survive a Docker/process restart.
    with sessions() as session:
        graph = serialize_topology(session, "100000", "674982", now=timestamp)
        reciprocal = next(
            edge
            for edge in graph["edges"]
            if {edge["source"], edge["target"]} == {"674982", "63573"}
        )
        by_identifier = {node["identifier"]: node for node in graph["nodes"]}

        assert reciprocal["confirmed"] is True
        assert reciprocal["reported_by"] == ["63573", "674982"]
        assert by_identifier["55553"]["depth"] == 2
        assert graph["progress"]["queued"] == 1


def test_recent_node_cache_prevents_duplicate_api_query_between_roots(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'dedupe.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)
    timestamp = datetime(2026, 8, 24, 3, tzinfo=UTC)

    with sessions() as session:
        ensure_topology_crawl(session, "100000", "674982", now=timestamp)
        work = next_crawl_work(session, refresh_seconds=900, now=timestamp)
        assert work is not None
        apply_topology_snapshot(
            session,
            work,
            snapshot("674982", [("63573", "KI5KUD")], timestamp),
            refresh_seconds=900,
            now=timestamp,
        )

        ensure_topology_crawl(session, "100000", "674982", restart=True, now=timestamp)
        repeated = next_crawl_work(session, refresh_seconds=900, now=timestamp)
        assert repeated is not None
        cached = cached_topology_values(session, repeated, cache_seconds=300, now=timestamp)
        assert cached is not None
        apply_topology_snapshot(
            session,
            repeated,
            cached,
            refresh_seconds=900,
            queried=False,
            now=timestamp,
        )
        graph = serialize_topology(session, "100000", "674982", now=timestamp)

    assert graph["progress"]["queried"] == 0
    assert graph["progress"]["queued"] == 1


def test_topology_reports_configured_safety_limit(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'limit.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)
    timestamp = datetime(2026, 8, 24, 3, tzinfo=UTC)

    with sessions() as session:
        ensure_topology_crawl(session, "100000", "674982", max_nodes=2, max_depth=12, now=timestamp)
        root = next_crawl_work(session, refresh_seconds=900, now=timestamp)
        assert root is not None
        apply_topology_snapshot(
            session,
            root,
            snapshot("674982", [("63573", "A"), ("55553", "B")], timestamp),
            refresh_seconds=900,
            now=timestamp,
        )
        child = next_crawl_work(session, refresh_seconds=900, now=timestamp)
        assert child is not None
        apply_topology_snapshot(
            session,
            child,
            snapshot(child.identifier, [], timestamp),
            refresh_seconds=900,
            now=timestamp,
        )
        graph = serialize_topology(session, "100000", "674982", now=timestamp)

    assert graph["status"] == "limited"
    assert graph["limited"] is True
    assert graph["progress"]["discovered"] == 2


def test_favorite_priority_refresh_updates_root_without_consuming_neighbor_work(
    tmp_path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'favorite-priority.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)
    old_fetch = datetime.now(UTC) - timedelta(seconds=20)

    with sessions() as session:
        create_favorite(
            session,
            home_node="100000",
            target_identifier="674982",
            label="Favorite hub",
        )
        ensure_topology_crawl(session, "100000", "674982", now=old_fetch)
        crawl_work = next_crawl_work(session, refresh_seconds=900, now=old_fetch)
        assert crawl_work is not None
        apply_topology_snapshot(
            session,
            crawl_work,
            snapshot("674982", [("63573", "KI5KUD")], old_fetch),
            refresh_seconds=900,
            now=old_fetch,
        )

    service = TopologyService(
        sessions,
        base_url="http://example.invalid/api/stats",
        request_interval_seconds=3,
        timeout_seconds=1,
        favorite_refresh_seconds=15,
        max_nodes=200,
        max_depth=12,
        refresh_seconds=900,
        cache_seconds=300,
    )
    favorite_work = service._seed_and_take()
    assert favorite_work is not None
    assert favorite_work.favorite_refresh is True
    assert favorite_work.identifier == "674982"
    refreshed = snapshot("674982", [("63573", "KI5KUD")], datetime.now(UTC))
    refreshed["total_keyups"] = 13
    service._store(favorite_work, refreshed, queried=True)

    with sessions() as session:
        graph = serialize_topology(session, "100000", "674982")

    assert graph["progress"]["queried"] == 2
    assert graph["progress"]["queued"] == 1


def test_closed_map_refresh_discovers_direct_links_but_parks_neighbor_queries(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'closed-map.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)

    with sessions() as session:
        create_favorite(
            session,
            home_node="100000",
            target_identifier="674982",
            label="Favorite hub",
        )

    service = TopologyService(
        sessions,
        base_url="http://example.invalid/api/stats",
        request_interval_seconds=3,
        timeout_seconds=1,
        favorite_refresh_seconds=15,
        max_nodes=200,
        max_depth=12,
        refresh_seconds=900,
        cache_seconds=300,
    )
    favorite_work = service._seed_and_take()
    assert favorite_work is not None and favorite_work.favorite_refresh is True
    service._store(
        favorite_work,
        snapshot("674982", [("63573", "KI5KUD")], datetime.now(UTC)),
        queried=True,
    )

    with sessions() as session:
        graph = serialize_topology(session, "100000", "674982")

    assert graph["progress"]["discovered"] == 2
    assert graph["progress"]["queued"] == 1
    assert service._seed_and_take() is None


def test_due_favorites_cannot_starve_a_visible_map(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'fair-scheduling.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)
    timestamp = datetime.now(UTC) - timedelta(minutes=10)

    with sessions() as session:
        for target in ("674982", "63573", "55553", "55554", "55555", "55556"):
            create_favorite(
                session,
                home_node="100000",
                target_identifier=target,
                label=f"Node {target}",
            )
            ensure_topology_crawl(session, "100000", target, now=timestamp)

    service = TopologyService(
        sessions,
        base_url="http://example.invalid/api/stats",
        request_interval_seconds=3,
        timeout_seconds=1,
        favorite_refresh_seconds=15,
        max_nodes=200,
        max_depth=12,
        refresh_seconds=900,
        cache_seconds=300,
    )
    service.mark_viewer("100000", "674982")

    first = service._seed_and_take()
    second = service._seed_and_take()
    focused = service._seed_and_take()

    assert first is not None and first.favorite_refresh is True
    assert second is not None and second.favorite_refresh is True
    assert focused is not None and focused.favorite_refresh is False
    assert focused.root_identifier == "674982"


def test_closing_viewer_stream_immediately_parks_map(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'stream-viewer.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)
    service = TopologyService(
        sessions,
        base_url="http://example.invalid/api/stats",
        request_interval_seconds=3,
        timeout_seconds=1,
        favorite_refresh_seconds=15,
        max_nodes=200,
        max_depth=12,
        refresh_seconds=900,
        cache_seconds=300,
    )

    async def open_then_close() -> None:
        stream = service.events(
            0.001,
            home_node="100000",
            root_identifier="674982",
        )
        assert await anext(stream) == {"heartbeat": True}
        assert service.active_roots() == {("100000", "674982")}
        await stream.aclose()

    asyncio.run(open_then_close())
    assert service.active_roots() == set()


def test_unviewed_maps_stop_walking_until_a_viewer_opens_them(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'viewers.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)
    timestamp = datetime(2026, 8, 24, 3, tzinfo=UTC)

    with sessions() as session:
        for root in ("674982", "63573"):
            ensure_topology_crawl(session, "100000", root, now=timestamp)
            work = next_crawl_work(
                session,
                refresh_seconds=900,
                active_roots={("100000", root)},
                now=timestamp,
            )
            assert work is not None and work.identifier == root
            apply_topology_snapshot(
                session,
                work,
                snapshot(root, [("55553", "W5XYZ")], timestamp),
                refresh_seconds=900,
                now=timestamp,
            )

        assert (
            next_crawl_work(session, refresh_seconds=900, active_roots=set(), now=timestamp) is None
        )

        service = TopologyService(
            sessions,
            base_url="http://example.invalid/api/stats",
            request_interval_seconds=3,
            timeout_seconds=1,
            favorite_refresh_seconds=15,
            max_nodes=200,
            max_depth=12,
            refresh_seconds=900,
            cache_seconds=300,
        )
        service.mark_viewer("100000", "63573", now=timestamp)
        viewed = next_crawl_work(
            session,
            refresh_seconds=900,
            active_roots=service.active_roots(now=timestamp),
            now=timestamp,
        )

    assert viewed is not None
    assert viewed.root_identifier == "63573"
    assert viewed.identifier == "55553"


def test_viewer_registration_expires_after_the_ttl(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'viewer-ttl.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)
    timestamp = datetime(2026, 8, 24, 3, tzinfo=UTC)

    service = TopologyService(
        sessions,
        base_url="http://example.invalid/api/stats",
        request_interval_seconds=3,
        timeout_seconds=1,
        favorite_refresh_seconds=15,
        max_nodes=200,
        max_depth=12,
        refresh_seconds=900,
        cache_seconds=300,
        viewer_ttl_seconds=90,
    )
    service.mark_viewer("100000", "674982", now=timestamp)

    assert service.active_roots(now=timestamp + timedelta(seconds=60)) == {("100000", "674982")}
    assert service.active_roots(now=timestamp + timedelta(seconds=120)) == set()


def test_lookup_budget_holds_requests_at_the_per_minute_limit() -> None:
    engine = create_engine("sqlite://")
    sessions = sessionmaker(bind=engine, future=True)
    service = TopologyService(
        sessions,
        base_url="http://example.invalid/api/stats",
        request_interval_seconds=0,
        timeout_seconds=1,
        favorite_refresh_seconds=15,
        max_nodes=200,
        max_depth=12,
        refresh_seconds=900,
        cache_seconds=300,
        max_requests_per_minute=3,
    )

    async def reserve(count: int) -> None:
        for _ in range(count):
            await service._reserve_request_slot()

    asyncio.run(asyncio.wait_for(reserve(3), timeout=1))
    with pytest.raises(TimeoutError):
        asyncio.run(asyncio.wait_for(reserve(1), timeout=0.2))


def test_home_node_directory_refresh_persists_and_hydrates(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'home-directory.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)
    fetched = datetime.now(UTC)

    service = TopologyService(
        sessions,
        base_url="http://example.invalid/api/stats",
        request_interval_seconds=3,
        timeout_seconds=1,
        favorite_refresh_seconds=15,
        max_nodes=200,
        max_depth=12,
        refresh_seconds=900,
        cache_seconds=300,
        home_nodes=["668390", "1999"],
    )

    assert service._take_home_directory_refresh() == "668390"
    directory = service._store_home_directory(
        "668390",
        snapshot("668390", [("KM7GHS", "KM7GHS")], fetched)
        | {"callsign": "KM7GHS", "location": "Goodyear, AZ"},
    )

    assert directory == {"callsign": "KM7GHS", "location": "Goodyear, AZ"}
    assert service.hydrate_home_directories() == {
        "668390": {"callsign": "KM7GHS", "location": "Goodyear, AZ"}
    }
    assert "1999" not in service.home_nodes
    assert service._take_home_directory_refresh() is None
