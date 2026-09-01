from sqlalchemy.pool import NullPool

from asl_transcriber.database import engine


def test_sqlite_uses_connection_per_session_instead_of_bounded_queue_pool() -> None:
    assert isinstance(engine.pool, NullPool)
