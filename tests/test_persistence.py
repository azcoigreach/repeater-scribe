from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from asl_transcriber.database import Base
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
