from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from queue import Queue

from sqlalchemy import delete, func, inspect, select, text
from sqlalchemy.engine import Engine

from asl_transcriber.database import SessionLocal
from asl_transcriber.ingestion.activity import ActivityLogEvent, ActivityLogParser
from asl_transcriber.ingestion.jobs import IngestionJob, JobState, JobStore
from asl_transcriber.ingestion.service import ArchiveIngestionService
from asl_transcriber.models import Base as ModelBase
from asl_transcriber.models import IngestionJob as DbIngestionJob
from asl_transcriber.models import Transcript
from asl_transcriber.transcription.base import TranscriptResult
from asl_transcriber.workers.processor import ProcessingResult, ProcessingWorker


class ArchiveRuntime:
    """Coordinates read-only archive discovery and its in-memory job queue."""

    def __init__(
        self,
        roots: Sequence[str | Path],
        session_factory: Callable = SessionLocal,
        stable_seconds: float = 0.0,
        retention_days: int = 0,
    ) -> None:
        self.roots = [Path(root) for root in roots]
        self.session_factory = session_factory
        self.stable_seconds = stable_seconds
        self.retention_days = retention_days
        bind = session_factory.kw.get("bind") if hasattr(session_factory, "kw") else None
        if bind is not None:
            ModelBase.metadata.create_all(bind)
            self._ensure_schema(bind)
            self._backfill_archive_roots(bind)
        self.job_store = JobStore()
        self.services = [
            ArchiveIngestionService(
                root,
                self.job_store,
                require_stable=True,
                stable_seconds=stable_seconds,
                retention_days=retention_days,
            )
            for root in self.roots
        ]
        self.results: dict[str, ProcessingResult] = {}
        self.live_results: dict[str, ProcessingResult] = {}
        self._events: Queue[dict[str, object]] = Queue()
        self._restore_state()

    @staticmethod
    def _ensure_schema(bind: Engine) -> None:
        inspector = inspect(bind)
        columns = {column["name"] for column in inspector.get_columns("ingestion_jobs")}
        if "archive_root" not in columns:
            with bind.begin() as connection:  # type: ignore[union-attr]
                connection.execute(
                    text("ALTER TABLE ingestion_jobs ADD COLUMN archive_root VARCHAR(1024)")
                )

    def _backfill_archive_roots(self, bind: Engine) -> None:
        if not self.roots:
            return
        with bind.begin() as connection:
            connection.execute(
                text("UPDATE ingestion_jobs SET archive_root = :root WHERE archive_root IS NULL"),
                {"root": str(self.roots[0].resolve())},
            )

    def scan_once(self) -> list[IngestionJob]:
        jobs: list[IngestionJob] = []
        with self.session_factory() as session:
            persisted = {
                (row.archive_root, row.source_path) for row in session.query(DbIngestionJob).all()
            }
        for service in self.services:
            root_key = str(service.root.resolve())
            service._seen_paths.update(
                source_path for archive_root, source_path in persisted if archive_root == root_key
            )
            new_jobs = service.scan_once()
            jobs.extend(new_jobs)
            with self.session_factory() as session:
                for job in new_jobs:
                    session.add(
                        DbIngestionJob(
                            id=job.id,
                            source_path=job.source_path,
                            archive_root=root_key,
                            status=job.status.value,
                        )
                    )
                session.commit()
            for job in new_jobs:
                self._publish(job)
        return jobs

    def jobs(self) -> list[IngestionJob]:
        return self.job_store.list()

    def database_totals(self) -> dict[str, int]:
        """Return persisted recording and transcript counts for configured archives."""
        archive_roots = [str(root.resolve()) for root in self.roots]
        if not archive_roots:
            return {"recordings": 0, "transcribed": 0}

        recording_count = (
            select(func.count())
            .select_from(DbIngestionJob)
            .where(DbIngestionJob.archive_root.in_(archive_roots))
        )
        transcript_count = (
            select(func.count())
            .select_from(Transcript)
            .join(DbIngestionJob, Transcript.job_id == DbIngestionJob.id)
            .where(DbIngestionJob.archive_root.in_(archive_roots))
        )
        with self.session_factory() as session:
            return {
                "recordings": int(session.scalar(recording_count) or 0),
                "transcribed": int(session.scalar(transcript_count) or 0),
            }

    def waiting_sources(self) -> list[str]:
        return sorted(path for service in self.services for path in service.waiting_paths())

    def process_pending(
        self,
        transcribe: Callable[[str], TranscriptResult],
        *,
        limit: int | None = None,
    ) -> list[ProcessingResult]:
        def process(source_path: str) -> ProcessingResult:
            source = self._resolve_source(source_path)
            transcript = transcribe(str(source))
            return ProcessingResult(
                source_path=source_path,
                status="completed",
                raw_text=transcript.raw_text,
                display_text=transcript.display_text,
                language=transcript.language,
                confidence=transcript.confidence,
            )

        worker = ProcessingWorker(job_store=self.job_store, process_func=process)
        results: list[ProcessingResult] = []
        pending_jobs = [job for job in self.job_store.list() if job.status == JobState.PENDING]
        if limit is not None:
            pending_jobs = pending_jobs[: max(0, limit)]
        for job in pending_jobs:
            job.status = JobState.PROCESSING
            job.touch()
            self._publish(job)
            result = worker.process_job(job.id)
            self.results[job.id] = result
            self._persist_result(job, result)
            self._publish(job, result)
            results.append(result)
        return results

    def subscribe(self) -> Queue[dict[str, object]]:
        return self._events

    def set_live_result(
        self, source_path: str, transcript: TranscriptResult, *, display_text: str | None = None
    ) -> None:
        result = ProcessingResult(
            source_path=source_path,
            status="live",
            raw_text=transcript.raw_text,
            display_text=display_text if display_text is not None else transcript.display_text,
            language=transcript.language,
            confidence=transcript.confidence,
        )
        self.live_results[source_path] = result
        self._events.put(
            {
                "id": None,
                "source_path": source_path,
                "status": "live",
                "provisional": True,
                "transcript": result.display_text,
            }
        )

    def clear_live_result(self, source_path: str) -> None:
        self.live_results.pop(source_path, None)

    @staticmethod
    def _event_payload(
        job: IngestionJob, result: ProcessingResult | None = None
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "id": job.id,
            "source_path": job.source_path,
            "status": job.status.value,
        }
        if result is not None:
            payload["transcript"] = result.display_text
        return payload

    def _publish(self, job: IngestionJob, result: ProcessingResult | None = None) -> None:
        self._events.put(self._event_payload(job, result))

    def _resolve_source(self, source_path: str) -> Path:
        for root in self.roots:
            candidate = (root / source_path).resolve()
            if candidate.is_relative_to(root.resolve()) and candidate.is_file():
                if self.retention_days > 0:
                    cutoff = datetime.now(UTC) - timedelta(days=self.retention_days)
                    if candidate.stat().st_mtime < cutoff.timestamp():
                        continue
                return candidate
        raise FileNotFoundError(f"Archive recording not found: {source_path}")

    def purge_expired(self) -> int:
        """Remove derived records that are older than the configured visibility horizon."""
        if self.retention_days <= 0:
            return 0
        cutoff = datetime.now(UTC) - timedelta(days=self.retention_days)
        expired_ids: list[str] = []
        with self.session_factory() as session:
            for stored in session.scalars(select(DbIngestionJob)).all():
                created_at = stored.created_at
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=UTC)
                source_is_old = created_at < cutoff
                if stored.archive_root:
                    candidate = (Path(stored.archive_root) / stored.source_path).resolve()
                    if (
                        candidate.is_relative_to(Path(stored.archive_root).resolve())
                        and candidate.is_file()
                    ):
                        source_is_old = candidate.stat().st_mtime < cutoff.timestamp()
                if source_is_old:
                    expired_ids.append(stored.id)
            if expired_ids:
                session.execute(delete(Transcript).where(Transcript.job_id.in_(expired_ids)))
                session.execute(delete(DbIngestionJob).where(DbIngestionJob.id.in_(expired_ids)))
                session.commit()
        for job_id in expired_ids:
            self.job_store.remove(job_id)
            self.results.pop(job_id, None)
        return len(expired_ids)

    def _source_exists(self, source_path: str) -> bool:
        try:
            self._resolve_source(source_path)
        except FileNotFoundError:
            return False
        return True

    def _restore_state(self) -> None:
        with self.session_factory() as session:
            for stored in session.query(DbIngestionJob).all():
                if stored.archive_root not in {str(root.resolve()) for root in self.roots}:
                    continue
                if not self._source_exists(stored.source_path):
                    continue
                job = IngestionJob(source_path=stored.source_path, id=stored.id)
                status = JobState(stored.status)
                if status == JobState.COMPLETED and stored.transcript is not None:
                    source = self._resolve_source(stored.source_path)
                    processed_at = stored.transcript.updated_at or stored.transcript.created_at
                    if processed_at.tzinfo is None:
                        processed_at = processed_at.replace(tzinfo=UTC)
                    if source.stat().st_mtime > processed_at.timestamp():
                        status = JobState.PENDING
                        stored.status = status.value
                job.status = status
                job.attempt_count = stored.attempt_count
                job.last_error = stored.last_error
                self.job_store.add(job)
                if stored.transcript is not None and status == JobState.COMPLETED:
                    self.results[job.id] = ProcessingResult(
                        source_path=job.source_path,
                        status="completed",
                        raw_text=stored.transcript.raw_text,
                        display_text=stored.transcript.display_text,
                        language=stored.transcript.language,
                        confidence=stored.transcript.confidence,
                    )
            session.commit()

    def _persist_result(self, job: IngestionJob, result: ProcessingResult) -> None:
        with self.session_factory() as session:
            stored_job = session.get(DbIngestionJob, job.id)
            if stored_job is None:
                stored_job = DbIngestionJob(id=job.id, source_path=job.source_path)
                session.add(stored_job)
            stored_job.status = job.status.value
            stored_job.attempt_count = job.attempt_count
            stored_job.last_error = job.last_error
            transcript = stored_job.transcript
            if transcript is None:
                transcript = Transcript(
                    job_id=job.id,
                )
                session.add(transcript)
            transcript.raw_text = result.raw_text
            transcript.display_text = result.display_text
            transcript.language = result.language
            transcript.confidence = result.confidence
            session.commit()

    def activity_events(self) -> list[ActivityLogEvent]:
        events: list[ActivityLogEvent] = []
        for root in self.roots:
            log_paths = list(root.rglob("activity.log")) + list(root.rglob("????????.txt"))
            for path in sorted(log_paths) if root.exists() else []:
                events.extend(ActivityLogParser(path).parse())
        return sorted(events, key=lambda event: event.timestamp)
