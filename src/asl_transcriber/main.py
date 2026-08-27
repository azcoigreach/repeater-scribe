from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from asl_transcriber import __version__
from asl_transcriber.ami import AmiError, AmiResponse
from asl_transcriber.config import settings
from asl_transcriber.database import SessionLocal, get_db
from asl_transcriber.favorites import (
    FavoriteNotFound,
    create_favorite,
    delete_favorite,
    list_favorite_items,
    record_remote_key_transition,
    update_favorite,
)
from asl_transcriber.models import Favorite
from asl_transcriber.node_control import RemoteKeyTransition
from asl_transcriber.node_service import NodeStateService
from asl_transcriber.runtime import ArchiveRuntime
from asl_transcriber.topology import (
    TopologyService,
    ensure_topology_crawl,
    serialize_topology,
)
from asl_transcriber.transcription.callsigns import CallsignResolver
from asl_transcriber.transcription.context import DatabaseCallsignProvider
from asl_transcriber.transcription.faster_whisper import FasterWhisperEngine
from asl_transcriber.transcription.live import FfmpegSnapshotter, LiveTranscriptionService

logger = logging.getLogger(__name__)
runtime = ArchiveRuntime(
    settings.archive_path_list,
    stable_seconds=settings.file_stabilization_seconds,
)
node_monitor: NodeStateService | None = None
topology_service: TopologyService | None = None
transcription_engine: FasterWhisperEngine | None = None

NODE_COMMANDS = [
    {"name": "Announce", "template": "rpt cmd {node} status 11", "target": False},
    {"name": "Say Time of Day", "template": "rpt cmd {node} status 12", "target": False},
    {"name": "Force ID", "template": "rpt cmd {node} status 1", "target": False},
    {"name": "Link", "template": "rpt fun {node} *3", "target": False},
    {"name": "Show node status", "template": "rpt stats {node}", "target": False},
    {"name": "Show link status", "template": "rpt lstats {node}", "target": False},
    {"name": "Show IAX channels", "template": "iax2 show channels", "target": False},
    {"name": "Show IAX registry", "template": "iax2 show registry", "target": False},
    {"name": "Show Network Status", "template": "iax2 show netstats", "target": False},
    {"name": "Show uptime", "template": "core show uptime", "target": False},
    {"name": "Reconnect", "template": "rpt cmd {node} ilink 16", "target": False},
    {"name": "Connect node", "template": "rpt cmd {node} ilink 3 {target}", "target": True},
    {"name": "Connect monitor", "template": "rpt cmd {node} ilink 2 {target}", "target": True},
    {
        "name": "Connect local monitor",
        "template": "rpt cmd {node} ilink 8 {target}",
        "target": True,
    },
    {
        "name": "Connect permanent monitor",
        "template": "rpt cmd {node} ilink 12 {target}",
        "target": True,
    },
    {
        "name": "Connect permanent transceive",
        "template": "rpt cmd {node} ilink 13 {target}",
        "target": True,
    },
    {
        "name": "Connect permanent local monitor",
        "template": "rpt cmd {node} ilink 18 {target}",
        "target": True,
    },
    {"name": "Disconnect node", "template": "rpt cmd {node} ilink 1 {target}", "target": True},
    {"name": "Disconnect all links", "template": "rpt cmd {node} ilink 6", "target": False},
]


def render_node_command(name: str, node_id: int, target: str | None = None) -> str:
    command = next((item for item in NODE_COMMANDS if item["name"] == name), None)
    if command is None:
        raise ValueError("Unknown node command")
    if command["target"] and (
        target is None or re.fullmatch(r"[A-Za-z0-9_-]{1,32}", target) is None
    ):
        raise ValueError("A valid target identifier is required")
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
    if (
        runtime.roots != configured_roots
        or runtime.stable_seconds != settings.file_stabilization_seconds
    ):
        runtime = ArchiveRuntime(
            settings.archive_path_list,
            stable_seconds=settings.file_stabilization_seconds,
        )
    return runtime


