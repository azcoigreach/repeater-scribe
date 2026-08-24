from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from asl_transcriber.main import app


def test_dashboard_page_is_available(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("asl_transcriber.main.settings.archive_paths", str(tmp_path))
    monkeypatch.setattr("asl_transcriber.main.settings.auto_process", False)
    monkeypatch.setattr("asl_transcriber.main.settings.ami_node_id", "668390")

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "Connected nodes" in response.text
    assert 'data-controlled-node="668390"' in response.text
    assert "Operating node" not in response.text
    assert "/static/dashboard.css" in response.text


def test_dashboard_renders_normalized_callsign_rows_from_sse() -> None:
    script = Path("src/asl_transcriber/static/dashboard.js").read_text()

    assert "connection.identifier" in script
    assert "connection.callsign" in script
    assert "node-state" in script
    assert "setInterval(loadNodeStatus, 5000)" not in script
    assert "enableNodeRestFallback" in script
    assert "No connected nodes." in script


def test_dashboard_waits_for_snapshot_confirmation_after_control() -> None:
    script = Path("src/asl_transcriber/static/dashboard.js").read_text()

    assert "waiting for node state to confirm" in script
    assert "state confirmation was not received" in script
    assert "confirmPendingControl(connections)" in script
