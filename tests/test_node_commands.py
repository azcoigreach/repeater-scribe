from __future__ import annotations

from asl_transcriber.main import NODE_COMMANDS, render_node_command


def test_node_commands_are_allowlisted_and_rendered_server_side() -> None:
    names = {command["name"] for command in NODE_COMMANDS}

    assert {"Show node status", "Show link status", "Reconnect", "Connect node", "Disconnect node"} <= names
    assert render_node_command("Show node status", 668390) == "rpt stats 668390"
    assert render_node_command("Connect node", 668390, "674982") == "rpt cmd 668390 ilink 3 674982"


def test_unknown_command_and_invalid_target_are_rejected() -> None:
    try:
        render_node_command("raw command", 668390)
    except ValueError:
        pass
    else:
        raise AssertionError("unknown commands must be rejected")

    try:
        render_node_command("Connect node", 668390, "not-a-node")
    except ValueError:
        pass
    else:
        raise AssertionError("invalid node targets must be rejected")
