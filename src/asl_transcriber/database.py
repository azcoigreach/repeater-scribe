from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import NullPool

from asl_transcriber.config import settings


class Base(DeclarativeBase):
    pass


database_url = make_url(settings.database_url)
if database_url.drivername.startswith("sqlite") and database_url.database:
    Path(database_url.database).parent.mkdir(parents=True, exist_ok=True)

is_sqlite = database_url.drivername.startswith("sqlite")
connect_args = {"check_same_thread": False} if is_sqlite else {}
engine = create_engine(
    settings.database_url,
    connect_args=connect_args,
    poolclass=NullPool if is_sqlite else None,
    future=True,
)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)


def require_current_schema() -> None:
    """Fail startup rather than silently modifying an outdated database."""
    if not inspect(engine).has_table("alembic_version"):
        raise RuntimeError("Database is not migrated; run 'alembic upgrade head' before startup")
    with engine.connect() as connection:
        revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
    if revision != "archive_foundation":
        raise RuntimeError(
            "Database migration is outdated; run 'alembic upgrade head' before startup"
        )


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
