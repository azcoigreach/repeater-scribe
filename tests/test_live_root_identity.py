from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from asl_transcriber import main
from asl_transcriber.archive import archive_source_id
from asl_transcriber.database import Base
from asl_transcriber.runtime import ArchiveRuntime
from asl_transcriber.session_api import source_id
from asl_transcriber.transcription.base import TranscriptCallsignMention, TranscriptResult
from asl_transcriber.transcription.live import LiveTranscriptionService

PATH = "2026091200000000.wav"


def transcript(text, callsign="KM7GHS", end=1):
    return TranscriptResult(
        raw_text=text, display_text=text,
        callsign_mentions=[TranscriptCallsignMention(callsign, 0, end)],
    )


@pytest.fixture
def runtime(tmp_path):
    roots = [tmp_path / "one", tmp_path / "two"]
    for root in roots:
        root.mkdir()
        (root / PATH).write_bytes(b"audio" * 1000)
    engine = create_engine(f"sqlite:///{tmp_path / 'state.db'}")
    Base.metadata.create_all(engine)
    runtime = ArchiveRuntime(roots, session_factory=sessionmaker(bind=engine), stable_seconds=10)
    yield runtime
    engine.dispose()


def test_explicit_root_miss_never_uses_other_root_or_unscoped_preview(runtime):
    first, second = map(str, runtime.roots)
    runtime.set_live_result(PATH, transcript("Root one"), archive_root=first)
    assert runtime.live_result_for(PATH, archive_root=second) is None
    assert runtime.live_result_for(PATH).display_text == "Root one"  # Unambiguous legacy lookup.
    runtime.clear_live_result(PATH, archive_root=second)
    assert runtime.live_result_for(PATH, archive_root=first).display_text == "Root one"

    runtime.set_live_result(PATH, transcript("Legacy unknown root"))
    assert runtime.live_result_for(PATH, archive_root=second) is None
    runtime.clear_live_result(PATH, archive_root=first)
    assert runtime.live_result_for(PATH, archive_root=first) is None
    assert runtime.live_result_for(PATH).display_text == "Legacy unknown root"
    runtime.clear_live_result(PATH)
    assert runtime.live_result_for(PATH) is None

    for root in (first, second):
        runtime.set_live_result(PATH, transcript(root), archive_root=root)
    assert runtime.live_result_for(PATH) is None  # Ambiguous path-only lookup.
    runtime.clear_live_result(PATH)
    assert len(runtime.live_results) == 2
    runtime.clear_live_result(PATH, archive_root=first)
    assert runtime.live_result_for(PATH, archive_root=first) is None
    assert runtime.live_result_for(PATH, archive_root=second).display_text == second
    runtime.clear_live_result(PATH)
    assert runtime.live_results == {}


def test_live_service_isolates_two_roots_through_growth_ingestion_and_cleanup(runtime, tmp_path):
    first, second = runtime.roots
    now = [0.0]
    for discovery in runtime.services:
        discovery.clock = lambda: now[0]
    runtime.scan_once()
    decoded = []

    class Snapshotter:
        def snapshot(self, source: Path) -> Path:
            decoded.append(source)
            target = tmp_path / "snapshot.wav"
            target.write_text(f"{source.parent.name} transmission size {source.stat().st_size}")
            return target

    service = LiveTranscriptionService(
        snapshotter=Snapshotter(),  # type: ignore[arg-type]
        transcribe=lambda path: transcript(
            Path(path).read_text(), "K1AAA" if Path(path).read_text().startswith("one") else "K2BBB"
        ),
    )
    assert service.process_once(runtime) == 2
    assert set(decoded) == {first / PATH, second / PATH}
    preview = lambda root: runtime.live_result_for(PATH, archive_root=str(root))
    assert preview(first).display_text.startswith("one transmission")
    second_preview = preview(second)
    assert second_preview.display_text.startswith("two transmission")
    assert service.process_once(runtime) == 0

    now[0] = 1
    (first / PATH).write_bytes(b"audio" * 1200)
    runtime.scan_once()
    assert service.process_once(runtime) == 1
    assert decoded[-1] == first / PATH
    assert "two" not in preview(first).display_text
    assert [mention.callsign for mention in preview(first).callsign_mentions] == ["K1AAA"]
    assert preview(second) is second_preview

    # Only root one stabilizes; root two continues growing under the same filename.
    now[0] = 11
    (second / PATH).write_bytes(b"audio" * 1400)
    runtime.scan_once()
    assert len(runtime.jobs()) == 1
    job = runtime.jobs()[0]
    assert job.archive_root == str(first)
    first_preview = preview(first)
    assert service.process_once(runtime) == 1
    assert preview(first) is first_preview  # Keep evidence until final processing succeeds.
    assert (str(first), PATH) not in service._texts
    assert [mention.callsign for mention in preview(second).callsign_mentions] == ["K2BBB"]
    assert "one" not in preview(second).display_text
    second_preview = preview(second)
    assert len(runtime.process_pending(lambda _: transcript(first_preview.display_text, "K1AAA"))) == 1
    assert preview(first) is None
    assert preview(second) is second_preview
    assert runtime.results[job.id].display_text == first_preview.display_text


def test_last_heard_preserves_roots_for_counts_extensions_and_selected_source(runtime, monkeypatch):
    first, second = map(str, runtime.roots)
    runtime.set_live_result(PATH, transcript("KM7GHS", end=1), archive_root=first)
    # Exercise the completed-job projection alongside the live tuple projection.
    job = SimpleNamespace(id="second", archive_root=second, source_path=PATH)
    completed = transcript("KM7GHS", end=2)
    completed.callsign_mentions.append(TranscriptCallsignMention("K1ABC", 0, 2))
    runtime.results[job.id] = completed
    runtime.live_result_for(PATH, first).callsign_mentions.append(
        TranscriptCallsignMention("K1AB", 0, 1)
    )
    monkeypatch.setattr(runtime, "jobs", lambda: [job])
    monkeypatch.setattr(main, "current_runtime", lambda: runtime)
    monkeypatch.setattr(main, "current_qrz_client", lambda: None)
    items = {item["callsign"]: item for item in main.last_heard_callsigns()["items"]}
    assert items["KM7GHS"]["recording_count"] == 2
    assert items["KM7GHS"]["observation_count"] == 2
    assert items["KM7GHS"]["source_id"] == archive_source_id(second) == source_id(second)
    assert items["K1AB"]["source_id"] == archive_source_id(first)
    assert "possible_extension" not in items["K1AB"]
    assert "needs_review" not in items["K1AB"]
    assert first not in str(items) and second not in str(items)

    # A completed result from root one must share that root's recording identity.
    other_job = SimpleNamespace(id="first", archive_root=first, source_path=PATH)
    runtime.results[other_job.id] = transcript("KM7GHS", end=3)
    monkeypatch.setattr(runtime, "jobs", lambda: [job, other_job])
    items = {item["callsign"]: item for item in main.last_heard_callsigns()["items"]}
    assert items["KM7GHS"]["recording_count"] == 2
    assert items["KM7GHS"]["source_id"] == archive_source_id(first)
