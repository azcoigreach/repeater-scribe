"""Recorded-time membership. No runtime jobs or source files are consulted here."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, event, func, inspect, or_, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from asl_transcriber.models import RadioSession, Recording, SessionMarker, SessionRecording

BATCH = 200


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def included():
    return or_(
        SessionRecording.decision == "include",
        and_(SessionRecording.decision.is_(None), SessionRecording.automatic.is_(True)),
    )


def overlaps(recording: Any, session: Any) -> bool:
    if recording.archive_root != session.source_root or recording.started_at is None:
        return False
    start = utc(recording.started_at)
    lower = utc(session.started_at)
    upper = utc(session.ended_at) if session.ended_at else None
    duration = max(recording.duration_seconds or 0, 0)
    if duration:
        return (upper is None or start < upper) and start + timedelta(seconds=duration) > lower
    return start >= lower and (upper is None or start < upper)


def crossing(recording: Any, session: Any) -> bool:
    if recording.started_at is None:
        return False
    start = utc(recording.started_at)
    end = start + timedelta(seconds=max(recording.duration_seconds or 0, 0))
    return start < utc(session.started_at) or bool(session.ended_at and end > utc(session.ended_at))


def _save(connection: Connection, values: list[dict]) -> None:
    if not values:
        return
    statement = insert(SessionRecording).values(values)
    connection.execute(
        statement.on_conflict_do_update(
            index_elements=["session_id", "recording_id"],
            set_={"automatic": statement.excluded.automatic},
        )
    )


def reconcile_session(connection: Connection, session_id: str) -> None:
    s = RadioSession.__table__
    r = Recording.__table__
    row = connection.execute(select(s).where(s.c.id == session_id)).first()
    if row is None:
        return
    # Reset computed state only. Exclusions and inclusions are never erased.
    connection.execute(
        update(SessionRecording)
        .where(SessionRecording.session_id == session_id, SessionRecording.automatic.is_(True))
        .values(automatic=False)
    )
    cursor = ""
    while True:
        statement = select(r).where(
            r.c.archive_root == row.source_root,
            r.c.started_at.is_not(None),
            r.c.id > cursor,
        )
        if row.ended_at:
            statement = statement.where(r.c.started_at < row.ended_at)
        # Filter interval ends in SQL (with conservative millisecond rounding),
        # then apply exact datetime arithmetic below for half-open boundaries.
        statement = statement.where(
            or_(
                r.c.started_at >= row.started_at,
                func.julianday(r.c.started_at) + func.coalesce(r.c.duration_seconds, 0) / 86400.0
                >= func.julianday(row.started_at) - 0.001 / 86400.0,
            )
        )
        recordings = connection.execute(statement.order_by(r.c.id).limit(BATCH)).all()
        if not recordings:
            break
        _save(
            connection,
            [
                {
                    "session_id": session_id,
                    "recording_id": item.id,
                    "automatic": True,
                    "updated_at": datetime.now(UTC),
                }
                for item in recordings
                if overlaps(item, row)
            ],
        )
        cursor = recordings[-1].id
    associate_markers(connection, session_id)


def reconcile_recording(connection: Connection, recording_id: str) -> None:
    r = Recording.__table__
    s = RadioSession.__table__
    row = connection.execute(select(r).where(r.c.id == recording_id)).first()
    if row is None:
        return
    connection.execute(
        update(SessionRecording)
        .where(SessionRecording.recording_id == recording_id, SessionRecording.automatic.is_(True))
        .values(automatic=False)
    )
    if row.started_at is None:
        return
    end = utc(row.started_at) + timedelta(seconds=max(row.duration_seconds or 0, 0))
    cursor = ""
    while True:
        sessions = connection.execute(
            select(s)
            .where(
                s.c.source_root == row.archive_root,
                s.c.started_at <= end,
                or_(s.c.ended_at.is_(None), s.c.ended_at > row.started_at),
                s.c.id > cursor,
            )
            .order_by(s.c.id)
            .limit(BATCH)
        ).all()
        if not sessions:
            break
        matching = [item for item in sessions if overlaps(row, item)]
        _save(
            connection,
            [
                {
                    "session_id": item.id,
                    "recording_id": recording_id,
                    "automatic": True,
                    "updated_at": datetime.now(UTC),
                }
                for item in matching
            ],
        )
        for item in matching:
            associate_markers(connection, item.id)
        cursor = sessions[-1].id


def associate_markers(connection: Connection, session_id: str) -> None:
    m = SessionMarker.__table__
    r = Recording.__table__
    cursor = ""
    while True:
        markers = connection.execute(
            select(m)
            .where(
                m.c.session_id == session_id,
                m.c.recording_id.is_(None),
                m.c.id > cursor,
            )
            .order_by(m.c.id)
            .limit(BATCH)
        ).all()
        if not markers:
            break
        for marker in markers:
            # SQLite date functions round submillisecond timestamps. Use a
            # conservative candidate filter and exact arithmetic before anchoring.
            candidates = connection.execute(
                select(r)
                .join(SessionRecording, SessionRecording.recording_id == r.c.id)
                .where(
                    SessionRecording.session_id == session_id,
                    included(),
                    r.c.started_at <= marker.at,
                    r.c.duration_seconds > 0,
                    func.julianday(r.c.started_at) + r.c.duration_seconds / 86400.0
                    >= func.julianday(marker.at) - 0.001 / 86400.0,
                )
                .execution_options(yield_per=BATCH)
            )
            matches = []
            try:
                for candidate in candidates:
                    offset = (utc(marker.at) - utc(candidate.started_at)).total_seconds()
                    if 0 <= offset < candidate.duration_seconds:
                        matches.append(candidate)
                        if len(matches) == 2:
                            break
            finally:
                candidates.close()
            if len(matches) == 1:
                recording = matches[0]
                offset = (utc(marker.at) - utc(recording.started_at)).total_seconds()
                if 0 <= offset < recording.duration_seconds:
                    connection.execute(
                        update(SessionMarker)
                        .where(m.c.id == marker.id)
                        .values(recording_id=recording.id, audio_offset=offset)
                    )
        cursor = markers[-1].id


def recover_sessions(factory) -> None:
    """Startup catch-up includes ended events and uses bounded pages/transactions."""
    cursor = ""
    with factory() as db:
        if not inspect(db.get_bind()).has_table("radio_sessions"):
            return
    while True:
        with factory() as db:
            ids = list(
                db.scalars(
                    select(RadioSession.id)
                    .where(RadioSession.id > cursor)
                    .order_by(RadioSession.id)
                    .limit(BATCH)
                )
            )
            for identifier in ids:
                reconcile_session(db.connection(), identifier)
            db.commit()
        if not ids:
            return
        cursor = ids[-1]


@event.listens_for(Session, "before_flush")
def _track_membership_changes(db: Session, _context, _instances) -> None:
    recordings, sessions = [], []
    for item in db.new.union(db.dirty):
        if isinstance(item, Recording):
            fields = ("started_at", "duration_seconds", "archive_root")
            if item in db.new or any(
                inspect(item).attrs[field].history.has_changes() for field in fields
            ):
                recordings.append(item)
        elif isinstance(item, RadioSession):
            fields = ("started_at", "ended_at", "source_root")
            if item in db.new or any(
                inspect(item).attrs[field].history.has_changes() for field in fields
            ):
                sessions.append(item)
    db.info["membership_changes"] = recordings, sessions


@event.listens_for(Session, "after_flush_postexec")
def _apply_membership_changes(db: Session, _context) -> None:
    recordings, sessions = db.info.pop("membership_changes", ([], []))
    for recording in recordings:
        reconcile_recording(db.connection(), recording.id)
    for session in sessions:
        reconcile_session(db.connection(), session.id)
