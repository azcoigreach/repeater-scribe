from fastapi.testclient import TestClient

from asl_transcriber.main import app


def test_health_endpoint_returns_ok() -> None:
    client = TestClient(app)
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["service"] == "repeater-scribe"
    assert payload["version"] == "0.5.1"


def test_system_info_reports_local_transcription_profile() -> None:
    client = TestClient(app)
    response = client.get("/api/v1/system/info")

    assert response.status_code == 200
    assert response.json()["transcription"]["backend"] == "local"
    assert response.json()["transcription"]["model"]