def build_local_transcription_engine() -> FasterWhisperEngine:
    configured_callsigns = tuple(settings.known_callsign_list)
    resolver = CallsignResolver(configured_callsigns)
    candidate_provider = DatabaseCallsignProvider(
        SessionLocal,
        configured_callsigns=configured_callsigns,
        cache_seconds=settings.callsign_context_cache_seconds,
        max_candidates=settings.callsign_max_candidates,
    )
    return FasterWhisperEngine(
        model_size=settings.whisper_model,
        device=settings.whisper_device,
        compute_type=settings.whisper_compute_type,
        language=settings.whisper_language,
        beam_size=settings.whisper_beam_size,
        vad_filter=settings.whisper_vad_filter,
        initial_prompt=settings.whisper_initial_prompt,
        hotwords=settings.whisper_hotwords or None,
        workers=settings.worker_concurrency,
        model_dir=settings.whisper_model_dir,
        callsign_resolver=resolver,
        callsign_provider=candidate_provider,
        callsign_hotword_limit=settings.callsign_hotword_limit,
    )


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    global node_monitor, topology_service, transcription_engine
    active_runtime = current_runtime()
    active_runtime.scan_once()
    transcription_engine = build_local_transcription_engine()
    live_service = LiveTranscriptionService(
        snapshotter=FfmpegSnapshotter(
            Path(settings.tmp_dir) / "live",
            window_seconds=settings.live_window_seconds,
            ffmpeg_binary=settings.ffmpeg_binary,
        ),
        transcribe=lambda path: transcription_engine.transcribe(
            path,
            beam_size=settings.live_beam_size,
            vad_filter=False,
            condition_on_previous_text=False,
            use_hotwords=False,
        ),
        min_file_bytes=settings.live_min_file_bytes,
    )

    async def poll_archive() -> None:
        while True:
            active_runtime = current_runtime()
            active_runtime.scan_once()
            if settings.auto_process:
                try:
                    await asyncio.to_thread(
                        active_runtime.process_pending, transcription_engine.transcribe
                    )
                except Exception:
                    logger.exception("Background archive processing cycle failed")
            await asyncio.sleep(settings.archive_poll_seconds)

    async def transcribe_live_audio() -> None:
        while True:
            if settings.live_transcription:
                try:
                    await asyncio.to_thread(live_service.process_once, current_runtime())
                except Exception:
                    logger.exception("Live transcription cycle failed")
            await asyncio.sleep(settings.live_poll_seconds)

    async def persist_key_transition(transition: RemoteKeyTransition) -> None:
        await asyncio.to_thread(record_remote_key_transition, SessionLocal, transition)

    watcher = asyncio.create_task(poll_archive())
    live_watcher = asyncio.create_task(transcribe_live_audio())
    topology_service = (
        TopologyService(
            SessionLocal,
            base_url=settings.favorite_stats_base_url,
            request_interval_seconds=settings.favorite_stats_request_interval_seconds,
            timeout_seconds=settings.favorite_stats_timeout_seconds,
            favorite_refresh_seconds=settings.favorite_stats_refresh_seconds,
            max_nodes=settings.topology_max_nodes,
            max_depth=settings.topology_max_depth,
            refresh_seconds=settings.topology_refresh_seconds,
            cache_seconds=settings.topology_node_cache_seconds,
            viewer_ttl_seconds=settings.topology_viewer_ttl_seconds,
            max_requests_per_minute=settings.allstar_max_requests_per_minute,
            home_nodes=[item.strip() for item in settings.ami_node_id.split(",") if item.strip()]
            or [settings.ami_node_id],
        )
        if settings.favorite_stats_enabled
        else None
    )
    node_monitor = (
        NodeStateService(settings, transition_callback=persist_key_transition)
        if settings.ami_enabled and settings.ami_secret
        else None
    )
    if topology_service is not None and node_monitor is not None:

        def apply_home_directory(home: str, directory: dict[str, str | None]) -> None:
            assert node_monitor is not None
            changed = node_monitor.set_directory(
                home,
                callsign=directory.get("callsign"),
                location=directory.get("location"),
            )
            if changed:
                asyncio.create_task(node_monitor.publish_directory(home))

        topology_service.directory_callback = apply_home_directory
        for home, directory in topology_service.hydrate_home_directories().items():
            node_monitor.set_directory(
                home,
                callsign=directory.get("callsign"),
                location=directory.get("location"),
            )
    topology_watcher = (
        asyncio.create_task(topology_service.run()) if topology_service is not None else None
    )
    if node_monitor is not None:
        await node_monitor.start()
    try:
        yield
    finally:
        watcher.cancel()
        live_watcher.cancel()
        tasks = [watcher, live_watcher]
        if topology_watcher is not None:
            topology_watcher.cancel()
            tasks.append(topology_watcher)
        await asyncio.gather(*tasks, return_exceptions=True)
        if node_monitor is not None:
            await node_monitor.stop()
        node_monitor = None
        topology_service = None


