from __future__ import annotations

from pathlib import Path

from asl_transcriber.runtime import ArchiveRuntime
from asl_transcriber.transcription.base import TranscriptResult
from asl_transcriber.transcription.live import LiveTranscriptionService, merge_overlapping_text


def test_rolling_transcript_merges_window_overlap() -> None:
    assert merge_overlapping_text(
        "hello from kilo mike seven", "kilo mike seven golf hotel sierra"
    ) == "hello from kilo mike seven golf hotel sierra"


def test_live_service_publishes_provisional_result_for_growing_file(tmp_path: Path) -> None:
    recording = tmp_path / "archive" / "668390" / "active.wav"
    recording.parent.mkdir(parents=True)
    recording.write_bytes(b"audio" * 1000)
    runtime = ArchiveRuntime([recording.parents[1]])
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
    assert runtime.live_results["668390/active.wav"].display_text == "KM7GHS"
    event = runtime.subscribe().get_nowait()
    assert event["status"] == "live"
    assert event["provisional"] is True

    assert service.process_once(runtime) == 0
