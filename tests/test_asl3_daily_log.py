from __future__ import annotations

from asl_transcriber.ingestion.activity import ActivityLogParser


def test_parser_reads_asl3_daily_csv_log(tmp_path) -> None:
    log_path = tmp_path / "20260823.txt"
    log_path.write_text(
        "2026082300110491,RXKEY,MAIN\n"
        "2026082300110600,LINKMONITOR,27339\n"
        "2026082300110691,TELEMETRY,100000,COMPLETE(T),(1000,0,100,2048)\n"
    )

    events = ActivityLogParser(log_path).parse()

    assert [event.event_type for event in events] == ["RXKEY", "LINKMONITOR", "TELEMETRY"]
    assert events[0].timestamp.isoformat() == "2026-08-23T00:11:04.910000+00:00"
    assert events[0].node_id is None
    assert events[0].details == "MAIN"
    assert events[2].node_id == 100000


def test_parser_ignores_invalid_daily_log_rows(tmp_path) -> None:
    log_path = tmp_path / "20260823.txt"
    log_path.write_text("not-an-asl3-row\n2026082300110491,RXKEY,MAIN\n")

    assert len(ActivityLogParser(log_path).parse()) == 1
