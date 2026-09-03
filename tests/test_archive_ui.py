from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from asl_transcriber import auth
from asl_transcriber.auth import token_digest
from asl_transcriber.config import settings
from asl_transcriber.database import Base, get_db
from asl_transcriber.main import app
from asl_transcriber.models import AuthSession, Recording


@pytest.fixture()
def archive_ui_db(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)
    monkeypatch.setattr(auth, "SessionLocal", sessions)
    def override_db():
        with sessions() as session:
            yield session
    app.dependency_overrides[get_db] = override_db
    yield sessions
    app.dependency_overrides.clear()
    engine.dispose()


def add_recording(sessions, recording_id: str = "archive-ui-recording") -> str:
    with sessions() as session:
        session.add(
            Recording(
                id=recording_id,
                archive_root="/not-exposed/archive",
                source_path=f"100000/2026090101010100-{recording_id}.wav",
                started_at=datetime(2026, 9, 1, tzinfo=UTC),
                status="completed",
                audio_status="missing",
            )
        )
        session.commit()
    return recording_id


def session_for(sessions, role: str) -> str:
    raw = f"archive-ui-{role}-{uuid4()}"
    now = datetime.now(UTC)
    with sessions() as session:
        session.add(
            AuthSession(
                token_hash=token_digest(raw),
                subject=f"archive-ui-{role}",
                identity=f"{role}@example.test",
                role=role,
                csrf_token="csrf",
                created_at=now,
                last_seen_at=now,
                expires_at=now + timedelta(hours=1),
            )
        )
        session.commit()
    return raw


def test_archive_workspace_renders_navigation_and_safe_assets(archive_ui_db) -> None:
    response = TestClient(app).get("/archive")

    assert response.status_code == 200
    assert 'href="/">Dashboard' in response.text
    assert 'href="/archive" aria-current="page">Archive' in response.text
    assert "/static/archive.js" in response.text
    assert "/static/archive.css" in response.text
    assert "Radio Archive" in response.text


def test_archive_detail_renders_for_catalog_record_without_root_disclosure(archive_ui_db) -> None:
    recording_id = add_recording(archive_ui_db)
    response = TestClient(app).get(f"/archive/recordings/{recording_id}")

    assert response.status_code == 200
    assert recording_id in response.text
    assert "/not-exposed/archive" not in response.text
    assert "/static/archive_detail.js" in response.text


def test_archive_detail_returns_controlled_404_for_unknown_recording(archive_ui_db) -> None:
    response = TestClient(app).get("/archive/recordings/unknown")

    assert response.status_code == 404
    assert "Recording not found" in response.text


def test_anonymous_internet_archive_pages_redirect_to_safe_login(archive_ui_db, monkeypatch) -> None:
    monkeypatch.setattr(settings, "deployment_mode", "internet")
    monkeypatch.setattr(settings, "auth_mode", "oidc")
    client = TestClient(app, base_url="https://testserver")

    response = client.get("/archive", follow_redirects=False)
    detail = client.get("/archive/recordings/unknown", follow_redirects=False)

    assert response.status_code == detail.status_code == 303
    assert response.headers["location"] == "/auth/login?next=/archive"
    assert detail.headers["location"] == "/auth/login?next=/archive/recordings/unknown"


def test_all_authenticated_roles_can_open_archive_pages(archive_ui_db, monkeypatch) -> None:
    monkeypatch.setattr(settings, "deployment_mode", "internet")
    monkeypatch.setattr(settings, "auth_mode", "oidc")
    recording_id = add_recording(archive_ui_db, f"role-{uuid4()}")
    with TestClient(app, base_url="https://testserver") as client:
        for role in ("viewer", "operator", "admin"):
            client.cookies.set(settings.session_cookie_name, session_for(archive_ui_db, role))
            assert client.get("/archive").status_code == 200
            assert client.get(f"/archive/recordings/{recording_id}").status_code == 200


def test_archive_assets_use_database_api_and_id_based_audio(archive_ui_db) -> None:
    client = TestClient(app)
    archive_html = client.get("/archive").text
    archive_script = client.get("/static/archive.js").text
    detail_script = client.get("/static/archive_detail.js").text

    assert "/static/archive.css?v=20260903-1" in archive_html
    assert "/static/archive.js?v=20260903-1" in archive_html
    assert "/api/v1/archive/recordings" in archive_script
    assert "/api/v1/archive/recordings/${encodeURIComponent(item.id)}/audio" in detail_script
    assert "/api/v1/audio?path=" not in archive_script + detail_script
    assert "const filterLabel = field =>" in archive_script
    assert "field.labels?.[0]?.firstChild?.textContent?.trim()" in archive_script


def test_archive_browser_client_replaces_searches_and_keeps_cursors_out_of_urls(
    archive_ui_db,
) -> None:
    script = TestClient(app).get("/static/archive.js").text

    assert "if (append && loading) return" in script
    assert "controller?.abort()" in script
    assert "requestVersion += 1" in script
    assert "hasActiveFilters" in script
    assert "query.set('cursor', cursor)" in script
    assert "query.set(field.name, field.value)" in script
    assert "form.elements[name].value = query.get(name) ?? ''" in script
    assert "form.elements[name].value = stored ? stored.slice(0, 10) : ''" in script
    assert "window.addEventListener('popstate', () => { clearTimeout(debounce);" in script


def test_archive_detail_client_refetches_metadata_after_audio_failure(archive_ui_db) -> None:
    script = TestClient(app).get("/static/archive_detail.js").text

    assert "method: 'HEAD'" not in script
    assert "audioStatus = (await response.json()).audio_status" in script
    assert "Audio expired under the retention policy." in script


def test_dashboard_reconciles_jobs_and_ignores_stale_responses() -> None:
    script = TestClient(app).get("/static/dashboard.js").text

    assert "let jobsRequestVersion = 0" in script
    assert "requestVersion === jobsRequestVersion" in script
    assert "setInterval(loadJobs, 30000)" in script
