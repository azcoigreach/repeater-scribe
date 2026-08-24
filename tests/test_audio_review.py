from __future__ import annotations

from fastapi.testclient import TestClient

from asl_transcriber.main import app


def test_recording_includes_timestamp_and_audio_url(monkeypatch, tmp_path) -> None:
    node_dir = tmp_path / "100000"
    node_dir.mkdir()
    recording = node_dir / "2026082300415497.wav"
    recording.write_bytes(b"RIFF test audio")

    monkeypatch.setattr("asl_transcriber.main.settings.archive_paths", str(tmp_path))
    monkeypatch.setattr("asl_transcriber.main.settings.auto_process", False)
    with TestClient(app) as client:
        client.post("/api/v1/ingestion/scan")
        client.post("/api/v1/ingestion/scan")
        response = client.get("/api/v1/recordings")
        audio = client.get(response.json()["items"][0]["audio_url"])

    item = response.json()["items"][0]
    assert item["timestamp"] == "2026-08-23T00:41:54.970000+00:00"
    assert item["audio_url"].startswith("/api/v1/audio?")
    assert audio.status_code == 200
    assert audio.content == b"RIFF test audio"


def test_audio_endpoint_rejects_paths_outside_configured_archive(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("asl_transcriber.main.settings.archive_paths", str(tmp_path))
    with TestClient(app) as client:
        response = client.get("/api/v1/audio?path=../secret.wav")

    assert response.status_code == 404