app = FastAPI(title=settings.app_name, version=__version__, lifespan=lifespan)
app.mount("/static", StaticFiles(directory="src/asl_transcriber/static"), name="static")
templates = Jinja2Templates(directory="src/asl_transcriber/templates")


@app.get("/")
def dashboard(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "app_name": settings.app_name,
            "archive_path": settings.archive_paths,
            "ami_node_id": settings.ami_node_id,
        },
    )


@app.get("/health")
def root_health() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name}


@app.get("/api/v1/health")
def health() -> dict[str, str | bool]:
    return {
        "status": "ok",
        "service": settings.app_name,
        "version": __version__,
        "ready": True,
    }


@app.get("/api/v1/system/info")
def system_info() -> dict[str, object]:
    return {
        "service": settings.app_name,
        "environment": settings.app_env,
        "database_url": settings.database_url,
        "archive_paths": settings.archive_path_list,
        "api_version": "v1",
        "transcription": {
            "backend": "local",
            "model": settings.whisper_model,
            "device": settings.whisper_device,
            "compute_type": settings.whisper_compute_type,
            "final_beam_size": settings.whisper_beam_size,
            "live_enabled": settings.live_transcription,
            "live_beam_size": settings.live_beam_size,
            "live_window_seconds": settings.live_window_seconds,
        },
    }


@app.get("/api/v1/node/status")
def node_status() -> dict[str, object]:
    monitor = active_node_monitor()
    return monitor.serialize(monitor.state(monitor.home_nodes[0]))


def active_node_monitor() -> NodeStateService:
    if node_monitor is None:
        raise HTTPException(status_code=503, detail="AMI integration is disabled")
    return node_monitor


def validate_node_identifier(value: str, *, label: str = "node") -> str:
    normalized = value.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{1,32}", normalized) is None:
        raise HTTPException(status_code=422, detail=f"Invalid {label} identifier")
    return normalized


@app.get("/api/v1/nodes")
def nodes() -> dict[str, object]:
    monitor = active_node_monitor()
    return {"items": [monitor.serialize(state) for state in monitor.states.values()]}


@app.get("/api/v1/nodes/{home}/state")
def node_state(home: str) -> dict[str, object]:
    monitor = active_node_monitor()
    return monitor.serialize(monitor.state(validate_node_identifier(home, label="home node")))


@app.get("/api/v1/nodes/{home}/links")
def node_links(home: str) -> dict[str, object]:
    monitor = active_node_monitor()
    state = monitor.state(validate_node_identifier(home, label="home node"))
    return {"items": [monitor.serialize_link(link) for link in state.links.values()]}


class FavoriteCreateRequest(BaseModel):
    target_identifier: str
    label: str | None = Field(default=None, max_length=255)
    callsign: str | None = Field(default=None, max_length=32)
    description: str | None = Field(default=None, max_length=255)
    location: str | None = Field(default=None, max_length=255)


class FavoriteUpdateRequest(BaseModel):
    label: str | None = Field(default=None, max_length=255)
    callsign: str | None = Field(default=None, max_length=32)
    description: str | None = Field(default=None, max_length=255)
    location: str | None = Field(default=None, max_length=255)
    group_name: str | None = Field(default=None, max_length=64)
    sort_order: int | None = None
    default_connection_mode: str | None = Field(default=None, max_length=32)
    permanent: bool | None = None
    exclusive_connect: bool | None = None


