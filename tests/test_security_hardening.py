from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)
from fastapi.testclient import TestClient
from joserfc import jwt
from joserfc.jwk import import_key
from pydantic import ValidationError

from asl_transcriber import auth
from asl_transcriber.auth import create_api_token
from asl_transcriber.config import Settings, settings
from asl_transcriber.database import SessionLocal
from asl_transcriber.main import app
from asl_transcriber.models import ApiToken, AuthSession, OidcLoginState
from asl_transcriber.security import SseConnectionLimiter


def _session(role: str) -> tuple[str, str]:
    raw = f"test-session-{uuid4()}"
    csrf = f"test-csrf-{uuid4()}"
    now = datetime.now(UTC)
    with SessionLocal() as session:
        session.add(
            AuthSession(
                token_hash=hashlib.sha256(raw.encode()).hexdigest(),
                subject=f"subject-{uuid4()}",
                identity=f"{role}@example.test",
                role=role,
                csrf_token=csrf,
                created_at=now,
                last_seen_at=now,
                expires_at=now + timedelta(hours=1),
            )
        )
        session.commit()
    return raw, csrf


def test_internet_configuration_fails_closed() -> None:
    with pytest.raises(ValidationError, match="ASLT_AUTH_MODE must be oidc"):
        Settings(
            _env_file=None,
            deployment_mode="internet",
            auth_mode="off",
            public_base_url="http://radio.example.test",
        )


def test_anonymous_internet_access_is_limited_to_health(monkeypatch) -> None:
    monkeypatch.setattr(settings, "deployment_mode", "internet")
    monkeypatch.setattr(settings, "auth_mode", "oidc")
    monkeypatch.setattr(settings, "public_base_url", "https://testserver")
    with TestClient(app, base_url="https://testserver") as client:
        assert client.get("/api/v1/health").status_code == 200
        assert "version" not in client.get("/api/v1/health").json()
        assert client.get("/api/v1/recordings").status_code == 401
        assert client.get("/api/v1/system/info").status_code == 401


