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


def test_dashboard_renders_active_nodes_from_connected_node_ids() -> None:
    script = Path("src/asl_transcriber/static/dashboard.js").read_text()

    assert "data.connected_nodes.forEach" in script
    assert "ASL3 remote node" in script
    assert "No connected nodes." in script
