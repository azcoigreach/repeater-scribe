from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from asl_transcriber.config import settings


class Base(DeclarativeBase):
    pass


database_url = make_url(settings.database_url)
if database_url.drivername.startswith("sqlite") and database_url.database:
    Path(database_url.database).parent.mkdir(parents=True, exist_ok=True)

connect_args = {"check_same_thread": False} if database_url.drivername.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
