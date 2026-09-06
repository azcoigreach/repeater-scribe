"""Events workspace APIs. `/api/v1/events` remains the existing SSE stream."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from asl_transcriber.archive import (
    _decode_cursor,
    _encode_cursor,
    refresh_audio,
    serialize_recording,
)
from asl_transcriber.auth import (
    Principal,
    require_api_operator,
    require_ui_operator,
    require_viewer,
)
from asl_transcriber.callsign_service import canonical_callsign
from asl_transcriber.config import settings
from asl_transcriber.database import get_db
from asl_transcriber.models import (
    Callsign,
    CallsignMention,
    RadioSession,
    Recording,
    RecordingTag,
    SessionCheckIn,
    SessionMarker,
    SessionRecording,
    SessionRequest,
    SessionTag,
    Tag,
    Transcript,
    Transmission,
)
from asl_transcriber.session_membership import associate_markers, crossing, included, overlaps, utc

router = APIRouter(dependencies=[Depends(require_viewer)])
DB = Annotated[Session, Depends(get_db)]
EventType = Literal[
    "Net",
    "Exercise",
    "Club Event",
    "POTA",
    "Testing",
    "Maintenance",
    "Roundtable",
    "Special Event Station",
    "QSO Session",
    "Custom",
]
MarkerType = Literal[
    "Net Started", "Check-In", "Topic", "Emergency Traffic", "Net Closed", "Custom"
]


def source_id(root: str) -> str:
    return hashlib.sha256(root.encode()).hexdigest()


def sources(db: Session) -> list[dict]:
    roots = {str(Path(root).resolve()) for root in settings.archive_path_list}
    roots.update(
        db.scalars(
            select(Recording.archive_root)
            .where(Recording.archive_root.is_not(None))
            .distinct()
            .limit(500)
        )
    )
    roots.update(db.scalars(select(RadioSession.source_root).distinct().limit(500)))
    return [
        {"id": source_id(root), "label": f"{Path(root).name} · {source_id(root)[:8]}", "root": root}
        for root in sorted(roots)
        if root
    ]


def resolve_source(db: Session, identifier: str) -> str:
    for item in sources(db):
        if item["id"] == identifier:
            return item["root"]
    raise HTTPException(422, "Choose a known monitored source")


def writer(request: Request) -> Principal:
    return (
        require_ui_operator(request)
        if request.url.path.startswith("/ui/")
        else require_api_operator(request)
    )


Writer = Annotated[Principal, Depends(writer)]


def transaction(db: DB, principal: Writer):
    # Reserve the SQLite writer before any state-dependent read. This makes
    # check/create and retries atomic across threads and application processes.
    db.execute(text("BEGIN IMMEDIATE"))
    try:
        yield db
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            409, "Conflict: source already has an active event, or entry exists"
        ) from error
    except Exception:
        db.rollback()
        raise


WriteDB = Annotated[Session, Depends(transaction)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


class Tags(Input):
    tags: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("tags")
    @classmethod
    def valid_tags(cls, values):
        result = sorted({value.strip().lower() for value in values})
        if any(not value or len(value) > 64 for value in result):
            raise ValueError("Tags must contain 1–64 characters")
        return result


class CreateEvent(Tags):
    name: str = Field(min_length=1, max_length=255)
    type: EventType = "Net"
    source_id: str = Field(min_length=1, max_length=64)
    started_at: AwareDatetime | None = None
    ended_at: AwareDatetime | None = None
    description: str = Field(default="", max_length=20000)
    net_control: str | None = Field(default=None, max_length=32)
    recording_ids: list[str] = Field(default_factory=list, max_length=100)


class EditEvent(Input):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    type: EventType | None = None
    started_at: AwareDatetime | None = None
    ended_at: AwareDatetime | None = None
    description: str | None = Field(default=None, max_length=20000)
    net_control: str | None = Field(default=None, max_length=32)


class EndEvent(Input):
    ended_at: AwareDatetime | None = None


class Membership(Input):
    decision: Literal["include", "exclude", "automatic"]


class MarkerInput(Input):
    type: MarkerType = "Custom"
    note: str = Field(min_length=1, max_length=10000)
    callsign: str | None = Field(default=None, max_length=32)
    at: AwareDatetime
    recording_id: str | None = None
    audio_offset: float | None = Field(default=None, ge=0)


class CheckInInput(Input):
    callsign: str = Field(min_length=1, max_length=32)
    at: AwareDatetime
    note: str = Field(default="", max_length=10000)
    recording_id: str | None = None
    audio_offset: float | None = Field(default=None, ge=0)


def canonical(value: str | None) -> str | None:
    try:
        return canonical_callsign(value) if value else None
    except ValueError as error:
        raise HTTPException(422, "Invalid callsign") from error


def window(start: datetime, end: datetime | None) -> None:
    if utc(start) > datetime.now(UTC):
        raise HTTPException(422, "Start time cannot be in the future")
    if end and (utc(end) <= utc(start) or utc(end) > datetime.now(UTC)):
        raise HTTPException(422, "End must be after start and cannot be in the future")


def get_event(db: Session, identifier: str) -> RadioSession:
    event = db.get(RadioSession, identifier)
    if event is None:
        raise HTTPException(404, "Event not found")
    return event


def tags_for(db: Session, ids: list[str], *, recording=False) -> dict[str, list[str]]:
    model = RecordingTag if recording else SessionTag
    key = RecordingTag.recording_id if recording else SessionTag.session_id
    result: dict[str, list[str]] = {identifier: [] for identifier in ids}
    for identifier, tag in db.execute(
        select(key, model.tag).where(key.in_(ids)).order_by(model.tag)
    ):
        result[identifier].append(tag)
    return result


def save_tags(db: Session, identifier: str, tags: list[str], *, recording=False) -> None:
    model = RecordingTag if recording else SessionTag
    key = RecordingTag.recording_id if recording else SessionTag.session_id
    db.execute(delete(model).where(key == identifier))
    for value in tags:
        if db.get(Tag, value) is None:
            db.add(Tag(name=value))
            db.flush()
        db.add(model(**{("recording_id" if recording else "session_id"): identifier, "tag": value}))
    db.flush()


def summary(item: RadioSession) -> dict:
    return {
        "id": item.id,
        "name": item.name,
        "type": item.type,
        "description": item.description,
        "source_id": source_id(item.source_root),
        "source_label": f"{Path(item.source_root).name} · {source_id(item.source_root)[:8]}",
        "started_at": utc(item.started_at),
        "ended_at": utc(item.ended_at) if item.ended_at else None,
        "status": "ended" if item.ended_at else "active",
        "net_control": item.net_control,
        "created_at": utc(item.created_at),
        "updated_at": utc(item.updated_at),
        "created_by": item.created_by,
        "updated_by": item.updated_by,
    }


def attribution(db: Session, session_id: str, callsign: str | None = None) -> dict:
    duration = func.coalesce(
        Transmission.duration_milliseconds / 1000.0, Transmission.duration_seconds
    )
    statement = (
        select(func.count(), func.sum(duration))
        .select_from(Transmission)
        .join(SessionRecording, SessionRecording.recording_id == Transmission.recording_id)
        .where(
            SessionRecording.session_id == session_id,
            included(),
            Transmission.operator_callsign.is_not(None),
            Transmission.operator_callsign != "",
            Transmission.attribution_level != "unknown",
            Transmission.attribution_level != "",
        )
    )
    if callsign:
        statement = statement.where(func.upper(Transmission.operator_callsign) == callsign)
    count, airtime = db.execute(statement).one()
    return {
        "attributed_transmission_count": count if count else None,
        "attributed_airtime_seconds": airtime if count else None,
        "attribution_status": "partial" if count else "unavailable",
    }


def detail(db: Session, item: RadioSession) -> dict:
    count, audio, unknown = db.execute(
        select(
            func.count(),
            func.coalesce(func.sum(Recording.duration_seconds), 0),
            func.count().filter(Recording.duration_seconds.is_(None)),
        )
        .select_from(SessionRecording)
        .join(Recording)
        .where(SessionRecording.session_id == item.id, included())
    ).one()
    return {
        **summary(item),
        "tags": tags_for(db, [item.id])[item.id],
        "recording_count": count,
        "included_audio_seconds": audio,
        "unknown_audio_duration_count": unknown,
        "elapsed_seconds": (
            (utc(item.ended_at) if item.ended_at else datetime.now(UTC)) - utc(item.started_at)
        ).total_seconds(),
        "station_attributed_airtime_seconds": attribution(db, item.id)[
            "attributed_airtime_seconds"
        ],
    }


def page_query(statement, time_column, id_column, cursor, limit):
    if cursor:
        try:
            at, identifier = _decode_cursor(cursor)
        except ValueError as error:
            raise HTTPException(422, "Invalid pagination cursor") from error
        statement = statement.where(
            or_(time_column > at, (time_column == at) & (id_column > identifier))
        )
    return statement.order_by(time_column, id_column).limit(limit + 1)


def page_result(items, limit, key):
    more = len(items) > limit
    items = items[:limit]
    return {
        "items": items,
        "has_more": more,
        "next_cursor": _encode_cursor(*key(items[-1])) if more else None,
    }


@router.get("/sources")
def source_list(db: DB):
    return {
        "items": [
            {key: value for key, value in item.items() if key != "root"} for item in sources(db)
        ],
        "display_timezone": "browser local",
    }


@router.get("")
def list_events(
    db: DB,
    limit: int = Query(25, ge=1, le=100),
    cursor: str | None = None,
    q: str | None = None,
    type: EventType | None = None,
    source_id: str | None = None,
    status: Literal["active", "ended"] | None = None,
    from_: Annotated[AwareDatetime | None, Query(alias="from")] = None,
    to: AwareDatetime | None = None,
    tag: str | None = None,
    recording_id: str | None = None,
    callsign: str | None = None,
):
    statement = select(RadioSession)
    if q:
        statement = statement.where(RadioSession.name.icontains(q, autoescape=True))
    if type:
        statement = statement.where(RadioSession.type == type)
    if source_id:
        statement = statement.where(RadioSession.source_root == resolve_source(db, source_id))
    if status:
        statement = statement.where(
            RadioSession.ended_at.is_(None)
            if status == "active"
            else RadioSession.ended_at.is_not(None)
        )
    if from_:
        statement = statement.where(RadioSession.started_at >= utc(from_))
    if to:
        statement = statement.where(RadioSession.started_at < utc(to))
    if from_ and to and from_ >= to:
        raise HTTPException(422, "From must be before To")
    if tag:
        statement = statement.where(
            RadioSession.id.in_(
                select(SessionTag.session_id).where(SessionTag.tag == tag.strip().lower())
            )
        )
    if recording_id:
        statement = statement.where(
            RadioSession.id.in_(
                select(SessionRecording.session_id).where(
                    SessionRecording.recording_id == recording_id, included()
                )
            )
        )
    if callsign:
        statement = statement.where(
            RadioSession.id.in_(
                select(SessionCheckIn.session_id)
                .join(Callsign)
                .where(Callsign.normalized_callsign == canonical(callsign))
            )
        )
    rows = list(
        db.scalars(page_query(statement, RadioSession.started_at, RadioSession.id, cursor, limit))
    )
    tags = tags_for(db, [row.id for row in rows])
    return page_result(
        [{**summary(row), "tags": tags[row.id]} for row in rows],
        limit,
        lambda row: (row["started_at"], row["id"]),
    )


def selected_recordings(db: Session, payload: CreateEvent, root: str) -> list[Recording]:
    ids = set(payload.recording_ids)
    rows = list(db.scalars(select(Recording).where(Recording.id.in_(ids))))
    if len(rows) != len(ids) or any(row.archive_root != root for row in rows):
        raise HTTPException(422, "Selected recordings must exist in the event's monitored source")
    return rows


@router.post("/preview")
def preview(db: DB, payload: CreateEvent):
    root = resolve_source(db, payload.source_id)
    selected = selected_recordings(db, payload, root)
    start = utc(payload.started_at) if payload.started_at else datetime.now(UTC)
    end = utc(payload.ended_at) if payload.ended_at else None
    window(start, end)
    candidate = RadioSession(source_root=root, started_at=start, ended_at=end)
    statement = select(Recording).where(
        Recording.archive_root == root, Recording.started_at.is_not(None)
    )
    if end:
        statement = statement.where(Recording.started_at < end)
    total, extra = 0, 0
    examples: list[dict] = []
    selected_ids = {row.id for row in selected}
    for row in db.scalars(statement.execution_options(yield_per=200)):
        if not overlaps(row, candidate):
            continue
        total += 1
        if row.id not in selected_ids:
            extra += 1
            if len(examples) < 10:
                examples.append(
                    {
                        "id": row.id,
                        "source_path": row.source_path,
                        "started_at": utc(row.started_at) if row.started_at else None,
                        "boundary_crossing": crossing(row, candidate),
                    }
                )
    return {
        "automatic_count": total,
        "explicit_inclusion_count": len(selected_ids),
        "additional_automatic_count": extra,
        "additional_examples": examples,
        "explanation": "Selected recordings remain explicit inclusions. Other overlapping recordings from this source are included automatically. Boundary-crossing audio is included whole, without trimming.",
    }


@router.post("")
@router.post("/start")
def create_event(
    db: WriteDB,
    principal: Writer,
    payload: CreateEvent,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=128)],
):
    values = payload.model_dump(mode="json")
    # Canonicalize offsets and set-like inputs before hashing retry intent.
    for key in ("started_at", "ended_at"):
        if values[key]:
            values[key] = utc(datetime.fromisoformat(values[key])).isoformat()
    values["recording_ids"] = sorted(set(values["recording_ids"]))
    values["net_control"] = canonical(payload.net_control)
    fingerprint = hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()
    previous = db.get(SessionRequest, (principal.subject, idempotency_key))
    if previous:
        if previous.fingerprint != fingerprint:
            raise HTTPException(409, "Idempotency key already used with different input")
        return detail(db, get_event(db, previous.session_id))
    root = resolve_source(db, payload.source_id)
    selected = selected_recordings(db, payload, root)
    start = utc(payload.started_at) if payload.started_at else datetime.now(UTC)
    end = utc(payload.ended_at) if payload.ended_at else None
    window(start, end)
    if end is None and db.scalar(
        select(RadioSession.id).where(
            RadioSession.source_root == root, RadioSession.ended_at.is_(None)
        )
    ):
        raise HTTPException(
            409, "This source already has an active event. End it before starting another."
        )
    item = RadioSession(
        name=payload.name,
        type=payload.type,
        description=payload.description,
        source_root=root,
        started_at=start,
        ended_at=end,
        net_control=values["net_control"],
        created_by=principal.identity,
        updated_by=principal.identity,
    )
    db.add(item)
    db.flush()
    for recording in selected:
        set_membership(db, item, recording.id, "include", principal.identity)
    save_tags(db, item.id, payload.tags)
    db.add(
        SessionRequest(
            actor=principal.subject,
            key=idempotency_key,
            fingerprint=fingerprint,
            session_id=item.id,
        )
    )
    db.flush()
    return detail(db, item)


@router.get("/{session_id}")
def read_event(db: DB, session_id: str):
    return detail(db, get_event(db, session_id))


@router.patch("/{session_id}")
def edit_event(db: WriteDB, principal: Writer, session_id: str, payload: EditEvent):
    item = get_event(db, session_id)
    values = payload.model_dump(exclude_unset=True)
    if any(
        values.get(key, "present") is None for key in ("name", "type", "description", "started_at")
    ):
        raise HTTPException(422, "Name, type, description and start cannot be null")
    for key, value in values.items():
        if key in ("started_at", "ended_at") and value:
            value = utc(value)
        if key == "net_control":
            value = canonical(value)
        setattr(item, key, value)
    window(item.started_at, item.ended_at)
    item.updated_by = principal.identity
    db.flush()
    return detail(db, item)


@router.post("/{session_id}/end")
def end_event(db: WriteDB, principal: Writer, session_id: str, payload: EndEvent):
    item = get_event(db, session_id)
    if item.ended_at:
        if payload.ended_at and utc(item.ended_at) != utc(payload.ended_at):
            raise HTTPException(
                409, "Event already ended at another time; edit its boundaries to change it"
            )
        return detail(db, item)
    item.ended_at = utc(payload.ended_at) if payload.ended_at else datetime.now(UTC)
    window(item.started_at, item.ended_at)
    item.updated_by = principal.identity
    db.flush()
    return detail(db, item)


@router.post("/{session_id}/reopen")
def reopen_event(db: WriteDB, principal: Writer, session_id: str):
    item = get_event(db, session_id)
    if item.ended_at is not None:
        item.ended_at = None
        item.updated_by = principal.identity
        db.flush()
    return detail(db, item)


def set_membership(db, item, recording_id, decision, actor):
    recording = db.get(Recording, recording_id)
    if not recording or recording.archive_root != item.source_root:
        raise HTTPException(422, "Recording must exist in this event's monitored source")
    row = db.get(SessionRecording, (item.id, recording_id), populate_existing=True)
    if row is None:
        row = SessionRecording(session_id=item.id, recording_id=recording_id)
        db.add(row)
    row.decision = None if decision == "automatic" else decision
    row.automatic = overlaps(recording, item)
    row.updated_by = actor
    row.updated_at = datetime.now(UTC)
    db.flush()
    associate_markers(db.connection(), item.id)
    return {
        "recording_id": recording_id,
        "decision": row.decision or "automatic",
        "automatic": row.automatic,
        "included": row.decision == "include" or (row.decision is None and row.automatic),
    }


@router.patch("/{session_id}/recordings/{recording_id}")
def membership(
    db: WriteDB, principal: Writer, session_id: str, recording_id: str, payload: Membership
):
    return set_membership(
        db, get_event(db, session_id), recording_id, payload.decision, principal.identity
    )


@router.get("/{session_id}/recordings")
def recordings(
    db: DB,
    session_id: str,
    limit: int = Query(25, ge=1, le=100),
    cursor: str | None = None,
    membership: Literal["included", "decisions"] = "included",
    latest: bool = False,
):
    item = get_event(db, session_id)
    time = func.coalesce(Recording.started_at, Recording.created_at)
    statement = (
        select(Recording, SessionRecording)
        .join(SessionRecording, SessionRecording.recording_id == Recording.id)
        .where(SessionRecording.session_id == item.id)
    )
    statement = statement.where(
        included() if membership == "included" else SessionRecording.decision.is_not(None)
    )
    statement = statement.options(
        joinedload(Recording.current_transcript).options(
            selectinload(Transcript.segments), selectinload(Transcript.callsign_mentions)
        ),
        selectinload(Recording.transcripts).options(
            selectinload(Transcript.segments), selectinload(Transcript.callsign_mentions)
        ),
        selectinload(Recording.ingestion_jobs),
    )
    if latest and cursor:
        raise HTTPException(422, "Latest traffic cannot be combined with a history cursor")
    if latest:
        rows = db.execute(
            statement.order_by(time.desc(), Recording.id.desc()).limit(limit + 1)
        ).all()
        has_earlier = len(rows) > limit
        rows = list(reversed(rows[:limit]))
    else:
        has_earlier = False
        rows = db.execute(page_query(statement, time, Recording.id, cursor, limit)).all()
    tags = tags_for(db, [r.id for r, _ in rows], recording=True)
    output = []
    # The existing current revision serializer is authoritative. Only the bounded
    # visible page may receive a provisional runtime result when no final exists.
    for recording, member in rows:
        refresh_audio(recording)
        data = serialize_recording(recording)
        data["started_at"] = utc(recording.started_at) if recording.started_at else None
        data["created_at"] = utc(recording.created_at)
        data["provisional"] = False
        if data["transcript"] is None:
            from asl_transcriber.main import current_runtime

            live = current_runtime().live_results.get(recording.id)
            if live:
                data["transcript"] = {
                    "display_text": live.display_text,
                    "segments": [],
                    "callsign_mentions": [],
                }
                data["provisional"] = True
        data.update(
            decision=member.decision or "automatic",
            automatic=member.automatic,
            included=member.decision == "include" or (member.decision is None and member.automatic),
            boundary_crossing=crossing(recording, item),
            tags=tags[recording.id],
        )
        output.append(data)
    db.commit()
    return {
        **page_result(
            output, limit, lambda row: (row["started_at"] or row["created_at"], row["id"])
        ),
        "has_earlier": has_earlier,
        "latest": latest,
    }


@router.patch("/{session_id}/tags")
def event_tags(db: WriteDB, principal: Writer, session_id: str, payload: Tags):
    item = get_event(db, session_id)
    item.updated_by = principal.identity
    item.updated_at = datetime.now(UTC)
    save_tags(db, session_id, payload.tags)
    return {"tags": payload.tags}


@router.patch("/{session_id}/recordings/{recording_id}/tags")
def recording_tags(db: WriteDB, session_id: str, recording_id: str, payload: Tags):
    item = get_event(db, session_id)
    recording = db.get(Recording, recording_id)
    if not recording or recording.archive_root != item.source_root:
        raise HTTPException(422, "Recording is outside this source")
    save_tags(db, recording_id, payload.tags, recording=True)
    return {"tags": payload.tags}


def validate_anchor(db, item, recording_id, offset):
    if recording_id is None:
        if offset is not None:
            raise HTTPException(422, "An audio offset requires a recording")
        return
    recording = db.get(Recording, recording_id)
    member = db.scalar(
        select(SessionRecording).where(
            SessionRecording.session_id == item.id,
            SessionRecording.recording_id == recording_id,
            included(),
        )
    )
    if recording is None or recording.archive_root != item.source_root or member is None:
        raise HTTPException(422, "Supporting recording must be included in this event")
    if (
        offset is not None
        and recording.duration_seconds is not None
        and offset > recording.duration_seconds
    ):
        raise HTTPException(422, "Audio offset exceeds recording duration")


def annotation_data(db, row):
    data = {column.name: getattr(row, column.name) for column in row.__table__.columns}
    for key, value in data.items():
        if isinstance(value, datetime):
            data[key] = utc(value)
    if isinstance(row, SessionCheckIn):
        data["callsign"] = row.callsign.normalized_callsign
    data["evidence_outside_event"] = False
    data["audio_available"] = False
    if row.recording_id:
        recording = db.get(Recording, row.recording_id)
        data["evidence_outside_event"] = not bool(
            db.scalar(
                select(SessionRecording.recording_id).where(
                    SessionRecording.session_id == row.session_id,
                    SessionRecording.recording_id == row.recording_id,
                    included(),
                )
            )
        )
        data["audio_available"] = bool(recording and recording.audio_status == "available")
    return data


@router.get("/{session_id}/markers")
def markers(
    db: DB, session_id: str, limit: int = Query(25, ge=1, le=100), cursor: str | None = None
):
    get_event(db, session_id)
    rows = db.scalars(
        page_query(
            select(SessionMarker).where(SessionMarker.session_id == session_id),
            SessionMarker.at,
            SessionMarker.id,
            cursor,
            limit,
        )
    )
    return page_result(
        [annotation_data(db, row) for row in rows], limit, lambda row: (row["at"], row["id"])
    )


@router.post("/{session_id}/markers")
def add_marker(db: WriteDB, principal: Writer, session_id: str, payload: MarkerInput):
    item = get_event(db, session_id)
    validate_anchor(db, item, payload.recording_id, payload.audio_offset)
    values = payload.model_dump()
    values.update(at=utc(payload.at), callsign=canonical(payload.callsign))
    row = SessionMarker(
        session_id=session_id,
        created_by=principal.identity,
        updated_by=principal.identity,
        **values,
    )
    db.add(row)
    db.flush()
    associate_markers(db.connection(), session_id)
    db.refresh(row)
    return annotation_data(db, row)


@router.patch("/{session_id}/markers/{marker_id}")
def edit_marker(
    db: WriteDB, principal: Writer, session_id: str, marker_id: str, payload: MarkerInput
):
    item = get_event(db, session_id)
    row = db.get(SessionMarker, marker_id)
    if not row or row.session_id != session_id:
        raise HTTPException(404, "Marker not found")
    # Unchanged anchors remain editable even if membership was later excluded.
    if (row.recording_id, row.audio_offset) != (payload.recording_id, payload.audio_offset):
        validate_anchor(db, item, payload.recording_id, payload.audio_offset)
    for key, value in payload.model_dump().items():
        setattr(
            row,
            key,
            utc(value) if key == "at" else canonical(value) if key == "callsign" else value,
        )
    row.updated_by = principal.identity
    db.flush()
    return annotation_data(db, row)


@router.delete("/{session_id}/markers/{marker_id}")
def remove_marker(db: WriteDB, session_id: str, marker_id: str):
    get_event(db, session_id)
    db.execute(
        delete(SessionMarker).where(
            SessionMarker.id == marker_id, SessionMarker.session_id == session_id
        )
    )
    return {"deleted": True}


@router.get("/{session_id}/detected")
def detected(
    db: DB, session_id: str, limit: int = Query(25, ge=1, le=100), cursor: str | None = None
):
    get_event(db, session_id)
    call = CallsignMention.canonical_callsign
    statement = select(
        call.label("callsign"),
        func.min(CallsignMention.heard_at).label("first_mention_at"),
        func.max(CallsignMention.heard_at).label("last_mention_at"),
        func.count().label("mention_count"),
        func.count(func.distinct(CallsignMention.recording_id)).label("recording_count"),
    )
    statement = (
        statement.join(Recording, Recording.id == CallsignMention.recording_id)
        .join(SessionRecording, SessionRecording.recording_id == Recording.id)
        .where(
            SessionRecording.session_id == session_id,
            included(),
            CallsignMention.transcript_id == Recording.current_transcript_id,
            CallsignMention.is_current.is_(True),
            CallsignMention.review_status != "rejected",
        )
    )
    if cursor:
        if canonical(cursor) != cursor:
            raise HTTPException(422, "Invalid station cursor")
        statement = statement.where(call > cursor)
    rows = db.execute(statement.group_by(call).order_by(call).limit(limit + 1)).mappings().all()
    result = []
    for row in rows[:limit]:
        data = dict(row)
        for key in ("first_mention_at", "last_mention_at"):
            if data[key]:
                data[key] = utc(data[key])
        # Bounded evidence sample; full evidence uses the paginated station history.
        evidence = (
            db.execute(
                select(CallsignMention.recording_id, CallsignMention.start_offset)
                .join(Recording, Recording.id == CallsignMention.recording_id)
                .join(SessionRecording, SessionRecording.recording_id == Recording.id)
                .where(
                    SessionRecording.session_id == session_id,
                    included(),
                    call == row["callsign"],
                    CallsignMention.transcript_id == Recording.current_transcript_id,
                    CallsignMention.is_current.is_(True),
                    CallsignMention.review_status != "rejected",
                )
                .order_by(CallsignMention.heard_at, CallsignMention.id)
                .limit(3)
            )
            .mappings()
            .all()
        )
        data["evidence"] = [dict(value) for value in evidence]
        data.update(attribution(db, session_id, row["callsign"]))
        result.append(data)
    return {
        "items": result,
        "has_more": len(rows) > limit,
        "next_cursor": result[-1]["callsign"] if len(rows) > limit else None,
    }


@router.get("/{session_id}/checkins")
def checkins(
    db: DB, session_id: str, limit: int = Query(25, ge=1, le=100), cursor: str | None = None
):
    get_event(db, session_id)
    statement = (
        select(SessionCheckIn)
        .options(joinedload(SessionCheckIn.callsign))
        .where(SessionCheckIn.session_id == session_id)
    )
    rows = db.scalars(page_query(statement, SessionCheckIn.at, SessionCheckIn.id, cursor, limit))
    return page_result(
        [annotation_data(db, row) for row in rows], limit, lambda row: (row["at"], row["id"])
    )


def checkin_values(db, payload):
    value = canonical(payload.callsign)
    callsign = db.scalar(select(Callsign).where(Callsign.normalized_callsign == value))
    if callsign is None:
        callsign = Callsign(normalized_callsign=value)
        db.add(callsign)
        db.flush()
    values = payload.model_dump(exclude={"callsign"})
    values.update(callsign_id=callsign.id, at=utc(payload.at))
    return values


@router.post("/{session_id}/checkins")
def add_checkin(db: WriteDB, principal: Writer, session_id: str, payload: CheckInInput):
    item = get_event(db, session_id)
    validate_anchor(db, item, payload.recording_id, payload.audio_offset)
    values = checkin_values(db, payload)
    if db.scalar(
        select(SessionCheckIn.id).where(
            SessionCheckIn.session_id == session_id,
            SessionCheckIn.callsign_id == values["callsign_id"],
        )
    ):
        raise HTTPException(409, "This callsign already has a confirmed check-in")
    row = SessionCheckIn(
        session_id=session_id,
        confirmed_by=principal.identity,
        updated_by=principal.identity,
        **values,
    )
    db.add(row)
    db.flush()
    return annotation_data(db, row)


@router.patch("/{session_id}/checkins/{checkin_id}")
def edit_checkin(
    db: WriteDB, principal: Writer, session_id: str, checkin_id: str, payload: CheckInInput
):
    item = get_event(db, session_id)
    row = db.get(SessionCheckIn, checkin_id)
    if row is None or row.session_id != session_id:
        raise HTTPException(404, "Check-in not found")
    if (row.recording_id, row.audio_offset) != (payload.recording_id, payload.audio_offset):
        validate_anchor(db, item, payload.recording_id, payload.audio_offset)
    for key, value in checkin_values(db, payload).items():
        setattr(row, key, value)
    row.updated_by = principal.identity
    db.flush()
    db.expire(row, ["callsign"])
    return annotation_data(db, row)


@router.delete("/{session_id}/checkins/{checkin_id}")
def undo_checkin(db: WriteDB, session_id: str, checkin_id: str):
    get_event(db, session_id)
    db.execute(
        delete(SessionCheckIn).where(
            SessionCheckIn.session_id == session_id, SessionCheckIn.id == checkin_id
        )
    )
    return {"deleted": True}
