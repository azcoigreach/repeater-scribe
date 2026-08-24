from __future__ import annotations

from fastapi.testclient import TestClient

from asl_transcriber.main import app, render_node_command


def test_network_status_uses_asterisk_iax_network_statistics_command() -> None:
    assert render_node_command("Show Network Status", 100000) == "iax2 show netstats"


def test_announce_functions_use_app_rpt_status_commands() -> None:
    assert render_node_command("Announce", 100000) == "rpt cmd 100000 status 11"
    assert render_node_command("Say Time of Day", 100000) == "rpt cmd 100000 status 12"
    assert render_node_command("Force ID", 100000) == "rpt cmd 100000 status 1"


def test_node_function_requires_control_enablement_and_api_key(monkeypatch) -> None:
    monkeypatch.setattr("asl_transcriber.main.settings.ami_control_enabled", True)
    monkeypatch.setattr("asl_transcriber.main.settings.api_key", "test-key")

    with TestClient(app) as client:
        missing = client.post("/api/v1/node/100000/function", json={"function": "*3"})
        invalid = client.post(
            "/api/v1/node/100000/function",
            headers={"X-API-Key": "test-key"},
            json={"function": "shutdown"},
        )

    assert missing.status_code == 401
    assert invalid.status_code == 422


def test_node_command_requires_api_key(monkeypatch) -> None:
    monkeypatch.setattr("asl_transcriber.main.settings.ami_control_enabled", True)
    monkeypatch.setattr("asl_transcriber.main.settings.api_key", "test-key")

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/node/100000/command",
            json={"name": "Disconnect node", "target": "674982"},
        )

    assert response.status_code == 401


def test_node_command_requires_confirmation(monkeypatch) -> None:
    monkeypatch.setattr("asl_transcriber.main.settings.ami_control_enabled", True)
    monkeypatch.setattr("asl_transcriber.main.settings.api_key", "test-key")

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/node/100000/command",
            headers={"X-API-Key": "test-key"},
            json={"name": "Disconnect node", "target": "674982"},
        )

    assert response.status_code == 400


def test_ui_command_uses_server_configured_credentials(monkeypatch) -> None:
    monkeypatch.setattr("asl_transcriber.main.settings.ami_control_enabled", False)

    with TestClient(app) as client:
        response = client.post(
            "/ui/node/100000/command",
            json={"name": "Show node status", "confirmed": True},
        )

    assert response.status_code == 503
