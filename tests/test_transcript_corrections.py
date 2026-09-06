import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from asl_transcriber import auth, main
from asl_transcriber.archive import serialize_recording
from asl_transcriber.config import settings
from asl_transcriber.database import Base, get_db
from asl_transcriber.models import AuthSession, CallsignMention, Transcript
from asl_transcriber.runtime import ArchiveRuntime
from asl_transcriber.transcript_corrections import CorrectionConflict, correct_selection
from asl_transcriber.transcription.base import TranscriptResult, TranscriptSegment

WORDS = 'Hello Kilo Mike Seven Golf Hotel Sierra, this is the radio check.'
SELECTED = 'Kilo Mike Seven Golf Hotel Sierra'


@pytest.fixture()
def context(tmp_path, monkeypatch):
    archive = tmp_path / 'archive'
    archive.mkdir()
    (archive / 'call.wav').write_bytes(b'audio')
    engine = create_engine(f"sqlite:///{tmp_path / 'corrections.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    runtime = ArchiveRuntime([archive], session_factory=sessions)
    runtime.scan_once()
    runtime.scan_once()
    runtime.process_pending(lambda _: decoded())
    monkeypatch.setattr(main, 'current_runtime', lambda: runtime)
    monkeypatch.setattr(auth, 'SessionLocal', sessions)
    def get_session():
        with sessions() as session:
            yield session
    main.app.dependency_overrides[get_db] = get_session
    yield runtime, sessions
    main.app.dependency_overrides.clear()
    engine.dispose()


def decoded(text=WORDS):
    return TranscriptResult(raw_text=text, display_text=text, segments=[TranscriptSegment(1, 7, text)])


def edit(session, text=WORDS, selected=SELECTED, value='KM7GHS', occurrence=0):
    transcript = session.scalar(select(Transcript))
    start = text.index(selected, occurrence)
    return correct_selection(session, transcript, expected_text=text, start=start,
                             end=start + len(selected), callsign=value, reviewer='operator@test')


def test_missed_callsign_updates_text_segment_and_history_without_changing_raw(context):
    runtime, sessions = context
    with sessions() as session:
        mention = edit(session)
        session.commit()
        transcript = session.scalar(select(Transcript))
        assert transcript.raw_text == WORDS
        assert transcript.display_text == WORDS.replace(SELECTED, 'KM7GHS')
        assert transcript.segments[0].raw_text == WORDS
        assert transcript.segments[0].display_text == transcript.display_text
        assert mention.canonical_callsign == 'KM7GHS'
        assert mention.review_status == 'corrected'
        assert mention.raw_observed_value == SELECTED
        assert mention.start_offset == 1 and mention.end_offset == 7
        assert mention.confidence is None
        assert mention.timing_precision == 'segment'
        assert serialize_recording(transcript.recording)['transcript']['callsign_mentions'][0]['callsign'] == 'KM7GHS'
    runtime.refresh_transcript(runtime.jobs()[0].id)
    assert main.recordings(q='KM7GHS')['items'][0]['transcript']['display_text'] == WORDS.replace(SELECTED, 'KM7GHS')


def test_repeated_phrase_only_changes_selected_occurrence(context):
    runtime, sessions = context
    text = '👋 Kilo Mike Seven and Kilo Mike Seven checked in.'
    runtime.retry(runtime.jobs()[0].id)
    runtime.process_pending(lambda _: decoded(text))
    with sessions() as session:
        edit(session, text, 'Kilo Mike Seven', occurrence=text.index(' and '))
        session.commit()
        assert session.scalar(select(Transcript)).display_text == '👋 Kilo Mike Seven and KM7GHS checked in.'


def test_correction_survives_restart_and_identical_retranscription_without_duplicate_mentions(context):
    runtime, sessions = context
    with sessions() as session:
        mention_id = edit(session).id
        session.commit()
    for _ in range(2):
        runtime = ArchiveRuntime(runtime.roots, session_factory=sessions)
        assert runtime.results[runtime.jobs()[0].id].display_text == WORDS.replace(SELECTED, 'KM7GHS')
        runtime.retry(runtime.jobs()[0].id)
        runtime.process_pending(lambda _: decoded())
        with sessions() as session:
            mentions = session.scalars(select(CallsignMention)).all()
            assert len(mentions) == 1
            assert mentions[0].id == mention_id and mentions[0].is_current
            assert session.scalar(select(Transcript)).display_text == WORDS.replace(SELECTED, 'KM7GHS')


def test_unmatched_retranscription_retains_correction_history_without_editing_unrelated_words(context):
    runtime, sessions = context
    with sessions() as session:
        edit(session)
        session.commit()
    runtime.retry(runtime.jobs()[0].id)
    replacement = 'A different recording has changed words and must receive its own careful review.'
    runtime.process_pending(lambda _: decoded(replacement))
    with sessions() as session:
        transcript = session.scalar(select(Transcript))
        assert transcript.display_text == replacement
        assert len(json.loads(transcript.text_corrections_json)) == 1
        assert session.scalar(select(CallsignMention)).is_current is False


def test_stale_selection_and_busy_recording_are_rejected(context):
    runtime, sessions = context
    with sessions() as session:
        edit(session)
        session.commit()
    with sessions() as session, pytest.raises(CorrectionConflict, match='changed'):
        edit(session)
    runtime.retry(runtime.jobs()[0].id)
    with sessions() as session, pytest.raises(CorrectionConflict, match='finish'):
        edit(session, WORDS.replace(SELECTED, 'KM7GHS'), 'KM7GHS')


@pytest.mark.parametrize('value', ['<script>', 'words', 'KM7GHS/another', 'KM7GHS<script>'])
def test_invalid_callsign_rolls_back(context, value):
    _, sessions = context
    with sessions() as session, pytest.raises(ValueError):
        edit(session, value=value)
    with sessions() as session:
        assert session.scalar(select(Transcript)).display_text == WORDS
        assert session.scalar(select(CallsignMention)) is None


@pytest.mark.parametrize('role,csrf,expected', [('viewer', 'csrf', 403), ('operator', '', 403), ('operator', 'csrf', 200)])
def test_ui_correction_requires_operator_csrf_and_updates_dashboard(context, monkeypatch, role, csrf, expected):
    runtime, sessions = context
    monkeypatch.setattr(settings, 'deployment_mode', 'internet')
    monkeypatch.setattr(settings, 'auth_mode', 'oidc')
    monkeypatch.setattr(settings, 'public_base_url', 'https://testserver')
    now = datetime.now(UTC)
    with sessions() as session:
        session.add(AuthSession(token_hash=auth.token_digest('correction-session'), subject=role,
            identity=role, role=role, csrf_token='csrf', created_at=now, last_seen_at=now,
            expires_at=now + timedelta(hours=1)))
        session.commit()
    client = TestClient(main.app, base_url='https://testserver')
    client.cookies.set(settings.session_cookie_name, 'correction-session')
    start = WORDS.index(SELECTED)
    response = client.post(f'/ui/ingestion/jobs/{runtime.jobs()[0].id}/callsign-correction',
        headers={'X-CSRF-Token': csrf, 'Origin': 'https://testserver'},
        json={'expected_text': WORDS, 'start': start, 'end': start + len(SELECTED), 'callsign': 'km7ghs'})
    assert response.status_code == expected, response.text
    if expected == 200:
        assert main.recordings()['items'][0]['transcript']['display_text'] == WORDS.replace(SELECTED, 'KM7GHS')


def test_editing_a_detected_callsign_reuses_reviewed_mention(context):
    from asl_transcriber.callsign_service import review_mention
    from asl_transcriber.transcription.base import TranscriptCallsignMention
    runtime, sessions = context
    original = 'Hello KM7GHS, this is the radio check.'
    runtime.retry(runtime.jobs()[0].id)
    value = decoded(original)
    value.callsign_mentions = [TranscriptCallsignMention('KM7GHS', 1, 7, raw_observed_value='K M 7 G H S')]
    runtime.process_pending(lambda _: value)
    with sessions() as session:
        before = session.scalar(select(CallsignMention))
        identifier = before.id
        review_mention(session, identifier, action='correct', corrected_callsign='K2ABC', reviewer_identity='operator')
        session.commit()
        assert session.scalar(select(Transcript)).display_text == original.replace('KM7GHS', 'K2ABC')
        assert session.scalar(select(Transcript)).raw_text == original
        mentions = session.scalars(select(CallsignMention)).all()
        assert len(mentions) == 1
        assert mentions[0].id == identifier
        assert mentions[0].raw_observed_value == 'K M 7 G H S'


def test_redecoding_corrected_text_does_not_duplicate_or_undo_human_review(context):
    from asl_transcriber.callsign_service import review_mention
    from asl_transcriber.transcription.base import TranscriptCallsignMention
    runtime, sessions = context
    with sessions() as session:
        mention = edit(session)
        identifier = mention.id
        review_mention(session, identifier, action='reject', corrected_callsign=None, reviewer_identity='reviewer')
        session.commit()
    runtime.retry(runtime.jobs()[0].id)
    value = decoded(WORDS.replace(SELECTED, 'KM7GHS'))
    value.callsign_mentions = [TranscriptCallsignMention('KM7GHS', 1, 7)]
    runtime.process_pending(lambda _: value)
    with sessions() as session:
        mentions = session.scalars(select(CallsignMention)).all()
        assert len(mentions) == 1
        assert mentions[0].id == identifier
        assert mentions[0].review_status == 'rejected'
        assert mentions[0].reviewer_identity == 'reviewer'


def test_consecutive_corrections_replay_without_resetting_latest_callsign(context):
    runtime, sessions = context
    with sessions() as session:
        first = edit(session)
        identifier = first.id
        session.commit()
    with sessions() as session:
        edit(session, WORDS.replace(SELECTED, 'KM7GHS'), 'KM7GHS', 'K2ABC')
        session.commit()
    runtime.retry(runtime.jobs()[0].id)
    runtime.process_pending(lambda _: decoded())
    with sessions() as session:
        assert session.scalar(select(Transcript)).display_text == WORDS.replace(SELECTED, 'K2ABC')
        mentions = session.scalars(select(CallsignMention)).all()
        assert len(mentions) == 1
        assert mentions[0].id == identifier
        assert mentions[0].canonical_callsign == 'K2ABC'


def test_new_correction_does_not_reactivate_an_unmatched_older_revision(context):
    runtime, sessions = context
    with sessions() as session:
        old_id = edit(session).id
        session.commit()
    newer = 'Different words now identify Kilo Two Alpha Bravo Charlie as the other station.'
    runtime.retry(runtime.jobs()[0].id)
    runtime.process_pending(lambda _: decoded(newer))
    with sessions() as session:
        new_id = edit(session, newer, 'Kilo Two Alpha Bravo Charlie', 'K2ABC').id
        session.commit()
    runtime.retry(runtime.jobs()[0].id)
    runtime.process_pending(lambda _: decoded(newer.replace('Kilo Two Alpha Bravo Charlie', 'K2ABC')))
    with sessions() as session:
        assert session.get(CallsignMention, old_id).is_current is False
        assert session.get(CallsignMention, new_id).is_current is True
        edits = json.loads(session.scalar(select(Transcript)).text_corrections_json)
        assert [edit['applied'] for edit in edits] == [False, True]


def test_repeated_detected_callsign_maps_to_selected_segment_occurrence(context):
    from asl_transcriber.transcription.base import TranscriptCallsignMention
    runtime, sessions = context
    phrase = 'KM7GHS and KM7GHS.'
    words = f'{phrase} {phrase}'
    value = TranscriptResult(raw_text=words, display_text=words,
        segments=[TranscriptSegment(0, 4, phrase, ordinal=0), TranscriptSegment(5, 9, phrase, ordinal=1)],
        callsign_mentions=[TranscriptCallsignMention('KM7GHS', start, start + 0.5) for start in (1, 2, 6, 7)])
    runtime.retry(runtime.jobs()[0].id)
    runtime.process_pending(lambda _: value)
    with sessions() as session:
        edit(session, words, 'KM7GHS', 'K2ABC', occurrence=len(phrase))
        session.commit()
        corrected = session.scalar(select(CallsignMention).where(CallsignMention.canonical_callsign == 'K2ABC'))
        assert corrected.start_offset == 6
        assert session.scalar(select(Transcript)).display_text == f'{phrase} K2ABC and KM7GHS.'
