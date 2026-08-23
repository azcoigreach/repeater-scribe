from __future__ import annotations

from pathlib import Path


def test_dashboard_has_one_dark_theme_without_selector() -> None:
    template = Path("src/asl_transcriber/templates/dashboard.html").read_text()
    script = Path("src/asl_transcriber/static/dashboard.js").read_text()
    stylesheet = Path("src/asl_transcriber/static/dashboard.css").read_text()

    assert "theme-select" not in template
    assert "themeSelect" not in script
    assert "--paper: #15191e" in stylesheet
    assert "--ink: #f4f7fb" in stylesheet


def test_dashboard_uses_graphite_surfaces_and_blue_accent() -> None:
    stylesheet = Path("src/asl_transcriber/static/dashboard.css").read_text()

    assert "--paper: #15191e" in stylesheet
    assert "--panel: #222830" in stylesheet
    assert "--green: #74c7ff" in stylesheet
