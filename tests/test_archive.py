from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from asl_transcriber.archive import get_or_create_recording, list_recordings, refresh_audio
from asl_transcriber.auth import create_api_token
from asl_transcriber.config import settings
from asl_transcriber.database import Base, get_db
from asl_transcriber.main import app
from asl_transcriber.models import IngestionJob, Recording, Transcript


@pytest.fixture()
def archive_db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text("CREATE VIRTUAL TABLE transcript_fts USING fts5(raw_text, display_text, content='transcripts', content_rowid='rowid')")
        )
        connection.execute(text("CREATE TRIGGER transcript_fts_insert AFTER INSERT ON transcripts BEGIN INSERT INTO transcript_fts(rowid, raw_text, display_text) VALUES (new.rowid, new.raw_text, new.display_text); END"))
        connection.execute(text("CREATE TRIGGER transcript_fts_delete AFTER DELETE ON transcripts BEGIN INSERT INTO transcript_fts(transcript_fts, rowid, raw_text, display_text) VALUES ('delete', old.rowid, old.raw_text, old.display_text); END"))
        connection.execute(text("CREATE TRIGGER transcript_fts_update AFTER UPDATE OF raw_text, display_text ON transcripts BEGIN INSERT INTO transcript_fts(transcript_fts, rowid, raw_text, display_text) VALUES ('delete', old.rowid, old.raw_text, old.display_text); INSERT INTO transcript_fts(rowid, raw_text, display_text) VALUES (new.rowid, new.raw_text, new.display_text); END"))
    sessions = sessionmaker(bind=engine, future=True)
    yield sessions
    engine.dispose()


def add_recording(sessions, root: Path, recording_id: str, *, offset: int = 0, text_value: str = "raw beacon", display: str = "corrected KM7GHS", status: str = "completed", audio_status: str = "available", mentions: str = '[{"callsign": "KM7GHS"}]', started_at: datetime | None = None) -> Recording:
    with sessions() as session:
        recording = Recording(
            id=recording_id,
            archive_root=str(root),
            source_path=f"202609010101{offset % 60:02d}00-{recording_id}.wav",
            started_at=started_at or datetime(2026, 9, 1, 1, 1, offset % 60, tzinfo=UTC),
            status=status,
            audio_status=audio_status,
        )
        job = IngestionJob(id=recording_id, source_path=recording.source_path, archive_root=str(root), recording=recording, status=status)
        session.add_all([recording, job, Transcript(job=job, recording=recording, raw_text=text_value, display_text=display, callsign_mentions_json=mentions)])
        session.commit()
        return recording


def archive_client(sessions):
    def override_db():
        with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    return TestClient(app)


def test_archive_fts_sync_and_safe_querying(archive_db, tmp_path: Path) -> None:
    add_recording(archive_db, tmp_path, "one", text_value="raw unicode cafe", display="corrected KM7GHS")
    with archive_db() as session:
        for query, expected in (("raw", 1), ("KM7GHS", 1), ("   ", 0), ("!!!", 0), ('" OR (', 0), ("cafe", 1)):
            items, _, _ = list_recordings(session, cursor=None, limit=50, query=query, status=None, audio_status=None, from_at=None, to_at=None, callsign=None)
            assert len(items) == expected
        transcript = session.query(Transcript).one()
        transcript.display_text = "updated display"
        session.commit()
        items, _, _ = list_recordings(session, cursor=None, limit=50, query="KM7GHS", status=None, audio_status=None, from_at=None, to_at=None, callsign=None)
        assert not items
        items, _, _ = list_recordings(session, cursor=None, limit=50, query="updated", status=None, audio_status=None, from_at=None, to_at=None, callsign=None)
        assert len(items) == 1
        session.delete(transcript)
        session.commit()
        items, _, _ = list_recordings(session, cursor=None, limit=50, query="updated", status=None, audio_status=None, from_at=None, to_at=None, callsign=None)
        assert not items


@pytest.mark.parametrize("page_size", [1, 37, 100])
def test_archive_cursor_paginates_503_records_once_with_stable_ties(
    archive_db, tmp_path: Path, page_size: int
) -> None:
    timestamp = datetime(2026, 9, 1, tzinfo=UTC)
    recording_ids = [f"00000000-0000-0000-0000-{index:012d}" for index in range(503)]
    for index in range(503):
        add_recording(archive_db, tmp_path, recording_ids[index], offset=index, started_at=timestamp)
    expected = list(reversed(recording_ids))
    seen: list[str] = []
    cursor = None
    with archive_db() as session:
        while True:
            items, cursor, has_more = list_recordings(session, cursor=cursor, limit=page_size, query=None, status=None, audio_status=None, from_at=None, to_at=None, callsign=None)
            seen.extend(str(item["id"]) for item in items)
            if not has_more:
                assert cursor is None
                break
    assert seen == expected
    assert len(set(seen)) == 503


