from __future__ import annotations

from pathlib import Path

from asl_transcriber.ingestion.jobs import JobStore
from asl_transcriber.ingestion.service import ArchiveIngestionService


def test_runtime_style_scan_waits_for_recording_to_stop_growing(tmp_path: Path) -> None:
    recording = tmp_path / "668390" / "long-call.wav"
    recording.parent.mkdir()
    recording.write_bytes(b"partial")
    service = ArchiveIngestionService(tmp_path, JobStore(), require_stable=True)

    assert service.scan_once() == []
    recording.write_bytes(b"complete-recording")
    assert service.scan_once() == []
    assert len(service.scan_once()) == 1