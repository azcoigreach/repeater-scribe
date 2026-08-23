from __future__ import annotations

from pathlib import Path


def test_dashboard_uses_monospace_font_stack() -> None:
    stylesheet = Path("src/asl_transcriber/static/dashboard.css").read_text()

    assert '"IBM Plex Mono"' in stylesheet
    assert '"DejaVu Sans Mono"' in stylesheet
