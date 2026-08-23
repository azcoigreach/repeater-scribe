from __future__ import annotations

from fastapi.testclient import TestClient

from asl_transcriber.main import app


def test_dashboard_page_is_available(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("asl_transcriber.main.settings.archive_paths", str(tmp_path))
    monkeypatch.setattr("asl_transcriber.main.settings.auto_process", False)

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "Radio traffic, made searchable." in response.text
    assert "/static/dashboard.css" in response.text
