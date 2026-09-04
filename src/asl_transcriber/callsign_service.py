from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import Select, case, exists, func, select
from sqlalchemy.orm import Session

from asl_transcriber.models import (
    Callsign,
    CallsignMention,
    Recording,
    Transcript,
    TranscriptSegment,
    Transmission,
)
from asl_transcriber.qrz import QrzCallsign
from asl_transcriber.transcription.callsigns import normalize_callsigns


def canonical_callsign(value: str) -> str:
    normalized = normalize_callsigns((value,))
    if not normalized:
        raise ValueError("invalid callsign")
    return normalized[0]


def _get_or_create(session: Session, value: str) -> Callsign:
    callsign = session.scalar(select(Callsign).where(Callsign.normalized_callsign == value))
    if callsign is None:
        callsign = Callsign(normalized_callsign=value)
        session.add(callsign)
        session.flush()
    return callsign


def persist_transcript_details(
    session: Session, transcript: Transcript, recording: Recording, result: object
) -> None:
    """Replace durable details for a transcript inside its caller's transaction."""
    previous_reviews = {
        (mention.raw_observed_value, mention.start_offset, mention.end_offset): (
            mention.callsign_id,
            mention.canonical_callsign,
            mention.review_status,
            mention.reviewer_identity,
            mention.reviewed_at,
        )
        for mention in session.query(CallsignMention).filter(
            CallsignMention.transcript_id == transcript.id
        )
    }
    session.query(CallsignMention).filter(CallsignMention.transcript_id == transcript.id).delete()
    session.query(TranscriptSegment).filter(TranscriptSegment.transcript_id == transcript.id).delete()
    segments = getattr(result, "segments", None) or []
    segment_rows: list[TranscriptSegment] = []
    for ordinal, segment in enumerate(segments):
        row = TranscriptSegment(
            transcript_id=transcript.id,
            recording_id=recording.id,
            ordinal=getattr(segment, "ordinal", ordinal),
            start_offset=float(segment.start),
            end_offset=float(segment.end),
            raw_text=getattr(segment, "raw_text", None) or segment.text,
            display_text=getattr(segment, "display_text", None) or segment.text,
            language=segment.language,
            avg_logprob=segment.confidence,
        )
        session.add(row)
        segment_rows.append(row)
    session.flush()
    mentions = getattr(result, "callsign_mentions", None) or []
    started_at = recording.started_at
    for mention in mentions:
        value = canonical_callsign(mention.callsign)
        callsign = _get_or_create(session, value)
        segment = max(
            segment_rows,
            key=lambda row: max(
                0.0,
                min(row.end_offset, mention.end) - max(row.start_offset, mention.start),
            ),
            default=None,
        )
        if segment is not None and max(
            0.0, min(segment.end_offset, mention.end) - max(segment.start_offset, mention.start)
        ) <= 0:
            segment = None
        heard_at = None
        if started_at is not None:
            heard_at = started_at + timedelta(seconds=max(0.0, mention.end))
        mention_row = CallsignMention(
            callsign_id=callsign.id,
            transcript_id=transcript.id,
            recording_id=recording.id,
            segment_id=segment.id if segment else None,
            raw_observed_value=getattr(mention, "raw_observed_value", None) or mention.callsign,
            canonical_callsign=value,
            start_offset=mention.start,
            end_offset=mention.end,
            heard_at=heard_at,
            timing_precision=getattr(mention, "timing_precision", "segment"),
            confidence=mention.confidence,
            acoustic_confidence=mention.acoustic_confidence,
            recognition_confidence=mention.recognition_confidence,
            recognition_method=getattr(mention, "recognition_method", "legacy"),
            evidence_json=json.dumps(list(mention.evidence)),
            qrz_validation_status=callsign.qrz_status,
        )
        review = previous_reviews.get(
            (mention_row.raw_observed_value, mention_row.start_offset, mention_row.end_offset)
        )
        if review is not None:
            (
                mention_row.callsign_id,
                mention_row.canonical_callsign,
                mention_row.review_status,
                mention_row.reviewer_identity,
                mention_row.reviewed_at,
            ) = review
        session.add(mention_row)
    recording.current_transcript_id = transcript.id


