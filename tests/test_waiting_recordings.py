from __future__ import annotations

from pathlib import Path

from asl_transcriber.runtime import ArchiveRuntime


def test_runtime_exposes_file_seen_but_not_yet_stable(tmp_path: Path) -> None:
    recording = tmp_path / "100000" / "active.wav"
    recording.parent.mkdir()
    recording.write_bytes(b"still-writing")
    runtime = ArchiveRuntime([tmp_path])

    runtime.scan_once()

    assert runtime.waiting_sources() == ["100000/active.wav"]
    assert runtime.jobs() == []
