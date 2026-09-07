"""UTC boundaries for persisted timestamps and API values.

SQLite drops tzinfo even from DateTime(timezone=True) columns. Our persisted
naive values are UTC; never interpret them using the container's local zone.
"""

from datetime import UTC, datetime


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def iso_utc(value: datetime | None) -> str | None:
    return utc(value).isoformat() if value is not None else None
