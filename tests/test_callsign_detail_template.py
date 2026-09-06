from pathlib import Path

from fastapi.testclient import TestClient

from asl_transcriber.main import app


def test_callsign_history_uses_versioned_client_asset() -> None:
    template = Path("src/asl_transcriber/templates/callsign_detail.html").read_text()
    script = Path("src/asl_transcriber/static/callsign_detail.js").read_text()

    assert 'data-callsign="{{ callsign }}"' in template
    assert 'data-role="{{ role }}"' in template
    assert 'href="/static/archive.css?v=0.9.0"' in template
    assert 'src="/static/callsign_detail.js?v=3"' in template
    assert "window.callsignName" not in template
    assert "workspace?.dataset.callsign" in script
    assert "image.className = 'callsign-profile-image'" in script
    assert "text('a', 'View QRZ profile', 'callsign-evidence')" in script
    assert "text('a', 'Open recording', 'control-button')" in script
    assert "text('button', 'Play from mention', 'control-button')" in script
    assert "const player = new Audio()" in script
    assert "function playMention(button, mention)" in script
    assert "player.pause();" in script
    assert "player.addEventListener('ended', resetPlaybackButton)" in script


def test_dynamic_callsign_pages_are_not_cached() -> None:
    response = TestClient(app).get("/callsigns/KC5KKT")

    assert response.headers["cache-control"] == "no-store"


def test_callsign_history_cards_are_visible_on_desktop() -> None:
    stylesheet = Path("src/asl_transcriber/static/archive.css").read_text()

    assert "#profile.recording-cards,#history.recording-cards{display:grid;gap:10px}" in stylesheet
    assert ".callsign-profile-image{width:min(100%,320px);max-height:320px;object-fit:cover" in stylesheet
    assert ".recording .control-button{display:inline-flex" in stylesheet