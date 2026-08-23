from __future__ import annotations

from fastapi.testclient import TestClient

from asl_transcriber.main import app


def test_recordings_endpoint_filters_transcript_text(monkeypatch, tmp_path) -> None:
    node_dir = tmp_path / "668390"
    node_dir.mkdir()
    (node_dir / "one.wav").write_bytes(b"one")
    (node_dir / "two.wav").write_bytes(b"two")

    monkeypatch.setattr("asl_transcriber.main.settings.archive_paths", str(tmp_path))
    monkeypatch.setattr("asl_transcriber.main.settings.auto_process", False)
    with TestClient(app) as client:
        client.post("/api/v1/ingestion/scan")
        active_runtime = __import__("asl_transcriber.main", fromlist=["runtime"]).runtime
        active_runtime.results[active_runtime.jobs()[0].id] = type(
            "Result",
            (),
            {"raw_text": "weather check", "display_text": "Weather check", "language": "en"},
        )()
        response = client.get("/api/v1/recordings?q=weather")

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["transcript"]["display_text"] == "Weather check"


def test_recordings_total_is_not_truncated_by_limit(monkeypatch, tmp_path) -> None:
    node_dir = tmp_path / "668390"
    node_dir.mkdir()
    for index in range(3):
        (node_dir / f"call-{index}.wav").write_bytes(str(index).encode())

    monkeypatch.setattr("asl_transcriber.main.settings.archive_paths", str(tmp_path))
    monkeypatch.setattr("asl_transcriber.main.settings.auto_process", False)
    with TestClient(app) as client:
        client.post("/api/v1/ingestion/scan")
        client.post("/api/v1/ingestion/scan")
        response = client.get("/api/v1/recordings?limit=1")

    assert response.json()["total"] == 3
    assert len(response.json()["items"]) == 1
