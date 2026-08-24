from __future__ import annotations

from pathlib import Path

from asl_transcriber.runtime import ArchiveRuntime
from asl_transcriber.transcription.base import TranscriptResult


def test_runtime_processes_pending_archive_job_with_injected_engine(tmp_path: Path) -> None:
    recording = tmp_path / "100000" / "recording.wav"
    recording.parent.mkdir()
    recording.write_bytes(b"audio")
    runtime = ArchiveRuntime([tmp_path])
    runtime.scan_once()
    runtime.scan_once()

    def transcribe(path: str) -> TranscriptResult:
        assert Path(path) == recording
        return TranscriptResult(raw_text="hello", display_text="hello", language="en")

    results = runtime.process_pending(transcribe)

    assert len(results) == 1
    assert results[0].display_text == "hello"
    assert runtime.jobs()[0].status.value == "completed"
