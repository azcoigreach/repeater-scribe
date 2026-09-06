from pathlib import Path


def test_dashboard_station_history_reuses_show_transcript_style() -> None:
    script = Path("src/asl_transcriber/static/dashboard.js").read_text()
    archive_script = Path("src/asl_transcriber/static/archive.js").read_text()
    detail_script = Path("src/asl_transcriber/static/archive_detail.js").read_text()
    archive_stylesheet = Path("src/asl_transcriber/static/archive.css").read_text()

    assert "element('a', 'View station history', 'callsign-evidence'" in script
    assert "show.addEventListener('click', () => revealTranscript(item.source_path))" in script
    assert "link.className = 'callsign-evidence'" in archive_script
    assert "history.className = 'callsign-history-link'" in detail_script
    assert ".callsign-history-link{display:inline-flex" in archive_stylesheet