from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from urllib.parse import quote

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from asl_transcriber.ami import AmiClient, AmiError, AmiResponse
from asl_transcriber.config import settings
from asl_transcriber.ingestion.activity import ActivityLogEvent
from asl_transcriber.runtime import ArchiveRuntime
from asl_transcriber.transcription.faster_whisper import FasterWhisperEngine

logger = logging.getLogger(__name__)
runtime = ArchiveRuntime(settings.archive_path_list)
last_connected_summary: dict[str, object] | None = None
last_connected_at = 0.0

NODE_COMMANDS = [
    {"name": "Show node status", "template": "rpt stats {node}", "target": False},
    {"name": "Show link status", "template": "rpt lstats {node}", "target": False},
    {"name": "Show IAX channels", "template": "iax2 show channels", "target": False},
    {"name": "Show IAX registry", "template": "iax2 show registry", "target": False},
    {"name": "Show uptime", "template": "core show uptime", "target": False},
    {"name": "Reconnect", "template": "rpt cmd {node} ilink 16", "target": False},
    {"name": "Connect node", "template": "rpt cmd {node} ilink 3 {target}", "target": True},
    {"name": "Disconnect node", "template": "rpt cmd {node} ilink 1 {target}", "target": True},
]


def render_node_command(name: str, node_id: int, target: str | None = None) -> str:
    command = next((item for item in NODE_COMMANDS if item["name"] == name), None)
    if command is None:
        raise ValueError("Unknown node command")
    if command["target"] and (target is None or re.fullmatch(r"\d{3,7}", target) is None):
        raise ValueError("A numeric target node is required")
    return str(command["template"]).format(node=node_id, target=target or "")


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


app = FastAPI(title=settings.app_name, version="0.2.0", lifespan=lifespan)
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
        "version": "0.2.0",
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


def ami_client() -> AmiClient:
    if not settings.ami_enabled or not settings.ami_secret:
        raise HTTPException(status_code=503, detail="AMI integration is disabled")
    return AmiClient(
        settings.ami_host,
        settings.ami_port,
        settings.ami_username,
        settings.ami_secret,
        settings.ami_timeout_seconds,
    )


def summarize_node(response: AmiResponse, activity: list[ActivityLogEvent]) -> dict[str, object]:
    stations: list[dict[str, str]] = []
    connected_nodes: set[int] = set()
    for message in response.messages:
        channel = message.get("Channel", "")
        is_remote_channel = channel.startswith(("IAX2/", "DAHDI/", "PJSIP/"))
        caller_id = message.get("CallerIDNum", "")
        remote_id = message.get("Exten", "") if is_remote_channel else caller_id
        if is_remote_channel and remote_id.isdigit() and int(remote_id) > 0:
            caller_id = remote_id
            connected_nodes.add(int(caller_id))
            stations.append(
                {
                    "id": caller_id,
                    "name": message.get("CallerIDName", "<unknown>"),
                    "channel": channel,
                    "state": message.get("ChannelStateDesc", "unknown"),
                }
            )
    link_state: dict[int, str] = {}
    for event in sorted(activity, key=lambda item: item.timestamp):
        if event.event_type == "LINKCONN" and event.details:
            match = re.search(r"\b(\d{3,7})\b", event.details)
            if match:
                link_state[int(match.group(1))] = "connected"
        elif event.event_type in {"LINKDISC", "REMDISC"} and event.details:
            match = re.search(r"\b(\d{3,7})\b", event.details)
            if match:
                link_state[int(match.group(1))] = "disconnected"
    connected_nodes.update(node for node, state in link_state.items() if state == "connected")
    known_station_ids = {station["id"] for station in stations}
    for node, state in link_state.items():
        if state == "connected" and str(node) not in known_station_ids:
            stations.append(
                {"id": str(node), "name": "ASL3 remote node", "channel": "", "state": "Connected"}
            )
    recent_talkers = sorted(
        {
            event.details.split(",", 1)[0].strip()
            for event in activity
            if event.event_type == "RXKEY"
            and event.details
            and event.details.split(",", 1)[0].strip().isdigit()
            and (datetime.now(UTC) - event.timestamp).total_seconds() <= 15
        }
    )
    return {
        "ami_connected": response.success,
        "connected_nodes": sorted(connected_nodes),
        "connected_stations": stations,
        "active_channels": [
            message.get("Channel", "")
            for message in response.messages
            if message.get("Event") == "Status" and message.get("Channel")
        ],
        "talkers": recent_talkers,
    }