def _cursor_value(value: datetime | None, mention_id: str) -> str:
    payload = json.dumps([value.isoformat() if value else "", mention_id], separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def _decode_cursor(value: str) -> tuple[datetime | None, str]:
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        timestamp, mention_id = json.loads(raw)
        return (datetime.fromisoformat(timestamp) if timestamp else None), str(mention_id)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("cursor must be valid") from error


def list_callsigns(
    session: Session, *, query: str | None, cursor: str | None, limit: int,
    alphabetical: bool = False, review_status: str | None = None,
) -> tuple[list[dict[str, object]], str | None, bool]:
    counted = func.count(CallsignMention.id).label("mention_count")
    confirmed = func.sum(
        case((CallsignMention.review_status == "confirmed", 1), else_=0)
    ).label("confirmed_mentions")
    attributed = exists(
        select(1).where(
            func.upper(Transmission.operator_callsign) == Callsign.normalized_callsign,
            Transmission.operator_callsign.is_not(None),
            Transmission.attribution_level != "unknown",
        )
    ).label("has_attributed_transmissions")
    recent_confidence = (
        select(CallsignMention.confidence)
        .join(Recording, Recording.id == CallsignMention.recording_id)
        .where(
            CallsignMention.callsign_id == Callsign.id,
            CallsignMention.review_status != "rejected",
            CallsignMention.transcript_id == Recording.current_transcript_id,
        )
        .order_by(CallsignMention.heard_at.desc(), CallsignMention.id.desc())
        .limit(1)
        .correlate(Callsign)
        .scalar_subquery()
        .label("most_recent_confidence")
    )
    statement: Select = select(
        Callsign, func.min(CallsignMention.heard_at).label("first_heard"),
        func.max(CallsignMention.heard_at).label("last_heard"), counted,
        func.count(func.distinct(CallsignMention.recording_id)).label("recording_count"),
        func.count(func.distinct(func.date(CallsignMention.heard_at))).label("active_days"),
        confirmed, attributed, recent_confidence,
    ).join(CallsignMention, CallsignMention.callsign_id == Callsign.id).join(
        Recording, Recording.id == CallsignMention.recording_id
    ).where(
        CallsignMention.review_status != "rejected",
        CallsignMention.transcript_id == Recording.current_transcript_id,
    ).group_by(Callsign.id)
    if query:
        statement = statement.where(Callsign.normalized_callsign.ilike(f"%{query.strip().upper()}%"))
    if review_status:
        statement = statement.where(CallsignMention.review_status == review_status)
    cursor_time: datetime | None = None
    cursor_callsign: str | None = None
    if cursor:
        cursor_time, cursor_callsign = _decode_cursor(cursor)
    if alphabetical:
        if cursor_callsign:
            statement = statement.having(Callsign.normalized_callsign > cursor_callsign)
        statement = statement.order_by(Callsign.normalized_callsign.asc())
    else:
        if cursor_callsign and cursor_time is None:
            statement = statement.having(Callsign.normalized_callsign < cursor_callsign)
        elif cursor_time is not None and cursor_callsign:
            latest = func.max(CallsignMention.heard_at)
            statement = statement.having(
                (latest.is_(None))
                | (latest < cursor_time)
                | ((latest == cursor_time) & (Callsign.normalized_callsign < cursor_callsign))
            )
        statement = statement.order_by(func.max(CallsignMention.heard_at).desc(), Callsign.normalized_callsign.desc())
    rows = list(session.execute(statement.limit(limit + 1)))
    has_more = len(rows) > limit
    rows = rows[:limit]
    result = [
        {
            "callsign": row[0].normalized_callsign,
            "qrz_display_name": row[0].qrz_display_name,
            "qrz_location": row[0].qrz_location,
            "first_heard": row[1].isoformat() if row[1] else None,
            "last_heard": row[2].isoformat() if row[2] else None,
            "mention_count": row[3], "recording_count": row[4], "active_days": row[5],
            "confirmed_mentions": int(row[6] or 0),
            "has_attributed_transmissions": bool(row[7]),
            "most_recent_confidence": row[8],
        }
        for row in rows
    ]
    next_cursor = None
    if has_more and rows:
        last = rows[-1]
        next_cursor = _cursor_value(
            None if alphabetical else last[2], last[0].normalized_callsign
        )
    return result, next_cursor, has_more


def list_call_sign_mentions(
    session: Session, value: str, *, cursor: str | None, limit: int,
    from_at: datetime | None = None, to_at: datetime | None = None,
    review_status: str | None = None, audio_status: str | None = None,
) -> tuple[list[dict[str, object]], str | None, bool]:
    normalized = canonical_callsign(value)
    statement = (
        select(CallsignMention, Transcript, Recording, TranscriptSegment)
        .join(Transcript, Transcript.id == CallsignMention.transcript_id)
        .join(Recording, Recording.id == CallsignMention.recording_id)
        .outerjoin(TranscriptSegment, TranscriptSegment.id == CallsignMention.segment_id)
        .where(
            CallsignMention.canonical_callsign == normalized,
            CallsignMention.transcript_id == Recording.current_transcript_id,
        )
    )
    if review_status:
        statement = statement.where(CallsignMention.review_status == review_status)
    else:
        statement = statement.where(CallsignMention.review_status != "rejected")
    if from_at:
        statement = statement.where(CallsignMention.heard_at >= from_at)
    if to_at:
        statement = statement.where(CallsignMention.heard_at <= to_at)
    if audio_status:
        statement = statement.where(Recording.audio_status == audio_status)
    if cursor:
        cursor_time, mention_id = _decode_cursor(cursor)
        if cursor_time is None:
            statement = statement.where(CallsignMention.id < mention_id)
        else:
            statement = statement.where(
                (CallsignMention.heard_at.is_(None))
                | (CallsignMention.heard_at < cursor_time)
                | ((CallsignMention.heard_at == cursor_time) & (CallsignMention.id < mention_id))
            )
    statement = statement.order_by(CallsignMention.heard_at.desc(), CallsignMention.id.desc())
    rows = list(session.execute(statement.limit(limit + 1)))
    has_more = len(rows) > limit
    rows = rows[:limit]
    items = [
        {
            "mention_id": mention.id, "recording_id": mention.recording_id,
            "transcript_id": mention.transcript_id, "segment_id": mention.segment_id,
            "heard_at": mention.heard_at.isoformat() if mention.heard_at else None,
            "start_offset": mention.start_offset, "end_offset": mention.end_offset,
            "timing_precision": mention.timing_precision,
            "raw_observed_value": mention.raw_observed_value,
            "canonical_callsign": mention.canonical_callsign,
            "recognition_method": mention.recognition_method,
            "qrz_validation_status": mention.qrz_validation_status,
            "confidence": mention.confidence,
            "acoustic_confidence": mention.acoustic_confidence,
            "recognition_confidence": mention.recognition_confidence,
            "evidence": json.loads(mention.evidence_json), "review_status": mention.review_status,
            "audio_status": recording.audio_status,
            "excerpt": (
                segment.display_text or segment.raw_text
                if segment is not None
                else transcript.display_text[:240]
            ),
            "recording_url": f"/archive/recordings/{recording.id}",
            "audio_available": recording.audio_status == "available",
        }
        for mention, transcript, recording, segment in rows
    ]
    next_cursor = None
    if has_more and rows:
        mention = rows[-1][0]
        next_cursor = _cursor_value(mention.heard_at, mention.id)
    return items, next_cursor, has_more


def last_heard_rows(session: Session, limit: int) -> list[dict[str, object]]:
    latest = (
        select(CallsignMention)
        .join(Recording, Recording.id == CallsignMention.recording_id)
        .where(
            CallsignMention.callsign_id == Callsign.id,
            CallsignMention.review_status != "rejected",
            CallsignMention.transcript_id == Recording.current_transcript_id,
        )
        .order_by(CallsignMention.heard_at.desc(), CallsignMention.id.desc())
        .limit(1)
        .correlate(Callsign)
    )
    latest_heard_at = latest.with_only_columns(CallsignMention.heard_at).scalar_subquery()
    latest_end_offset = latest.with_only_columns(CallsignMention.end_offset).scalar_subquery()
    latest_evidence = latest.with_only_columns(CallsignMention.evidence_json).scalar_subquery()
    latest_recording = latest.with_only_columns(CallsignMention.recording_id).scalar_subquery()
    latest_source_path = latest.with_only_columns(Recording.source_path).scalar_subquery()
    statement = select(
        Callsign,
        func.min(CallsignMention.heard_at),
        func.max(CallsignMention.heard_at),
        func.count(CallsignMention.id),
        func.count(func.distinct(CallsignMention.recording_id)),
        func.max(CallsignMention.confidence),
        func.max(CallsignMention.acoustic_confidence),
        latest_heard_at,
        latest_end_offset,
        latest_evidence,
        latest_recording,
        latest_source_path,
    ).join(CallsignMention, CallsignMention.callsign_id == Callsign.id).join(
        Recording, Recording.id == CallsignMention.recording_id
    ).where(
        CallsignMention.review_status != "rejected",
        CallsignMention.transcript_id == Recording.current_transcript_id,
    ).group_by(Callsign.id).order_by(
        func.max(CallsignMention.heard_at).desc(), Callsign.normalized_callsign.desc()
    ).limit(min(max(limit, 1), 100))
    rows = []
    for row in session.execute(statement).all():
        details = row[0]
        evidence = json.loads(row[9] or "[]") if row[9] else []
        rows.append({
            "callsign": details.normalized_callsign,
            "last_heard_at": row[2].isoformat() if row[2] else None,
            "heard_offset_seconds": row[8],
            "time_precision": "segment" if row[8] is not None else "recording",
            "observation_count": int(row[3]), "recording_count": int(row[4]),
            "_best_observation": float(row[5] or 0.45),
            "acoustic_quality_percent": round(float(row[6]) * 100) if row[6] is not None else None,
            "evidence": [*map(str, evidence), f"Heard {row[3]} times across {row[4]} recording{'s' if row[4] != 1 else ''}"],
            "source_path": row[11],
            "_recording_id": row[10],
            "qrz_status": details.qrz_status,
            "qrz_display_name": details.qrz_display_name,
            "qrz_location": details.qrz_location,
            "qrz_image_url": details.qrz_image_url,
            "qrz_profile_url": details.qrz_profile_url,
            "qrz_cache_expires_at": details.qrz_cache_expires_at,
        })
    return rows


def callsign_profile(session: Session, value: str) -> dict[str, object] | None:
    normalized = canonical_callsign(value)
    callsign = session.scalar(select(Callsign).where(Callsign.normalized_callsign == normalized))
    if callsign is None:
        return None
    rows = session.execute(
        select(
            func.min(CallsignMention.heard_at), func.max(CallsignMention.heard_at),
            func.count(CallsignMention.id), func.count(func.distinct(CallsignMention.recording_id)),
            func.count(func.distinct(func.date(CallsignMention.heard_at))),
        ).join(Recording, Recording.id == CallsignMention.recording_id).where(
            CallsignMention.callsign_id == callsign.id,
            CallsignMention.review_status != "rejected",
            CallsignMention.transcript_id == Recording.current_transcript_id,
        )
    ).one()
    counts: dict[str, int] = {
        str(status): int(count)
        for status, count in session.execute(
            select(CallsignMention.review_status, func.count()).join(
                Recording, Recording.id == CallsignMention.recording_id
            ).where(
                CallsignMention.callsign_id == callsign.id,
                CallsignMention.transcript_id == Recording.current_transcript_id,
            ).group_by(CallsignMention.review_status)
        ).all()
    }
    attribution = session.execute(
        select(
            func.count(Transmission.id),
            func.coalesce(
                func.sum(
                    func.coalesce(
                        Transmission.duration_milliseconds / 1000.0,
                        Transmission.duration_seconds,
                    )
                ),
                0.0,
            ),
        ).where(
            func.upper(Transmission.operator_callsign) == normalized,
            Transmission.operator_callsign.is_not(None),
            Transmission.attribution_level != "unknown",
        )
    ).one()
    confidence = session.execute(
        select(
            func.min(CallsignMention.confidence), func.avg(CallsignMention.confidence),
            func.max(CallsignMention.confidence),
        ).join(Recording, Recording.id == CallsignMention.recording_id).where(
            CallsignMention.callsign_id == callsign.id,
            CallsignMention.review_status != "rejected",
            CallsignMention.transcript_id == Recording.current_transcript_id,
        )
    ).one()
    return {
        "callsign": normalized, "qrz_display_name": callsign.qrz_display_name,
        "qrz_location": callsign.qrz_location, "qrz_image_url": callsign.qrz_image_url,
        "qrz_profile_url": callsign.qrz_profile_url, "qrz_status": callsign.qrz_status,
        "first_heard": rows[0].isoformat() if rows[0] else None,
        "last_heard": rows[1].isoformat() if rows[1] else None,
        "total_mentions": rows[2], "unique_recordings": rows[3], "active_days": rows[4],
        "detected_mentions": counts.get("detected", 0), "confirmed_mentions": counts.get("confirmed", 0),
        "corrected_mentions": counts.get("corrected", 0), "rejected_mentions": counts.get("rejected", 0),
        "attributed_transmission_count": int(attribution[0] or 0),
        "attributed_airtime_seconds": float(attribution[1] or 0.0),
        "attribution_status": "partial" if attribution[0] else "unavailable",
        "attribution_complete": False,
        "confidence_summary": {
            "minimum": confidence[0], "average": confidence[1], "maximum": confidence[2],
        },
    }


def review_mention(
    session: Session, mention_id: str, *, action: str, corrected_callsign: str | None,
    reviewer_identity: str,
) -> CallsignMention:
    mention = session.get(CallsignMention, mention_id)
    if mention is None:
        raise LookupError("mention not found")
    if action not in {"confirm", "reject", "correct"}:
        raise ValueError("action must be confirm, reject, or correct")
    if action == "correct":
        if corrected_callsign is None:
            raise ValueError("corrected_callsign is required")
        normalized = canonical_callsign(corrected_callsign)
        mention.callsign_id = _get_or_create(session, normalized).id
        mention.canonical_callsign = normalized
        mention.review_status = "corrected"
    else:
        if corrected_callsign is not None:
            raise ValueError("corrected_callsign is only valid for correction")
        mention.review_status = "confirmed" if action == "confirm" else "rejected"
    mention.reviewer_identity = reviewer_identity[:255]
    mention.reviewed_at = datetime.now(UTC)
    return mention


def update_qrz_snapshot(
    session: Session, value: str, details: QrzCallsign, *, cache_seconds: float
) -> Callsign:
    callsign = _get_or_create(session, canonical_callsign(value))
    now = datetime.now(UTC)
    callsign.qrz_status = details.status
    callsign.qrz_display_name = details.name
    callsign.qrz_location = details.location
    callsign.qrz_image_url = details.image_url
    callsign.qrz_profile_url = details.profile_url
    callsign.qrz_lookup_at = now
    callsign.qrz_cache_expires_at = now + timedelta(seconds=cache_seconds)
    session.flush()
    return callsign