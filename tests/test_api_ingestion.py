from __future__ import annotations

from fastapi.testclient import TestClient

from asl_transcriber.main import app


def test_ingestion_api_reports_configured_archive_and_scans(monkeypatch, tmp_path) -> None:
    node_dir = tmp_path / "668390"
    node_dir.mkdir()
    (node_dir / "2026082214012300.wav").write_bytes(b"audio")
    (node_dir / "activity.log").write_text(
        "2026-08-22 14:01:23 NODE 668390: RXKEY (COR 0)\n"
    )

    monkeypatch.setattr("asl_transcriber.main.settings.archive_paths", str(tmp_path))
    monkeypatch.setattr("asl_transcriber.main.settings.auto_process", False)
    with TestClient(app) as client:
        scan = client.post("/api/v1/ingestion/scan")
        jobs = client.get("/api/v1/ingestion/jobs")
        activity = client.get("/api/v1/activity")

    assert scan.status_code == 200
    assert scan.json()["created"] == 0
    assert jobs.json()["total"] == 1
    assert jobs.json()["items"][0]["source_path"] == "668390/2026082214012300.wav"
    assert activity.json()["total"] == 1
    assert activity.json()["items"][0]["event_type"] == "RXKEY"
