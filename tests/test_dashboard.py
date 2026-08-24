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


def test_dashboard_renders_normalized_callsign_rows_from_sse() -> None:
    script = Path("src/asl_transcriber/static/dashboard.js").read_text()

    assert "connection.identifier" in script
    assert "connection.callsign" in script
    assert "node-state" in script
    assert "setInterval(loadNodeStatus, 5000)" not in script
    assert "enableNodeRestFallback" in script
    assert "No connected nodes." in script
    assert "Array.isArray(data.connections)" in script
    assert "Array.isArray(data.links) ? data.links : currentConnections" in script


def test_queue_summary_uses_database_totals() -> None:
    script = Path("src/asl_transcriber/static/dashboard.js").read_text()

    assert "databaseTotals.recordings ?? items.length" in script
    assert "databaseTotals.transcribed ?? counts.completed ?? 0" in script
    assert "renderJobs(data.items, data.database_totals)" in script


def test_connected_node_columns_have_stable_layout() -> None:
    template = Path("src/asl_transcriber/templates/dashboard.html").read_text()
    styles = Path("src/asl_transcriber/static/dashboard.css").read_text()

    assert 'class="stations-table connected-stations-table"' in template
    assert '<col class="station-col-state">' in template
    assert ".connected-stations-table { min-width: 900px; table-layout: fixed; }" in styles


def test_dashboard_waits_for_snapshot_confirmation_after_control() -> None:
    script = Path("src/asl_transcriber/static/dashboard.js").read_text()

    assert "waiting for node state to confirm" in script
    assert "state confirmation was not received" in script
    assert "confirmPendingControl(connections)" in script


def test_favicon_tracks_transcription_and_keyed_states() -> None:
    template = Path("src/asl_transcriber/templates/dashboard.html").read_text()
    script = Path("src/asl_transcriber/static/dashboard.js").read_text()

    assert '<link rel="icon" id="favicon"' in template
    assert "/static/repeater-scribe-state0-256px.png" in template
    for state in range(4):
        assert f"/static/repeater-scribe-state{state}-256px.png" in script
    assert "const state = (transcriptionActive ? 1 : 0) + (nodeKeyed ? 2 : 0);" in script
    assert "transcriptionActive = processing > 0;" in script
    assert "nodeKeyed = talkers.length > 0" in script


def test_queue_summary_shows_large_state_emblem() -> None:
    template = Path("src/asl_transcriber/templates/dashboard.html").read_text()
    script = Path("src/asl_transcriber/static/dashboard.js").read_text()
    styles = Path("src/asl_transcriber/static/dashboard.css").read_text()

    assert '<img id="state-emblem-image" src="/static/repeater-scribe-state0.png"' in template
    assert 'id="state-emblem-label"' in template
    for state in range(4):
        assert f"'/static/repeater-scribe-state{state}.png'" in script
    assert "ACTIVITY_STATE_LABELS = ['IDLE', 'TRANSCRIBING', 'NODE KEYED', 'KEYED + TRANSCRIBING']" in script
    assert ".state-emblem img" in styles


def test_favicon_state_images_are_served() -> None:
    static_dir = Path("src/asl_transcriber/static")

    for state in range(4):
        assert (static_dir / f"repeater-scribe-state{state}-256px.png").is_file()
        assert (static_dir / f"repeater-scribe-state{state}.png").is_file()
