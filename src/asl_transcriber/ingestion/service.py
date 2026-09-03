from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from time import monotonic

from asl_transcriber.ingestion.jobs import IngestionJob, JobState, JobStore
from asl_transcriber.ingestion.scanner import ArchiveScanner


class ArchiveIngestionService:
    def __init__(
        self,
        root: str | Path,
        job_store: JobStore | None = None,
        require_stable: bool = False,
        stable_seconds: float = 0.0,
        retention_days: int = 0,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.root = Path(root)
        self.job_store = job_store or JobStore()
        self.require_stable = require_stable
        self.stable_seconds = stable_seconds
        self.retention_days = retention_days
        self.clock = clock
        self._seen_paths: set[str] = set()
        self._snapshots: dict[str, tuple[tuple[int, int], float]] = {}

    def _hash_file(self, path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def scan_once(self, *, publish: bool = True) -> list[IngestionJob]:
        discovered = ArchiveScanner(self.root, retention_days=self.retention_days).discover()
        jobs: list[IngestionJob] = []
        now = self.clock()

        for entry in discovered:
            rel_path = entry.source_path
            snapshot = (entry.size_bytes, entry.modified_ns)
            previous = self._snapshots.get(rel_path)
            if previous is None or previous[0] != snapshot:
                self._snapshots[rel_path] = (snapshot, now)
                if self.require_stable:
                    continue
            elif self.require_stable and now - previous[1] < self.stable_seconds:
                continue
            if rel_path in self._seen_paths:
                continue

            job = IngestionJob(
                source_path=rel_path,
                archive_root=str(self.root.resolve()),
                status=JobState.PENDING,
            )
            if publish:
                self.job_store.add(job)
                self._seen_paths.add(rel_path)
            jobs.append(job)

        return jobs

    def publish(self, job: IngestionJob) -> None:
        """Expose a discovered job after its durable state has committed."""
        self.job_store.add(job)
        self._seen_paths.add(job.source_path)

    def waiting_paths(self) -> list[str]:
        return sorted(path for path in self._snapshots if path not in self._seen_paths)
