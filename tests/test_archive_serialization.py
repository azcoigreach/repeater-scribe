"""Archive compatibility and query cost must survive callsign review changes."""
from sqlalchemy import event
from test_archive import add_recording, archive_db  # noqa: F401
from test_callsign_regressions import seed

from asl_transcriber.archive import list_recordings, serialize_recording
from asl_transcriber.models import Recording


def test_legacy_mentions_remain_visible_without_a_current_normalized_transcript(request, tmp_path):
    sessions = request.getfixturevalue("archive_db")
    add_recording(sessions, tmp_path, "legacy")
    with sessions() as db:
        recording = db.get(Recording, "legacy")
        assert recording.current_transcript_id is None
        assert serialize_recording(recording)["transcript"]["callsign_mentions"] == [{"callsign": "KM7GHS"}]
        # Selecting a current transcript makes its empty normalized result authoritative.
        recording.current_transcript_id = recording.transcripts[0].id
        db.commit()
        db.expire_all()
        assert serialize_recording(recording)["transcript"]["callsign_mentions"] == []


def test_archive_page_serialization_has_bounded_queries(request):
    sessions = request.getfixturevalue("archive_db")
    with sessions() as db:
        for _ in range(8):
            seed(db)
    def page(limit):
        statements = []
        with sessions() as db:
            engine = db.get_bind()
            def track(_connection, _cursor, statement, _parameters, _context, _many):
                if statement.lstrip().upper().startswith("SELECT"):
                    statements.append(statement)
            event.listen(engine, "before_cursor_execute", track)
            try:
                items, _, _ = list_recordings(db, cursor=None, limit=limit, query=None, status=None, audio_status=None, from_at=None, to_at=None, callsign=None)
            finally:
                event.remove(engine, "before_cursor_execute", track)
        assert len(items) == limit
        assert all(row["transcript"]["callsign_mentions"][0]["callsign"] == "KM7GHS" for row in items)
        assert all(len(row["transcript"]["segments"]) == 1 for row in items)
        return len(statements)
    assert page(8) == page(1)