def test_archive_filters_and_null_start_ordering(archive_db, tmp_path: Path) -> None:
    add_recording(archive_db, tmp_path, "first", status="failed", audio_status="missing", mentions='[{"callsign":"N0CALL"}]')
    add_recording(archive_db, tmp_path, "second", status="completed", audio_status="available")
    with archive_db() as session:
        stored_first = session.get(Recording, "first")
        assert stored_first is not None
        stored_first.started_at = None
        stored_first.created_at = datetime(2026, 9, 2, tzinfo=UTC)
        session.commit()
        items, _, _ = list_recordings(session, cursor=None, limit=50, query=None, status="failed", audio_status="missing", from_at=None, to_at=None, callsign="N0CALL")
        assert [item["id"] for item in items] == ["first"]
        items, _, _ = list_recordings(session, cursor=None, limit=50, query=None, status=None, audio_status=None, from_at=datetime(2026, 9, 2, tzinfo=UTC), to_at=None, callsign=None)
        assert [item["id"] for item in items] == ["first"]
        items, _, _ = list_recordings(session, cursor=None, limit=50, query=None, status=None, audio_status=None, from_at=None, to_at=None, callsign="KM7GHS")
        assert [item["id"] for item in items] == ["second"]


def test_archive_routes_validate_cursor_limits_detail_and_audio_safety(archive_db, tmp_path: Path) -> None:
    audio = tmp_path / "inside.wav"
    audio.write_bytes(b"audio")
    add_recording(archive_db, tmp_path, "inside", offset=1)
    with archive_db() as session:
        row = session.get(Recording, "inside")
        assert row is not None
        row.source_path = "inside.wav"
        session.commit()
    with archive_client(archive_db) as client:
        assert client.get("/api/v1/archive/recordings?limit=0").status_code == 422
        assert client.get("/api/v1/archive/recordings?limit=101").status_code == 422
        for cursor in ("not-a-cursor", base64.urlsafe_b64encode(b'[]').decode(), base64.urlsafe_b64encode(json.dumps(["invalid", "id"]).encode()).decode(), base64.urlsafe_b64encode(json.dumps(["2026-09-01T00:00:00+00:00", "not-a-uuid"]).encode()).decode()):
            assert client.get("/api/v1/archive/recordings", params={"cursor": cursor}).status_code == 422
        assert client.get("/api/v1/archive/recordings/unknown").status_code == 404
        detail = client.get("/api/v1/archive/recordings/inside")
        assert detail.status_code == 200
        assert str(tmp_path) not in detail.text
        assert client.get("/api/v1/archive/recordings/inside/audio").status_code == 200
        with archive_db() as session:
            row = session.get(Recording, "inside")
            assert row is not None
            row.source_path = "../outside.wav"
            session.commit()
        response = client.get("/api/v1/archive/recordings/inside/audio")
        assert response.status_code == 404
        assert str(tmp_path) not in response.text
        with archive_db() as session:
            row = session.get(Recording, "inside")
            assert row is not None
            row.source_path = "/etc/passwd"
            session.commit()
        assert client.get("/api/v1/archive/recordings/inside/audio").status_code == 404
        outside = tmp_path.parent / "outside.wav"
        outside.write_bytes(b"outside")
        (tmp_path / "link.wav").symlink_to(outside)
        with archive_db() as session:
            row = session.get(Recording, "inside")
            assert row is not None
            row.source_path = "link.wav"
            session.commit()
        assert client.get("/api/v1/archive/recordings/inside/audio").status_code == 404
        with archive_db() as session:
            row = session.get(Recording, "inside")
            assert row is not None
            row.audio_status = "expired"
            session.commit()
        expired = client.get("/api/v1/archive/recordings/inside/audio")
        assert expired.status_code == 410
        assert expired.json()["detail"] == {"code": "audio_expired"}


def test_audio_refresh_preserves_intentional_states_and_reappearance(archive_db, tmp_path: Path) -> None:
    with archive_db() as session:
        row = get_or_create_recording(session, source_path="gone.wav", archive_root=str(tmp_path), recording_id="gone")
        session.commit()
        assert row.audio_status == "missing"
        (tmp_path / "gone.wav").write_bytes(b"audio")
        refresh_audio(row)
        assert row.audio_status == "available"
        row.audio_status = "protected"
        (tmp_path / "gone.wav").unlink()
        refresh_audio(row)
        assert row.audio_status == "protected"
        row.audio_status = "archived"
        refresh_audio(row)
        assert row.audio_status == "archived"
        assert session.query(Recording).count() == 1


def test_archive_routes_require_viewer_in_internet_mode(archive_db, monkeypatch) -> None:
    monkeypatch.setattr(settings, "deployment_mode", "internet")
    monkeypatch.setattr(settings, "auth_mode", "oidc")
    monkeypatch.setattr(settings, "public_base_url", "https://testserver")
    with archive_client(archive_db) as client:
        assert client.get("/api/v1/archive/recordings").status_code == 401
        for role in ("viewer", "operator", "admin"):
            token = create_api_token(f"archive-{role}-{uuid4()}", role)
            response = client.get(
                "/api/v1/archive/recordings", headers={"Authorization": f"Bearer {token}"}
            )
            assert response.status_code == 200
