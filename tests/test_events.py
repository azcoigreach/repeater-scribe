from __future__ import annotations

from pathlib import Path

from asl_transcriber.runtime import ArchiveRuntime


def test_runtime_publishes_discovered_job_event(tmp_path: Path) -> None:
    node_dir = tmp_path / "100000"
    node_dir.mkdir()
    (node_dir / "call.wav").write_bytes(b"audio")
    runtime = ArchiveRuntime([tmp_path])
    subscriber = runtime.subscribe()

    runtime.scan_once()
    runtime.scan_once()
    event = subscriber.get_nowait()

    assert event["source_path"] == "100000/call.wav"
    assert event["status"] == "pending"


def test_runtime_broadcasts_events_to_independent_subscribers(tmp_path: Path) -> None:
    node_dir = tmp_path / "100000"
    node_dir.mkdir()
    (node_dir / "call.wav").write_bytes(b"audio")
    runtime = ArchiveRuntime([tmp_path])
    first = runtime.subscribe()
    second = runtime.subscribe()

    runtime.scan_once()
    runtime.scan_once()

    assert first is not second
    assert first.get_nowait()["source_path"] == "100000/call.wav"
    assert second.get_nowait()["source_path"] == "100000/call.wav"
    runtime.unsubscribe(first)
    assert first not in runtime._subscribers
