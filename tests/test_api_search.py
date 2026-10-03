from __future__ import annotations

from fastapi.testclient import TestClient

from asl_transcriber.archive import archive_source_id
from asl_transcriber.auth import create_api_token
from asl_transcriber.main import app


def test_recordings_endpoint_filters_transcript_text(monkeypatch, tmp_path) -> None:
    node_dir = tmp_path / "100000"
    node_dir.mkdir()
    (node_dir / "one.wav").write_bytes(b"one")
    (node_dir / "two.wav").write_bytes(b"two")

    monkeypatch.setattr("asl_transcriber.main.settings.archive_paths", str(tmp_path))
    monkeypatch.setattr("asl_transcriber.main.settings.auto_process", False)
    monkeypatch.setattr("asl_transcriber.main.settings.file_stabilization_seconds", 0)
    headers = {"X-API-Key": create_api_token("api-search-filter", "admin")}
    with TestClient(app) as client:
        scan = client.post("/api/v1/ingestion/scan", headers=headers)
        assert scan.status_code == 200
        assert scan.json()["total"] == 2
        active_runtime = __import__("asl_transcriber.main", fromlist=["runtime"]).runtime
        active_runtime.results[active_runtime.jobs()[0].id] = type(
            "Result",
            (),
            {
                "raw_text": "weather check from km7ghs",
                "display_text": "Weather check from KM7GHS",
                "language": "en",
                "status": "completed",
            },
        )()
        response = client.get("/api/v1/recordings?q=weather")

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["transcript"]["display_text"] == (
        "Weather check from KM7GHS"
    )
    assert response.json()["items"][0]["callsigns"] == ["KM7GHS"]


def test_recordings_total_is_not_truncated_by_limit(monkeypatch, tmp_path) -> None:
    node_dir = tmp_path / "100000"
    node_dir.mkdir()
    for index in range(3):
        (node_dir / f"call-{index}.wav").write_bytes(str(index).encode())

    monkeypatch.setattr("asl_transcriber.main.settings.archive_paths", str(tmp_path))
    monkeypatch.setattr("asl_transcriber.main.settings.auto_process", False)
    monkeypatch.setattr("asl_transcriber.main.settings.file_stabilization_seconds", 0)
    headers = {"X-API-Key": create_api_token("api-search-total", "admin")}
    with TestClient(app) as client:
        client.post("/api/v1/ingestion/scan", headers=headers)
        client.post("/api/v1/ingestion/scan", headers=headers)
        response = client.get("/api/v1/recordings?limit=1")

    assert response.json()["total"] == 3
    assert len(response.json()["items"]) == 1
    assert response.json()["database_totals"] == {
        "recordings": 3,
        "transcribed": 0,
    }


def test_recordings_endpoint_filters_exact_archive_identity(monkeypatch, tmp_path) -> None:
    roots = [tmp_path / "root-one", tmp_path / "root-two"]
    for root in roots:
        node_dir = root / "100000"
        node_dir.mkdir(parents=True)
        (node_dir / "same.wav").write_bytes(b"audio")

    monkeypatch.setattr(
        "asl_transcriber.main.settings.archive_paths", ",".join(map(str, roots))
    )
    monkeypatch.setattr("asl_transcriber.main.settings.auto_process", False)
    monkeypatch.setattr("asl_transcriber.main.settings.file_stabilization_seconds", 0)
    headers = {"X-API-Key": create_api_token("api-search-source", "admin")}
    with TestClient(app) as client:
        scan = client.post("/api/v1/ingestion/scan", headers=headers)
        assert scan.status_code == 200
        assert scan.json()["total"] == 2
        response = client.get(
            "/api/v1/recordings",
            params={
                "limit": 1,
                "source_path": "100000/same.wav",
                "source_id": archive_source_id(str(roots[1].resolve())),
            },
        )

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert len(response.json()["items"]) == 1
    assert response.json()["items"][0]["source_id"] == archive_source_id(
        str(roots[1].resolve())
    )
