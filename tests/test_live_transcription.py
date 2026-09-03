from __future__ import annotations

import os
from pathlib import Path

from asl_transcriber.runtime import ArchiveRuntime
from asl_transcriber.transcription.base import TranscriptResult
from asl_transcriber.transcription.live import LiveTranscriptionService, merge_overlapping_text


def test_rolling_transcript_merges_window_overlap() -> None:
    assert merge_overlapping_text(
        "hello from kilo mike seven", "kilo mike seven golf hotel sierra"
    ) == "hello from kilo mike seven golf hotel sierra"


def test_rolling_transcript_replaces_redecoded_window() -> None:
    current = (
        "Hopefully I have something by winter. I just have to figure out what it is "
        "I absolutely want, you know what I mean?"
    )

    assert (
        merge_overlapping_text(
            "Hopefully I have something, by winter. I just have to figure out how to get it.",
            current,
        )
        == current
    )


def test_rolling_transcript_keeps_stable_prefix_when_replacing_redecoded_window() -> None:
    current = (
        "Hopefully I have something by winter. I just have to figure out what it is "
        "I absolutely want."
    )

    assert merge_overlapping_text(
        "Earlier stable words stay here. Hopefully I have something, by winter. "
        "I just have to figure out how to get it.",
        current,
    ) == f"Earlier stable words stay here. {current}"


def test_rolling_transcript_appends_unrelated_window() -> None:
    assert merge_overlapping_text(
        "The first completed thought ends here.",
        "A completely unrelated topic starts now.",
    ) == "The first completed thought ends here. A completely unrelated topic starts now."


def test_live_service_publishes_provisional_result_for_growing_file(tmp_path: Path) -> None:
    recording = tmp_path / "archive" / "100000" / "active.wav"
    recording.parent.mkdir(parents=True)
    recording.write_bytes(b"audio" * 1000)
    runtime = ArchiveRuntime([recording.parents[1]])
    subscriber = runtime.subscribe()
    runtime.scan_once()

    class Snapshotter:
        def snapshot(self, source: Path) -> Path:
            assert source == recording
            snapshot = tmp_path / "snapshot.wav"
            snapshot.write_bytes(b"snapshot")
            return snapshot

    service = LiveTranscriptionService(
        snapshotter=Snapshotter(),  # type: ignore[arg-type]
        transcribe=lambda _: TranscriptResult(
            raw_text="Kilo Mike Seven Golf Hotel Sierra",
            display_text="KM7GHS",
            language="en",
        ),
    )

    assert service.process_once(runtime) == 1
    assert runtime.live_results["100000/active.wav"].display_text == "KM7GHS"
    event = subscriber.get_nowait()
    assert event["status"] == "live"
    assert event["provisional"] is True

    assert service.process_once(runtime) == 0


def test_live_service_processes_all_growing_files_newest_first(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    older = archive / "100000" / "older.wav"
    newer = archive / "100000" / "newer.wav"
    older.parent.mkdir(parents=True)
    older.write_bytes(b"older" * 1000)
    newer.write_bytes(b"newer" * 1000)
    os.utime(older, ns=(1_000_000_000, 1_000_000_000))
    os.utime(newer, ns=(2_000_000_000, 2_000_000_000))
    runtime = ArchiveRuntime([archive])
    runtime.scan_once()
    processed: list[str] = []

    class Snapshotter:
        def snapshot(self, source: Path) -> Path:
            processed.append(source.name)
            snapshot = tmp_path / "snapshot.wav"
            snapshot.write_bytes(b"snapshot")
            return snapshot

    service = LiveTranscriptionService(
        snapshotter=Snapshotter(),  # type: ignore[arg-type]
        transcribe=lambda _: TranscriptResult(raw_text="live", display_text="live"),
    )

    assert service.process_once(runtime) == 2
    assert processed == ["newer.wav", "older.wav"]
    assert "100000/newer.wav" in runtime.live_results
    assert "100000/older.wav" in runtime.live_results