def stable_node_summary(
    current: dict[str, object],
    previous: dict[str, object] | None,
    now: float,
    last_seen: float,
) -> dict[str, object]:
    if (
        previous is not None
        and current["ami_connected"]
        and not current["connected_nodes"]
        and now - last_seen <= settings.ami_connection_grace_seconds
    ):
        return {**current, "connected_nodes": previous["connected_nodes"], "connected_stations": previous["connected_stations"]}
    return current


@app.get("/api/v1/node/status")
def node_status() -> dict[str, object]:
    global last_connected_summary, last_connected_at
    try:
        response = ami_client().status()
    except AmiError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    current = summarize_node(response, current_runtime().activity_events())
    current = stable_node_summary(current, last_connected_summary, monotonic(), last_connected_at)
    if current["connected_nodes"]:
        last_connected_summary = current
        last_connected_at = monotonic()
    return {
        **current,
        "response": response.headers,
        "messages": response.messages,
    }


@app.post("/api/v1/node/ping")
def node_ping() -> dict[str, object]:
    try:
        response = ami_client().ping()
    except AmiError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return {"response": response.headers}


@app.get("/api/v1/node/{node_id}/commands")
def node_commands(node_id: int) -> dict[str, object]:
    return {
        "node_id": node_id,
        "commands": [{"name": item["name"], "requires_target": item["target"]} for item in NODE_COMMANDS],
    }


class NodeCommandRequest(BaseModel):
    name: str
    target: str | None = None
    confirmed: bool = False


def execute_named_command(node_id: int, request: NodeCommandRequest) -> dict[str, object]:
    if not settings.ami_control_enabled:
        raise HTTPException(status_code=503, detail="AMI node control is disabled")
    if not request.confirmed:
        raise HTTPException(status_code=400, detail="Command confirmation is required")
    try:
        command = render_node_command(request.name, node_id, request.target)
        response = ami_client().command(command)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except AmiError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return {"node_id": node_id, "name": request.name, "response": response.headers, "messages": response.messages}


@app.post("/api/v1/node/{node_id}/command")
def node_command(
    node_id: int,
    request: NodeCommandRequest,
    x_api_key: str | None = Header(default=None),
) -> dict[str, object]:
    if not settings.api_key or x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="A valid API key is required")
    return execute_named_command(node_id, request)


@app.post("/ui/node/{node_id}/command")
def ui_node_command(node_id: int, request: NodeCommandRequest) -> dict[str, object]:
    if settings.auth_mode != "off":
        raise HTTPException(status_code=401, detail="Web authentication is required")
    return execute_named_command(node_id, request)


class NodeFunctionRequest(BaseModel):
    function: str


def execute_node_function(node_id: int, request: NodeFunctionRequest) -> dict[str, object]:
    if not settings.ami_control_enabled:
        raise HTTPException(status_code=503, detail="AMI node control is disabled")
    if re.fullmatch(r"[0-9*#A-D]+", request.function.strip().upper()) is None:
        raise HTTPException(status_code=422, detail="Invalid AllStar function code")
    try:
        response = ami_client().command(f"rpt fun {node_id} {request.function.strip().upper()}")
    except AmiError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return {"node_id": node_id, "function": request.function.strip().upper(), "response": response.headers}


@app.post("/api/v1/node/{node_id}/function")
def node_function(
    node_id: int,
    request: NodeFunctionRequest,
    x_api_key: str | None = Header(default=None),
) -> dict[str, object]:
    if not settings.api_key or x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="A valid API key is required")
    return execute_node_function(node_id, request)


@app.post("/ui/node/{node_id}/function")
def ui_node_function(node_id: int, request: NodeFunctionRequest) -> dict[str, object]:
    if settings.auth_mode != "off":
        raise HTTPException(status_code=401, detail="Web authentication is required")
    return execute_node_function(node_id, request)


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
