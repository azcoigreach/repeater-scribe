from __future__ import annotations

from datetime import UTC, datetime

from asl_transcriber.ami import AmiResponse
from asl_transcriber.node_control import parse_xstat_snapshot


def response(**headers: list[str]) -> AmiResponse:
    raw = {"response": ["Success"], **{key.casefold(): value for key, value in headers.items()}}
    return AmiResponse({"Response": "Success"}, [], raw_headers=raw)


def test_xstat_snapshot_parses_repeated_numeric_callsign_and_key_timing() -> None:
    now = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
    xstat = response(
        Conn=[
            "27339 198.51.100.4 0 OUT 00:01:05 ESTABLISHED",
            "KM7GHS (none) 0 IN 00:00:02 CONNECTING",
        ],
        LinkedNodes=["T27339, R99999"],
    )
    sawstat = response(Conn=["27339 1 3 40", "KM7GHS 0 -1 2"])

    links, topology = parse_xstat_snapshot(xstat, sawstat, now=now)

    assert list(links) == ["27339", "KM7GHS"]
    assert links["27339"].node_number == "27339"
    assert links["27339"].keyed is True
    assert links["27339"].seconds_since_keyed == 3
    assert links["27339"].direction == "out"
    assert links["27339"].connected_at.isoformat() == "2026-08-23T11:58:55+00:00"
    assert links["KM7GHS"].callsign == "KM7GHS"
    assert links["KM7GHS"].connection_state == "connecting"
    assert links["KM7GHS"].peer is None
    assert links["KM7GHS"].keyed is False
    assert topology == ["T27339", "R99999"]
