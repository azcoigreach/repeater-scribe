"""Callsign contracts through real authorization, sessions, CSRF and machine tokens."""

import base64
import json
from datetime import timedelta
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_callsign_intelligence import archive_db  # noqa: F401
from test_callsign_regressions import NOW, seed
from test_security_hardening import _session

from asl_transcriber import main
from asl_transcriber.auth import create_api_token
from asl_transcriber.config import settings
from asl_transcriber.database import SessionLocal
from asl_transcriber.models import CallsignMention, SecurityAudit, Transmission
from asl_transcriber.qrz import QrzCallsign, QrzError


@pytest.fixture
def secured(monkeypatch, request):
    request.getfixturevalue("archive_db")
    monkeypatch.setattr(settings, "deployment_mode", "internet")
    monkeypatch.setattr(settings, "auth_mode", "oidc")
    monkeypatch.setattr(settings, "public_base_url", "https://testserver")
    monkeypatch.setattr(settings, "request_rate_per_minute", 10000)
    monkeypatch.setattr(settings, "qrz_username", "credential-marker")
    monkeypatch.setattr(settings, "qrz_password", "password-marker")
    client = Mock()
    client.lookup.side_effect = lambda value: QrzCallsign(
        value, name="Cached Operator", status="found"
    )
    monkeypatch.setattr(main, "current_qrz_client", lambda: client)
    with SessionLocal() as db:
        recording, _ = seed(db)
        mention = db.query(CallsignMention).one()
        mention_id = mention.id
        db.add(Transmission(recording_id=recording.id, attribution_level="unknown"))
        db.commit()
    with TestClient(main.app, base_url="https://testserver") as browser:
        yield browser, mention_id, client


def reads():
    return [
        "/api/v1/callsigns",
        "/api/v1/callsigns/KM7GHS",
        "/api/v1/callsigns/KM7GHS/mentions",
        "/api/v1/callsigns/last-heard",
    ]


def writes(mention):
    return [
        ("PATCH", f"/{prefix}/callsign-mentions/{mention}", {"action": "confirm"})
        for prefix in ("ui", "api/v1")
    ] + [("POST", f"/{prefix}/callsigns/KM7GHS/qrz-refresh", None) for prefix in ("ui", "api/v1")]


def login(browser, role):
    raw, csrf = _session(role)
    browser.cookies.set(settings.session_cookie_name, raw)
    return {"X-CSRF-Token": csrf, "Origin": "https://testserver"}


def test_anonymous_denied_and_viewer_reads_only(secured):
    browser, mention, qrz = secured
    for path in reads():
        assert browser.get(path).status_code == 401
    for method, path, body in writes(mention):
        assert browser.request(method, path, json=body).status_code == 401
    headers = login(browser, "viewer")
    for path in reads():
        assert browser.get(path).status_code == 200
    qrz.reset_mock()
    for method, path, body in writes(mention):
        assert browser.request(method, path, json=body, headers=headers).status_code == 403
    qrz.lookup.assert_not_called()


@pytest.mark.parametrize(
    "token,origin",
    [
        (None, "https://testserver"),
        ("wrong", "https://testserver"),
        ("valid", "https://evil.example"),
        ("valid", None),
    ],
)
def test_browser_writes_reject_bad_csrf_or_origin(secured, token, origin):
    browser, mention, qrz = secured
    headers = login(browser, "operator")
    if token != "valid":
        headers.pop("X-CSRF-Token")
        if token:
            headers["X-CSRF-Token"] = token
    if origin:
        headers["Origin"] = origin
    else:
        headers.pop("Origin")
    for method, path, body in writes(mention):
        assert browser.request(method, path, json=body, headers=headers).status_code == 403
    qrz.lookup.assert_not_called()


def test_operator_browser_reviews_audit_changes_without_attributing_transmissions(secured):
    browser, mention, _ = secured
    headers = login(browser, "operator")
    for action in ("confirm", "reject", "correct"):
        body = {"action": action}
        if action == "correct":
            body["corrected_callsign"] = "KE7WIL"
        reply = browser.patch(f"/ui/callsign-mentions/{mention}", json=body, headers=headers)
        assert reply.status_code == 200
        assert set(reply.json()) == {
            "mention_id",
            "canonical_callsign",
            "review_status",
            "reviewer_identity",
            "reviewed_at",
        }
        with SessionLocal() as db:
            assert db.query(Transmission).one().operator_callsign is None
            audit = (
                db.query(SecurityAudit)
                .filter_by(
                    action="callsign_mention_review", path=f"/ui/callsign-mentions/{mention}"
                )
                .order_by(SecurityAudit.occurred_at.desc())
                .first()
            )
            assert audit.actor == "operator@example.test" and audit.outcome == "success"
            detail = json.loads(audit.detail)
            assert detail["mention_id"] == mention and detail["operation"] == action
            assert detail["before"]["callsign"] == "KM7GHS"
            assert detail["after"]["callsign"] == ("KE7WIL" if action == "correct" else "KM7GHS")
        profile = browser.get(f"/api/v1/callsigns/{reply.json()['canonical_callsign']}").json()
        assert profile["attributed_transmission_count"] == 0
        assert profile["attributed_airtime_seconds"] == 0


