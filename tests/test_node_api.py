from __future__ import annotations

from fastapi.testclient import TestClient

from asl_transcriber.main import app


def test_node_status_is_disabled_without_ami_configuration(monkeypatch) -> None:
    monkeypatch.setattr("asl_transcriber.main.settings.ami_enabled", False)

    with TestClient(app) as client:
        response = client.get("/api/v1/node/status")

    assert response.status_code == 503
    assert response.json()["detail"] == "AMI integration is disabled"
