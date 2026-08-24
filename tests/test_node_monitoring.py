from __future__ import annotations

from asl_transcriber.ami import AmiFrame
from asl_transcriber.node_control import NodeState, normalize_app_rpt_event, parse_alinks


def test_parse_alinks_supports_numeric_and_callsign_identifiers() -> None:
    links = parse_alinks("2,41522TU,667342TK")

    assert [(link.identifier, link.mode, link.keyed) for link in links] == [
        ("41522", "T", False),
        ("667342", "T", True),
    ]


def test_parse_alinks_preserves_unknown_mode() -> None:
    link = parse_alinks("1,KC2ABCZU")[0]

    assert link.identifier == "KC2ABC"
    assert link.mode == "Z"
    assert link.mode_name == "unknown"
    assert not link.keyed


def test_native_keying_does_not_assign_home_callsign_to_local_rf() -> None:
    state = NodeState(home_node="668390")
    state = normalize_app_rpt_event(
        state, AmiFrame({"event": ["RPT_RXKEYED"], "eventvalue": ["1"]})
    )

    assert state.local_rx_keyed
    assert state.keyed_links == []
