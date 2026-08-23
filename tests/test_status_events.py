from __future__ import annotations

from pathlib import Path

from asl_transcriber.runtime import ArchiveRuntime
from asl_transcriber.transcription.base import TranscriptResult


def test_runtime_publishes_processing_and_completed_events(tmp_path: Path) -> None:
    recording = tmp_path / "668390" / "call.wav"
    recording.parent.mkdir()
    recording.write_bytes(b"audio")
    runtime = ArchiveRuntime([tmp_path])
    runtime.scan_once()
    runtime.scan_once()
    runtime.subscribe().get_nowait()

    runtime.process_pending(
        lambda _: TranscriptResult(raw_text="hello", display_text="hello")
    )

    events = [runtime.subscribe().get_nowait(), runtime.subscribe().get_nowait()]
    assert [event["status"] for event in events] == ["processing", "completed"]
