from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from asl_transcriber.database import Base
from asl_transcriber.ingestion.scanner import ArchiveScanner
from asl_transcriber.models import IngestionJob as DbIngestionJob
from asl_transcriber.models import Transcript
from asl_transcriber.runtime import ArchiveRuntime


def test_retention_hides_old_archive_audio(tmp_path) -> None:
    archive = tmp_path / "archive"
    archive.mkdir()
    old = archive / "old.wav"
    recent = archive / "recent.wav"
    old.write_bytes(b"old")
    recent.write_bytes(b"recent")
    old_time = (datetime.now(UTC) - timedelta(days=2)).timestamp()
    os.utime(old, (old_time, old_time))

    entries = ArchiveScanner(archive, retention_days=1).discover()

    assert [entry.source_path for entry in entries] == ["recent.wav"]


def test_retention_purges_derived_transcript_without_deleting_source(tmp_path) -> None:
    archive = tmp_path / "archive"
    archive.mkdir()
    old = archive / "old.wav"
    old.write_bytes(b"old")
    old_time = (datetime.now(UTC) - timedelta(days=2)).timestamp()
    os.utime(old, (old_time, old_time))
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)
    with sessions() as session:
        job = DbIngestionJob(
            source_path="old.wav",
            archive_root=str(archive.resolve()),
            status="completed",
            created_at=datetime.now(UTC) - timedelta(days=2),
        )
        session.add(job)
        session.flush()
        session.add(Transcript(job_id=job.id, raw_text="private", display_text="private"))
        session.commit()

    runtime = ArchiveRuntime([archive], sessions, retention_days=1)

    assert runtime.purge_expired() == 1
    assert old.exists()
    with sessions() as session:
        assert session.scalar(select(DbIngestionJob)) is None
        assert session.scalar(select(Transcript)) is None
