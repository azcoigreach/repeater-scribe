from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

from sqlalchemy.engine import make_url


def _sqlite_path(database_url: str) -> Path:
    url = make_url(database_url)
    if not url.drivername.startswith("sqlite") or not url.database or url.database == ":memory:":
        raise ValueError("backup commands require a file-backed SQLite database")
    return Path(url.database).resolve()


def verify_database(path: Path) -> list[str]:
    resolved = path.resolve()
    if not resolved.is_file():
        raise ValueError(f"database does not exist: {resolved}")
    uri = f"{resolved.as_uri()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        result = connection.execute("PRAGMA integrity_check").fetchone()
        if result != ("ok",):
            raise ValueError(f"database integrity check failed: {result!r}")
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        ).fetchall()
    return [str(row[0]) for row in rows]


def backup_database(database_url: str, destination: Path, *, force: bool = False) -> Path:
    source = _sqlite_path(database_url)
    target = destination.resolve()
    if not source.is_file():
        raise ValueError(f"source database does not exist: {source}")
    if source == target:
        raise ValueError("backup destination must differ from the live database")
    if target.exists() and not force:
        raise ValueError(f"backup destination already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent, delete=False
    ) as handle:
        temporary = Path(handle.name)
    try:
        with (
            sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True) as source_connection,
            sqlite3.connect(temporary) as backup_connection,
        ):
            source_connection.backup(backup_connection)
        verify_database(temporary)
        if target.exists() and not force:
            raise ValueError(f"backup destination already exists: {target}")
        os.replace(temporary, target)
        target.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)
    return target
