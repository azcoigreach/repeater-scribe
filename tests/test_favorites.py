from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from asl_transcriber.database import Base, get_db
from asl_transcriber.favorites import (
    create_favorite,
    delete_favorite,
    list_favorite_items,
    record_remote_key_transition,
    update_favorite,
)
from asl_transcriber.main import app
from asl_transcriber.models import RemoteNodeStat
from asl_transcriber.node_control import RemoteKeyTransition


def test_key_totals_are_retained_when_node_is_favorited_later(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'favorites.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)
    started_at = datetime(2026, 8, 23, 12, tzinfo=UTC)

    record_remote_key_transition(
        sessions,
        RemoteKeyTransition("remote_keyed_started", "100000", "674982", started_at),
    )
    record_remote_key_transition(
        sessions,
        RemoteKeyTransition(
            "remote_keyed_started", "100000", "674982", started_at + timedelta(seconds=1)
        ),
    )
    record_remote_key_transition(
        sessions,
        RemoteKeyTransition(
            "remote_keyed_ended",
            "100000",
            "674982",
            started_at + timedelta(seconds=7),
            duration_seconds=7,
        ),
    )

    with sessions() as session:
        favorite = create_favorite(
            session,
            home_node="100000",
            target_identifier="674982",
            label="Netoholics HUB",
            callsign="KN4EWT",
            description="Evening net",
            location="Carthage, TN",
        )
        duplicate = create_favorite(
            session,
            home_node="100000",
            target_identifier="674982",
            label="Ignored duplicate",
        )
        items = list_favorite_items(session, "100000", {})

        assert duplicate.id == favorite.id
        assert items[0]["keyup_count"] == 1
        assert items[0]["total_tx_milliseconds"] == 7_000
        assert items[0]["callsign"] == "KN4EWT"
        assert items[0]["description"] == "Evening net"
        assert items[0]["location"] == "Carthage, TN"

        update_favorite(session, "100000", favorite.id, {"description": "Weekly net"})
        assert list_favorite_items(session, "100000", {})[0]["description"] == "Weekly net"

        delete_favorite(session, "100000", favorite.id)
        assert list_favorite_items(session, "100000", {}) == []
        assert session.scalar(select(RemoteNodeStat)).keyup_count == 1  # type: ignore[union-attr]


def test_favorites_api_persists_metadata_and_protects_machine_writes(
    monkeypatch, tmp_path
) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)

    def test_db() -> Iterator[Session]:
        with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = test_db
    monkeypatch.setattr("asl_transcriber.main.settings.archive_paths", str(tmp_path))
    monkeypatch.setattr("asl_transcriber.main.settings.auto_process", False)
    monkeypatch.setattr("asl_transcriber.main.settings.ami_enabled", False)
    monkeypatch.setattr("asl_transcriber.main.settings.auth_mode", "off")
    monkeypatch.setattr("asl_transcriber.main.settings.api_key", "test-key")
    try:
        with TestClient(app) as client:
            created = client.post(
                "/ui/nodes/100000/favorites",
                json={
                    "target_identifier": "KM7GHS",
                    "label": "KM7GHS",
                    "callsign": "KM7GHS",
                    "description": "East valley hub",
                    "location": "Mesa, AZ",
                },
            )
            assert created.status_code == 200
            favorite_id = created.json()["id"]

            updated = client.patch(
                f"/ui/nodes/100000/favorites/{favorite_id}",
                json={"description": "Drive-time hub", "location": "Phoenix, AZ"},
            )
            listed = client.get("/api/v1/nodes/100000/favorites")
            unauthorized = client.post(
                "/api/v1/nodes/100000/favorites",
                json={"target_identifier": "674982"},
            )
            removed = client.delete(f"/ui/nodes/100000/favorites/{favorite_id}")

        assert updated.status_code == 200
        assert updated.json()["description"] == "Drive-time hub"
        assert updated.json()["location"] == "Phoenix, AZ"
        assert listed.json()["total"] == 1
        assert listed.json()["items"][0]["target_identifier"] == "KM7GHS"
        assert unauthorized.status_code == 401
        assert removed.json() == {"deleted": True}
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_dashboard_exposes_favorite_controls_and_key_totals() -> None:
    template = Path("src/asl_transcriber/templates/dashboard.html").read_text()
    script = Path("src/asl_transcriber/static/dashboard.js").read_text()

    assert 'data-win="favorites"' in template
    assert "<th>Keys</th>" in template
    assert "Add node to favorites" in script
    assert "/favorites`" in script
    assert "node-transition" in script
    assert "window.confirm" not in script
