from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from uuid import uuid4


class JobState(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    IGNORED = "ignored"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"


@dataclass
class IngestionJob:
    source_path: str
    archive_root: str | None = None
    id: str = field(default_factory=lambda: str(uuid4()))
    status: JobState = JobState.PENDING
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    attempt_count: int = 0
    last_error: str | None = None
    dead_letter: bool = False
    retry_at: datetime | None = None

    def touch(self) -> None:
        self.updated_at = datetime.now(UTC)


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, IngestionJob] = {}

    def add(self, job: IngestionJob) -> IngestionJob:
        self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> IngestionJob:
        return self._jobs[job_id]

    def list(self) -> list[IngestionJob]:
        return list(self._jobs.values())

    def remove(self, job_id: str) -> None:
        self._jobs.pop(job_id, None)

    def mark_processing(self, job_id: str) -> IngestionJob:
        job = self._jobs[job_id]
        job.status = JobState.PROCESSING
        job.touch()
        return job

    def mark_failed(self, job_id: str, error: str) -> IngestionJob:
        job = self._jobs[job_id]
        job.status = JobState.FAILED
        job.last_error = error
        job.attempt_count += 1
        job.touch()
        return job

    def mark_dead_letter(self, job_id: str) -> IngestionJob:
        job = self._jobs[job_id]
        job.status = JobState.DEAD_LETTER
        job.dead_letter = True
        job.touch()
        return job

    def mark_completed(self, job_id: str) -> IngestionJob:
        job = self._jobs[job_id]
        job.status = JobState.COMPLETED
        job.touch()
        return job
