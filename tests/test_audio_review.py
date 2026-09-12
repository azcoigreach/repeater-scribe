from __future__ import annotations

from fastapi.testclient import TestClient

from asl_transcriber.archive import archive_source_id
from asl_transcriber.main import app, current_runtime


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


def test_duplicate_paths_keep_root_identity_and_audio_through_ingestion(monkeypatch, tmp_path):
    roots = [tmp_path / "one", tmp_path / "two"]
    for root in roots:
        root.mkdir()
        (root / "same.wav").write_bytes(root.name.encode())
    monkeypatch.setattr("asl_transcriber.main.settings.archive_paths", ",".join(map(str, roots)))
    monkeypatch.setattr("asl_transcriber.main.settings.auto_process", False)
    monkeypatch.setattr("asl_transcriber.main.settings.file_stabilization_seconds", 0)
    expected = {archive_source_id(str(root.resolve())): root.name.encode() for root in roots}
    # Avoid lifespan background scans so both discovery phases are deterministic.
    client = TestClient(app)
    runtime = current_runtime()
    runtime.scan_once()
    waiting = client.get("/api/v1/recordings").json()["items"]
    assert len(waiting) == 2
    assert all(item["id"] is None for item in waiting)
    for item in waiting:
        assert client.get(item["audio_url"]).content == expected[item["source_id"]]
    runtime.scan_once()
    ingested = client.get("/api/v1/recordings").json()["items"]
    assert len(ingested) == 2
    assert all(item["id"] for item in ingested)
    assert {(item["source_id"], item["source_path"]) for item in waiting} == {
        (item["source_id"], item["source_path"]) for item in ingested
    }
    for item in ingested:
        assert client.get(item["audio_url"]).content == expected[item["source_id"]]
    source_id = archive_source_id(str(roots[1].resolve()))
    (roots[1] / "same.wav").unlink()
    assert client.get("/api/v1/audio", params={
        "path": "same.wav", "source_id": source_id,
    }).status_code == 404  # Must not fall back to the other root.
    for path, source in (("../one/same.wav", source_id), ("same.wav", "unknown")):
        assert client.get("/api/v1/audio", params={
            "path": path, "source_id": source,
        }).status_code == 404
