from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from asl_transcriber.ingestion.jobs import IngestionJob, JobState, JobStore


@dataclass
class ProcessingResult:
    source_path: str
    status: str
    raw_text: str
    display_text: str
    language: str | None = None
    confidence: float | None = None


class ProcessingWorker:
    def __init__(
        self,
        job_store: JobStore | None = None,
        process_func: Callable[[str], ProcessingResult] | None = None,
    ) -> None:
        self.job_store = job_store or JobStore()
        self.process_func = process_func or self._default_process

    def _default_process(self, path: str) -> ProcessingResult:
        return ProcessingResult(
            source_path=path,
            status="completed",
            raw_text="",
            display_text="",
            language=None,
            confidence=None,
        )

    def process_job(self, job_id: str) -> ProcessingResult:
        job = self.job_store.get(job_id)
        job.status = JobState.PROCESSING
        job.touch()

        try:
            result = self.process_func(job.source_path)
        except Exception as error:
            self.job_store.mark_failed(job_id, str(error))
            raise
        job.status = JobState.COMPLETED
        job.touch()
        self.job_store.add(job)
        return result

    def queue_ready_jobs(self) -> list[IngestionJob]:
        jobs = [job for job in self.job_store.list() if job.status == JobState.PENDING]
        for job in jobs:
            self.process_job(job.id)
        return jobs
