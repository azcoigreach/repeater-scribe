from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from asl_transcriber.archive import serialize_recording
from asl_transcriber.callsign_service import (
    callsign_profile,
    list_call_sign_mentions,
    list_callsigns,
    persist_transcript_details,
    review_mention,
    update_qrz_snapshot,
)
from asl_transcriber.database import SessionLocal, engine, get_db
from asl_transcriber.main import app, last_heard_callsigns
from asl_transcriber.models import (
    Callsign,
    CallsignMention,
    IngestionJob,
    Recording,
    Transcript,
    Transmission,
)
from asl_transcriber.models import TranscriptSegment as DbTranscriptSegment
from asl_transcriber.qrz import QrzCallsign
from asl_transcriber.transcription.base import TranscriptCallsignMention, TranscriptSegment


@pytest.fixture
def archive_db():
    with SessionLocal() as session:
        session.query(CallsignMention).delete()
        session.query(DbTranscriptSegment).delete()
        session.query(Transmission).delete()
        session.query(Recording).update({Recording.current_transcript_id: None})
        session.query(Transcript).delete()
        session.query(IngestionJob).delete()
        session.query(Callsign).delete()
        session.query(Recording).delete()
        session.commit()
    yield SessionLocal


def _stored_transcript(session, recording_id: str, transcript_id: str) -> tuple[Recording, Transcript]:
    recording = session.get(Recording, recording_id)
    assert recording is not None
    transcript = Transcript(id=transcript_id, job_id=transcript_id, recording_id=recording_id)
    session.add(transcript)
    session.flush()
    return recording, transcript


def test_normalized_persistence_and_current_transcript_statistics(archive_db) -> None:
    with archive_db() as session:
        recording = Recording(
            id="recording-1", source_path="2026090312000000.wav", archive_root="",
            started_at=datetime(2026, 9, 3, 12, tzinfo=UTC), status="completed",
        )
        session.add(recording)
        session.flush()
        session.add(IngestionJob(id="transcript-1", source_path=recording.source_path, recording_id=recording.id))
        session.flush()
        transcript = Transcript(id="transcript-1", job_id="transcript-1", recording_id=recording.id)
        session.add(transcript)
        session.flush()
        result = SimpleNamespace(
            segments=[TranscriptSegment(2.0, 4.0, "KM7GHS", ordinal=0, raw_text="KM7GHS", display_text="KM7GHS")],
            callsign_mentions=[TranscriptCallsignMention("KM7GHS", 2.0, 4.0, confidence=0.9)],
        )
        persist_transcript_details(session, transcript, recording, result)
        session.commit()

        profile = callsign_profile(session, "km7ghs")
        assert profile is not None
        assert profile["total_mentions"] == 1
        assert profile["unique_recordings"] == 1
        assert profile["active_days"] == 1
        assert profile["attributed_transmission_count"] == 0
        mention = session.query(CallsignMention).one()
        assert mention.heard_at.replace(tzinfo=UTC) == datetime(2026, 9, 3, 12, 0, 4, tzinfo=UTC)


def test_current_transcript_excludes_superseded_mentions(archive_db) -> None:
    with archive_db() as session:
        recording = Recording(id="recording-2", source_path="old.wav", archive_root="", status="completed")
        session.add(recording)
        session.flush()
        session.add_all([
            IngestionJob(id="old", source_path=recording.source_path, recording_id=recording.id),
            IngestionJob(id="current", source_path=recording.source_path, recording_id=recording.id),
        ])
        session.flush()
        old = Transcript(id="old", job_id="old", recording_id=recording.id)
        current = Transcript(id="current", job_id="current", recording_id=recording.id)
        session.add_all([old, current])
        session.flush()
        persist_transcript_details(
            session, old, recording,
            SimpleNamespace(segments=[], callsign_mentions=[TranscriptCallsignMention("KM7GHS", 0, 1)]),
        )
        persist_transcript_details(
            session, current, recording,
                SimpleNamespace(segments=[], callsign_mentions=[TranscriptCallsignMention("N0CAL", 0, 1)]),
        )
        session.commit()
        assert callsign_profile(session, "KM7GHS") is not None
        assert callsign_profile(session, "N0CAL")["total_mentions"] == 1
        recording.current_transcript_id = current.id
        session.commit()
        items, _, _ = list_call_sign_mentions(session, "KM7GHS", cursor=None, limit=50)
        assert items == []


