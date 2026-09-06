from pathlib import Path


def test_exact_callsign_search_opens_station_history() -> None:
    script = Path("src/asl_transcriber/static/callsigns.js").read_text()
    template = Path("src/asl_transcriber/templates/callsigns.html").read_text()

    assert "const callsignPattern = /^[A-Z0-9]{1,3}\\d[A-Z]{1,4}$/" in script
    assert "location.assign(`/callsigns/${encodeURIComponent(query)}`)" in script
    assert 'src="/static/callsigns.js?v=2"' in template