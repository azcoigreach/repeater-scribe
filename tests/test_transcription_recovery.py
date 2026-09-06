from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from asl_transcriber import auth, main
from asl_transcriber.config import settings
from asl_transcriber.database import Base
from asl_transcriber.ingestion.jobs import JobState
from asl_transcriber.models import AuthSession, IngestionJob, Recording, Transcript
from asl_transcriber.runtime import ArchiveRuntime
from asl_transcriber.transcription.base import TranscriptResult
from asl_transcriber.transcription.live import LiveTranscriptionService

PREVIEW = 'This is a complete transmission with several sentences and all of the original words.'


def result(text=PREVIEW):
    return TranscriptResult(raw_text=text, display_text=text, language='en')


@pytest.fixture()
def recovery_runtime(tmp_path, monkeypatch):
    archive = tmp_path / 'archive'
    archive.mkdir()
    (archive / 'call.wav').write_bytes(b'audio')
    engine = create_engine(f"sqlite:///{tmp_path / 'state.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    runtime = ArchiveRuntime([archive], session_factory=sessions)
    monkeypatch.setattr(main, 'current_runtime', lambda: runtime)
    monkeypatch.setattr(auth, 'SessionLocal', sessions)
    runtime.scan_once()
    runtime.set_live_result('call.wav', result())
    runtime.scan_once()
    yield runtime
    engine.dispose()


@pytest.mark.parametrize('final_text', ['', 'Only words'])
def test_collapsed_final_preserves_preview_and_persists_failure(recovery_runtime, final_text):
    runtime = recovery_runtime
    job = runtime.jobs()[0]
    subscriber = runtime.subscribe()
    # The live poller can run after the recording leaves its waiting list.
    service = LiveTranscriptionService(snapshotter=None, transcribe=result)  # type: ignore[arg-type]
    service._last_sizes['call.wav'] = 5
    assert service.process_once(runtime) == 0

    def final_pass(_):
        item = main.recordings(q='complete transmission')['items'][0]
        assert item['status'] == 'processing'
        assert item['transcript']['display_text'] == PREVIEW
        assert item['transcript']['provisional'] is True
        return result(final_text)

    assert runtime.process_pending(final_pass, recovery_transcribe=final_pass) == []
    assert job.status == JobState.FAILED
    assert runtime.live_results['call.wav'].display_text == PREVIEW
    assert main.recordings()['items'][0]['last_error'] == job.last_error
    with runtime.session_factory() as session:
        assert session.get(IngestionJob, job.id).status == 'failed'
        assert session.get(Recording, job.id).status == 'failed'
        assert session.query(Transcript).count() == 0
    assert [subscriber.get_nowait()['status'] for _ in range(2)] == ['processing', 'failed']


def test_recovery_pass_replaces_preview_only_after_success(recovery_runtime):
    runtime = recovery_runtime
    paths = []

    def recover(path):
        paths.append(path)
        assert runtime.live_results['call.wav'].display_text == PREVIEW
        return result(PREVIEW + ' Final words.')

    completed = runtime.process_pending(lambda _: result('Only words'), recovery_transcribe=recover)
    assert paths == [str(runtime.roots[0] / 'call.wav')]
    assert completed[0].display_text.endswith('Final words.')
    assert runtime.live_results == {}
    runtime.set_live_result('call.wav', result('A late live update'))
    assert runtime.live_results == {}
    with runtime.session_factory() as session:
        saved = session.query(Transcript).one()
        assert saved.display_text == completed[0].display_text
        assert session.get(Recording, runtime.jobs()[0].id).current_transcript_id == saved.id


def test_failed_retry_keeps_saved_text_across_restart_and_later_succeeds(recovery_runtime):
    runtime = recovery_runtime
    runtime.process_pending(lambda _: result())
    job = runtime.jobs()[0]
    runtime.retry(job.id)

    def fail(_):
        raise RuntimeError('Decoder unavailable')

    runtime.process_pending(fail)
    assert job.status == JobState.FAILED
    restored = ArchiveRuntime(runtime.roots, session_factory=runtime.session_factory)
    assert restored.results[job.id].display_text == PREVIEW
    assert restored.jobs()[0].last_error == 'Decoder unavailable'
    restored.retry(job.id)
    restored.process_pending(lambda _: result(PREVIEW + ' Recovered.'))
    with restored.session_factory() as session:
        assert session.query(Transcript).count() == 1
        assert session.query(Transcript).one().display_text.endswith('Recovered.')
        assert session.get(IngestionJob, job.id).last_error is None


def test_normal_revision_and_genuinely_empty_recording_are_accepted(recovery_runtime):
    runtime = recovery_runtime
    shorter = 'A complete transmission with several sentences and original words.'
    assert runtime.process_pending(lambda _: result(shorter))[0].display_text == shorter
    second = runtime.roots[0] / 'silent.wav'
    second.write_bytes(b'audio')
    runtime.scan_once()
    runtime.scan_once()
    assert runtime.process_pending(lambda _: result(''))[0].display_text == ''


def test_parallel_processors_cannot_decode_the_same_job_twice(recovery_runtime):
    runtime = recovery_runtime
    started, finish = Event(), Event()

    def decode(_):
        started.set()
        assert finish.wait(5)
        return result()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(runtime.process_pending, decode)
        try:
            assert started.wait(5)
            assert runtime.process_pending(decode) == []
            with pytest.raises(ValueError, match='already queued'):
                runtime.retry(runtime.jobs()[0].id)
        finally:
            finish.set()
        assert len(first.result(timeout=5)) == 1


def test_retry_endpoint_runs_recovery_even_when_auto_processing_is_off(recovery_runtime, monkeypatch):
    runtime = recovery_runtime
    runtime.process_pending(lambda _: result())
    calls = []

    class Engine:
        def transcribe(self, path, **options):
            calls.append((path, options))
            return result(PREVIEW + ' Retried.')

    monkeypatch.setattr(main, 'transcription_engine', Engine())
    monkeypatch.setattr(settings, 'auto_process', False)
    job = runtime.jobs()[0]
    response = TestClient(main.app).post(f'/ui/ingestion/jobs/{job.id}/retry')
    assert response.status_code == 202
    assert calls == [(str(runtime.roots[0] / 'call.wav'), {
        'vad_filter': False, 'condition_on_previous_text': False, 'use_hotwords': False,
    })]
    assert runtime.results[job.id].display_text.endswith('Retried.')


def test_retry_endpoint_rejects_unknown_busy_and_missing_audio(recovery_runtime):
    runtime = recovery_runtime
    client = TestClient(main.app)
    job = runtime.jobs()[0]
    assert client.post('/ui/ingestion/jobs/unknown/retry').status_code == 404
    assert client.post(f'/ui/ingestion/jobs/{job.id}/retry').status_code == 409
    runtime.process_pending(lambda _: result())
    (runtime.roots[0] / 'call.wav').unlink()
    assert client.post(f'/ui/ingestion/jobs/{job.id}/retry').status_code == 409
    assert job.status == JobState.COMPLETED
    assert runtime.results[job.id].display_text == PREVIEW


@pytest.mark.parametrize('role,csrf,expected', [
    ('viewer', 'csrf', 403), ('operator', '', 403), ('operator', 'csrf', 202),
])
def test_retry_requires_operator_and_csrf(recovery_runtime, monkeypatch, role, csrf, expected):
    runtime = recovery_runtime
    runtime.process_pending(lambda _: result())
    monkeypatch.setattr(settings, 'deployment_mode', 'internet')
    monkeypatch.setattr(settings, 'auth_mode', 'oidc')
    monkeypatch.setattr(settings, 'public_base_url', 'https://testserver')
    monkeypatch.setattr(main, 'process_transcription_jobs', lambda **_: None)
    now = datetime.now(UTC)
    with runtime.session_factory() as session:
        session.add(AuthSession(
            token_hash=auth.token_digest('retry-session'), subject=role, identity=role, role=role,
            csrf_token='csrf', created_at=now, last_seen_at=now,
            expires_at=now + timedelta(hours=1),
        ))
        session.commit()
    client = TestClient(main.app, base_url='https://testserver')
    client.cookies.set(settings.session_cookie_name, 'retry-session')
    response = client.post(
        f'/ui/ingestion/jobs/{runtime.jobs()[0].id}/retry',
        headers={'X-CSRF-Token': csrf, 'Origin': 'https://testserver'},
    )
    assert response.status_code == expected
