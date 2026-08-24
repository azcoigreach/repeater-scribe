from __future__ import annotations

from datetime import UTC, datetime

from asl_transcriber.ingestion.activity import ActivityLogParser


def test_activity_log_parser_reads_asl3_event_lines(tmp_path) -> None:
    log_path = tmp_path / "activity.log"
    log_path.write_text(
        "2026-08-22 14:01:23 NODE 100000: RXKEY (COR 0)\n"
        "2026-08-22 14:01:23 NODE 100000: Audio level: 8500\n"
        "2026-08-22 14:10:13 NODE 100000: RXUNKEY (COR 0)"
    )

    events = ActivityLogParser(log_path).parse()

    assert [event.event_type for event in events] == ["RXKEY", "Audio level", "RXUNKEY"]
    assert events[0].node_id == 100000
    assert events[1].details == "8500"


def test_activity_log_parser_correlates_recordings_to_nearest_event() -> None:
    parser = ActivityLogParser.from_text(
        "2026-08-22 14:01:23 NODE 100000: RXKEY (COR 0)\n"
        "2026-08-22 14:10:03 NODE 100000: Audio level: 9100\n"
        "2026-08-22 14:10:13 NODE 100000: RXUNKEY (COR 0)"
    )

    recording_time = datetime(2026, 8, 22, 14, 10, 13, tzinfo=UTC)
    match = parser.correlate_recording(recording_time, tolerance_seconds=30)

    assert match is not None
    assert match.event_type == "RXUNKEY"
    assert match.node_id == 100000