@pytest.mark.parametrize("role,expected", [("operator", 200), ("viewer", 403)])
def test_machine_token_path_and_insufficient_role(secured, role, expected):
    browser, mention, qrz = secured
    token = create_api_token(f"callsign-{uuid4()}", role)
    headers = {"Authorization": f"Bearer {token}"}
    for method, path, body in writes(mention):
        reply = browser.request(method, path, json=body, headers=headers)
        assert reply.status_code == (403 if path.startswith("/ui/") else expected)
    if role == "viewer":
        qrz.lookup.assert_not_called()


def test_qrz_refresh_audit_invalid_input_and_failure_redaction(secured):
    browser, _, qrz = secured
    headers = login(browser, "operator")
    path = "/ui/callsigns/KM7GHS/qrz-refresh"
    assert browser.post(path, headers=headers).status_code == 200
    with SessionLocal() as db:
        audit = (
            db.query(SecurityAudit)
            .filter_by(action="callsign_qrz_refresh", path=path)
            .order_by(SecurityAudit.occurred_at.desc())
            .first()
        )
        assert audit.actor == "operator@example.test"
        assert audit.outcome == "success" and "callsign=KM7GHS;status=found" in audit.detail
        count = db.query(SecurityAudit).filter_by(action="callsign_qrz_refresh").count()
    qrz.reset_mock()
    for prefix in ("ui", "api/v1"):
        assert (
            browser.post(f"/{prefix}/callsigns/invalid!/qrz-refresh", headers=headers).status_code
            == 422
        )
    qrz.lookup.assert_not_called()
    qrz.lookup.side_effect = QrzError("QRZ session-key-marker password-marker /private/archive")
    reply = browser.post(path, headers=headers)
    assert reply.status_code == 502 and reply.json() == {"detail": "QRZ lookup failed"}
    with SessionLocal() as db:
        assert db.query(SecurityAudit).filter_by(action="callsign_qrz_refresh").count() == count


@pytest.mark.parametrize(
    "payload",
    [
        "bad",
        "%%%",
        base64.urlsafe_b64encode(b'[{},"id"]').decode(),
        base64.urlsafe_b64encode(b'["",null]').decode(),
        base64.urlsafe_b64encode(b"{}").decode(),
    ],
)
def test_malformed_cursors_are_structured_422(secured, payload):
    browser, _, _ = secured
    login(browser, "viewer")
    for path in ("/api/v1/callsigns", "/api/v1/callsigns/KM7GHS/mentions"):
        response = browser.get(path, params={"cursor": payload})
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "invalid_cursor"
        assert response.json()["detail"]["message"]


def test_contract_filters_bounded_excerpt_and_private_field_exclusion(secured):
    browser, mention_id, _ = secured
    login(browser, "viewer")
    with SessionLocal() as db:
        mention = db.get(CallsignMention, mention_id)
        mention.segment.display_text = "<script>hostile literal</script>" * 30
        db.commit()
    directory = browser.get("/api/v1/callsigns").json()
    assert set(directory) == {"items", "next_cursor", "has_more"}
    assert set(directory["items"][0]) == {
        "callsign",
        "qrz_display_name",
        "qrz_location",
        "first_heard",
        "last_heard",
        "mention_count",
        "recording_count",
        "active_days",
        "confirmed_mentions",
        "has_attributed_transmissions",
        "most_recent_confidence",
    }
    path = "/api/v1/callsigns/KM7GHS/mentions"
    mention = browser.get(path).json()["items"][0]
    assert len(mention["excerpt"]) == 240
    assert mention["segment_avg_logprob"] == -0.25
    assert mention["audio_available"] is False
    assert mention["raw_observed_value"] == "KM7GHS"
    for params, count in [
        ({"from": NOW.date().isoformat(), "to": NOW.date().isoformat()}, 1),
        ({"from": (NOW + timedelta(days=1)).isoformat()}, 0),
        ({"to": (NOW - timedelta(days=1)).date().isoformat()}, 0),
        ({"review_status": "detected"}, 1),
        ({"review_status": "confirmed"}, 0),
        ({"audio_status": "missing"}, 1),
        ({"audio_status": "available"}, 0),
    ]:
        response = browser.get(path, params=params)
        assert response.status_code == 200 and len(response.json()["items"]) == count
    assert browser.get("/api/v1/callsigns?qrz_validation_status=found").json()["items"] == []
    headers = login(browser, "operator")
    browser.post("/ui/callsigns/KM7GHS/qrz-refresh", headers=headers)
    assert len(browser.get("/api/v1/callsigns?qrz_validation_status=found").json()["items"]) == 1
    for path in reads():
        rendered = browser.get(path).text
        for private in (
            "credential-marker",
            "password-marker",
            "session-key-marker",
            "/private/archive",
            "qrz_password",
            "qrz_username",
        ):
            assert private not in rendered
