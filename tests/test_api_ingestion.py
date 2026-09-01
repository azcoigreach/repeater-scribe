from __future__ import annotations

from fastapi.testclient import TestClient

from asl_transcriber.main import app


def test_ingestion_api_reports_configured_archive_and_scans(monkeypatch, tmp_path) -> None:
    node_dir = tmp_path / "100000"
    node_dir.mkdir()
    (node_dir / "2026082214012300.wav").write_bytes(b"audio")
    (node_dir / "activity.log").write_text(
        "2026-08-22 14:01:23 NODE 100000: RXKEY (COR 0)\n"
    )

    monkeypatch.setattr("asl_transcriber.main.settings.archive_paths", str(tmp_path))
    monkeypatch.setattr("asl_transcriber.main.settings.auto_process", False)
    monkeypatch.setattr("asl_transcriber.main.settings.file_stabilization_seconds", 0)
    monkeypatch.setattr("asl_transcriber.main.settings.api_key", "test-key")
    with TestClient(app) as client:
        unauthorized = client.post("/api/v1/ingestion/scan")
        scan = client.post(
            "/api/v1/ingestion/scan", headers={"X-API-Key": "test-key"}
        )
        jobs = client.get("/api/v1/ingestion/jobs")
        activity = client.get("/api/v1/activity")

    assert unauthorized.status_code == 401
    assert scan.status_code == 200
    assert scan.json()["created"] == 0
    assert jobs.json()["total"] == 1
    assert jobs.json()["items"][0]["source_path"] == "100000/2026082214012300.wav"
    assert activity.json()["total"] == 1
    assert activity.json()["items"][0]["event_type"] == "RXKEY"
