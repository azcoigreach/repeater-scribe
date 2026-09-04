from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import wave
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

ROOT = Path(__file__).parents[1]


def alembic(database: Path, revision: str) -> None:
    environment = os.environ | {"ASLT_DATABASE_URL": f"sqlite:///{database}"}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def downgrade(database: Path, revision: str) -> None:
    environment = os.environ | {"ASLT_DATABASE_URL": f"sqlite:///{database}"}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", revision],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_fresh_archive_migration_creates_catalog_and_fts(tmp_path: Path) -> None:
    database = tmp_path / "fresh.db"
    alembic(database, "archive_foundation")
    connection = sqlite3.connect(database)
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'trigger')")}
    assert {"recordings", "ingestion_jobs", "transcripts", "transcript_fts", "transcript_fts_insert"} <= tables
    assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == ("archive_foundation",)
    from asl_transcriber.models import TopologyNodeSnapshot

    engine = sa.create_engine(f"sqlite:///{database}")
    with sa.orm.Session(engine) as session:
        session.add(TopologyNodeSnapshot(home_node="100", identifier="200"))
        session.commit()


def test_seeded_security_database_backfills_catalog_across_roots_and_reupgrades(tmp_path: Path) -> None:
    database = tmp_path / "legacy.db"
    root_one = tmp_path / "root-one"
    root_two = tmp_path / "root-two"
    root_one.mkdir()
    root_two.mkdir()
    with wave.open(str(root_one / "2026090101010100.wav"), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\0\0" * 8000)
    alembic(database, "security_hardening")
    connection = sqlite3.connect(database)
    connection.execute("ALTER TABLE ingestion_jobs ADD COLUMN archive_root VARCHAR(1024)")
    created = datetime(2026, 9, 1, tzinfo=UTC).isoformat()
    evidence = '[{"callsign":"KM7GHS","start":1.0,"end":2.0,"evidence":["direct"]}]'
    jobs = [
        ("done", "2026090101010100.wav", str(root_one), "completed"),
        ("pending", "same.wav", str(root_one), "pending"),
        ("failed", "same.wav", str(root_two), "failed"),
        ("missing", "gone.wav", str(root_two), "completed"),
    ]
    connection.executemany("INSERT INTO ingestion_jobs (id, created_at, updated_at, source_path, archive_root, status, attempt_count, dead_letter) VALUES (?, ?, ?, ?, ?, ?, 0, 0)", [(job_id, created, created, path, root, status) for job_id, path, root, status in jobs])
    connection.execute("INSERT INTO transcripts (id, created_at, updated_at, job_id, raw_text, display_text, language, confidence, callsign_mentions_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", ("transcript", created, created, "done", "raw historic", "corrected historic", "en", 0.9, evidence))
    connection.commit()
    connection.close()

    alembic(database, "archive_foundation")
    connection = sqlite3.connect(database)
    rows = connection.execute("SELECT id, archive_root, source_path, audio_status FROM recordings ORDER BY id").fetchall()
    assert rows == [
        ("done", str(root_one), "2026090101010100.wav", "available"),
        ("failed", str(root_two), "same.wav", "missing"),
        ("missing", str(root_two), "gone.wav", "missing"),
        ("pending", str(root_one), "same.wav", "missing"),
    ]
    assert connection.execute("SELECT recording_id, callsign_mentions_json FROM transcripts").fetchone() == ("done", evidence)
    assert connection.execute("SELECT recording_id FROM ingestion_jobs WHERE id = 'done'").fetchone() == ("done",)
    assert connection.execute("SELECT duration_seconds FROM recordings WHERE id = 'done'").fetchone() == (1.0,)
    assert connection.execute("SELECT count(*) FROM transcript_fts WHERE transcript_fts MATCH 'historic'").fetchone() == (1,)
    connection.close()

    downgrade(database, "security_hardening")
    alembic(database, "archive_foundation")
    connection = sqlite3.connect(database)
    assert connection.execute("SELECT count(*) FROM recordings").fetchone() == (4,)
    archive_root, started_at, audio_status = connection.execute(
        "SELECT archive_root, started_at, audio_status FROM recordings WHERE id = 'done'"
    ).fetchone()
    assert archive_root == str(root_one)
    assert datetime.fromisoformat(started_at) == datetime(2026, 9, 1, 1, 1, 1, tzinfo=UTC)
    assert audio_status == "available"


def test_historical_migrations_do_not_create_future_registered_models(tmp_path: Path) -> None:
    database = tmp_path / "frozen.db"
    from asl_transcriber.database import Base

    future = sa.Table("hypothetical_future_model", Base.metadata, sa.Column("id", sa.Integer, primary_key=True))
    try:
        alembic(database, "security_hardening")
        inspector = sa.inspect(sa.create_engine(f"sqlite:///{database}"))
        assert not inspector.has_table(future.name)
    finally:
        Base.metadata.remove(future)


def test_populated_archive_foundation_upgrades_callsign_history_safely(tmp_path: Path) -> None:
    database = tmp_path / "archive-foundation.db"
    alembic(database, "archive_foundation")
    connection = sqlite3.connect(database)
    created = datetime(2026, 9, 2, tzinfo=UTC).isoformat()
    mention_json = (
        '[{"callsign":"KM7GHS","start":"1.5","end":"2.5",'
        '"confidence":"Infinity","acoustic_confidence":"NaN",'
        '"recognition_confidence":"bad"},'
        '{"callsign":"KM7GHS","start":3,"end":4},'
        '{"callsign":"bad","start":0,"end":1}]'
    )
    connection.execute(
        "INSERT INTO recordings (id, created_at, updated_at, source_path, archive_root, "
        "started_at, status, audio_status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("recording", created, created, "2026090200000000.wav", "/one", created, "completed", "missing"),
    )
    connection.execute(
        "INSERT INTO ingestion_jobs (id, created_at, updated_at, source_path, archive_root, "
        "status, attempt_count, dead_letter, recording_id) VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?)",
        ("job", created, created, "2026090200000000.wav", "/one", "completed", "recording"),
    )
    connection.execute(
        "INSERT INTO transcripts (id, created_at, updated_at, job_id, raw_text, display_text, "
        "callsign_mentions_json, recording_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("transcript", created, created, "job", "raw", "display", mention_json, "recording"),
    )
    connection.commit()
    connection.close()

    alembic(database, "callsign_intelligence")
    connection = sqlite3.connect(database)
    assert connection.execute("SELECT count(*) FROM callsigns").fetchone() == (1,)
    assert connection.execute("SELECT count(*) FROM callsign_mentions").fetchone() == (2,)
    assert connection.execute(
        "SELECT confidence, acoustic_confidence, recognition_confidence FROM callsign_mentions LIMIT 1"
    ).fetchone() == (None, None, None)
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert connection.execute(
        "SELECT current_transcript_id FROM recordings WHERE id = 'recording'"
    ).fetchone() == ("transcript",)


def test_startup_schema_guard_accepts_current_and_rejects_outdated(tmp_path: Path, monkeypatch) -> None:
    from asl_transcriber import database as database_module
    from asl_transcriber.main import app

    current = tmp_path / "current.db"
    alembic(current, "head")
    current_engine = sa.create_engine(f"sqlite:///{current}")
    monkeypatch.setattr(database_module, "engine", current_engine)
    with TestClient(app):
        pass

    outdated = tmp_path / "outdated.db"
    alembic(outdated, "security_hardening")
    monkeypatch.setattr(database_module, "engine", sa.create_engine(f"sqlite:///{outdated}"))
    with pytest.raises(RuntimeError, match="alembic upgrade head"), TestClient(app):
        pass
