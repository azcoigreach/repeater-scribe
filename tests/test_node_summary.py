from __future__ import annotations

from datetime import UTC, datetime

from asl_transcriber.ami import AmiResponse
from asl_transcriber.ingestion.activity import ActivityLogEvent
from asl_transcriber.main import summarize_node


def test_node_summary_extracts_channels_stations_and_recent_talkers() -> None:
    response = AmiResponse(
        headers={"Response": "Success"},
        messages=[
            {
                "Event": "Status",
                "Channel": "IAX2/27339-1",
                "ChannelStateDesc": "Up",
                "CallerIDNum": "27339",
                "CallerIDName": "Remote station",
                "Exten": "27339",
            },
            {
                "Event": "Status",
                "Channel": "Local/pseudo@default-1;1",
                "ChannelStateDesc": "Up",
                "CallerIDNum": "<unknown>",
                "CallerIDName": "<unknown>",
            },
        ],
    )
    events = [
        ActivityLogEvent(
            timestamp=datetime.now(UTC), node_id=None, event_type="RXKEY", details="27339"
        ),
        ActivityLogEvent(
            timestamp=datetime.now(UTC), node_id=668390, event_type="LINKCONN", details="27339"
        ),
    ]

    summary = summarize_node(response, events)

    assert summary["ami_connected"] is True
    assert summary["connected_nodes"] == [27339]
    assert summary["connected_stations"][0]["id"] == "27339"
    assert summary["talkers"] == ["27339"]
