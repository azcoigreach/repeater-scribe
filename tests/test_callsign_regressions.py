"""Permanent behavioral coverage for reviewed evidence and bounded QRZ requests."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from asl_transcriber import callsign_service, main
from asl_transcriber.archive import list_recordings, serialize_recording
from asl_transcriber.callsign_service import (
    callsign_profile,
    list_call_sign_mentions,
    list_callsigns,
    persist_transcript_details,
    review_mention,
    update_qrz_snapshot,
)
from asl_transcriber.config import settings
from asl_transcriber.database import Base
from asl_transcriber.models import Callsign, CallsignMention, IngestionJob, Recording, Transcript
from asl_transcriber.qrz import QrzCallsign, QrzError
from asl_transcriber.transcription.base import TranscriptCallsignMention, TranscriptSegment

NOW = datetime(2026, 9, 5, 12, tzinfo=UTC)


@pytest.fixture
def db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'regression.db'}")
    Base.metadata.create_all(engine)

    class ClockMeta(type):
        def __instancecheck__(cls, value):
            return isinstance(value, datetime)

    class Clock(datetime, metaclass=ClockMeta):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz else NOW.replace(tzinfo=None)

    monkeypatch.setattr(callsign_service, "datetime", Clock)
    monkeypatch.setattr(main, "datetime", Clock)
    with Session(engine) as session:
        yield session
    engine.dispose()


def seed(session, callsign="KM7GHS", *, started=NOW, mentions=None):
    identifier = str(uuid4())
    recording = Recording(
        id=identifier,
        source_path=f"{identifier}.wav",
        archive_root="/private/archive",
        started_at=started,
        status="completed",
        audio_status="missing",
    )
    session.add(recording)
    session.flush()
    session.add(
        IngestionJob(id=identifier, recording_id=identifier, source_path=recording.source_path)
    )
    session.flush()
    transcript = Transcript(
        id=identifier, job_id=identifier, recording_id=identifier, display_text="saved transcript"
    )
    session.add(transcript)
    persist_transcript_details(
        session,
        transcript,
        recording,
        SimpleNamespace(
            segments=[TranscriptSegment(2, 4, "original evidence", confidence=-0.25)],
            callsign_mentions=mentions
            if mentions is not None
            else [
                TranscriptCallsignMention(
                    callsign,
                    2,
                    4,
                    confidence=0.87,
                    acoustic_confidence=0.78,
                    recognition_confidence=0.96,
                    raw_observed_value=callsign,
                    recognition_method="direct",
                    evidence=("Original evidence",),
                )
            ],
        ),
    )
    session.commit()
    return recording, transcript


PRESERVED = (
    "id",
    "reviewer_identity",
    "reviewed_at",
    "created_at",
    "updated_at",
    "callsign_id",
    "canonical_callsign",
    "raw_observed_value",
    "evidence_json",
    "confidence",
    "acoustic_confidence",
    "recognition_confidence",
    "recognition_method",
    "start_offset",
    "end_offset",
    "heard_at",
    "timing_precision",
    "review_status",
    "qrz_validation_status",
)


@pytest.mark.parametrize("action", ["confirm", "correct", "reject"])
def test_review_preserves_entire_evidence_and_timing_then_survives_empty_retranscription(
    db, action
):
    recording, transcript = seed(db)
    update_qrz_snapshot(db, "KM7GHS", QrzCallsign("KM7GHS", status="not_found"), cache_seconds=3600)
    update_qrz_snapshot(db, "KE7WIL", QrzCallsign("KE7WIL", status="found"), cache_seconds=3600)
    mention = db.query(CallsignMention).one()
    review_mention(
        db,
        mention.id,
        action=action,
        corrected_callsign="KE7WIL" if action == "correct" else None,
        reviewer_identity="reviewer@example.test",
    )
    db.commit()
    original = {key: getattr(mention, key) for key in PRESERVED}
    db.expunge(mention)
    replacement = SimpleNamespace(
        segments=[TranscriptSegment(2.1, 4.1, "replacement evidence")],
        callsign_mentions=[
            TranscriptCallsignMention("KM7GHS", 2.1, 4.1, confidence=0.1, evidence=("replacement",))
        ],
    )
    persist_transcript_details(db, transcript, recording, replacement)
    db.commit()
    preserved = db.query(CallsignMention).one()
    assert {key: getattr(preserved, key) for key in PRESERVED} == original
    assert preserved.heard_at == (NOW + timedelta(seconds=preserved.end_offset)).replace(
        tzinfo=None
    )
    assert preserved.is_current
    if action == "correct":
        assert preserved.canonical_callsign == "KE7WIL"
        assert preserved.qrz_validation_status == "found"
    value = preserved.canonical_callsign
    db.expunge(preserved)
    for _ in range(3):
        persist_transcript_details(
            db, transcript, recording, SimpleNamespace(segments=[], callsign_mentions=[])
        )
        db.commit()
        historical = db.query(CallsignMention).one()
        assert {key: getattr(historical, key) for key in PRESERVED} == original
        assert historical.is_current is False
        assert historical.segment_id is None
        assert list_callsigns(db, query=None, cursor=None, limit=100)[0] == []
        assert callsign_profile(db, value)["total_mentions"] == 0
        assert (
            list_call_sign_mentions(
                db, value, cursor=None, limit=100, review_status=historical.review_status
            )[0]
            == []
        )
        assert main.last_heard_callsigns(db=db)["items"] == []
        db.expire(recording)
        assert serialize_recording(recording)["transcript"]["callsign_mentions"] == []
        assert (
            list_recordings(
                db,
                callsign=value,
                cursor=None,
                limit=50,
                query=None,
                status=None,
                audio_status=None,
                from_at=None,
                to_at=None,
            )[0]
            == []
        )


def test_detected_mentions_are_replaced_and_rejected_current_history_is_explicit(db):
    recording, transcript = seed(db)
    old_id = db.query(CallsignMention).one().id
    persist_transcript_details(
        db,
        transcript,
        recording,
        SimpleNamespace(segments=[], callsign_mentions=[TranscriptCallsignMention("KE7WIL", 5, 6)]),
    )
    db.commit()
    mention = db.query(CallsignMention).one()
    assert mention.id != old_id and mention.canonical_callsign == "KE7WIL"
    assert mention.start_offset == 5 and mention.end_offset == 6
    review_mention(
        db, mention.id, action="reject", corrected_callsign=None, reviewer_identity="operator"
    )
    db.commit()
    assert list_call_sign_mentions(db, "KE7WIL", cursor=None, limit=10)[0] == []
    assert [
        item["mention_id"]
        for item in list_call_sign_mentions(
            db, "KE7WIL", cursor=None, limit=10, review_status="rejected"
        )[0]
    ] == [mention.id]


@pytest.mark.parametrize("alphabetical", [False, True])
def test_directory_and_history_traverse_dated_and_undated_exactly_once(db, alphabetical):
    for value in ("K1AA", "K1AB", "K1AC", "K1AD"):
        seed(db, value, started=NOW if value < "K1AC" else None)

    def traverse(fetch, key):
        cursor, seen = None, []
        for _ in range(10):
            rows, cursor, more = fetch(cursor)
            seen.extend(row[key] for row in rows)
            if not more:
                assert cursor is None
                break
        else:
            pytest.fail("pagination did not terminate")
        assert len(seen) == len(set(seen)) == 4

    traverse(
        lambda cursor: list_callsigns(
            db, query=None, cursor=cursor, limit=1, alphabetical=alphabetical
        ),
        "callsign",
    )
    for mention in db.query(CallsignMention):
        mention.canonical_callsign = "K1AA"
    db.commit()
    traverse(
        lambda cursor: list_call_sign_mentions(db, "K1AA", cursor=cursor, limit=1), "mention_id"
    )


def test_last_heard_finds_valid_after_1000_cached_negatives_and_directory_keeps_negatives(
    db, monkeypatch
):
    recording, transcript = seed(db, "K1OK", started=NOW - timedelta(days=1))
    valid = db.query(Callsign).one()
    valid.qrz_status = "found"
    valid.qrz_cache_expires_at = NOW + timedelta(days=1)
    for index in range(1000):
        value = f"K{index // 676}{chr(65 + index // 26 % 26)}{chr(65 + index % 26)}"
        call = Callsign(
            id=str(uuid4()),
            normalized_callsign=value,
            qrz_status="not_found",
            qrz_cache_expires_at=NOW + timedelta(days=1),
        )
        db.add(call)
        db.add(
            CallsignMention(
                callsign_id=call.id,
                canonical_callsign=value,
                recording_id=recording.id,
                transcript_id=transcript.id,
                heard_at=NOW,
            )
        )
    db.commit()
    client = Mock()
    monkeypatch.setattr(main, "current_qrz_client", lambda: client)
    assert [row["callsign"] for row in main.last_heard_callsigns(db=db, limit=1)["items"]] == [
        "K1OK"
    ]
    client.lookup.assert_not_called()
    rows, _, _ = list_callsigns(
        db, query=None, cursor=None, limit=100, qrz_validation_status="not_found"
    )
    assert len(rows) == 100


@pytest.mark.parametrize("failure,limit,expected", [(False, 2, 2), (True, 4, 1), (False, 0, 0)])
def test_expired_negatives_refresh_with_attempt_bound_and_stop_on_failure(
    db, monkeypatch, failure, limit, expected
):
    for value in ("K1AA", "K1AB", "K1AC", "K1AD"):
        seed(db, value)
        update_qrz_snapshot(db, value, QrzCallsign(value, status="not_found"), cache_seconds=-1)
    db.commit()
    client = Mock()
    client.lookup.side_effect = (
        QrzError("unavailable") if failure else lambda value: QrzCallsign(value, status="found")
    )
    factory = Mock(return_value=client)
    monkeypatch.setattr(main, "current_qrz_client", factory)
    monkeypatch.setattr(settings, "qrz_last_heard_refresh_limit", limit)
    result = main.last_heard_callsigns(db=db, limit=4)
    assert result["configured"] is True
    factory.assert_called_once_with()
    assert client.lookup.call_count == expected
    assert len(result["items"]) == 4
    assert sum(row["status"] == "found" for row in result["items"]) == (0 if failure else expected)


def test_sqlite_timestamps_have_explicit_utc_across_archive_and_callsigns(db):
    recording, _ = seed(db)
    db.expire_all()
    assert db.get(Recording, recording.id).started_at.tzinfo is None
    serialized = serialize_recording(recording)
    assert serialized['started_at'] == '2026-09-05T12:00:00+00:00'
    for key in ['created_at', 'updated_at']:
        assert datetime.fromisoformat(serialized[key]).utcoffset() == timedelta(0)
    profile = callsign_profile(db, 'KM7GHS')
    directory, _, _ = list_callsigns(db, query=None, cursor=None, limit=50)
    mentions, _, _ = list_call_sign_mentions(db, 'KM7GHS', cursor=None, limit=50)
    for value in [profile['first_heard'], profile['last_heard'], directory[0]['last_heard'], mentions[0]['heard_at']]:
        assert datetime.fromisoformat(value) == NOW + timedelta(seconds=4)


def test_offset_search_bounds_are_normalized_before_sqlite_queries(db):
    recording, _ = seed(db)
    start = datetime.fromisoformat('2026-09-05T05:00:00-07:00')
    end = datetime.fromisoformat('2026-09-05T05:00:05-07:00')
    rows, _, _ = list_recordings(db, cursor=None, limit=50, query=None, status=None,
                               audio_status=None, from_at=start, to_at=end, callsign=None)
    assert [row['id'] for row in rows] == [recording.id]
    mentions, _, _ = list_call_sign_mentions(db, 'KM7GHS', cursor=None, limit=50,
                                           from_at=start, to_at=end)
    assert [row['recording_id'] for row in mentions] == [recording.id]


def test_aware_midnight_callsign_bound_is_an_instant_not_an_extra_day(db):
    seed(db)
    mentions, _, _ = list_call_sign_mentions(
        db, 'KM7GHS', cursor=None, limit=50,
        to_at=datetime(2026, 9, 5, tzinfo=UTC),
    )
    assert mentions == []
    # Existing clients with bare UTC dates retain inclusive-day behavior.
    mentions, _, _ = list_call_sign_mentions(
        db, 'KM7GHS', cursor=None, limit=50, to_at=datetime(2026, 9, 5, tzinfo=UTC).replace(tzinfo=None),
    )
    assert len(mentions) == 1


def test_callsign_local_day_bounds_include_final_microsecond_and_exclude_next_day(db):
    last, _ = seed(db, started=datetime(2026, 9, 6, 6, 59, 55, 999999, tzinfo=UTC))
    seed(db, started=datetime(2026, 9, 6, 6, 59, 56, tzinfo=UTC))
    mentions, _, _ = list_call_sign_mentions(
        db, 'KM7GHS', cursor=None, limit=50,
        from_at=datetime.fromisoformat('2026-09-05T07:00:00Z'),
        to_at=datetime.fromisoformat('2026-09-06T06:59:59.999999Z'),
    )
    assert [item['recording_id'] for item in mentions] == [last.id]
