from __future__ import annotations

import hashlib
import time

from asl_transcriber.ingestion.jobs import IngestionJob, JobState, JobStore
from asl_transcriber.ingestion.scanner import ArchiveScanner
from asl_transcriber.ingestion.stabilizer import FileStabilizer


def test_archive_scanner_discovers_supported_files_only(tmp_path) -> None:
    root = tmp_path / "archive"
    node_dir = root / "123"
    node_dir.mkdir(parents=True)

    valid = node_dir / "20240101010101.wav"
    valid.write_bytes(b"hello")
    (node_dir / ".hidden.wav").write_bytes(b"ignore")
    (node_dir / "partial.tmp").write_bytes(b"ignore")
    (node_dir / "notes.txt").write_bytes(b"ignore")

    discovered = ArchiveScanner(root).discover()

    assert len(discovered) == 1
    assert discovered[0].source_path == "123/20240101010101.wav"
    assert discovered[0].size_bytes == 5


def test_file_stabilizer_waits_for_file_to_stabilize(tmp_path) -> None:
    target = tmp_path / "stable.wav"
    target.write_bytes(b"abc")

    stabilizer = FileStabilizer(stable_seconds=0.15, poll_interval=0.05)
    start = time.monotonic()
    ready = stabilizer.wait_for_stable(target)
    elapsed = time.monotonic() - start

    assert ready is True
    assert elapsed >= 0.15


def test_job_store_tracks_retry_and_dead_letter_state() -> None:
    store = JobStore()
    job = IngestionJob(source_path="123/recording.wav")
    store.add(job)

    assert job.status == JobState.PENDING
    store.mark_processing(job.id)
    assert store.get(job.id).status == JobState.PROCESSING

    store.mark_failed(job.id, "temporary failure")
    assert store.get(job.id).status == JobState.FAILED
    assert store.get(job.id).attempt_count == 1

    store.mark_dead_letter(job.id)
    assert store.get(job.id).status == JobState.DEAD_LETTER


def test_sha256_hash_matches_bytes() -> None:
    payload = b"radio traffic example"
    digest = hashlib.sha256(payload).hexdigest()
    assert FileStabilizer.hash_file_bytes(payload) == digest