def test_session_role_and_csrf_are_enforced(monkeypatch) -> None:
    monkeypatch.setattr(settings, "deployment_mode", "internet")
    monkeypatch.setattr(settings, "auth_mode", "oidc")
    monkeypatch.setattr(settings, "public_base_url", "https://testserver")
    operator_session, csrf = _session("operator")
    viewer_session, viewer_csrf = _session("viewer")

    with TestClient(app, base_url="https://testserver") as client:
        client.cookies.set(settings.session_cookie_name, operator_session)
        assert client.get("/api/v1/recordings").status_code == 200
        assert client.post("/api/v1/node/ping").status_code == 403
        assert (
            client.post(
                "/api/v1/node/ping",
                headers={"X-CSRF-Token": csrf, "Origin": "https://evil.example"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/v1/node/ping",
                headers={"X-CSRF-Token": csrf, "Origin": "https://testserver"},
            ).status_code
            == 503
        )
        client.cookies.set(settings.session_cookie_name, viewer_session)
        assert (
            client.post(
                "/api/v1/node/ping",
                headers={"X-CSRF-Token": viewer_csrf, "Origin": "https://testserver"},
            ).status_code
            == 403
        )


def test_security_headers_are_set() -> None:
    response = TestClient(app).get("/api/v1/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["referrer-policy"] == "no-referrer"


def test_host_request_size_rate_and_sse_limits(monkeypatch) -> None:
    assert TestClient(app, base_url="https://untrusted.example").get("/health").status_code == 400
    oversized = TestClient(app).post(
        "/api/v1/ingestion/scan",
        content=b"x" * (settings.request_body_max_bytes + 1),
    )
    assert oversized.status_code == 413
    assert oversized.headers["x-content-type-options"] == "nosniff"

    token = create_api_token(f"rate-{uuid4()}", "viewer")
    monkeypatch.setattr(settings, "request_rate_per_minute", 10)
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}
    assert all(client.get("/health", headers=headers).status_code == 200 for _ in range(10))
    assert client.get("/health", headers=headers).status_code == 429

    monkeypatch.setattr(settings, "sse_connections_per_identity", 1)
    limiter = SseConnectionLimiter()

    async def exercise_limiter() -> None:
        await limiter.acquire("viewer")
        with pytest.raises(Exception, match="Too many live event connections"):
            await limiter.acquire("viewer")
        await limiter.release("viewer")
        await limiter.acquire("viewer")
        await limiter.release("viewer")

    asyncio.run(exercise_limiter())


def test_oidc_identity_admission_is_explicit(monkeypatch) -> None:
    monkeypatch.setattr(settings, "oidc_allowed_subjects", "viewer-1")
    monkeypatch.setattr(settings, "oidc_allowed_groups", "radio-viewers")
    monkeypatch.setattr(settings, "oidc_operator_subjects", "")
    monkeypatch.setattr(settings, "oidc_admin_subjects", "")
    monkeypatch.setattr(settings, "oidc_operator_groups", "radio-operators")
    monkeypatch.setattr(settings, "oidc_admin_groups", "radio-admins")
    assert auth._identity_is_allowed({"sub": "viewer-1", "groups": []})
    assert auth._identity_is_allowed({"sub": "viewer-2", "groups": ["radio-operators"]})
    assert not auth._identity_is_allowed({"sub": "unknown", "groups": ["unrelated"]})


def test_database_api_tokens_are_hashed_and_authorized() -> None:
    name = f"test-admin-{uuid4()}"
    raw = create_api_token(name, "admin")
    with SessionLocal() as session:
        stored = session.query(ApiToken).filter_by(name=name).one()
        assert stored.token_hash == hashlib.sha256(raw.encode()).hexdigest()
        assert raw not in stored.token_hash
    response = TestClient(app).get(
        "/api/v1/system/info", headers={"Authorization": f"Bearer {raw}"}
    )
    assert response.status_code == 200


def test_oidc_code_flow_validates_signed_token_and_prevents_state_replay(monkeypatch) -> None:
    issuer = "https://identity.example.test"
    monkeypatch.setattr(settings, "auth_mode", "oidc")
    monkeypatch.setattr(settings, "oidc_issuer_url", issuer)
    monkeypatch.setattr(settings, "oidc_client_id", "repeater-scribe")
    monkeypatch.setattr(settings, "oidc_client_secret", "oidc-secret")
    monkeypatch.setattr(settings, "public_base_url", "https://radio.example.test")
    monkeypatch.setattr(settings, "oidc_admin_groups", "radio-admins")
    monkeypatch.setattr(auth, "_discovery_cache", None)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    public_pem = key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    signing_key = import_key(private_pem, "RSA", {"kid": "test-key"})
    public_jwk = import_key(public_pem, "RSA", {"kid": "test-key"}).as_dict()
    id_token: str | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "issuer": issuer,
                    "authorization_endpoint": f"{issuer}/authorize",
                    "token_endpoint": f"{issuer}/token",
                    "jwks_uri": f"{issuer}/jwks",
                },
            )
        if request.url.path == "/token":
            assert id_token is not None
            return httpx.Response(200, json={"id_token": id_token})
        if request.url.path == "/jwks":
            return httpx.Response(200, json={"keys": [public_jwk]})
        raise AssertionError(f"Unexpected OIDC request: {request.url}")

    real_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(auth.httpx, "AsyncClient", client_factory)

    async def scenario() -> None:
        nonlocal id_token
        authorization_url = await auth.oidc_authorization_url("/after-login")
        state = parse_qs(urlparse(authorization_url).query)["state"][0]
        with SessionLocal() as session:
            login = session.get(
                OidcLoginState, hashlib.sha256(state.encode()).hexdigest()
            )
            assert login is not None
            now = int(datetime.now(UTC).timestamp())
            id_token = jwt.encode(
                {"alg": "RS256", "kid": "test-key"},
                {
                    "iss": issuer,
                    "aud": "repeater-scribe",
                    "sub": "operator-1",
                    "email": "operator@example.test",
                    "groups": ["radio-admins"],
                    "nonce": login.nonce,
                    "iat": now,
                    "exp": now + 300,
                },
                signing_key,
                algorithms=["RS256"],
            )
        raw_session, principal, next_path = await auth.complete_oidc_login("code", state)
        assert raw_session
        assert principal.identity == "operator@example.test"
        assert principal.role == "admin"
        assert next_path == "/after-login"
        with pytest.raises(Exception, match="Invalid or expired login state"):
            await auth.complete_oidc_login("code", state)

    asyncio.run(scenario())
