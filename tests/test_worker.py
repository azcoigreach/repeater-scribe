from __future__ import annotations

from asl_transcriber.ingestion.jobs import IngestionJob, JobState, JobStore
from asl_transcriber.workers.processor import ProcessingResult, ProcessingWorker


def test_processing_worker_marks_success_and_stores_result() -> None:
    job = IngestionJob(source_path="123/sample.wav")
    store = JobStore()
    store.add(job)

    def fake_process(path: str) -> ProcessingResult:
        assert path == "123/sample.wav"
        return ProcessingResult(
            source_path=path,
            status="completed",
            raw_text="hello radio",
            display_text="hello radio",
            language="en",
            confidence=None,
        )

    worker = ProcessingWorker(job_store=store, process_func=fake_process)
    result = worker.process_job(job.id)

    assert result.status == "completed"
    assert result.raw_text == "hello radio"
    assert store.get(job.id).status == JobState.COMPLETED
