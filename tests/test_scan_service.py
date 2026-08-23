from __future__ import annotations

from pathlib import Path

from asl_transcriber.ingestion.jobs import JobStore
from asl_transcriber.ingestion.service import ArchiveIngestionService


def create_wav(path: Path, payload: bytes = b"radio traffic") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def test_scan_once_creates_jobs_for_new_files_only(tmp_path) -> None:
    root = tmp_path / "archive"
    node_dir = root / "123"
    node_dir.mkdir(parents=True)
    target = node_dir / "20240101010101.wav"
    create_wav(target)

    service = ArchiveIngestionService(root=root, job_store=JobStore())
    jobs = service.scan_once()

    assert len(jobs) == 1
    assert jobs[0].source_path == "123/20240101010101.wav"

    duplicate_jobs = service.scan_once()
    assert duplicate_jobs == []


def test_scan_once_keeps_distinct_paths_even_with_same_content(tmp_path) -> None:
    root = tmp_path / "archive"
    node_dir = root / "123"
    node_dir.mkdir(parents=True)

    create_wav(node_dir / "a.wav", b"same-payload")
    create_wav(node_dir / "b.wav", b"same-payload")

    service = ArchiveIngestionService(root=root, job_store=JobStore())
    jobs = service.scan_once()

    assert len(jobs) == 2
    assert {job.source_path for job in jobs} == {"123/a.wav", "123/b.wav"}
