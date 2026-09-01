from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from asl_transcriber.backup import backup_database, verify_database


def test_online_backup_is_verified_and_does_not_replace_by_default(tmp_path: Path) -> None:
    source = tmp_path / "live.db"
    destination = tmp_path / "backups" / "snapshot.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE example (value TEXT NOT NULL)")
        connection.execute("INSERT INTO example VALUES ('preserved')")

    result = backup_database(f"sqlite:///{source}", destination)

    assert result == destination
    assert "example" in verify_database(destination)
    assert destination.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(destination) as connection:
        assert connection.execute("SELECT value FROM example").fetchone() == ("preserved",)
    with pytest.raises(ValueError, match="already exists"):
        backup_database(f"sqlite:///{source}", destination)


def test_backup_rejects_live_database_as_destination(tmp_path: Path) -> None:
    source = tmp_path / "live.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE example (value TEXT)")

    with pytest.raises(ValueError, match="must differ"):
        backup_database(f"sqlite:///{source}", source, force=True)
