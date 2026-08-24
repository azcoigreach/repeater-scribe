from __future__ import annotations

from pathlib import Path

from asl_transcriber.runtime import ArchiveRuntime


def test_runtime_scans_all_roots_and_reads_node_activity_logs(tmp_path: Path) -> None:
    first = tmp_path / "first" / "100000"
    second = tmp_path / "second" / "700001"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    (first / "2026082214012300.wav").write_bytes(b"first")
    (second / "2026082214022300.wav").write_bytes(b"second")
    (first / "activity.log").write_text(
        "2026-08-22 14:01:23 NODE 100000: RXKEY (COR 0)\n"
    )

    runtime = ArchiveRuntime([first.parent, second.parent])

    assert runtime.scan_once() == []
    jobs = runtime.scan_once()

    assert {job.source_path for job in jobs} == {
        "100000/2026082214012300.wav",
        "700001/2026082214022300.wav",
    }
    assert runtime.activity_events()[0].node_id == 100000
    assert runtime.activity_events()[0].event_type == "RXKEY"
