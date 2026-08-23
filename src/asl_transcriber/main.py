from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from asl_transcriber.config import settings
from asl_transcriber.runtime import ArchiveRuntime
from asl_transcriber.transcription.faster_whisper import FasterWhisperEngine

logger = logging.getLogger(__name__)
runtime = ArchiveRuntime(settings.archive_path_list)


def recording_timestamp(source_path: str) -> str | None:
    match = re.match(r"^(\d{14})(\d{2})", Path(source_path).name)
    if match is None:
        return None
    timestamp = datetime.strptime(match.group(1), "%Y%m%d%H%M%S").replace(
        tzinfo=UTC, microsecond=int(match.group(2)) * 10000
    )
    return timestamp.isoformat()


def current_runtime() -> ArchiveRuntime:
    global runtime
    configured_roots = [Path(root) for root in settings.archive_path_list]
    if runtime.roots != configured_roots:
        runtime = ArchiveRuntime(settings.archive_path_list)
    return runtime


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    active_runtime = current_runtime()
    active_runtime.scan_once()
    engine = FasterWhisperEngine(
        model_size=settings.whisper_model,
        device=settings.whisper_device,
        compute_type=settings.whisper_compute_type,
        language=settings.whisper_language,
        workers=settings.worker_concurrency,
        model_dir=settings.whisper_model_dir,
    )

    async def poll_archive() -> None:
        while True:
            active_runtime = current_runtime()
            active_runtime.scan_once()
            if settings.auto_process:
                try:
                    await asyncio.to_thread(active_runtime.process_pending, engine.transcribe)
                except Exception:
                    logger.exception("Background archive processing cycle failed")
            await asyncio.sleep(settings.archive_poll_seconds)

    watcher = asyncio.create_task(poll_archive())
    try:
        yield
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="src/asl_transcriber/static"), name="static")
templates = Jinja2Templates(directory="src/asl_transcriber/templates")


@app.get("/")
def dashboard(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={"app_name": settings.app_name, "archive_path": settings.archive_paths},
    )


@app.get("/health")
def root_health() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name}


@app.get("/api/v1/health")
def health() -> dict[str, str | bool]:
    return {
        "status": "ok",
        "service": settings.app_name,
        "version": "0.1.0",
        "ready": True,
    }


@app.get("/api/v1/system/info")
def system_info() -> dict[str, str | list[str] | bool]:
    return {
        "service": settings.app_name,
        "environment": settings.app_env,
        "database_url": settings.database_url,
        "archive_paths": settings.archive_path_list,
        "api_version": "v1",
        "read_only_mode": settings.read_only_mode,
    }


@app.post("/api/v1/ingestion/scan")
def scan_archive() -> dict[str, int]:
    active_runtime = current_runtime()
    jobs = active_runtime.scan_once()
    return {"created": len(jobs), "total": len(active_runtime.jobs())}


@app.get("/api/v1/ingestion/jobs")
def ingestion_jobs() -> dict[str, object]:
    active_runtime = current_runtime()
    items = [
        {
            "id": job.id,
            "source_path": job.source_path,
            "status": job.status.value,
            "attempt_count": job.attempt_count,
            "last_error": job.last_error,
            "transcript": (
                {
                    "raw_text": active_runtime.results[job.id].raw_text,
                    "display_text": active_runtime.results[job.id].display_text,
                    "language": active_runtime.results[job.id].language,
                }
                if job.id in active_runtime.results
                else None
            ),
        }
        for job in active_runtime.jobs()
    ]
    return {"total": len(items), "items": items}


@app.post("/api/v1/ingestion/process")
def process_ingestion() -> dict[str, int]:
    active_runtime = current_runtime()
    engine = FasterWhisperEngine(
        model_size=settings.whisper_model,
        device=settings.whisper_device,
        compute_type=settings.whisper_compute_type,
        language=settings.whisper_language,
        workers=settings.worker_concurrency,
    )
    results = active_runtime.process_pending(engine.transcribe)
    return {"processed": len(results), "total": len(active_runtime.jobs())}


@app.get("/api/v1/activity")
def activity_events() -> dict[str, object]:
    active_runtime = current_runtime()
    items = [
        {
            "timestamp": event.timestamp.isoformat(),
            "node_id": event.node_id,
            "event_type": event.event_type,
            "details": event.details,
            "raw": event.raw,
        }
        for event in active_runtime.activity_events()
    ]
    return {"total": len(items), "items": items}


@app.get("/api/v1/recordings")
def recordings(q: str | None = None, status: str | None = None, limit: int = 100) -> dict[str, object]:
    active_runtime = current_runtime()
    normalized_query = q.casefold() if q else None
    items: list[dict[str, object]] = []
    jobs = sorted(active_runtime.jobs(), key=lambda job: job.source_path, reverse=True)
    waiting_items: list[dict[str, object]] = [
        {
            "id": None,
            "source_path": source_path,
            "status": "waiting",
            "transcript": None,
            "timestamp": recording_timestamp(source_path),
            "audio_url": f"/api/v1/audio?path={quote(source_path)}",
        }
        for source_path in active_runtime.waiting_sources()
    ]
    all_items: list[dict[str, object]] = waiting_items + [
        {
            "id": job.id,
            "source_path": job.source_path,
            "status": job.status.value,
            "timestamp": recording_timestamp(job.source_path),
            "audio_url": f"/api/v1/audio?path={quote(job.source_path)}",
            "transcript": (
                {
                    "raw_text": result.raw_text,
                    "display_text": result.display_text,
                    "language": result.language,
                }
                if (result := active_runtime.results.get(job.id)) is not None
                else None
            ),
        }
        for job in jobs
    ]
    for item in all_items:
        job_source_path = str(item["source_path"])
        result = active_runtime.results.get(str(item["id"])) if item["id"] else None
        searchable = f"{job_source_path} {result.raw_text if result else ''} {result.display_text if result else ''}".casefold()
        if normalized_query and normalized_query not in searchable:
            continue
        if status and item["status"] != status:
            continue
        items.append(item)
    result_limit = max(1, min(limit, 500))
    return {"total": len(items), "items": items[:result_limit]}


@app.get("/api/v1/audio")
def audio(path: str) -> FileResponse:
    try:
        source = current_runtime()._resolve_source(path)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="Audio recording not found") from error
    return FileResponse(source, media_type="audio/wav", filename=source.name)


@app.get("/api/v1/events")
async def events() -> StreamingResponse:
    active_runtime = current_runtime()
    event_queue = active_runtime.subscribe()

    async def stream() -> AsyncIterator[str]:
        yield "event: ready\ndata: {}\n\n"
        while True:
            payload = await asyncio.to_thread(event_queue.get)
            import json

            yield f"event: job\ndata: {json.dumps(payload)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")
