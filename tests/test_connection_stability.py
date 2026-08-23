from __future__ import annotations

from time import monotonic

from asl_transcriber.main import stable_node_summary


def test_node_summary_keeps_last_remote_connection_during_brief_ami_gap() -> None:
    connected = {
        "ami_connected": True,
        "connected_nodes": [674982],
        "connected_stations": [
            {"id": "674982", "name": "Remote", "channel": "IAX2/remote", "state": "Up"}
        ],
        "active_channels": ["IAX2/remote"],
        "talkers": [],
    }
    empty = {
        "ami_connected": True,
        "connected_nodes": [],
        "connected_stations": [],
        "active_channels": ["Local/pseudo"],
        "talkers": [],
    }

    now = monotonic()
    retained = stable_node_summary(empty, connected, now, last_seen=now)

    assert retained["connected_nodes"] == [674982]
    assert retained["connected_stations"] == connected["connected_stations"]
    assert retained["active_channels"] == empty["active_channels"]
