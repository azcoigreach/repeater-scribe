from __future__ import annotations

import base64
import json
import re
import wave
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import Select, and_, exists, false, func, or_, select, text, true
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.sql.elements import ColumnElement

from asl_transcriber.models import Recording, Transcript


class ArchiveQueryError(ValueError):
    pass


def recording_started_at(source_path: str) -> datetime | None:
    match = re.match(r"^(\d{14})(\d{2})", Path(source_path).name)
    if match is None:
        return None
    return datetime.strptime(match.group(1), "%Y%m%d%H%M%S").replace(
        tzinfo=UTC, microsecond=int(match.group(2)) * 10000
    )


def _encode_cursor(timestamp: datetime, recording_id: str) -> str:
    value = json.dumps([timestamp.isoformat(), recording_id], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        payload = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        value = json.loads(payload)
        timestamp, recording_id = value
        parsed = datetime.fromisoformat(timestamp)
        if not isinstance(recording_id, str):
            raise TypeError
        UUID(recording_id)
        return parsed, recording_id
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise ArchiveQueryError("cursor must be a valid archive cursor") from error


def refresh_audio(recording: Recording) -> None:
    if recording.audio_status in {"archived", "protected", "expired"}:
        return
    if not recording.archive_root:
        recording.audio_status = "missing"
        return
    candidate = (Path(recording.archive_root) / recording.source_path).resolve()
    root = Path(recording.archive_root).resolve()
    if not candidate.is_relative_to(root):
        recording.audio_status = "missing"
        return
    try:
        stat = candidate.stat()
    except OSError:
        recording.audio_status = "missing"
        return
    recording.audio_status = "available"
    recording.file_size = stat.st_size
    recording.source_modified_at = datetime.fromtimestamp(stat.st_mtime, UTC)
    try:
        with wave.open(str(candidate), "rb") as source:
            recording.duration_seconds = source.getnframes() / source.getframerate()
    except (EOFError, OSError, wave.Error, ZeroDivisionError):
        pass


def get_or_create_recording(
    session: Session, *, source_path: str, archive_root: str, recording_id: str | None = None,
    status: str = "pending",
) -> Recording:
    recording = session.scalar(
        select(Recording).where(
            Recording.archive_root == archive_root, Recording.source_path == source_path
        )
    )
    if recording is None:
        recording = Recording(
            id=recording_id,
            source_path=source_path,
            archive_root=archive_root,
            started_at=recording_started_at(source_path),
            status=status,
        )
        session.add(recording)
    recording.status = status
    refresh_audio(recording)
    return recording


def serialize_recording(recording: Recording) -> dict[str, object]:
    transcript = next((item for item in recording.transcripts if item.job_id == recording.id), None)
    transcript = transcript or (recording.transcripts[0] if recording.transcripts else None)
    job = next((item for item in recording.ingestion_jobs if item.id == recording.id), None)
    job = job or (recording.ingestion_jobs[0] if recording.ingestion_jobs else None)
    return {
        "id": recording.id,
        "source_path": recording.source_path,
        "started_at": recording.started_at.isoformat() if recording.started_at else None,
        "source_modified_at": recording.source_modified_at.isoformat() if recording.source_modified_at else None,
        "duration_seconds": recording.duration_seconds,
        "file_size": recording.file_size,
        "content_hash": recording.content_hash,
        "local_node": recording.local_node,
        "source_node": recording.source_node,
        "status": recording.status,
        "audio_status": recording.audio_status,
        "audio_available": recording.audio_status == "available",
        "created_at": recording.created_at.isoformat(),
        "updated_at": recording.updated_at.isoformat(),
        "expired_at": recording.expired_at.isoformat() if recording.expired_at else None,
        "ingestion": ({"status": job.status, "attempt_count": job.attempt_count, "last_error": job.last_error, "dead_letter": job.dead_letter} if job else None),
        "transcript": ({"raw_text": transcript.raw_text, "display_text": transcript.display_text, "language": transcript.language, "confidence": transcript.confidence, "callsign_mentions": json.loads(transcript.callsign_mentions_json)} if transcript else None),
    }


def list_recordings(
    session: Session, *, cursor: str | None, limit: int, query: str | None,
    status: str | None, audio_status: str | None, from_at: datetime | None, to_at: datetime | None,
    callsign: str | None,
) -> tuple[list[dict[str, object]], str | None, bool]:
    order_time = func.coalesce(Recording.started_at, Recording.created_at)
    statement: Select = select(Recording).options(
        joinedload(Recording.transcripts), joinedload(Recording.ingestion_jobs)
    )
    conditions: list[ColumnElement[bool]] = []
    if query is not None:
        tokens = re.findall(r"[\w-]+", query, re.UNICODE)
        if tokens:
            # Tokens are produced by an allowlist and quoted as literals, so user
            # input cannot introduce FTS5 operators or syntax errors.
            fts_query = " AND ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)
            matching_rows = text(
                "SELECT t.recording_id FROM transcripts t JOIN transcript_fts f "
                "ON f.rowid = t.rowid WHERE transcript_fts MATCH :query"
            ).bindparams(query=fts_query)
            conditions.append(Recording.id.in_(matching_rows))
        else:
            conditions.append(false())
    if status:
        conditions.append(Recording.status == status)
    if audio_status:
        conditions.append(Recording.audio_status == audio_status)
    if from_at:
        conditions.append(order_time >= from_at)
    if to_at:
        conditions.append(order_time <= to_at)
    if callsign:
        normalized_callsign = callsign.strip().upper()
        if not normalized_callsign:
            conditions.append(false())
        else:
            mentions = func.json_each(Transcript.callsign_mentions_json).table_valued(
                "value"
            ).alias("mentions")
            conditions.append(
                exists(
                    select(1).select_from(Transcript).join(mentions, true()).where(
                        Transcript.recording_id == Recording.id,
                        func.json_extract(mentions.c.value, "$.callsign") == normalized_callsign,
                    )
                )
            )
    if cursor:
        cursor_time, cursor_id = _decode_cursor(cursor)
        conditions.append(or_(order_time < cursor_time, and_(order_time == cursor_time, Recording.id < cursor_id)))
    if conditions:
        statement = statement.where(*conditions)
    rows = list(session.scalars(statement.order_by(order_time.desc(), Recording.id.desc()).limit(limit + 1)).unique())
    has_more = len(rows) > limit
    rows = rows[:limit]
    last_row = rows[-1] if rows else None
    next_cursor = (
        _encode_cursor(last_row.started_at or last_row.created_at, last_row.id)
        if has_more and last_row
        else None
    )
    return [serialize_recording(row) for row in rows], next_cursor, has_more