def favorite_items(db: Session, home: str) -> list[dict[str, object]]:
    links = node_monitor.state(home).links if node_monitor is not None else {}
    return list_favorite_items(
        db,
        home,
        links,
        public_stale_seconds=settings.favorite_stats_stale_seconds,
        recent_activity_seconds=settings.favorite_stats_recent_activity_seconds,
    )


def require_ui_write() -> None:
    if settings.auth_mode != "off":
        raise HTTPException(status_code=401, detail="Web authentication is required")


def require_api_key(x_api_key: str | None) -> None:
    if not settings.api_key or x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="A valid API key is required")


def create_favorite_record(
    home: str, request: FavoriteCreateRequest, db: Session
) -> dict[str, object]:
    home = validate_node_identifier(home, label="home node")
    target = validate_node_identifier(request.target_identifier, label="target")
    create_favorite(
        db,
        home_node=home,
        target_identifier=target,
        label=(request.label or target).strip() or target,
        callsign=request.callsign.strip() if request.callsign else None,
        description=request.description.strip() if request.description else None,
        location=request.location.strip() if request.location else None,
    )
    return next(item for item in favorite_items(db, home) if item["target_identifier"] == target)


def update_favorite_record(
    home: str, favorite_id: str, request: FavoriteUpdateRequest, db: Session
) -> dict[str, object]:
    home = validate_node_identifier(home, label="home node")
    values = request.model_dump(exclude_unset=True)
    for field in ("label", "callsign", "description", "location", "group_name"):
        if isinstance(values.get(field), str):
            values[field] = values[field].strip() or None
    if "label" in values and not values["label"]:
        raise HTTPException(status_code=422, detail="Favorite label cannot be empty")
    try:
        update_favorite(db, home, favorite_id, values)
    except FavoriteNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return next(item for item in favorite_items(db, home) if item["id"] == favorite_id)


def delete_favorite_record(home: str, favorite_id: str, db: Session) -> dict[str, bool]:
    home = validate_node_identifier(home, label="home node")
    try:
        delete_favorite(db, home, favorite_id)
    except FavoriteNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {"deleted": True}


@app.get("/api/v1/nodes/{home}/favorites")
def favorites(home: str, db: Annotated[Session, Depends(get_db)]) -> dict[str, object]:
    home = validate_node_identifier(home, label="home node")
    items = favorite_items(db, home)
    return {"total": len(items), "items": items}


@app.post("/api/v1/nodes/{home}/favorites")
def api_create_favorite(
    home: str,
    request: FavoriteCreateRequest,
    db: Annotated[Session, Depends(get_db)],
    x_api_key: str | None = Header(default=None),
) -> dict[str, object]:
    require_api_key(x_api_key)
    return create_favorite_record(home, request, db)


@app.patch("/api/v1/nodes/{home}/favorites/{favorite_id}")
def api_update_favorite(
    home: str,
    favorite_id: str,
    request: FavoriteUpdateRequest,
    db: Annotated[Session, Depends(get_db)],
    x_api_key: str | None = Header(default=None),
) -> dict[str, object]:
    require_api_key(x_api_key)
    return update_favorite_record(home, favorite_id, request, db)


@app.delete("/api/v1/nodes/{home}/favorites/{favorite_id}")
def api_delete_favorite(
    home: str,
    favorite_id: str,
    db: Annotated[Session, Depends(get_db)],
    x_api_key: str | None = Header(default=None),
) -> dict[str, bool]:
    require_api_key(x_api_key)
    return delete_favorite_record(home, favorite_id, db)


