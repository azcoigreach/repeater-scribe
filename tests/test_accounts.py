from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from asl_transcriber import account_api, accounts, auth, auth_streams, main
from asl_transcriber.config import settings
from asl_transcriber.database import Base
from asl_transcriber.models import Account, ApiToken, AuthSession, SecurityAudit

ISSUER = "https://identity.example.test"


@pytest.fixture
def db_factory(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'accounts.db'}", connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    for module in (auth, accounts, account_api):
        monkeypatch.setattr(module, "SessionLocal", factory)
    monkeypatch.setattr(settings, "auth_mode", "oidc")
    monkeypatch.setattr(settings, "deployment_mode", "internet")
    monkeypatch.setattr(settings, "public_base_url", "https://testserver")
    monkeypatch.setattr(settings, "ami_enabled", False)
    monkeypatch.setattr(settings, "ami_control_enabled", False)
    yield factory
    engine.dispose()


def login(factory, role="user", *, subject=None, issuer=ISSUER, allowed=True, **metadata):
    claims = {"iss": issuer, "sub": subject or str(uuid4())} | metadata
    with factory() as db:
        accounts.begin_account_write(db)
        account = accounts.admit_account(db, claims, allowed=allowed, role=role)
        raw = str(uuid4())
        now = datetime.now(UTC)
        db.add(
            AuthSession(
                account_id=account.id,
                token_hash=auth.token_digest(raw),
                subject=account.subject,
                identity=account.identity,
                role=role,
                csrf_token="csrf",
                created_at=now,
                last_seen_at=now,
                expires_at=now + timedelta(hours=1),
            )
        )
        db.commit()
        return account.id, raw, auth._session_principal(raw)


def client_for(raw):
    client = TestClient(main.app, base_url="https://testserver")
    client.cookies.set(settings.session_cookie_name, raw)
    client.headers.update({"X-CSRF-Token": "csrf", "Origin": "https://testserver"})
    return client


def test_identity_key_is_issuer_subject_and_metadata_is_mutable(db_factory):
    identifier, raw, principal = login(db_factory, subject="one", email="old@example.test")
    second, _, _ = login(
        db_factory, subject="one", issuer="https://other.example.test", email="old@example.test"
    )
    third, _, _ = login(db_factory, subject="two", email="old@example.test")
    updated, _, _ = login(
        db_factory, subject="one", allowed=False, email="new@example.test", name="Name"
    )
    assert len({identifier, second, third}) == 3 and updated == identifier
    assert auth._session_principal(raw).identity == "new@example.test"
    with db_factory() as db:
        account = db.get(Account, identifier)
        assert account.first_sign_in_at <= account.last_sign_in_at
        assert account.display_name == "Name"
        assert (
            db.scalar(
                select(AuthSession.identity).where(AuthSession.token_hash == auth.token_digest(raw))
            )
            == "old@example.test"
        )
        history = db.scalars(
            select(SecurityAudit).where(SecurityAudit.account_id == identifier)
        ).all()
        assert any("old@example.test" in row.detail for row in history)
        assert principal.subject == f"account:{identifier}"
        db.add(
            Account(
                issuer=ISSUER,
                subject="one",
                identity="duplicate",
                role="viewer",
                created_by="test",
                updated_by="test",
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()


def test_admission_disabled_and_managed_roles_override_provider(db_factory):
    with pytest.raises(HTTPException, match="not allowed"):
        login(db_factory, allowed=False)
    identifier, raw, _ = login(db_factory, subject="managed")
    _, _, principal = login(db_factory, role="admin", subject="managed")
    assert principal.role == "user"
    with db_factory() as db:
        db.get(Account, identifier).enabled = False
        db.commit()
    with pytest.raises(HTTPException, match="disabled"):
        login(db_factory, role="admin", subject="managed")
    assert auth._session_principal(raw) is None


def test_configured_admin_and_operator_bootstrap(db_factory, monkeypatch):
    monkeypatch.setattr(settings, "oidc_admin_subjects", "configured-admin")
    monkeypatch.setattr(settings, "oidc_operator_groups", "old-operators")
    for claims, expected in [
        ({"sub": "configured-admin"}, "admin"),
        ({"sub": "worker", "groups": ["old-operators"]}, "user"),
    ]:
        assert auth._identity_is_allowed(claims)
        _, _, principal = login(
            db_factory, role=auth._role_from_claims(claims), subject=claims["sub"]
        )
        assert principal.role == expected


def test_demotion_disable_reenable_and_local_logout(db_factory):
    _, admin_raw, admin = login(db_factory, "admin")
    identifier, raw, _ = login(db_factory, "admin", subject="target")
    _, raw2, _ = login(db_factory, "admin", subject="target")
    client = client_for(admin_raw)
    assert (
        client.patch(f"/api/v1/accounts/{identifier}", json={"role": "viewer"}).status_code == 200
    )
    assert client_for(raw).get("/api/v1/system/info").status_code == 403
    assert auth._session_principal(raw2).role == "viewer"
    assert (
        client.patch(f"/api/v1/accounts/{identifier}", json={"enabled": False}).status_code == 200
    )
    with db_factory() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuthSession)
                .where(AuthSession.account_id == identifier)
            )
            == 0
        )
    assert client.patch(f"/api/v1/accounts/{identifier}", json={"enabled": True}).status_code == 200
    assert auth._session_principal(raw) is None and auth._session_principal(raw2) is None
    _, raw3, _ = login(db_factory, subject="target")
    _, raw4, _ = login(db_factory, subject="target")
    auth.revoke_session(raw3)
    assert auth._session_principal(raw3) is None and auth._session_principal(raw4) is not None
    assert auth.refresh_principal(admin) is not None


