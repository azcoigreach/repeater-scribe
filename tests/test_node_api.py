from __future__ import annotations

from fastapi.testclient import TestClient

from asl_transcriber.config import Settings
from asl_transcriber.main import app, node_status
from asl_transcriber.node_control import parse_alinks
from asl_transcriber.node_service import NodeStateService


def test_node_status_returns_shared_service_cache_without_ami_action(monkeypatch) -> None:
    service = NodeStateService(Settings(ami_node_id="100000", ami_secret="secret"))
    state = service.state("100000")
    state.links = {link.identifier: link for link in parse_alinks("1,KM7GHSTK")}
    state.ami_state = "authenticated"
    state.stale = False
    service.set_directory("100000", callsign="KM7GHS", location="Goodyear, AZ")
    monkeypatch.setattr("asl_transcriber.main.node_monitor", service)

    response = node_status()

    assert response["connected_nodes"] == ["KM7GHS"]
    assert response["connections"][0]["callsign"] == "KM7GHS"
    assert response["connections"][0]["source"] == "rpt_alinks"
    assert response["callsign"] == "KM7GHS"
    assert response["location"] == "Goodyear, AZ"


def test_node_status_is_disabled_without_ami_configuration(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("asl_transcriber.main.settings.ami_enabled", False)
    monkeypatch.setattr("asl_transcriber.main.settings.archive_paths", str(tmp_path))
    monkeypatch.setattr("asl_transcriber.main.settings.auto_process", False)

    with TestClient(app) as client:
        response = client.get("/api/v1/node/status")

    assert response.status_code == 503
    assert response.json()["detail"] == "AMI integration is disabled"
