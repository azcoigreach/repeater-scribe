from __future__ import annotations

from pathlib import Path

from asl_transcriber.runtime import ArchiveRuntime


def test_runtime_publishes_discovered_job_event(tmp_path: Path) -> None:
    node_dir = tmp_path / "100000"
    node_dir.mkdir()
    (node_dir / "call.wav").write_bytes(b"audio")
    runtime = ArchiveRuntime([tmp_path])

    runtime.scan_once()
    runtime.scan_once()
    event = runtime.subscribe().get_nowait()

    assert event["source_path"] == "100000/call.wav"
    assert event["status"] == "pending"
