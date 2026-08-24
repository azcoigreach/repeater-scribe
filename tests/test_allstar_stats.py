from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from asl_transcriber.allstar_stats import parse_allstar_stats, store_allstar_snapshot
from asl_transcriber.database import Base
from asl_transcriber.favorites import create_favorite, list_favorite_items


def stats_payload(*, keyups: int = 116, tx_seconds: int = 5369, keyed: bool = False):
    return {
        "stats": {
            "node": 674982,
            "data": {
                "apprptuptime": "6231",
                "totalkeyups": str(keyups),
                "totaltxtime": str(tx_seconds),
                "totalkerchunks": "2",
                "keyed": keyed,
                "time": "1787536562",
                "links": ["63573", "KI5KUD"],
                "nodes": "T63573,RKI5KUD",
                "linkedNodes": [
                    {
                        "name": 63573,
                        "callsign": "KI5KUD",
                        "Status": "Active",
                        "node_frequency": "South Coast Hub",
                        "server": {"Location": "Vancleave, MS"},
                    },
                    {"name": "KI5KUD"},
                ],
            },
        },
        "node": {
            "name": 674982,
            "callsign": "KN4EWT",
            "Status": "Active",
            "node_frequency": "Netoholics HUB",
            "server": {"Location": "Carthage, TN"},
        },
    }


def test_allstar_stats_populate_disconnected_favorite_and_topology(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'allstar.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)
    fetched_at = datetime(2026, 8, 24, 2, tzinfo=UTC)
    values = parse_allstar_stats(stats_payload(), fetched_at=fetched_at)

    assert values["total_keyups"] == 116
    assert values["link_count"] == 2

    with sessions() as session:
        create_favorite(
            session,
            home_node="668390",
            target_identifier="674982",
            label="Netoholics HUB",
        )
        store_allstar_snapshot(session, ["668390"], values)
        item = list_favorite_items(session, "668390", {}, now=fetched_at)[0]

    assert item["connected"] is False
    assert item["public_active"] is True
    assert item["keyup_count"] == 116
    assert item["total_tx_milliseconds"] == 5_369_000
    assert item["reported_link_count"] == 2
    assert item["callsign"] == "KN4EWT"
    assert item["location"] == "Carthage, TN"
    assert item["topology"][0] == {
        "identifier": "63573",
        "callsign": "KI5KUD",
        "description": "South Coast Hub",
        "location": "Vancleave, MS",
        "active": True,
        "mode": "transceive",
    }


def test_stats_deltas_mark_recent_activity_without_requiring_keyed_flag(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'activity.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)
    first_fetch = datetime(2026, 8, 24, 2, tzinfo=UTC)

    with sessions() as session:
        create_favorite(
            session,
            home_node="668390",
            target_identifier="674982",
            label="Netoholics HUB",
        )
        initial = parse_allstar_stats(stats_payload(), fetched_at=first_fetch)
        store_allstar_snapshot(session, ["668390"], initial)
        assert list_favorite_items(session, "668390", {}, now=first_fetch)[0][
            "recently_active"
        ] is False

        changed = parse_allstar_stats(
            stats_payload(keyups=117, tx_seconds=5375),
            fetched_at=first_fetch + timedelta(seconds=3),
        )
        store_allstar_snapshot(session, ["668390"], changed)
        item = list_favorite_items(
            session, "668390", {}, now=first_fetch + timedelta(seconds=4)
        )[0]

    assert item["keyup_count"] == 117
    assert item["recently_active"] is True
    assert item["last_activity_at"] == (first_fetch + timedelta(seconds=3)).isoformat()
