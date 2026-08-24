from __future__ import annotations

from fastapi.testclient import TestClient

from asl_transcriber.ami import AmiResponse
from asl_transcriber.main import app, node_status, summarize_node


def test_node_summary_extracts_remote_node_from_status_complete_data() -> None:
    response = AmiResponse(
        headers={"Response": "Success"},
        messages=[
            {
                "Event": "StatusComplete",
                "Channel": "IAX2/allstar-public-8859",
                "CallerIDNum": "0",
                "CallerIDName": "KM7GHS",
                "Data": "668390,X",
                "ChannelStateDesc": "Up",
            }
        ],
    )

    summary = summarize_node(response, [])

    assert summary["connected_nodes"] == [668390]
    assert summary["connected_stations"] == [
        {
            "id": "668390",
            "name": "KM7GHS",
            "channel": "IAX2/allstar-public-8859",
            "state": "Up",
        }
    ]
    assert summary["active_channels"] == ["IAX2/allstar-public-8859"]


def test_node_status_does_not_retain_nodes_missing_from_fresh_ami_response(monkeypatch) -> None:
    monkeypatch.setattr("asl_transcriber.main.settings.ami_enabled", True)
    monkeypatch.setattr("asl_transcriber.main.settings.ami_secret", "secret")
    monkeypatch.setattr(
        "asl_transcriber.main.ami_client",
        lambda: type("Client", (), {"status": lambda self: AmiResponse({"Response": "Success"}, [])})(),
    )

    response = node_status()

    assert response["connected_nodes"] == []
    assert response["connected_stations"] == []


def test_node_status_is_disabled_without_ami_configuration(monkeypatch) -> None:
    monkeypatch.setattr("asl_transcriber.main.settings.ami_enabled", False)

    with TestClient(app) as client:
        response = client.get("/api/v1/node/status")

    assert response.status_code == 503
    assert response.json()["detail"] == "AMI integration is disabled"
