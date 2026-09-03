from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from asl_transcriber.main import app


def test_dashboard_page_is_available(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("asl_transcriber.main.settings.archive_paths", str(tmp_path))
    monkeypatch.setattr("asl_transcriber.main.settings.auto_process", False)
    monkeypatch.setattr("asl_transcriber.main.settings.ami_node_id", "100000")

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "Connected nodes" in response.text
    assert 'data-controlled-node="100000"' in response.text
    assert "Operating node" not in response.text
    assert "/static/dashboard.css" in response.text
    assert 'class="workspace-link active" href="/"' in response.text
    assert 'class="workspace-link" href="/archive"' in response.text


def test_dashboard_renders_normalized_callsign_rows_from_sse() -> None:
    script = Path("src/asl_transcriber/static/dashboard.js").read_text()

    assert "connection.identifier" in script
    assert "connection.callsign" in script
    assert "node-state" in script
    assert "node-directory" in script
    assert "data.callsign" in script
    assert "data.location" in script
    assert "Virtual node" in script
    assert "setInterval(loadNodeStatus, 5000)" not in script
    assert "enableNodeRestFallback" in script
    assert "No connected nodes." in script
    assert "Array.isArray(data.connections)" in script
    assert "Array.isArray(data.links) ? data.links : currentConnections" in script


def test_dashboard_node_window_exposes_directory_placeholder() -> None:
    html = Path("src/asl_transcriber/templates/dashboard.html").read_text()

    assert 'id="node-directory"' in html
    assert "Virtual node" in html
    assert "live AMI connection" in html


def test_queue_summary_uses_database_totals() -> None:
    script = Path("src/asl_transcriber/static/dashboard.js").read_text()

    assert "databaseTotals.recordings ?? items.length" in script
    assert "databaseTotals.transcribed ?? counts.completed ?? 0" in script
    assert "renderJobs(data.items, data.database_totals)" in script


def test_dashboard_has_dockable_last_heard_callsign_window() -> None:
    template = Path("src/asl_transcriber/templates/dashboard.html").read_text()
    script = Path("src/asl_transcriber/static/dashboard.js").read_text()

    assert 'data-win="callsigns"' in template
    assert 'id="last-heard-callsigns"' in template
    assert "/api/v1/callsigns/last-heard" in script
    assert "callsigns: 'Last heard callsigns'" in script
    assert "Location and primary photos supplied by QRZ.com." in script
    assert 'class="transcript-callsign"' in script
    assert "confirmedCallsigns" in script
    assert "unconfirmed transcript fragment" in script
    assert "superseded by later audio" in script
    assert 'class="callsign-confidence"' in script
    assert 'class="confidence-meter"' in script
    assert "Why this score" in script
    assert "expandedEvidence" in script
    assert ".confidence-evidence[open]" in script
    assert "Show transcript" in script
    assert "revealCallsign" in script
    assert "revealTranscript" in script


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

    assert '<img id="state-emblem-image" src="/static/repeater-scribe-state0-256px.png"' in template
    assert 'id="state-emblem-label"' in template
    assert "const ACTIVITY_STATE_EMBLEMS = ACTIVITY_STATE_ICONS;" in script
    assert "ACTIVITY_STATE_LABELS = ['IDLE', 'TRANSCRIBING', 'NODE KEYED', 'KEYED + TRANSCRIBING']" in script
    assert ".state-emblem img { display: block; width: 128px; max-width: 100%;" in styles


def test_favicon_state_images_are_served() -> None:
    static_dir = Path("src/asl_transcriber/static")

    for state in range(4):
        assert (static_dir / f"repeater-scribe-state{state}-256px.png").is_file()
        assert (static_dir / f"repeater-scribe-state{state}.png").is_file()
