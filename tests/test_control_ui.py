from __future__ import annotations

from pathlib import Path


def test_dashboard_contains_explicit_protected_control_form() -> None:
    template = Path("src/asl_transcriber/templates/dashboard.html").read_text()
    script = Path("src/asl_transcriber/static/dashboard.js").read_text()

    assert 'id="command-buttons"' in template
    assert 'id="command-confirm"' in template
    assert "/ui/node/${encodeURIComponent(nodeId)}/command" in script
    assert "X-API-Key" not in script
