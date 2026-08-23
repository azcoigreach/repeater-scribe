from __future__ import annotations

from pathlib import Path


def test_dashboard_refreshes_activity_without_browser_reload() -> None:
    script = Path("src/asl_transcriber/static/dashboard.js").read_text()

    assert "fetch('/api/v1/activity', { cache: 'no-store' })" in script
    assert "setInterval(loadActivity, 5000)" in script
