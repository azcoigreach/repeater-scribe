from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

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


def snapshot(identifier: str, links: list[tuple[str, str]], fetched_at: datetime) -> dict[str, object]:
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
        ensure_topology_crawl(session, "668390", "674982", now=timestamp)
        root_work = next_crawl_work(session, refresh_seconds=900, now=timestamp)
        assert root_work is not None
        apply_topology_snapshot(
            session,
            root_work,
            snapshot("674982", [("63573", "KI5KUD"), ("KI5KUD", "KI5KUD")], timestamp),
            refresh_seconds=900,
            now=timestamp,
        )
        first = serialize_topology(session, "668390", "674982", now=timestamp)

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
        graph = serialize_topology(session, "668390", "674982", now=timestamp)
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
        ensure_topology_crawl(session, "668390", "674982", now=timestamp)
        work = next_crawl_work(session, refresh_seconds=900, now=timestamp)
        assert work is not None
        apply_topology_snapshot(
            session,
            work,
            snapshot("674982", [("63573", "KI5KUD")], timestamp),
            refresh_seconds=900,
            now=timestamp,
        )

        ensure_topology_crawl(session, "668390", "674982", restart=True, now=timestamp)
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
        graph = serialize_topology(session, "668390", "674982", now=timestamp)

    assert graph["progress"]["queried"] == 0
    assert graph["progress"]["queued"] == 1


def test_topology_reports_configured_safety_limit(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'limit.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)
    timestamp = datetime(2026, 8, 24, 3, tzinfo=UTC)

    with sessions() as session:
        ensure_topology_crawl(
            session, "668390", "674982", max_nodes=2, max_depth=12, now=timestamp
        )
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
        graph = serialize_topology(session, "668390", "674982", now=timestamp)

    assert graph["status"] == "limited"
    assert graph["limited"] is True
    assert graph["progress"]["discovered"] == 2


def test_favorite_priority_refresh_does_not_reset_or_advance_crawl(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'favorite-priority.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)
    old_fetch = datetime.now(UTC) - timedelta(seconds=20)

    with sessions() as session:
        create_favorite(
            session,
            home_node="668390",
            target_identifier="674982",
            label="Favorite hub",
        )
        ensure_topology_crawl(session, "668390", "674982", now=old_fetch)
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
        graph = serialize_topology(session, "668390", "674982")

    assert graph["progress"]["queried"] == 1
    assert graph["progress"]["queued"] == 1
