"""0.9 guarantees through real SQLite transactions and the authenticated HTTP surface."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, event, select, text
from sqlalchemy.orm import sessionmaker

from asl_transcriber import main
from asl_transcriber.auth import create_api_token
from asl_transcriber.callsign_service import persist_transcript_details, review_mention
from asl_transcriber.config import settings
from asl_transcriber.database import Base, get_db
from asl_transcriber.models import (
    CallsignMention,
    IngestionJob,
    Recording,
    SessionRecording,
    Transcript,
)
from asl_transcriber.runtime import ArchiveRuntime
from asl_transcriber.session_api import source_id
from asl_transcriber.session_membership import included
from asl_transcriber.transcription.base import TranscriptCallsignMention, TranscriptSegment

START = datetime(2026, 9, 1, 12, tzinfo=UTC)


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'events.db'}", connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    root = tmp_path / "source-one"
    root.mkdir()
    monkeypatch.setattr(settings, "archive_paths", str(root))
    monkeypatch.setattr(settings, "request_rate_per_minute", 10000)

    def override():
        with factory() as db:
            yield db

    main.app.dependency_overrides[get_db] = override
    client = TestClient(main.app)
    yield factory, client, root
    client.close()
    main.app.dependency_overrides.pop(get_db, None)
    engine.dispose()


def recording(factory, root, *, start=START, duration=10, identifier=None):
    with factory() as db:
        row = Recording(
            id=identifier or str(uuid4()),
            source_path=f"{uuid4()}.wav",
            archive_root=str(root),
            started_at=start,
            duration_seconds=duration,
        )
        db.add(row)
        db.commit()
        return row.id


def create(client, root, *, key=None, **values):
    payload = {
        "name": "Groovy Late Shift",
        "type": "Net",
        "source_id": source_id(str(root)),
        "started_at": START.isoformat(),
        **values,
    }
    return client.post(
        "/ui/sessions", json=payload, headers={"Idempotency-Key": key or str(uuid4())}
    )


def members(factory, identifier):
    with factory() as db:
        return set(
            db.scalars(
                select(SessionRecording.recording_id).where(
                    SessionRecording.session_id == identifier, included()
                )
            )
        )


def test_live_end_reopen_and_idempotency_across_restart(catalog):
    factory, client, root = catalog
    key = str(uuid4())
    first = create(client, root, key=key)
    assert first.status_code == 200, first.text
    identifier = first.json()["id"]
    assert create(client, root, key=key).json()["id"] == identifier
    assert create(client, root, key=key, name="Different").status_code == 409
    assert create(client, root).status_code == 409
    recorded = recording(factory, root)
    assert members(factory, identifier) == {recorded}
    ending = client.post(
        f"/ui/sessions/{identifier}/end",
        json={"ended_at": (START + timedelta(hours=1)).isoformat()},
    )
    assert ending.status_code == 200, ending.text
    assert (
        client.post(f"/ui/sessions/{identifier}/end", json={}).json()["ended_at"]
        == ending.json()["ended_at"]
    )
    assert (
        client.post(
            f"/ui/sessions/{identifier}/end",
            json={"ended_at": (START + timedelta(hours=2)).isoformat()},
        ).status_code
        == 409
    )
    ArchiveRuntime([root], session_factory=factory)
    assert create(client, root, key=key).json()["id"] == identifier
    assert client.post(f"/ui/sessions/{identifier}/reopen").status_code == 200
    assert client.post(f"/ui/sessions/{identifier}/reopen").status_code == 200
    assert members(factory, identifier) == {recorded}


def test_interval_edges_sources_unknowns_and_late_discovery(catalog):
    factory, client, root = catalog
    end = START + timedelta(seconds=20)
    included_ids = {
        recording(factory, root, start=START - timedelta(seconds=3), duration=5),
        recording(factory, root),
        recording(factory, root, start=START + timedelta(seconds=19), duration=5),
        recording(factory, root, duration=None),
        recording(factory, root, duration=0),
    }
    for start, duration in [
        (START - timedelta(seconds=5), 5),
        (end, 10),
        (end, None),
        (None, 100),
        (START - timedelta(seconds=1), 0),
    ]:
        recording(factory, root, start=start, duration=duration)
    recording(factory, root.parent / "source-two")
    response = create(client, root, ended_at=end.isoformat())
    assert response.status_code == 200, response.text
    identifier = response.json()["id"]
    assert members(factory, identifier) == included_ids
    late = recording(factory, root, start=START + timedelta(seconds=15))
    assert members(factory, identifier) == included_ids | {late}
    rows = client.get(f"/api/v1/sessions/{identifier}/recordings").json()["items"]
    assert sum(row["boundary_crossing"] for row in rows) == 3
    assert all(not row["audio_available"] for row in rows)


def test_boundary_edits_and_duration_updates_preserve_explicit_decisions(catalog):
    factory, client, root = catalog
    inside = recording(factory, root)
    outside = recording(factory, root, start=START - timedelta(seconds=10), duration=1)
    no_time = recording(factory, root, start=None)
    identifier = create(
        client, root, ended_at=(START + timedelta(seconds=20)).isoformat(), recording_ids=[no_time]
    ).json()["id"]
    assert (
        client.patch(
            f"/ui/sessions/{identifier}/recordings/{inside}", json={"decision": "exclude"}
        ).status_code
        == 200
    )
    with factory() as db:
        db.get(Recording, outside).duration_seconds = 11
        db.commit()
    assert members(factory, identifier) == {outside, no_time}
    assert (
        client.patch(
            f"/ui/sessions/{identifier}",
            json={"started_at": (START + timedelta(seconds=15)).isoformat()},
        ).status_code
        == 200
    )
    assert members(factory, identifier) == {no_time}
    assert (
        client.patch(
            f"/ui/sessions/{identifier}", json={"started_at": START.isoformat()}
        ).status_code
        == 200
    )
    assert members(factory, identifier) == {outside, no_time}
    client.patch(f"/ui/sessions/{identifier}/recordings/{inside}", json={"decision": "automatic"})
    assert members(factory, identifier) == {inside, outside, no_time}
    client.patch(f"/ui/sessions/{identifier}/recordings/{no_time}", json={"decision": "automatic"})
    assert members(factory, identifier) == {inside, outside}
    other = recording(factory, root.parent / "other")
    assert (
        client.patch(
            f"/ui/sessions/{identifier}/recordings/{other}", json={"decision": "include"}
        ).status_code
        == 422
    )


def test_concurrent_starts_retries_and_roster_uniqueness(catalog):
    _factory, client, root = catalog
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: create(client, root), range(4)))
    assert sorted(result.status_code for result in results) == [200, 409, 409, 409]
    identifier = next(result.json()["id"] for result in results if result.status_code == 200)
    client.post(f"/ui/sessions/{identifier}/end", json={})
    key = str(uuid4())
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: create(client, root, key=key), range(4)))
    assert {result.status_code for result in results} == {200}
    assert len({result.json()["id"] for result in results}) == 1
    active = results[0].json()["id"]
    assert client.post(f"/ui/sessions/{identifier}/reopen").status_code == 409
    payload = {"callsign": "km7ghs/p", "at": START.isoformat(), "note": "Manual check-in"}
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda _: client.post(f"/ui/sessions/{active}/checkins", json=payload), range(4)
            )
        )
    assert sorted(result.status_code for result in results) == [200, 409, 409, 409]
    assert (
        client.get(f"/api/v1/sessions/{active}/checkins").json()["items"][0]["callsign"] == "KM7GHS"
    )


def test_startup_recovers_missing_associations_including_ended_events(catalog):
    factory, client, root = catalog
    identifier = create(client, root, ended_at=(START + timedelta(hours=1)).isoformat()).json()[
        "id"
    ]
    first = recording(factory, root)
    with factory() as db:
        db.execute(delete(SessionRecording))
        # Simulate catalog updates while the application was stopped, outside ORM hooks.
        db.execute(text("UPDATE recordings SET audio_status = 'missing'"))
        db.commit()
    ArchiveRuntime([root], session_factory=factory)
    assert members(factory, identifier) == {first}
    ArchiveRuntime([root], session_factory=factory)
    with factory() as db:
        assert db.query(SessionRecording).count() == 1


def transcript(factory, identifier):
    with factory() as db:
        row = db.get(Recording, identifier)
        job = IngestionJob(id=identifier, recording_id=identifier, source_path=row.source_path)
        db.add(job)
        db.flush()
        saved = Transcript(
            id=identifier,
            job_id=identifier,
            recording_id=identifier,
            raw_text="KM7GHS checking in",
            display_text="KM7GHS checking in",
        )
        db.add(saved)
        result = SimpleNamespace(
            segments=[TranscriptSegment(1, 4, "KM7GHS checking in")],
            callsign_mentions=[TranscriptCallsignMention("KM7GHS", 1, 4)],
        )
        persist_transcript_details(db, saved, row, result)
        db.commit()


def test_retranscription_review_and_missing_audio_preserve_markers_roster(catalog):
    factory, client, root = catalog
    recorded = recording(factory, root)
    transcript(factory, recorded)
    identifier = create(client, root).json()["id"]
    prefix = f"/ui/sessions/{identifier}"
    detected = client.get(f"/api/v1/sessions/{identifier}/detected").json()["items"]
    assert detected[0]["mention_count"] == 1
    assert detected[0]["recording_count"] == 1
    assert detected[0]["attributed_airtime_seconds"] is None
    assert client.get(f"/api/v1/sessions/{identifier}/checkins").json()["items"] == []
    with factory() as db:
        mention = db.query(CallsignMention).one()
        review_mention(
            db, mention.id, action="confirm", corrected_callsign=None, reviewer_identity="operator"
        )
        db.commit()
    assert client.get(f"/api/v1/sessions/{identifier}/checkins").json()["items"] == []
    marker = client.post(
        prefix + "/markers",
        json={
            "type": "Topic",
            "note": "Saved topic",
            "at": (START + timedelta(seconds=2)).isoformat(),
            "recording_id": recorded,
            "audio_offset": 2,
        },
    )
    assert marker.status_code == 200, marker.text
    checked = client.post(
        prefix + "/checkins",
        json={
            "callsign": "KM7GHS",
            "at": START.isoformat(),
            "note": "Confirmed by ear",
            "recording_id": recorded,
            "audio_offset": 1,
        },
    )
    assert checked.status_code == 200, checked.text
    with factory() as db:
        row = db.get(Recording, recorded)
        saved = db.get(Transcript, recorded)
        saved.display_text = "Replacement transcript without callsigns"
        persist_transcript_details(
            db, saved, row, SimpleNamespace(segments=[], callsign_mentions=[])
        )
        row.audio_status = "missing"
        db.commit()
    assert client.get(f"/api/v1/sessions/{identifier}/detected").json()["items"] == []
    assert (
        client.get(f"/api/v1/sessions/{identifier}/markers").json()["items"][0]["audio_offset"] == 2
    )
    rows = client.get(f"/api/v1/sessions/{identifier}/recordings").json()["items"]
    assert rows[0]["transcript"]["display_text"] == "Replacement transcript without callsigns"
    assert rows[0]["audio_available"] is False
    client.patch(prefix + f"/recordings/{recorded}", json={"decision": "exclude"})
    roster = client.get(f"/api/v1/sessions/{identifier}/checkins").json()["items"][0]
    assert roster["evidence_outside_event"] is True
    assert roster["confirmed_at"] == checked.json()["confirmed_at"]
    assert client.delete(prefix + f"/checkins/{roster['id']}").status_code == 200
    assert client.get(f"/api/v1/sessions/{identifier}/checkins").json()["items"] == []


def test_live_unanchored_markers_attach_only_when_unambiguous(catalog):
    factory, client, root = catalog
    identifier = create(client, root).json()["id"]
    at = (START + timedelta(seconds=5)).isoformat()
    marker = client.post(
        f"/ui/sessions/{identifier}/markers", json={"note": "Live marker", "at": at}
    ).json()
    assert marker["recording_id"] is None
    recorded = recording(factory, root)
    marker = client.get(f"/api/v1/sessions/{identifier}/markers").json()["items"][0]
    assert marker["recording_id"] == recorded
    assert marker["audio_offset"] == 5
    recording(factory, root)
    ambiguous = client.post(
        f"/ui/sessions/{identifier}/markers", json={"note": "Ambiguous", "at": at}
    ).json()
    assert ambiguous["recording_id"] is None
    invalid = {"note": "Bad", "at": at, "recording_id": recorded, "audio_offset": 11}
    assert client.post(f"/ui/sessions/{identifier}/markers", json=invalid).status_code == 422
    invalid["audio_offset"] = -1
    assert client.post(f"/ui/sessions/{identifier}/markers", json=invalid).status_code == 422
    invalid["recording_id"] = None
    invalid["audio_offset"] = 1
    assert client.post(f"/ui/sessions/{identifier}/markers", json=invalid).status_code == 422


def test_preview_selected_recordings_tags_and_paginated_history(catalog):
    factory, client, root = catalog
    ids = [recording(factory, root, start=START + timedelta(seconds=n)) for n in range(31)]
    payload = {
        "name": "Historical",
        "source_id": source_id(str(root)),
        "started_at": START.isoformat(),
        "ended_at": (START + timedelta(hours=1)).isoformat(),
        "recording_ids": [ids[0]],
        "tags": ["Groovy", "night"],
    }
    preview = client.post("/ui/sessions/preview", json=payload)
    assert preview.status_code == 200, preview.text
    assert preview.json()["additional_automatic_count"] == 30
    identifier = client.post(
        "/ui/sessions", json=payload, headers={"Idempotency-Key": "selected"}
    ).json()["id"]
    assert client.get("/api/v1/sessions?tag=groovy").json()["items"][0]["id"] == identifier
    seen, cursor = [], None
    while True:
        reply = client.get(
            f"/api/v1/sessions/{identifier}/recordings",
            params={"limit": 7, **({"cursor": cursor} if cursor else {})},
        )
        assert reply.status_code == 200, reply.text
        result = reply.json()
        seen.extend(row["id"] for row in result["items"])
        cursor = result["next_cursor"]
        if not result["has_more"]:
            break
    assert seen == ids
    assert client.get(f"/api/v1/sessions/{identifier}/recordings?cursor=bad").status_code == 422
    assert client.get(f"/api/v1/sessions/{identifier}/recordings?limit=101").status_code == 422
    assert (
        client.patch(
            f"/ui/sessions/{identifier}/recordings/{ids[0]}/tags", json={"tags": ["favorite"]}
        ).status_code
        == 200
    )
    assert client.get("/api/v1/archive/recordings?tag=favorite").json()["items"][0]["id"] == ids[0]
    assert (
        client.get(
            "/api/v1/archive/recordings", params={"source_id": source_id(str(root))}
        ).status_code
        == 200
    )


@pytest.mark.parametrize(
    "values",
    [
        {"started_at": "2026-09-01T12:00:00"},
        {"ended_at": START.isoformat()},
        {"started_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat()},
        {"type": "Not A Type"},
        {"net_control": "garbage"},
        {"tags": [""]},
        {"name": "   "},
    ],
)
def test_input_validation(catalog, values):
    _, client, root = catalog
    assert create(client, root, **values).status_code == 422


def test_authentication_viewer_csrf_and_machine_tokens(catalog, monkeypatch):
    from test_security_hardening import _session

    _, client, root = catalog
    identifier = create(client, root).json()["id"]
    monkeypatch.setattr(settings, "deployment_mode", "internet")
    monkeypatch.setattr(settings, "auth_mode", "oidc")
    monkeypatch.setattr(settings, "public_base_url", "https://testserver")
    assert client.get("/api/v1/sessions").status_code == 401
    token = create_api_token(f"event-viewer-{uuid4()}", "viewer")
    viewer = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/v1/sessions", headers=viewer).status_code == 200
    assert (
        client.post(f"/api/v1/sessions/{identifier}/end", json={}, headers=viewer).status_code
        == 403
    )
    raw, csrf = _session("operator")
    client.cookies.set(settings.session_cookie_name, raw)
    assert client.post(f"/ui/sessions/{identifier}/end", json={}).status_code == 403
    assert (
        client.post(
            f"/ui/sessions/{identifier}/end",
            json={},
            headers={"X-CSRF-Token": csrf, "Origin": "https://evil.test"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/ui/sessions/{identifier}/end",
            json={},
            headers={"X-CSRF-Token": csrf, "Origin": "https://testserver"},
        ).status_code
        == 200
    )
    client.cookies.clear()
    token = create_api_token(f"event-operator-{uuid4()}", "operator")
    assert (
        client.post(
            f"/api/v1/sessions/{identifier}/reopen", headers={"Authorization": f"Bearer {token}"}
        ).status_code
        == 200
    )
    # Existing SSE route remains the same function and content type contract.
    route = next(
        route for route in main.app.routes if getattr(route, "path", None) == "/api/v1/events"
    )
    assert route.endpoint is main.events


def test_event_listing_cursor_and_overlap_allowed(catalog):
    factory, client, root = catalog
    recording(factory, root)
    ids = []
    for index in range(6):
        response = create(
            client,
            root,
            name=f"Historical {index}",
            ended_at=(START + timedelta(hours=1)).isoformat(),
        )
        assert response.status_code == 200, response.text
        ids.append(response.json()["id"])
    cursor, found = None, []
    while True:
        result = client.get(
            "/api/v1/sessions", params={"limit": 2, **({"cursor": cursor} if cursor else {})}
        ).json()
        found.extend(row["id"] for row in result["items"])
        cursor = result["next_cursor"]
        if not cursor:
            break
    assert found == sorted(ids)
    for identifier in ids:
        assert len(members(factory, identifier)) == 1


def test_latest_traffic_is_bounded_and_keeps_advancing(catalog):
    factory, client, root = catalog
    ids = [recording(factory, root, start=START + timedelta(seconds=n)) for n in range(32)]
    identifier = create(client, root).json()["id"]
    latest = client.get(f"/api/v1/sessions/{identifier}/recordings?latest=true&limit=5").json()
    assert [row["id"] for row in latest["items"]] == ids[-5:]
    assert latest["has_earlier"] is True
    assert latest["next_cursor"] is None
    new = recording(factory, root, start=START + timedelta(minutes=1))
    latest = client.get(f"/api/v1/sessions/{identifier}/recordings?latest=true&limit=5").json()
    assert [row["id"] for row in latest["items"]] == ids[-4:] + [new]
    first = client.get(f"/api/v1/sessions/{identifier}/recordings?limit=5").json()
    assert [row["id"] for row in first["items"]] == ids[:5]
    assert first["has_more"] is True


def test_explicit_attribution_uses_existing_evidence_and_unknown_is_unavailable(catalog):
    from asl_transcriber.models import Transmission

    factory, client, root = catalog
    recorded = recording(factory, root)
    transcript(factory, recorded)
    identifier = create(client, root).json()["id"]
    with factory() as db:
        db.add(
            Transmission(
                recording_id=recorded,
                operator_callsign="KM7GHS",
                attribution_level="unknown",
                duration_milliseconds=10000,
            )
        )
        db.commit()
    assert (
        client.get(f"/api/v1/sessions/{identifier}").json()["station_attributed_airtime_seconds"]
        is None
    )
    with factory() as db:
        db.add(
            Transmission(
                recording_id=recorded,
                operator_callsign="KM7GHS",
                attribution_level="confirmed",
                duration_milliseconds=1500,
            )
        )
        db.commit()
    detail = client.get(f"/api/v1/sessions/{identifier}").json()
    assert detail["station_attributed_airtime_seconds"] == 1.5
    detected = client.get(f"/api/v1/sessions/{identifier}/detected").json()["items"][0]
    assert detected["attributed_transmission_count"] == 1
    assert detected["attributed_airtime_seconds"] == 1.5
    assert detected["attribution_status"] == "partial"


def test_creation_normalizes_timezone_retry_intent_and_preserves_actor(catalog):
    _, client, root = catalog
    first = create(client, root, key="offset", started_at="2026-09-01T05:00:00-07:00")
    assert first.status_code == 200, first.text
    second = create(client, root, key="offset", started_at="2026-09-01T12:00:00Z")
    assert second.status_code == 200, second.text
    assert first.json()["id"] == second.json()["id"]
    assert datetime.fromisoformat(first.json()["started_at"]) == START
    assert first.json()["created_by"]


def test_marker_and_roster_edits_keep_original_confirmation_and_creation(catalog):
    _, client, root = catalog
    identifier = create(client, root).json()["id"]
    prefix = f"/ui/sessions/{identifier}"
    body = {"callsign": "W1AW", "at": START.isoformat(), "note": "manual"}
    first = client.post(prefix + "/checkins", json=body).json()
    body.update(at=(START + timedelta(minutes=2)).isoformat(), note="corrected time")
    second = client.patch(prefix + f"/checkins/{first['id']}", json=body)
    assert second.status_code == 200, second.text
    assert second.json()["confirmed_at"] == first["confirmed_at"]
    assert second.json()["confirmed_by"] == first["confirmed_by"]
    assert second.json()["at"] != first["at"]
    marker_body = {"at": START.isoformat(), "note": "first"}
    marker = client.post(prefix + "/markers", json=marker_body).json()
    marker_body["note"] = "edited"
    edited = client.patch(prefix + f"/markers/{marker['id']}", json=marker_body)
    assert edited.status_code == 200, edited.text
    assert edited.json()["created_at"] == marker["created_at"]
    assert edited.json()["note"] == "edited"
    assert client.delete(prefix + f"/markers/{marker['id']}").status_code == 200
    assert client.delete(prefix + f"/markers/{marker['id']}").status_code == 200


def test_marker_ambiguity_uses_exact_offsets_at_submillisecond_boundary(catalog):
    factory, client, root = catalog
    recording(factory, root, duration=0.0001)
    long_recording = recording(factory, root, duration=1)
    identifier = create(client, root).json()["id"]
    response = client.post(
        f"/ui/sessions/{identifier}/markers",
        json={
            "note": "Only the longer recording contains this instant",
            "at": (START + timedelta(microseconds=100)).isoformat(),
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["recording_id"] == long_recording
    assert response.json()["audio_offset"] == 0.0001
