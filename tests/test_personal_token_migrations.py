from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime

import pytest
from test_archive_migrations import alembic, downgrade

STAMP = datetime(2026, 10, 18, tzinfo=UTC).isoformat()


def _insert_account(db: sqlite3.Connection, account_id: str) -> None:
    db.execute(
        """INSERT INTO accounts (
            id, issuer, subject, identity, role, enabled, created_at, updated_at,
            created_by, updated_by
        ) VALUES (?, ?, ?, ?, 'viewer', 1, ?, ?, 'test', 'test')""",
        (account_id, "https://identity.example.test", account_id, account_id, STAMP, STAMP),
    )


def test_personal_token_migration_preserves_service_token_and_allows_owner_scoped_names(
    tmp_path,
) -> None:
    database = tmp_path / "personal-tokens.db"
    alembic(database, "managed_accounts")
    with closing(sqlite3.connect(database)) as db, db:
        db.execute(
            """INSERT INTO api_tokens (
                id, name, token_hash, role, enabled, created_at, last_used_at, account_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL)""",
            ("service-id", "automation", "service-hash", "admin", 1, STAMP, STAMP),
        )

    alembic(database, "head")
    with closing(sqlite3.connect(database)) as db, db:
        assert db.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == ("personal_api_tokens",)
        service = db.execute(
            """SELECT id, name, token_hash, role, enabled, created_at, last_used_at,
                account_id, token_prefix, scopes_json, expires_at, revoked_at
                FROM api_tokens"""
        ).fetchone()
        assert service == (
            "service-id", "automation", "service-hash", "admin", 1, STAMP, STAMP,
            None, None, None, None, None,
        )
        _insert_account(db, "account-one")
        _insert_account(db, "account-two")
        db.executemany(
            """INSERT INTO api_tokens (
                id, account_id, name, token_hash, role, enabled, created_at, scopes_json
            ) VALUES (?, ?, 'automation', ?, 'viewer', 1, ?, '[]')""",
            [
                ("personal-one", "account-one", "hash-one", STAMP),
                ("personal-two", "account-two", "hash-two", STAMP),
            ],
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """INSERT INTO api_tokens (
                    id, account_id, name, token_hash, role, enabled, created_at
                ) VALUES ('duplicate-owner', 'account-one', 'automation', 'hash-three',
                    'viewer', 1, ?)""",
                (STAMP,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """INSERT INTO api_tokens (
                    id, name, token_hash, role, enabled, created_at
                ) VALUES ('duplicate-service', 'automation', 'hash-four', 'admin', 1, ?)""",
                (STAMP,),
            )
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []

    downgrade(database, "managed_accounts")
    with closing(sqlite3.connect(database)) as db:
        assert db.execute(
            "SELECT id, name, token_hash, account_id FROM api_tokens"
        ).fetchall() == [("service-id", "automation", "service-hash", None)]