def test_review_qrz_and_explicit_attribution_are_separate(archive_db) -> None:
    with archive_db() as session:
        recording = Recording(id="recording-3", source_path="third.wav", archive_root="", status="completed")
        session.add(recording)
        session.flush()
        session.add(IngestionJob(id="transcript-3", source_path=recording.source_path, recording_id=recording.id))
        session.flush()
        transcript = Transcript(id="transcript-3", job_id="transcript-3", recording_id=recording.id)
        session.add(transcript)
        session.flush()
        persist_transcript_details(
            session, transcript, recording,
            SimpleNamespace(segments=[], callsign_mentions=[TranscriptCallsignMention("KM7GHS", 0, 1)]),
        )
        session.flush()
        mention = session.query(CallsignMention).one()
        review_mention(session, mention.id, action="confirm", corrected_callsign=None, reviewer_identity="operator")
        update_qrz_snapshot(
            session, "KM7GHS", QrzCallsign("KM7GHS", name="Test Operator"), cache_seconds=3600
        )
        session.add(Transmission(
            recording_id=recording.id, operator_callsign="KM7GHS",
            attribution_level="explicit", duration_seconds=4,
        ))
        session.commit()
        profile = callsign_profile(session, "KM7GHS")
        assert profile["confirmed_mentions"] == 1
        assert profile["attributed_transmission_count"] == 1
        assert profile["attributed_airtime_seconds"] == 4.0
        assert profile["qrz_display_name"] == "Test Operator"
        assert session.query(CallsignMention).one().qrz_validation_status == "found"


def test_directory_cursor_is_returned_for_more_rows(archive_db) -> None:
    with archive_db() as session:
        for index, callsign in enumerate(("KM7GHS", "KE7WIL")):
            recording = Recording(id=f"recording-{index + 10}", source_path=f"{index}.wav", archive_root="", status="completed")
            session.add(recording)
            session.flush()
            session.add(IngestionJob(id=f"job-{index + 10}", source_path=recording.source_path, recording_id=recording.id))
            session.flush()
            transcript = Transcript(id=f"transcript-{index + 10}", job_id=f"job-{index + 10}", recording_id=recording.id)
            session.add(transcript)
            session.flush()
            persist_transcript_details(
                session, transcript, recording,
                SimpleNamespace(segments=[], callsign_mentions=[TranscriptCallsignMention(callsign, 0, 1)]),
            )
        session.commit()
        first, cursor, has_more = list_callsigns(session, query=None, cursor=None, limit=1)
        assert len(first) == 1 and cursor and has_more
        second, _, _ = list_callsigns(session, query=None, cursor=cursor, limit=1)
        assert second and second[0]["callsign"] != first[0]["callsign"]


def test_sqlite_foreign_keys_are_enforced(archive_db) -> None:
    with archive_db() as session:
        session.add(DbTranscriptSegment(
            id="orphan-segment", transcript_id="missing-transcript", ordinal=0,
            start_offset=0, end_offset=1,
        ))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
    with engine.connect() as connection:
        assert connection.scalar(text("PRAGMA foreign_keys")) == 1


def test_callsign_history_lookup_uses_an_index(archive_db) -> None:
    with archive_db() as session:
        plan = session.execute(text(
            "EXPLAIN QUERY PLAN SELECT id FROM callsign_mentions "
            "WHERE callsign_id = :callsign AND heard_at IS NOT NULL"
        ), {"callsign": "missing"}).all()
    rendered = " ".join(str(row) for row in plan).upper()
    assert "USING INDEX" in rendered or "USING COVERING INDEX" in rendered