@pytest.mark.parametrize("change", [{"role": "viewer"}, {"enabled": False}])
def test_last_admin_is_safe_across_concurrent_transactions(db_factory, change):
    first, _, _ = login(db_factory, "admin")
    second, _, _ = login(db_factory, "admin")
    # Independent legacy Admin credential may administer accounts, but cannot
    # satisfy the invariant that a managed enabled Admin must remain.
    raw = auth.create_api_token("automation", "admin")
    principal = auth._api_principal(raw)
    barrier = Barrier(2)

    def mutate(identifier):
        barrier.wait()
        try:
            accounts.change_account(
                identifier, principal, role=change.get("role"), enabled=change.get("enabled")
            )
            return 200
        except HTTPException as error:
            return error.status_code

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(mutate, [first, second])) == [200, 409]
    with db_factory() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(Account)
                .where(Account.role == "admin", Account.enabled.is_(True))
            )
            == 1
        )


def test_admin_caller_is_rechecked_under_transaction_lock(db_factory):
    _, _, first = login(db_factory, "admin")
    identifier, _, stale = login(db_factory, "admin")
    accounts.change_account(identifier, first, role="viewer", enabled=None)
    with pytest.raises(HTTPException, match="Administrator"):
        accounts.change_account(identifier, stale, role="admin", enabled=None)


@pytest.mark.parametrize("role", ["viewer", "user", "admin"])
def test_endpoint_permission_matrix(db_factory, role, monkeypatch):
    identifier, raw, _ = login(db_factory, role)
    client = client_for(raw)
    monkeypatch.setattr(main, "node_monitor", None)
    # Read, content edit, node control, ingestion, diagnostics, account management.
    cases = [
        ("GET", "/api/v1/auth/me", None, 200),
        ("POST", "/api/v1/sessions", {}, 403 if role == "viewer" else 422),
        ("POST", "/api/v1/node/ping", None, 403 if role == "viewer" else 503),
        ("POST", "/api/v1/ingestion/process", {}, 200 if role == "admin" else 403),
        ("GET", "/api/v1/system/info", None, 200 if role == "admin" else 403),
        ("GET", "/api/v1/accounts", None, 200 if role == "admin" else 403),
        (
            "PATCH",
            f"/api/v1/accounts/{identifier}",
            {"enabled": True},
            200 if role == "admin" else 403,
        ),
    ]
    monkeypatch.setattr(main, "process_transcription_jobs", lambda **_: {"processed": 0})
    for method, path, payload, expected in cases:
        response = client.request(method, path, json=payload)
        assert response.status_code == expected, (path, response.status_code, response.text)
    assert client.get("/api/v1/auth/me").json()["role"] == role


def test_account_mutation_csrf_origin_rate_limit_and_audit(db_factory, monkeypatch):
    identifier, raw, _ = login(db_factory, "admin")
    client = client_for(raw)
    path = f"/api/v1/accounts/{identifier}"
    for headers in (
        {"X-CSRF-Token": ""},
        {"Origin": "https://testserver.evil"},
        {"Origin": "https://testserver/"},
    ):
        assert client.patch(path, json={"enabled": True}, headers=headers).status_code == 403
    for payload in (
        {},
        {"role": None},
        {"enabled": None},
        {"role": "operator"},
        {"issuer": "https://evil"},
    ):
        assert client.patch(path, json=payload).status_code == 422
    monkeypatch.setattr(settings, "request_rate_per_minute", 1)
    assert client.patch(path, json={"enabled": True}).status_code == 429
    with db_factory() as db:
        assert db.scalar(
            select(SecurityAudit).where(
                SecurityAudit.action == "http_write", SecurityAudit.outcome == "denied"
            )
        )
        assert db.scalar(select(SecurityAudit).where(SecurityAudit.action == "rate_limit"))
        assert all(raw not in (row.detail or "") for row in db.scalars(select(SecurityAudit)))


def test_named_token_legacy_authority_and_owned_token_cap(db_factory):
    raw = auth.create_api_token("legacy", "operator")
    principal = auth._api_principal(raw)
    assert principal.role == "user" and principal.account_id is None
    identifier, _, _ = login(db_factory, "viewer")
    with db_factory() as db:
        token = db.get(ApiToken, principal.token_id)
        token.account_id = identifier
        db.commit()
    assert auth._api_principal(raw).role == "viewer"
    with db_factory() as db:
        db.get(Account, identifier).enabled = False
        db.commit()
    assert auth._api_principal(raw) is None


