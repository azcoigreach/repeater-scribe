from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from queue import Full, Queue
from threading import RLock
from time import monotonic

from sqlalchemy import func, inspect, select

from asl_transcriber.archive import get_or_create_recording, refresh_audio
from asl_transcriber.database import SessionLocal
from asl_transcriber.ingestion.activity import ActivityLogEvent, ActivityLogParser
from asl_transcriber.ingestion.jobs import IngestionJob, JobState, JobStore
from asl_transcriber.ingestion.service import ArchiveIngestionService
from asl_transcriber.models import IngestionJob as DbIngestionJob
from asl_transcriber.models import Recording, Transcript
from asl_transcriber.transcription.base import TranscriptCallsignMention, TranscriptResult
from asl_transcriber.workers.processor import ProcessingResult, ProcessingWorker


class ArchiveRuntime:
    """Coordinates read-only archive discovery and its in-memory job queue."""

    def __init__(
        self,
        roots: Sequence[str | Path],
        session_factory: Callable = SessionLocal,
        stable_seconds: float = 0.0,
        retention_days: int = 0,
        catalog_refresh_seconds: float = 300.0,
        catalog_refresh_batch_size: int = 100,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.roots = [Path(root) for root in roots]
        self.session_factory = session_factory
        self.stable_seconds = stable_seconds
        self.retention_days = retention_days
        self.catalog_refresh_seconds = catalog_refresh_seconds
        self.catalog_refresh_batch_size = catalog_refresh_batch_size
        self.clock = clock
        self._last_catalog_refresh: float | None = None
        self._catalog_refresh_cursor: str | None = None
        self._scan_lock = RLock()
        bind = session_factory.kw.get("bind") if hasattr(session_factory, "kw") else None
        self._database_ready = bind is None or inspect(bind).has_table("ingestion_jobs")
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
        self._subscribers: set[Queue[dict[str, object]]] = set()
        self._subscriber_lock = RLock()
        self._restore_state()

    def scan_once(self) -> list[IngestionJob]:
        with self._scan_lock:
            return self._scan_once_locked()

    def _scan_once_locked(self) -> list[IngestionJob]:
        jobs: list[IngestionJob] = []
        for service in self.services:
            root_key = str(service.root.resolve())
            entries = service.discover()
            current_paths = {entry.source_path for entry in entries}
            if self._database_ready and current_paths:
                service._seen_paths.update(self._persisted_paths(root_key, current_paths))
            new_jobs = service.scan_once(entries, publish=not self._database_ready)
            if self._database_ready:
                with self.session_factory() as session:
                    for job in new_jobs:
                        recording = get_or_create_recording(
                            session,
                            source_path=job.source_path,
                            archive_root=root_key,
                            recording_id=job.id,
                            status=job.status.value,
                        )
                        session.add(
                            DbIngestionJob(
                                id=job.id,
                                source_path=job.source_path,
                                archive_root=root_key,
                                recording_id=recording.id,
                                status=job.status.value,
                            )
                        )
                    session.commit()
                    for job in new_jobs:
                        service.publish(job)
                    jobs.extend(new_jobs)
            for job in new_jobs:
                self._publish(job)
        if self._database_ready:
            self._refresh_catalog_batch()
        return jobs

    def _persisted_paths(self, archive_root: str, source_paths: set[str]) -> set[str]:
        persisted: set[str] = set()
        path_list = list(source_paths)
        with self.session_factory() as session:
            for offset in range(0, len(path_list), 500):
                statement = select(DbIngestionJob.source_path).where(
                    DbIngestionJob.archive_root == archive_root,
                    DbIngestionJob.source_path.in_(path_list[offset : offset + 500]),
                )
                persisted.update(session.scalars(statement))
        return persisted

    def _refresh_catalog_batch(self) -> None:
        now = self.clock()
        if (
            self._catalog_refresh_cursor is None
            and self._last_catalog_refresh is not None
            and now - self._last_catalog_refresh < self.catalog_refresh_seconds
        ):
            return
        configured_roots = [str(root.resolve()) for root in self.roots]
        if not configured_roots:
            return
        with self.session_factory() as session:
            statement = select(Recording).where(Recording.archive_root.in_(configured_roots))
            if self._catalog_refresh_cursor is not None:
                statement = statement.where(Recording.id > self._catalog_refresh_cursor)
            rows = list(
                session.scalars(
                    statement.order_by(Recording.id).limit(self.catalog_refresh_batch_size)
                )
            )
            if not rows:
                completed_batch = self._catalog_refresh_cursor is not None
                self._catalog_refresh_cursor = None
                self._last_catalog_refresh = now
                if completed_batch and self.catalog_refresh_seconds == 0:
                    self._refresh_catalog_batch()
                return
            for recording in rows:
                refresh_audio(recording)
            self._catalog_refresh_cursor = rows[-1].id
            session.commit()

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
        def process(job: IngestionJob) -> ProcessingResult:
            source = self._resolve_source(job.source_path, job.archive_root)
            transcript = transcribe(str(source))
            return ProcessingResult(
                source_path=job.source_path,
                status="completed",
                raw_text=transcript.raw_text,
                display_text=transcript.display_text,
                language=transcript.language,
                confidence=transcript.confidence,
                callsign_mentions=transcript.callsign_mentions,
            )

        results: list[ProcessingResult] = []
        pending_jobs = [job for job in self.job_store.list() if job.status == JobState.PENDING]
        if limit is not None:
            pending_jobs = pending_jobs[: max(0, limit)]
        for job in pending_jobs:
            job.status = JobState.PROCESSING
            job.touch()
            self._publish(job)
            def process_current(_source_path: str, current_job: IngestionJob = job) -> ProcessingResult:
                return process(current_job)

            worker = ProcessingWorker(job_store=self.job_store, process_func=process_current)
            result = worker.process_job(job.id)
            self.results[job.id] = result
            self._persist_result(job, result)
            self._publish(job, result)
            results.append(result)
        return results

    def subscribe(self) -> Queue[dict[str, object]]:
        subscriber: Queue[dict[str, object]] = Queue(maxsize=100)
        with self._subscriber_lock:
            self._subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: Queue[dict[str, object]]) -> None:
        with self._subscriber_lock:
            self._subscribers.discard(subscriber)

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
            callsign_mentions=transcript.callsign_mentions,
        )
        self.live_results[source_path] = result
        self._broadcast(
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
        self._broadcast(self._event_payload(job, result))

    def _broadcast(self, payload: dict[str, object]) -> None:
        with self._subscriber_lock:
            subscribers = tuple(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber.put_nowait(payload)
            except Full:
                # A stale browser must not hold the publisher or other viewers hostage.
                self.unsubscribe(subscriber)

    def _resolve_source(self, source_path: str, archive_root: str | None = None) -> Path:
        roots = [Path(archive_root)] if archive_root else self.roots
        configured_roots = {root.resolve() for root in self.roots}
        for root in roots:
            if root.resolve() not in configured_roots:
                continue
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
                    if stored.recording is None:
                        stored.recording = get_or_create_recording(
                            session,
                            source_path=stored.source_path,
                            archive_root=stored.archive_root or "",
                            recording_id=stored.id,
                            status=stored.status,
                        )
                    expired_ids.append(stored.recording.id)
            if expired_ids:
                session.query(Recording).filter(Recording.id.in_(expired_ids)).update(
                    {"audio_status": "expired", "expired_at": datetime.now(UTC)},
                    synchronize_session=False,
                )
                session.commit()
        return len(expired_ids)

    def _source_exists(self, source_path: str, archive_root: str | None = None) -> bool:
        try:
            self._resolve_source(source_path, archive_root)
        except FileNotFoundError:
            return False
        return True

    def _restore_state(self) -> None:
        if not self._database_ready:
            return
        with self.session_factory() as session:
            for stored in session.query(DbIngestionJob).all():
                if stored.archive_root not in {str(root.resolve()) for root in self.roots}:
                    continue
                if not self._source_exists(stored.source_path, stored.archive_root):
                    continue
                job = IngestionJob(
                    source_path=stored.source_path,
                    archive_root=stored.archive_root,
                    id=stored.id,
                )
                status = JobState(stored.status)
                if status == JobState.COMPLETED and stored.transcript is not None:
                    source = self._resolve_source(stored.source_path, stored.archive_root)
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
                        callsign_mentions=self._deserialize_callsign_mentions(
                            stored.transcript.callsign_mentions_json
                        ),
                    )
            session.commit()

    def _persist_result(self, job: IngestionJob, result: ProcessingResult) -> None:
        with self.session_factory() as session:
            stored_job = session.get(DbIngestionJob, job.id)
            if stored_job is None:
                stored_job = DbIngestionJob(id=job.id, source_path=job.source_path)
                session.add(stored_job)
            if stored_job.recording is None:
                stored_job.recording = get_or_create_recording(
                    session,
                    source_path=stored_job.source_path,
                    archive_root=stored_job.archive_root or "",
                    recording_id=stored_job.id,
                    status=job.status.value,
                )
            stored_job.status = job.status.value
            stored_job.recording.status = job.status.value
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
            transcript.recording_id = stored_job.recording_id
            transcript.callsign_mentions_json = json.dumps(
                [
                    {
                        "callsign": mention.callsign,
                        "start": mention.start,
                        "end": mention.end,
                        "confidence": mention.confidence,
                        "acoustic_confidence": mention.acoustic_confidence,
                        "recognition_confidence": mention.recognition_confidence,
                        "evidence": list(mention.evidence),
                    }
                    for mention in (result.callsign_mentions or [])
                ]
            )
            session.commit()

    @staticmethod
    def _deserialize_callsign_mentions(value: str | None) -> list[TranscriptCallsignMention]:
        try:
            items = json.loads(value or "[]")
            return [
                TranscriptCallsignMention(
                    callsign=str(item["callsign"]),
                    start=float(item["start"]),
                    end=float(item["end"]),
                    confidence=float(item.get("confidence", 0.5)),
                    acoustic_confidence=(
                        float(item["acoustic_confidence"])
                        if item.get("acoustic_confidence") is not None
                        else None
                    ),
                    recognition_confidence=float(
                        item.get("recognition_confidence", 0.5)
                    ),
                    evidence=tuple(str(value) for value in item.get("evidence", [])),
                )
                for item in items
            ]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return []

    def activity_events(self) -> list[ActivityLogEvent]:
        events: list[ActivityLogEvent] = []
        for root in self.roots:
            log_paths = list(root.rglob("activity.log")) + list(root.rglob("????????.txt"))
            for path in sorted(log_paths) if root.exists() else []:
                events.extend(ActivityLogParser(path).parse())
        return sorted(events, key=lambda event: event.timestamp)
