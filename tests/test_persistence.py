from __future__ import annotations

import os
import time
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from asl_transcriber.database import Base
from asl_transcriber.models import IngestionJob, Transcript
from asl_transcriber.runtime import ArchiveRuntime
from asl_transcriber.transcription.base import TranscriptCallsignMention, TranscriptResult


def test_runtime_restores_jobs_and_transcripts_from_sqlite(tmp_path: Path) -> None:
    archive = tmp_path / "archive" / "100000"
    archive.mkdir(parents=True)
    (archive / "call.wav").write_bytes(b"audio")
    engine = create_engine(f"sqlite:///{tmp_path / 'state.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)

    first = ArchiveRuntime([archive.parent], session_factory=sessions)
    first.scan_once()
    first.scan_once()
    first.process_pending(
        lambda _: TranscriptResult(
            raw_text="hello KM7GHS",
            display_text="hello KM7GHS",
            language="en",
            callsign_mentions=[
                TranscriptCallsignMention(
                    "KM7GHS",
                    3.0,
                    4.25,
                    confidence=0.87,
                    acoustic_confidence=0.81,
                    recognition_confidence=0.94,
                    evidence=("Decoded directly as a formatted callsign",),
                )
            ],
        )
    )

    second = ArchiveRuntime([archive.parent], session_factory=sessions)

    assert second.jobs()[0].status.value == "completed"
    restored = second.results[second.jobs()[0].id]
    assert restored.display_text == "hello KM7GHS"
    assert restored.callsign_mentions == [
        TranscriptCallsignMention(
            "KM7GHS",
            3.0,
            4.25,
            confidence=0.87,
            acoustic_confidence=0.81,
            recognition_confidence=0.94,
            evidence=("Decoded directly as a formatted callsign",),
        )
    ]


def test_runtime_requeues_recording_that_grew_after_transcription(tmp_path: Path) -> None:
    archive = tmp_path / "archive" / "100000"
    archive.mkdir(parents=True)
    recording = archive / "call.wav"
    recording.write_bytes(b"partial audio")
    engine = create_engine(f"sqlite:///{tmp_path / 'state.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)

    first = ArchiveRuntime([archive.parent], session_factory=sessions)
    first.scan_once()
    first.scan_once()
    first.process_pending(
        lambda _: TranscriptResult(raw_text="partial", display_text="partial", language="en")
    )
    future = time.time() + 10
    recording.write_bytes(b"complete audio recording")
    os.utime(recording, (future, future))

    second = ArchiveRuntime([archive.parent], session_factory=sessions)

    assert second.jobs()[0].status.value == "pending"
    assert second.results == {}
    second.process_pending(
        lambda _: TranscriptResult(raw_text="complete", display_text="complete", language="en")
    )
    with sessions() as session:
        transcripts = session.query(Transcript).all()
        assert len(transcripts) == 1
        assert transcripts[0].display_text == "complete"


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
                source_path=f"100000/call-{index}.wav",
                archive_root=archive_root,
                status="completed",
            )
            session.add(job)
            if index < 502:
                session.add(Transcript(job=job, raw_text="hello", display_text="hello"))
        session.commit()

    runtime = ArchiveRuntime([archive], session_factory=sessions)

    assert runtime.database_totals() == {"recordings": 503, "transcribed": 502}