def test_last_heard_uses_persisted_snapshot_without_qrz_lookup(archive_db, monkeypatch) -> None:
    with archive_db() as session:
        recording = Recording(
            id="recording-last-heard", source_path="not-a-timestamp.wav", archive_root="",
            started_at=datetime(2026, 9, 3, 12, tzinfo=UTC), status="completed",
        )
        session.add(recording)
        session.flush()
        session.add(IngestionJob(
            id="job-last-heard", source_path=recording.source_path, recording_id=recording.id
        ))
        session.flush()
        transcript = Transcript(
            id="transcript-last-heard", job_id="job-last-heard", recording_id=recording.id
        )
        session.add(transcript)
        session.flush()
        persist_transcript_details(
            session, transcript, recording,
            SimpleNamespace(segments=[], callsign_mentions=[TranscriptCallsignMention("KM7GHS", 1, 2)]),
        )
        update_qrz_snapshot(
            session, "KM7GHS", QrzCallsign("KM7GHS", name="Cached Name"), cache_seconds=3600
        )
        session.commit()
        monkeypatch.setattr("asl_transcriber.main.current_qrz_client", lambda: None)
        response = last_heard_callsigns(db=session)
    item = response["items"][0]
    assert item["callsign"] == "KM7GHS"
    assert item["last_heard_at"] == "2026-09-03T12:00:02"
    assert item["heard_offset_seconds"] == 2.0
    assert item["status"] == "found"
    assert item["name"] == "Cached Name"
    assert "QRZ confirms this callsign exists" in item["evidence"]


def test_callsign_api_contracts_and_ui_review(archive_db) -> None:
    with archive_db() as session:
        recording = Recording(id="recording-api", source_path="api.wav", archive_root="", status="completed")
        session.add(recording)
        session.flush()
        session.add(IngestionJob(id="job-api", source_path=recording.source_path, recording_id=recording.id))
        session.flush()
        transcript = Transcript(id="transcript-api", job_id="job-api", recording_id=recording.id)
        session.add(transcript)
        session.flush()
        persist_transcript_details(
            session, transcript, recording,
            SimpleNamespace(segments=[], callsign_mentions=[TranscriptCallsignMention("KM7GHS", 0, 1)]),
        )
        session.commit()
        mention_id = session.query(CallsignMention).one().id
    def override_db():
        with archive_db() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        directory = client.get("/api/v1/callsigns?limit=1")
        assert directory.status_code == 200
        assert directory.json()["items"][0]["callsign"] == "KM7GHS"
        assert client.get("/api/v1/callsigns?cursor=bad").status_code == 422
        profile = client.get("/api/v1/callsigns/KM7GHS")
        assert profile.status_code == 200
        assert profile.json()["attribution_complete"] is False
        history = client.get("/api/v1/callsigns/KM7GHS/mentions?review_status=detected")
        assert history.status_code == 200
        assert history.json()["items"][0]["mention_id"] == mention_id
        assert history.json()["items"][0]["timing_precision"] == "segment"
        assert history.json()["items"][0]["recognition_method"] == "legacy"
        assert history.json()["items"][0]["qrz_validation_status"] is None
        reviewed = client.patch(
            f"/ui/callsign-mentions/{mention_id}", json={"action": "confirm"}
        )
        assert reviewed.status_code == 200
        assert reviewed.json()["review_status"] == "confirmed"
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_retranscription_preserves_corrected_assignment_and_hides_rejected_evidence(archive_db) -> None:
    with archive_db() as session:
        recording = Recording(id="recording-review", source_path="review.wav", archive_root="", status="completed")
        session.add(recording)
        session.flush()
        session.add(IngestionJob(id="job-review", source_path=recording.source_path, recording_id=recording.id))
        session.flush()
        transcript = Transcript(id="transcript-review", job_id="job-review", recording_id=recording.id)
        session.add(transcript)
        session.flush()
        result = SimpleNamespace(
            segments=[],
            callsign_mentions=[TranscriptCallsignMention("KM7GHS", 0, 1, raw_observed_value="AM7 VHS")],
        )
        persist_transcript_details(session, transcript, recording, result)
        session.flush()
        mention = session.query(CallsignMention).one()
        review_mention(session, mention.id, action="correct", corrected_callsign="KE7WIL", reviewer_identity="operator")
        session.commit()
        persist_transcript_details(session, transcript, recording, result)
        session.commit()
        preserved = session.query(CallsignMention).one()
        assert preserved.canonical_callsign == "KE7WIL"
        assert preserved.review_status == "corrected"
        review_mention(session, preserved.id, action="reject", corrected_callsign=None, reviewer_identity="operator")
        session.commit()
        session.refresh(recording)
        assert serialize_recording(recording)["transcript"]["callsign_mentions"] == []


