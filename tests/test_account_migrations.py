from __future__ import annotations

import sqlite3
from contextlib import closing

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from test_archive_migrations import alembic

from asl_transcriber import auth


@pytest.mark.parametrize("prior", [None, "events_sessions"])
def test_managed_accounts_fresh_and_released_upgrade(tmp_path, prior, monkeypatch):
    database = tmp_path / "migration.db"
    if prior:
        alembic(database, prior)
        with closing(sqlite3.connect(database)) as db, db:
            stamp = "2026-09-14 12:00:00"
            db.execute(
                "INSERT INTO auth_sessions VALUES (?,?,?,?,?,?,?,?)",
                (
                    "session-hash",
                    "subject",
                    "historical@example.test",
                    "operator",
                    "csrf",
                    stamp,
                    stamp,
                    "2099-01-01",
                ),
            )
            db.execute(
                "INSERT INTO oidc_login_states VALUES (?,?,?,?,?)",
                ("state", "nonce", "verifier", "/", "2099-01-01"),
            )
            db.execute(
                "INSERT INTO api_tokens VALUES (?,?,?,?,?,?,?)",
                (
                    "token-id",
                    "automation",
                    auth.token_digest("migration-test-credential"),
                    "operator",
                    1,
                    stamp,
                    stamp,
                ),
            )
            db.execute(
                "INSERT INTO security_audits VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    "audit-id",
                    stamp,
                    "historical@example.test",
                    "session",
                    "authorization",
                    "allowed",
                    "POST",
                    "/ui/node/1/command",
                    "127.0.0.1",
                    "role=operator",
                ),
            )
    alembic(database, "head")
    with closing(sqlite3.connect(database)) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "managed_accounts",
        )
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("SELECT count(*) FROM auth_sessions").fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM oidc_login_states").fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM accounts").fetchone() == (0,)
        assert db.execute(
            "SELECT action FROM security_audits WHERE action='account_migration'"
        ).fetchone()
        if prior:
            assert db.execute(
                "SELECT id,name,token_hash,role,enabled,account_id FROM api_tokens"
            ).fetchone() == (
                "token-id",
                "automation",
                auth.token_digest("migration-test-credential"),
                "user",
                1,
                None,
            )
            assert db.execute(
                "SELECT actor,detail FROM security_audits WHERE id='audit-id'"
            ).fetchone() == ("historical@example.test", "role=operator")

    if prior:
        engine = create_engine(f"sqlite:///{database}")
        monkeypatch.setattr(auth, "SessionLocal", sessionmaker(engine))
        principal = auth._api_principal("migration-test-credential")
        assert principal.role == "user" and principal.token_id == "token-id"
        engine.dispose()
