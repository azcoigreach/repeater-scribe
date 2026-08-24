from __future__ import annotations

from pathlib import Path


def test_dashboard_contains_grouped_direct_control_buttons() -> None:
    template = Path("src/asl_transcriber/templates/dashboard.html").read_text()
    script = Path("src/asl_transcriber/static/dashboard.js").read_text()

    assert 'class="control-group"' in template
    assert 'data-command="Connect permanent transceive"' in template
    assert 'data-command="Connect permanent local monitor"' in template
    assert 'data-command="Disconnect all links"' in template
    assert '<legend>Link</legend>' in template
    assert 'data-command="Link"' not in template
    assert "/ui/node/${encodeURIComponent(nodeId)}/command" in script
    assert "confirmed: true" in script
    assert "window.confirm" not in script
    assert "X-API-Key" not in script