def test_retranscription_preserves_review_identity_across_small_timing_shift(archive_db) -> None:
    with archive_db() as session:
        recording = Recording(id="recording-shift", source_path="shift.wav", archive_root="", status="completed")
        session.add(recording)
        session.flush()
        session.add(IngestionJob(id="job-shift", source_path=recording.source_path, recording_id=recording.id))
        session.flush()
        transcript = Transcript(id="transcript-shift", job_id="job-shift", recording_id=recording.id)
        session.add(transcript)
        session.flush()
        persist_transcript_details(
            session, transcript, recording,
            SimpleNamespace(segments=[], callsign_mentions=[TranscriptCallsignMention("KM7GHS", 0, 1)]),
        )
        session.flush()
        initial = session.query(CallsignMention).one()
        review_mention(session, initial.id, action="confirm", corrected_callsign=None, reviewer_identity="operator")
        session.commit()
        persist_transcript_details(
            session, transcript, recording,
            SimpleNamespace(segments=[], callsign_mentions=[TranscriptCallsignMention("KM7GHS", 0.05, 1.05)]),
        )
        session.commit()
        preserved = session.query(CallsignMention).one()
        assert preserved.id == initial.id
        assert preserved.review_status == "confirmed"
        assert preserved.reviewer_identity == "operator"


def test_directory_keeps_qrz_not_found_callsigns(archive_db) -> None:
    with archive_db() as session:
        for index, callsign in enumerate(("KM7GHS", "KE7WIL")):
            recording = Recording(id=f"recording-qrz-{index}", source_path=f"qrz-{index}.wav", archive_root="", status="completed")
            session.add(recording)
            session.flush()
            session.add(IngestionJob(id=f"job-qrz-{index}", source_path=recording.source_path, recording_id=recording.id))
            session.flush()
            transcript = Transcript(id=f"transcript-qrz-{index}", job_id=f"job-qrz-{index}", recording_id=recording.id)
            session.add(transcript)
            session.flush()
            persist_transcript_details(session, transcript, recording, SimpleNamespace(segments=[], callsign_mentions=[TranscriptCallsignMention(callsign, 0, 1)]))
        callsign = session.query(Callsign).filter_by(normalized_callsign="KE7WIL").one()
        callsign.qrz_status = "not_found"
        session.commit()
        items, _, _ = list_callsigns(session, query=None, cursor=None, limit=50)
        assert {item["callsign"] for item in items} == {"KM7GHS", "KE7WIL"}
        filtered, _, _ = list_callsigns(session, query=None, cursor=None, limit=50, qrz_validation_status="not_found")
        assert [item["callsign"] for item in filtered] == ["KE7WIL"]


def test_retranscription_replaces_unreviewed_detection(archive_db) -> None:
    with archive_db() as session:
        recording = Recording(id="recording-detected", source_path="detected.wav", archive_root="", status="completed")
        session.add(recording)
        session.flush()
        session.add(IngestionJob(id="job-detected", source_path=recording.source_path, recording_id=recording.id))
        session.flush()
        transcript = Transcript(id="transcript-detected", job_id="job-detected", recording_id=recording.id)
        session.add(transcript)
        session.flush()
        persist_transcript_details(
            session, transcript, recording,
            SimpleNamespace(segments=[], callsign_mentions=[TranscriptCallsignMention("KM7GHS", 0, 1)]),
        )
        session.commit()
        persist_transcript_details(
            session, transcript, recording,
            SimpleNamespace(segments=[], callsign_mentions=[TranscriptCallsignMention("KE7WIL", 0, 1)]),
        )
        session.commit()
        mention = session.query(CallsignMention).one()
        assert mention.canonical_callsign == "KE7WIL"
        assert mention.review_status == "detected"