def test_recovery_is_audited_and_requires_new_sign_in(db_factory):
    identifier, raw, _ = login(db_factory, "viewer", subject="recovery")
    assert accounts.recover_admin(ISSUER, "recovery") == identifier
    assert auth._session_principal(raw) is None
    _, _, principal = login(db_factory, subject="recovery", allowed=False)
    assert principal.role == "admin"
    created = accounts.recover_admin(ISSUER, "lost")
    with db_factory() as db:
        assert db.get(Account, created).first_sign_in_at is None
        assert db.scalar(
            select(SecurityAudit).where(
                SecurityAudit.action == "account_recovery", SecurityAudit.account_id == identifier
            )
        )


@pytest.mark.parametrize("change", ["disabled", "demoted", "logout", "expired", "token"])
def test_protected_stream_revokes_while_idle_and_closes_subscription(
    db_factory, monkeypatch, change
):
    _, _, admin = login(db_factory, "admin")
    identifier, raw, principal = login(db_factory, "user")
    if change == "token":
        raw = auth.create_api_token("stream", "user")
        principal = auth._api_principal(raw)
    monkeypatch.setattr(auth_streams, "STREAM_AUTH_INTERVAL", 0.01)
    closed = []

    async def source():
        try:
            yield "initial"
            await asyncio.Event().wait()
        finally:
            closed.append(True)

    async def exercise():
        stream = auth_streams.protected_stream(source(), principal)
        assert await anext(stream) == "initial"
        if change == "disabled":
            accounts.change_account(identifier, admin, role=None, enabled=False)
        elif change == "demoted":
            accounts.change_account(identifier, admin, role="viewer", enabled=None)
        elif change == "logout":
            auth.revoke_session(raw)
        elif change == "token":
            auth.revoke_api_token("stream")
        else:
            with db_factory() as db:
                db.get(AuthSession, principal.session_hash).expires_at = datetime.now(
                    UTC
                ) - timedelta(seconds=1)
                db.commit()
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(anext(stream), 1)
        assert closed == [True]

    asyncio.run(exercise())


def test_session_snapshot_cannot_elevate_current_account(db_factory):
    _, raw, _ = login(db_factory, "viewer")
    with db_factory() as db:
        db.get(AuthSession, auth.token_digest(raw)).role = "admin"
        db.commit()
    assert client_for(raw).get("/api/v1/system/info").status_code == 403


@pytest.mark.parametrize("endpoint", ["jobs", "node", "topology"])
def test_sse_endpoints_release_connection_after_account_disable(db_factory, monkeypatch, endpoint):
    from queue import Queue
    from types import SimpleNamespace

    from asl_transcriber.security import sse_connections

    _, _, admin = login(db_factory, "admin")
    identifier, _, principal = login(db_factory, "user")
    released = []

    async def service_events(*_args, **_kwargs):
        try:
            yield "node-state" if endpoint == "node" else {"heartbeat": True}
            await asyncio.Event().wait()
        finally:
            released.append(True)

    async def connected():
        return False

    service = SimpleNamespace(
        events=service_events,
        subscribe=lambda *_: Queue(),
        unsubscribe=lambda _: released.append(True),
    )
    monkeypatch.setattr(main, "node_monitor", service)
    monkeypatch.setattr(main, "topology_service", service)
    monkeypatch.setattr(main, "current_runtime", lambda: service)
    monkeypatch.setattr(auth_streams, "STREAM_AUTH_INTERVAL", 0.01)

    async def exercise():
        if endpoint == "jobs":
            response = await main.events(SimpleNamespace(is_disconnected=connected), principal)
        elif endpoint == "node":
            response = await main.node_events("1999", principal)
        else:
            response = await main.topology_events("1999", "2999", principal)
        stream = response.body_iterator
        await anext(stream)
        assert sse_connections._counts[principal.subject] == 1
        accounts.change_account(identifier, admin, role=None, enabled=False)
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(anext(stream), 1)
        assert principal.subject not in sse_connections._counts

    asyncio.run(exercise())


def test_legacy_event_retry_cannot_create_duplicate_after_account_login(db_factory):
    from asl_transcriber.models import RadioSession, SessionRequest
    from asl_transcriber.session_api import CreateEvent, create_event

    _, _, principal = login(db_factory, subject="legacy-subject")
    with db_factory() as db:
        original = RadioSession(
            name="Original",
            type="Net",
            source_root="/fixture",
            started_at=datetime.now(UTC),
            created_by="old-name",
            updated_by="old-name",
        )
        db.add(original)
        db.flush()
        db.add(
            SessionRequest(
                actor="legacy-subject", key="retry", fingerprint="old", session_id=original.id
            )
        )
        db.commit()
        with pytest.raises(HTTPException, match="predates managed accounts"):
            create_event(db, principal, CreateEvent(name="Original", source_id="unused"), "retry")
        assert db.scalar(select(func.count()).select_from(RadioSession)) == 1