@app.post("/ui/nodes/{home}/favorites")
def ui_create_favorite(
    home: str,
    request: FavoriteCreateRequest,
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    require_ui_write()
    return create_favorite_record(home, request, db)


@app.patch("/ui/nodes/{home}/favorites/{favorite_id}")
def ui_update_favorite(
    home: str,
    favorite_id: str,
    request: FavoriteUpdateRequest,
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    require_ui_write()
    return update_favorite_record(home, favorite_id, request, db)


@app.delete("/ui/nodes/{home}/favorites/{favorite_id}")
def ui_delete_favorite(
    home: str,
    favorite_id: str,
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, bool]:
    require_ui_write()
    return delete_favorite_record(home, favorite_id, db)


@app.get("/api/v1/nodes/{home}/topology")
def topology_graph(
    home: str,
    root: str,
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    home = validate_node_identifier(home, label="home node")
    root = validate_node_identifier(root, label="topology root")
    return serialize_topology(
        db,
        home,
        root,
        stale_seconds=settings.favorite_stats_stale_seconds,
    )


@app.post("/ui/nodes/{home}/topology/{root}/crawl")
def ui_start_topology_crawl(
    home: str,
    root: str,
    db: Annotated[Session, Depends(get_db)],
    restart: bool = False,
) -> dict[str, object]:
    require_ui_write()
    if topology_service is None:
        raise HTTPException(status_code=503, detail="AllStar topology crawling is disabled")
    home = validate_node_identifier(home, label="home node")
    root = validate_node_identifier(root, label="topology root")
    favorite = db.scalar(
        select(Favorite.id).where(
            Favorite.home_node == home,
            Favorite.target_identifier == root,
        )
    )
    if favorite is None:
        raise HTTPException(status_code=404, detail="Topology root must be a favorite node")
    try:
        ensure_topology_crawl(
            db,
            home,
            root,
            max_nodes=settings.topology_max_nodes,
            max_depth=settings.topology_max_depth,
            restart=restart,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return serialize_topology(
        db,
        home,
        root,
        stale_seconds=settings.favorite_stats_stale_seconds,
    )


@app.get("/api/v1/nodes/{home}/topology/events")
async def topology_events(home: str, root: str) -> StreamingResponse:
    service = topology_service
    if service is None:
        raise HTTPException(status_code=503, detail="AllStar topology crawling is disabled")
    home = validate_node_identifier(home, label="home node")
    root = validate_node_identifier(root, label="topology root")

    async def stream() -> AsyncIterator[str]:
        yield "retry: 3000\n\n"
        async for event in service.events(
            settings.topology_sse_heartbeat_seconds,
            home_node=home,
            root_identifier=root,
        ):
            if event.get("heartbeat"):
                yield ": keepalive\n\n"
            else:
                yield f"event: topology-update\ndata: {json.dumps(event)}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class LinkControlRequest(BaseModel):
    target: str
    mode: str = "transceive"
    permanent: bool = False
    exclusive: bool = False
    confirmed: bool = False


LINK_FUNCTIONS = {
    "disconnect": "1",
    "monitor": "2",
    "transceive": "3",
    "disconnect_all": "6",
    "local_monitor": "8",
    "permanent_disconnect": "11",
    "permanent_monitor": "12",
    "permanent_transceive": "13",
    "reconnect": "16",
    "permanent_local_monitor": "18",
}


def require_control(x_api_key: str | None) -> None:
    if not settings.ami_control_enabled:
        raise HTTPException(status_code=503, detail="AMI node control is disabled")
    if not settings.api_key or x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="A valid API key is required")


def ami_action_accepted(response: AmiResponse) -> bool:
    value = next(
        (value for key, value in response.headers.items() if key.casefold() == "response"), ""
    )
    return value.casefold() in {"success", "follows"}


async def execute_control_command(home: str, command: str) -> AmiResponse:
    monitor = active_node_monitor()
    try:
        response = await monitor.client_for(home).execute("Command", Command=command)
    except AmiError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    if not ami_action_accepted(response):
        raise HTTPException(status_code=502, detail="AMI did not accept the command")
    monitor.request_reconcile(home)
    return response


def pending_control_response(
    home: str,
    response: AmiResponse,
    *,
    target: str | None = None,
    desired_connected: bool | None = None,
) -> dict[str, object]:
    return {
        "home_node": home,
        "target": target,
        "desired_connected": desired_connected,
        "pending_confirmation": True,
        "confirmation_timeout_seconds": settings.ami_confirmation_timeout_seconds,
        "response": response.headers,
    }


@app.post("/api/v1/nodes/{home}/links")
async def connect_link(
    home: str, request: LinkControlRequest, x_api_key: str | None = Header(default=None)
) -> dict[str, object]:
    require_control(x_api_key)
    active_node_monitor()
    home = validate_node_identifier(home, label="home node")
    target = validate_node_identifier(request.target, label="target")
    mode = request.mode.strip().casefold()
    if request.permanent:
        mode = f"permanent_{mode}"
    function = LINK_FUNCTIONS.get(mode)
    if function is None or mode in {
        "disconnect",
        "disconnect_all",
        "permanent_disconnect",
        "reconnect",
    }:
        raise HTTPException(status_code=422, detail="Invalid connection mode")
    if not request.confirmed:
        raise HTTPException(status_code=400, detail="Command confirmation is required")
    response = await execute_control_command(home, f"rpt cmd {home} ilink {function} {target}")
    return {
        **pending_control_response(home, response, target=target, desired_connected=True),
        "mode": mode,
    }


@app.delete("/api/v1/nodes/{home}/links/{target}")
async def disconnect_link(
    home: str, target: str, x_api_key: str | None = Header(default=None)
) -> dict[str, object]:
    require_control(x_api_key)
    active_node_monitor()
    home = validate_node_identifier(home, label="home node")
    target = validate_node_identifier(target, label="target")
    response = await execute_control_command(home, f"rpt cmd {home} ilink 1 {target}")
    return pending_control_response(home, response, target=target, desired_connected=False)


@app.delete("/api/v1/nodes/{home}/links")
async def disconnect_all_links(
    home: str, x_api_key: str | None = Header(default=None)
) -> dict[str, object]:
    require_control(x_api_key)
    active_node_monitor()
    home = validate_node_identifier(home, label="home node")
    response = await execute_control_command(home, f"rpt cmd {home} ilink 6")
    return pending_control_response(home, response, desired_connected=False)


@app.post("/api/v1/nodes/{home}/reconnect")
async def reconnect_node(
    home: str, x_api_key: str | None = Header(default=None)
) -> dict[str, object]:
    require_control(x_api_key)
    active_node_monitor()
    home = validate_node_identifier(home, label="home node")
    response = await execute_control_command(home, f"rpt cmd {home} ilink 16")
    return pending_control_response(home, response)


@app.get("/api/v1/nodes/{home}/events")
async def node_events(home: str) -> StreamingResponse:
    monitor = active_node_monitor()
    home = validate_node_identifier(home, label="home node")
    return StreamingResponse(
        monitor.events(home, monitor.subscribe(home)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/v1/node/ping")
async def node_ping() -> dict[str, object]:
    monitor = active_node_monitor()
    try:
        response = await monitor.client.execute("Ping")
    except AmiError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return {"response": response.headers}


@app.get("/api/v1/node/{node_id}/commands")
def node_commands(node_id: int) -> dict[str, object]:
    return {
        "node_id": node_id,
        "commands": [
            {"name": item["name"], "requires_target": item["target"]} for item in NODE_COMMANDS
        ],
    }


class NodeCommandRequest(BaseModel):
    name: str
    target: str | None = None
    confirmed: bool = False


async def execute_named_command(node_id: int, request: NodeCommandRequest) -> dict[str, object]:
    if not settings.ami_control_enabled:
        raise HTTPException(status_code=503, detail="AMI node control is disabled")
    if not request.confirmed:
        raise HTTPException(status_code=400, detail="Command confirmation is required")
    try:
        command = render_node_command(request.name, node_id, request.target)
        response = (
            await active_node_monitor().client_for(str(node_id)).execute("Command", Command=command)
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except AmiError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    if not ami_action_accepted(response):
        raise HTTPException(status_code=502, detail="AMI did not accept the command")
    response_lines = [line for line in response.values("Output") if line != "--END COMMAND--"]
    active_node_monitor().request_reconcile(str(node_id))
    return {
        "node_id": node_id,
        "name": request.name,
        "response": response.headers,
        "messages": response.messages,
        "response_text": "\n".join(response_lines),
        "pending_confirmation": request.name.startswith(("Connect", "Disconnect")),
        "confirmation_timeout_seconds": settings.ami_confirmation_timeout_seconds,
    }


@app.post("/api/v1/node/{node_id}/command")
async def node_command(
    node_id: int,
    request: NodeCommandRequest,
    x_api_key: str | None = Header(default=None),
) -> dict[str, object]:
    if not settings.api_key or x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="A valid API key is required")
    return await execute_named_command(node_id, request)


@app.post("/ui/node/{node_id}/command")
async def ui_node_command(node_id: int, request: NodeCommandRequest) -> dict[str, object]:
    if settings.auth_mode != "off":
        raise HTTPException(status_code=401, detail="Web authentication is required")
    return await execute_named_command(node_id, request.model_copy(update={"confirmed": True}))


class NodeFunctionRequest(BaseModel):
    function: str


async def execute_node_function(node_id: int, request: NodeFunctionRequest) -> dict[str, object]:
    if not settings.ami_control_enabled:
        raise HTTPException(status_code=503, detail="AMI node control is disabled")
    if re.fullmatch(r"[0-9*#A-D]+", request.function.strip().upper()) is None:
        raise HTTPException(status_code=422, detail="Invalid AllStar function code")
    try:
        response = (
            await active_node_monitor()
            .client_for(str(node_id))
            .execute("Command", Command=f"rpt fun {node_id} {request.function.strip().upper()}")
        )
    except AmiError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    if not ami_action_accepted(response):
        raise HTTPException(status_code=502, detail="AMI did not accept the function")
    return {
        "node_id": node_id,
        "function": request.function.strip().upper(),
        "response": response.headers,
    }


@app.post("/api/v1/node/{node_id}/function")
async def node_function(
    node_id: int,
    request: NodeFunctionRequest,
    x_api_key: str | None = Header(default=None),
) -> dict[str, object]:
    if not settings.api_key or x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="A valid API key is required")
    return await execute_node_function(node_id, request)


@app.post("/ui/node/{node_id}/function")
async def ui_node_function(node_id: int, request: NodeFunctionRequest) -> dict[str, object]:
    if settings.auth_mode != "off":
        raise HTTPException(status_code=401, detail="Web authentication is required")
    return await execute_node_function(node_id, request)


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
    global transcription_engine
    active_runtime = current_runtime()
    if transcription_engine is None:
        transcription_engine = build_local_transcription_engine()
    results = active_runtime.process_pending(transcription_engine.transcribe)
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
def recordings(
    q: str | None = None, status: str | None = None, limit: int = 100
) -> dict[str, object]:
    active_runtime = current_runtime()
    normalized_query = q.casefold() if q else None
    items: list[dict[str, object]] = []
    jobs = sorted(active_runtime.jobs(), key=lambda job: job.source_path, reverse=True)
    waiting_items: list[dict[str, object]] = []
    for source_path in active_runtime.waiting_sources():
        live_result = active_runtime.live_results.get(source_path)
        waiting_items.append(
            {
                "id": None,
                "source_path": source_path,
                "status": "live" if live_result is not None else "waiting",
                "transcript": (
                    {
                        "raw_text": live_result.raw_text,
                        "display_text": live_result.display_text,
                        "language": live_result.language,
                        "provisional": True,
                    }
                    if live_result is not None
                    else None
                ),
                "timestamp": recording_timestamp(source_path),
                "audio_url": f"/api/v1/audio?path={quote(source_path)}",
            }
        )
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
        result = (
            active_runtime.results.get(str(item["id"]))
            if item["id"]
            else active_runtime.live_results.get(job_source_path)
        )
        searchable = f"{job_source_path} {result.raw_text if result else ''} {result.display_text if result else ''}".casefold()
        if normalized_query and normalized_query not in searchable:
            continue
        if status and item["status"] != status:
            continue
        items.append(item)
    result_limit = max(1, min(limit, 500))
    return {
        "total": len(items),
        "database_totals": active_runtime.database_totals(),
        "items": items[:result_limit],
    }


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
