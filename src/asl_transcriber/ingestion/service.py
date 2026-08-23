from __future__ import annotations

import hashlib
from pathlib import Path

from asl_transcriber.ingestion.jobs import IngestionJob, JobState, JobStore
from asl_transcriber.ingestion.scanner import ArchiveScanner


class ArchiveIngestionService:
    def __init__(
        self,
        root: str | Path,
        job_store: JobStore | None = None,
        require_stable: bool = False,
    ) -> None:
        self.root = Path(root)
        self.job_store = job_store or JobStore()
        self.require_stable = require_stable
        self._seen_paths: set[str] = set()
        self._snapshots: dict[str, tuple[int, int]] = {}

    def _hash_file(self, path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def scan_once(self) -> list[IngestionJob]:
        discovered = ArchiveScanner(self.root).discover()
        jobs: list[IngestionJob] = []

        for entry in discovered:
            rel_path = entry.source_path
            snapshot = (entry.size_bytes, entry.modified_ns)
            previous_snapshot = self._snapshots.get(rel_path)
            self._snapshots[rel_path] = snapshot
            if self.require_stable and previous_snapshot != snapshot:
                continue
            if rel_path in self._seen_paths:
                continue

            job = IngestionJob(source_path=rel_path, status=JobState.PENDING)
            self.job_store.add(job)
            self._seen_paths.add(rel_path)
            jobs.append(job)

        return jobs

    def waiting_paths(self) -> list[str]:
        return sorted(path for path in self._snapshots if path not in self._seen_paths)
