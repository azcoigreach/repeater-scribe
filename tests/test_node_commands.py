from __future__ import annotations

from asl_transcriber.main import NODE_COMMANDS, render_node_command


def test_node_commands_are_allowlisted_and_rendered_server_side() -> None:
    names = {command["name"] for command in NODE_COMMANDS}

    assert {
        "Show node status",
        "Show link status",
        "Reconnect",
        "Connect node",
        "Disconnect node",
    } <= names
    assert render_node_command("Show node status", 100000) == "rpt stats 100000"
    assert render_node_command("Connect node", 100000, "674982") == "rpt cmd 100000 ilink 3 674982"


def test_unknown_command_and_invalid_target_are_rejected() -> None:
    try:
        render_node_command("raw command", 100000)
    except ValueError:
        pass
    else:
        raise AssertionError("unknown commands must be rejected")

    try:
        render_node_command("Connect node", 100000, "not a node!")
    except ValueError:
        pass
    else:
        raise AssertionError("invalid node targets must be rejected")


def test_callsign_target_is_preserved_for_direct_client_control() -> None:
    assert (
        render_node_command("Disconnect node", 100000, "KM7GHS") == "rpt cmd 100000 ilink 1 KM7GHS"
    )
