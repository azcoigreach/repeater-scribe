from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from asl_transcriber.database import Base
from asl_transcriber.models import IngestionJob, Transcript
from asl_transcriber.runtime import ArchiveRuntime
from asl_transcriber.transcription.base import TranscriptResult


def test_runtime_restores_jobs_and_transcripts_from_sqlite(tmp_path: Path) -> None:
    archive = tmp_path / "archive" / "668390"
    archive.mkdir(parents=True)
    (archive / "call.wav").write_bytes(b"audio")
    engine = create_engine(f"sqlite:///{tmp_path / 'state.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)

    first = ArchiveRuntime([archive.parent], session_factory=sessions)
    first.scan_once()
    first.scan_once()
    first.process_pending(
        lambda _: TranscriptResult(raw_text="hello", display_text="hello", language="en")
    )

    second = ArchiveRuntime([archive.parent], session_factory=sessions)

    assert second.jobs()[0].status.value == "completed"
    assert second.results[second.jobs()[0].id].display_text == "hello"


def test_database_totals_are_not_limited_to_dashboard_page_size(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    archive.mkdir()
    engine = create_engine(f"sqlite:///{tmp_path / 'totals.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    archive_root = str(archive.resolve())

    with sessions() as session:
        for index in range(503):
            job = IngestionJob(
                source_path=f"668390/call-{index}.wav",
                archive_root=archive_root,
                status="completed",
            )
            session.add(job)
            if index < 502:
                session.add(Transcript(job=job, raw_text="hello", display_text="hello"))
        session.commit()

    runtime = ArchiveRuntime([archive], session_factory=sessions)

    assert runtime.database_totals() == {"recordings": 503, "transcribed": 502}
