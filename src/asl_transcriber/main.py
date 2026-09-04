from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from queue import Empty
from typing import Annotated
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session
from starlette.middleware.trustedhost import TrustedHostMiddleware

from asl_transcriber import __version__
from asl_transcriber.ami import AmiError, AmiResponse
from asl_transcriber.archive import (
    ArchiveQueryError,
    list_recordings,
    refresh_audio,
    serialize_recording,
)
from asl_transcriber.auth import (
    Admin,
    Principal,
    Viewer,
    audit_event,
    authenticate_request,
    complete_oidc_login,
    oidc_authorization_url,
    purge_security_state,
    require_admin,
    require_api_admin,
    require_api_operator,
    require_ui_operator,
    require_viewer,
    revoke_session,
    verify_csrf,
)
from asl_transcriber.callsign_service import (
    callsign_profile,
    canonical_callsign,
    last_heard_rows,
    list_call_sign_mentions,
    list_callsigns,
    review_mention,
    update_qrz_snapshot,
)
from asl_transcriber.config import settings
from asl_transcriber.database import SessionLocal, get_db, require_current_schema
from asl_transcriber.favorites import (
    FavoriteNotFound,
    create_favorite,
    delete_favorite,
    list_favorite_items,
    record_remote_key_transition,
    update_favorite,
)
from asl_transcriber.models import Favorite, Recording
from asl_transcriber.node_control import RemoteKeyTransition
from asl_transcriber.node_service import NodeStateService
from asl_transcriber.qrz import QrzClient, QrzError
from asl_transcriber.runtime import ArchiveRuntime
from asl_transcriber.security import SecurityMiddleware, sse_connections
from asl_transcriber.topology import (
    TopologyService,
    ensure_topology_crawl,
    serialize_topology,
)
from asl_transcriber.transcription.base import TranscriptCallsignMention
from asl_transcriber.transcription.callsigns import (
    CallsignResolver,
    extract_callsigns,
    normalize_callsigns,
)
from asl_transcriber.transcription.context import DatabaseCallsignProvider
from asl_transcriber.transcription.faster_whisper import FasterWhisperEngine
from asl_transcriber.transcription.live import FfmpegSnapshotter, LiveTranscriptionService

logger = logging.getLogger(__name__)
runtime = ArchiveRuntime(
    settings.archive_path_list,
    stable_seconds=settings.file_stabilization_seconds,
    retention_days=settings.retention_days,
)
node_monitor: NodeStateService | None = None
topology_service: TopologyService | None = None
transcription_engine: FasterWhisperEngine | None = None
qrz_client: QrzClient | None = None
qrz_client_credentials: tuple[str, str, str, float, float] | None = None

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


def callsign_confidence_score(
    best_observation: float,
    observation_count: int,
    recording_count: int,
    *,
    qrz_confirmed: bool = False,
) -> float:
    """Combine independent evidence into an explainable, bounded estimate."""
    score = max(0.05, min(0.98, best_observation))
    score = 1.0 - (
        (1.0 - score)
        * (0.9 ** min(max(0, observation_count - 1), 5))
        * (0.8 ** min(max(0, recording_count - 1), 3))
    )
    if qrz_confirmed:
        score = 1.0 - ((1.0 - score) * 0.65)
    return min(0.99, score)


def callsign_confidence_label(score: float) -> str:
    if score >= 0.88:
        return "High confidence"
    if score >= 0.75:
        return "Probable"
    return "Tentative"


def current_qrz_client() -> QrzClient | None:
    global qrz_client, qrz_client_credentials
    qrz_password = settings.resolved_qrz_password
    if not settings.qrz_username or not qrz_password:
        return None
    credentials = (
        settings.qrz_username,
        qrz_password,
        settings.qrz_base_url,
        settings.qrz_timeout_seconds,
        settings.qrz_cache_seconds,
    )
    if qrz_client is None or qrz_client_credentials != credentials:
        qrz_client = QrzClient(
            settings.qrz_username,
            qrz_password,
            base_url=settings.qrz_base_url,
            timeout_seconds=settings.qrz_timeout_seconds,
            cache_seconds=settings.qrz_cache_seconds,
            agent=f"repeater-scribe/{__version__}",
        )
        qrz_client_credentials = credentials
    return qrz_client


def current_runtime() -> ArchiveRuntime:
    global runtime
    configured_roots = [Path(root) for root in settings.archive_path_list]
    if (
        runtime.roots != configured_roots
        or runtime.stable_seconds != settings.file_stabilization_seconds
        or runtime.retention_days != settings.retention_days
    ):
        runtime = ArchiveRuntime(
            settings.archive_path_list,
            stable_seconds=settings.file_stabilization_seconds,
            retention_days=settings.retention_days,
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

    def callsign_candidates() -> tuple[str, ...]:
        client = current_qrz_client()
        qrz_callsigns = client.cached_callsigns() if client is not None else ()
        return normalize_callsigns(qrz_callsigns + tuple(candidate_provider()))[
            : settings.callsign_max_candidates
        ]

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
        callsign_provider=callsign_candidates,
        callsign_hotword_limit=settings.callsign_hotword_limit,
    )


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    global node_monitor, topology_service, transcription_engine
    require_current_schema()
    active_runtime = current_runtime()
    await asyncio.to_thread(active_runtime.scan_once)
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
            try:
                active_runtime = current_runtime()
                await asyncio.to_thread(active_runtime.scan_once)
                if settings.auto_process:
                    await asyncio.to_thread(
                        active_runtime.process_pending,
                        transcription_engine.transcribe,
                        limit=1,
                    )
            except Exception:
                logger.exception("Background archive polling cycle failed")
            await asyncio.sleep(settings.archive_poll_seconds)

    async def transcribe_live_audio() -> None:
        while True:
            if settings.live_transcription:
                try:
                    await asyncio.to_thread(live_service.process_once, current_runtime())
                except Exception:
                    logger.exception("Live transcription cycle failed")
            await asyncio.sleep(settings.live_poll_seconds)

    async def enforce_retention() -> None:
        while True:
            try:
                purged = await asyncio.to_thread(current_runtime().purge_expired)
                security_purged = await asyncio.to_thread(purge_security_state)
                if purged:
                    audit_event(
                        actor="system",
                        auth_source="internal",
                        action="retention_purge",
                        outcome="completed",
                        detail=f"records={purged}",
                    )
                if any(security_purged.values()):
                    logger.info("Purged expired security state: %s", security_purged)
            except Exception:
                logger.exception("Retention cycle failed")
            await asyncio.sleep(86400)

    async def persist_key_transition(transition: RemoteKeyTransition) -> None:
        await asyncio.to_thread(record_remote_key_transition, SessionLocal, transition)

    watcher = asyncio.create_task(poll_archive())
    live_watcher = asyncio.create_task(transcribe_live_audio())
    retention_watcher = asyncio.create_task(enforce_retention())
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
        if settings.ami_enabled and settings.resolved_ami_secret
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
        retention_watcher.cancel()
        tasks = [watcher, live_watcher, retention_watcher]
        if topology_watcher is not None:
            topology_watcher.cancel()
            tasks.append(topology_watcher)
        await asyncio.gather(*tasks, return_exceptions=True)
        if node_monitor is not None:
            await node_monitor.stop()
        node_monitor = None
        topology_service = None


app = FastAPI(
    title=settings.app_name,
    version=__version__,
    lifespan=lifespan,
    docs_url=None if settings.deployment_mode == "internet" else "/docs",
    redoc_url=None if settings.deployment_mode == "internet" else "/redoc",
    openapi_url=None if settings.deployment_mode == "internet" else "/openapi.json",
)
if settings.cors_origin_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-CSRF-Token", "X-API-Key"],
    )
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_host_list)
app.add_middleware(SecurityMiddleware)
app.mount("/static", StaticFiles(directory="src/asl_transcriber/static"), name="static")
templates = Jinja2Templates(directory="src/asl_transcriber/templates")


@app.get("/")
def dashboard(request: Request):
    principal = authenticate_request(request)
    if principal is None:
        return RedirectResponse(url="/auth/login?next=/", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "app_name": settings.app_name,
            "archive_path": settings.archive_paths,
            "ami_node_id": settings.ami_node_id,
            "csrf_token": principal.csrf_token or "",
            "identity": principal.identity,
            "role": principal.role,
        },
    )


def _workspace_context(
    request: Request, template_name: str, *, recording_id: str | None = None,
    callsign: str | None = None,
):
    principal = authenticate_request(request)
    if principal is None:
        next_path = request.url.path
        return RedirectResponse(url=f"/auth/login?next={quote(next_path, safe='/')}", status_code=303)
    context = {
        "request": request,
        "app_name": settings.app_name,
        "csrf_token": principal.csrf_token or "",
        "identity": principal.identity,
        "role": principal.role,
    }
    if recording_id is not None:
        context["recording_id"] = recording_id
    if callsign is not None:
        context["callsign"] = callsign
    return templates.TemplateResponse(request=request, name=template_name, context=context)


@app.get("/archive")
def archive_workspace(request: Request):
    return _workspace_context(request, "archive.html")


@app.get("/callsigns")
def callsign_directory_workspace(request: Request):
    return _workspace_context(request, "callsigns.html")


@app.get("/callsigns/{callsign}")
def callsign_history_workspace(request: Request, callsign: str):
    return _workspace_context(request, "callsign_detail.html", callsign=callsign)


@app.get("/archive/recordings/{recording_id}")
def archive_detail_workspace(
    db: Annotated[Session, Depends(get_db)], request: Request, recording_id: str
):
    response = _workspace_context(request, "archive_detail.html", recording_id=recording_id)
    if isinstance(response, RedirectResponse):
        return response
    if db.get(Recording, recording_id) is None:
        raise HTTPException(status_code=404, detail="Recording not found")
    return response


@app.get("/auth/login")
async def auth_login(next: str | None = None) -> RedirectResponse:
    return RedirectResponse(await oidc_authorization_url(next), status_code=303)


@app.get("/auth/callback")
async def auth_callback(request: Request, code: str, state: str) -> RedirectResponse:
    try:
        raw_session, principal, next_path = await complete_oidc_login(code, state)
    except HTTPException:
        audit_event(
            actor="anonymous",
            auth_source="oidc",
            action="login",
            outcome="denied",
            request=request,
        )
        raise
    response = RedirectResponse(next_path, status_code=303)
    response.set_cookie(
        settings.session_cookie_name,
        raw_session,
        max_age=settings.session_absolute_seconds,
        secure=settings.deployment_mode == "internet",
        httponly=True,
        samesite="lax",
        path="/",
    )
    audit_event(
        actor=principal.identity,
        auth_source="oidc",
        action="login",
        outcome="allowed",
        request=request,
        detail=f"role={principal.role}",
    )
    return response


@app.post("/auth/logout", dependencies=[Depends(require_viewer)])
def auth_logout(request: Request) -> RedirectResponse:
    principal = authenticate_request(request)
    assert principal is not None
    verify_csrf(request, principal)
    revoke_session(request.cookies.get(settings.session_cookie_name))
    response = RedirectResponse("/auth/login", status_code=303)
    response.delete_cookie(settings.session_cookie_name, path="/")
    return response


@app.get("/api/v1/auth/me")
def auth_me(principal: Viewer) -> dict[str, str | None]:
    return {
        "identity": principal.identity,
        "role": principal.role,
        "auth_source": principal.auth_source,
        "csrf_token": principal.csrf_token,
    }


@app.get("/health")
async def root_health() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name}


@app.get("/api/v1/health")
async def health() -> dict[str, str | bool]:
    payload: dict[str, str | bool] = {
        "status": "ok",
        "service": settings.app_name,
        "ready": True,
    }
    if settings.deployment_mode == "local":
        payload["version"] = __version__
    return payload


@app.get("/api/v1/system/info")
def system_info(_: Admin) -> dict[str, object]:
    return {
        "service": settings.app_name,
        "environment": settings.app_env,
        "api_version": "v1",
        "deployment_mode": settings.deployment_mode,
        "authentication": settings.auth_mode,
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


@app.get("/api/v1/node/status", dependencies=[Depends(require_viewer)])
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


@app.get("/api/v1/nodes", dependencies=[Depends(require_viewer)])
def nodes() -> dict[str, object]:
    monitor = active_node_monitor()
    return {"items": [monitor.serialize(state) for state in monitor.states.values()]}


@app.get("/api/v1/nodes/{home}/state", dependencies=[Depends(require_viewer)])
def node_state(home: str) -> dict[str, object]:
    monitor = active_node_monitor()
    return monitor.serialize(monitor.state(validate_node_identifier(home, label="home node")))


@app.get("/api/v1/nodes/{home}/links", dependencies=[Depends(require_viewer)])
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


@app.get("/api/v1/nodes/{home}/favorites", dependencies=[Depends(require_viewer)])
def favorites(home: str, db: Annotated[Session, Depends(get_db)]) -> dict[str, object]:
    home = validate_node_identifier(home, label="home node")
    items = favorite_items(db, home)
    return {"total": len(items), "items": items}


@app.post("/api/v1/nodes/{home}/favorites", dependencies=[Depends(require_api_operator)])
def api_create_favorite(
    home: str,
    request: FavoriteCreateRequest,
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    return create_favorite_record(home, request, db)


@app.patch(
    "/api/v1/nodes/{home}/favorites/{favorite_id}",
    dependencies=[Depends(require_api_operator)],
)
def api_update_favorite(
    home: str,
    favorite_id: str,
    request: FavoriteUpdateRequest,
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    return update_favorite_record(home, favorite_id, request, db)


@app.delete(
    "/api/v1/nodes/{home}/favorites/{favorite_id}",
    dependencies=[Depends(require_api_operator)],
)
def api_delete_favorite(
    home: str,
    favorite_id: str,
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, bool]:
    return delete_favorite_record(home, favorite_id, db)


@app.post("/ui/nodes/{home}/favorites", dependencies=[Depends(require_ui_operator)])
def ui_create_favorite(
    home: str,
    request: FavoriteCreateRequest,
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    return create_favorite_record(home, request, db)


@app.patch(
    "/ui/nodes/{home}/favorites/{favorite_id}",
    dependencies=[Depends(require_ui_operator)],
)
def ui_update_favorite(
    home: str,
    favorite_id: str,
    request: FavoriteUpdateRequest,
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    return update_favorite_record(home, favorite_id, request, db)


@app.delete(
    "/ui/nodes/{home}/favorites/{favorite_id}",
    dependencies=[Depends(require_ui_operator)],
)
def ui_delete_favorite(
    home: str,
    favorite_id: str,
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, bool]:
    return delete_favorite_record(home, favorite_id, db)


@app.get("/api/v1/nodes/{home}/topology", dependencies=[Depends(require_viewer)])
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


@app.post(
    "/ui/nodes/{home}/topology/{root}/crawl",
    dependencies=[Depends(require_ui_operator)],
)
def ui_start_topology_crawl(
    home: str,
    root: str,
    db: Annotated[Session, Depends(get_db)],
    restart: bool = False,
) -> dict[str, object]:
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
async def topology_events(home: str, root: str, principal: Viewer) -> StreamingResponse:
    service = topology_service
    if service is None:
        raise HTTPException(status_code=503, detail="AllStar topology crawling is disabled")
    home = validate_node_identifier(home, label="home node")
    root = validate_node_identifier(root, label="topology root")
    await sse_connections.acquire(principal.subject)

    async def stream() -> AsyncIterator[str]:
        try:
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
        finally:
            await sse_connections.release(principal.subject)

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


def require_control() -> None:
    if not settings.ami_control_enabled:
        raise HTTPException(status_code=503, detail="AMI node control is disabled")


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


@app.post("/api/v1/nodes/{home}/links", dependencies=[Depends(require_api_operator)])
async def connect_link(home: str, request: LinkControlRequest) -> dict[str, object]:
    require_control()
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


@app.delete("/api/v1/nodes/{home}/links/{target}", dependencies=[Depends(require_api_operator)])
async def disconnect_link(home: str, target: str) -> dict[str, object]:
    require_control()
    active_node_monitor()
    home = validate_node_identifier(home, label="home node")
    target = validate_node_identifier(target, label="target")
    response = await execute_control_command(home, f"rpt cmd {home} ilink 1 {target}")
    return pending_control_response(home, response, target=target, desired_connected=False)


@app.delete("/api/v1/nodes/{home}/links", dependencies=[Depends(require_api_operator)])
async def disconnect_all_links(home: str) -> dict[str, object]:
    require_control()
    active_node_monitor()
    home = validate_node_identifier(home, label="home node")
    response = await execute_control_command(home, f"rpt cmd {home} ilink 6")
    return pending_control_response(home, response, desired_connected=False)


@app.post("/api/v1/nodes/{home}/reconnect", dependencies=[Depends(require_api_operator)])
async def reconnect_node(home: str) -> dict[str, object]:
    require_control()
    active_node_monitor()
    home = validate_node_identifier(home, label="home node")
    response = await execute_control_command(home, f"rpt cmd {home} ilink 16")
    return pending_control_response(home, response)


@app.get("/api/v1/nodes/{home}/events")
async def node_events(home: str, principal: Viewer) -> StreamingResponse:
    monitor = active_node_monitor()
    home = validate_node_identifier(home, label="home node")
    await sse_connections.acquire(principal.subject)

    async def stream() -> AsyncIterator[str]:
        try:
            async for event in monitor.events(home, monitor.subscribe(home)):
                yield event
        finally:
            await sse_connections.release(principal.subject)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/v1/node/ping", dependencies=[Depends(require_api_operator)])
async def node_ping() -> dict[str, object]:
    monitor = active_node_monitor()
    try:
        response = await monitor.client.execute("Ping")
    except AmiError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return {"response": response.headers}


@app.get("/api/v1/node/{node_id}/commands", dependencies=[Depends(require_viewer)])
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


@app.post("/api/v1/node/{node_id}/command", dependencies=[Depends(require_api_operator)])
async def node_command(
    node_id: int,
    request: NodeCommandRequest,
) -> dict[str, object]:
    return await execute_named_command(node_id, request)


@app.post("/ui/node/{node_id}/command", dependencies=[Depends(require_ui_operator)])
async def ui_node_command(node_id: int, request: NodeCommandRequest) -> dict[str, object]:
    return await execute_named_command(node_id, request.model_copy(update={"confirmed": True}))


class NodeFunctionRequest(BaseModel):
    function: str


class CallsignMentionReviewRequest(BaseModel):
    action: str
    corrected_callsign: str | None = None


async def execute_node_function(node_id: int, request: NodeFunctionRequest) -> dict[str, object]:
    if not settings.ami_control_enabled:
        raise HTTPException(status_code=503, detail="AMI node control is disabled")
    if not settings.ami_raw_function_enabled:
        raise HTTPException(status_code=503, detail="Raw AllStar functions are disabled")
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


@app.post("/api/v1/node/{node_id}/function", dependencies=[Depends(require_api_operator)])
async def node_function(
    node_id: int,
    request: NodeFunctionRequest,
) -> dict[str, object]:
    return await execute_node_function(node_id, request)


@app.post("/ui/node/{node_id}/function", dependencies=[Depends(require_ui_operator)])
async def ui_node_function(node_id: int, request: NodeFunctionRequest) -> dict[str, object]:
    return await execute_node_function(node_id, request)


@app.post("/api/v1/ingestion/scan", dependencies=[Depends(require_api_admin)])
def scan_archive() -> dict[str, int]:
    active_runtime = current_runtime()
    jobs = active_runtime.scan_once()
    return {"created": len(jobs), "total": len(active_runtime.jobs())}


@app.get("/api/v1/ingestion/jobs", dependencies=[Depends(require_admin)])
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


@app.post("/api/v1/ingestion/process", dependencies=[Depends(require_api_admin)])
def process_ingestion() -> dict[str, int]:
    global transcription_engine
    active_runtime = current_runtime()
    if transcription_engine is None:
        transcription_engine = build_local_transcription_engine()
    results = active_runtime.process_pending(transcription_engine.transcribe)
    return {"processed": len(results), "total": len(active_runtime.jobs())}


@app.get("/api/v1/activity", dependencies=[Depends(require_viewer)])
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


@app.get("/api/v1/recordings", dependencies=[Depends(require_viewer)])
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
                "callsigns": list(extract_callsigns(live_result.display_text))
                if live_result
                else [],
            }
        )
    all_items: list[dict[str, object]] = waiting_items + [
        {
            "id": job.id,
            "source_path": job.source_path,
            "status": job.status.value,
            "timestamp": recording_timestamp(job.source_path),
            "audio_url": f"/api/v1/audio?path={quote(job.source_path)}",
            "callsigns": (
                list(extract_callsigns(result.display_text))
                if (result := active_runtime.results.get(job.id)) is not None
                else []
            ),
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


@app.get("/api/v1/audio", dependencies=[Depends(require_viewer)])
def audio(path: str) -> FileResponse:
    try:
        source = current_runtime()._resolve_source(path)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="Audio recording not found") from error
    return FileResponse(source, media_type="audio/wav", filename=source.name)


@app.get("/api/v1/archive/recordings", dependencies=[Depends(require_viewer)])
def archive_recordings(
    db: Annotated[Session, Depends(get_db)],
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    q: str | None = None,
    status: str | None = None,
    audio_status: str | None = None,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
    callsign: str | None = None,
) -> dict[str, object]:
    try:
        items, next_cursor, has_more = list_recordings(
            db, cursor=cursor, limit=limit, query=q, status=status,
            audio_status=audio_status, from_at=from_, to_at=to, callsign=callsign,
        )
    except ArchiveQueryError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {
        "items": items,
        "next_cursor": next_cursor,
        "has_more": has_more,
        "filters": {"q": q, "status": status, "audio_status": audio_status, "from": from_.isoformat() if from_ else None, "to": to.isoformat() if to else None, "callsign": callsign},
    }


@app.get("/api/v1/archive/recordings/{recording_id}", dependencies=[Depends(require_viewer)])
def archive_recording(
    db: Annotated[Session, Depends(get_db)], recording_id: str
) -> dict[str, object]:
    recording = db.get(Recording, recording_id)
    if recording is None:
        raise HTTPException(status_code=404, detail="Recording not found")
    refresh_audio(recording)
    db.commit()
    return serialize_recording(recording)


@app.get("/api/v1/archive/recordings/{recording_id}/audio", dependencies=[Depends(require_viewer)])
def archive_audio(
    db: Annotated[Session, Depends(get_db)], recording_id: str
) -> FileResponse:
    recording = db.get(Recording, recording_id)
    if recording is None:
        raise HTTPException(status_code=404, detail={"code": "recording_not_found"})
    configured_roots = {Path(root).resolve() for root in settings.archive_path_list}
    if not recording.archive_root or Path(recording.archive_root).resolve() not in configured_roots:
        raise HTTPException(status_code=404, detail={"code": "audio_missing"})
    refresh_audio(recording)
    db.commit()
    if recording.audio_status == "expired":
        raise HTTPException(status_code=410, detail={"code": "audio_expired"})
    if recording.audio_status != "available" or not recording.archive_root:
        raise HTTPException(status_code=404, detail={"code": "audio_missing"})
    source = (Path(recording.archive_root) / recording.source_path).resolve()
    root = Path(recording.archive_root).resolve()
    if not source.is_relative_to(root) or not source.is_file():
        raise HTTPException(status_code=404, detail={"code": "audio_missing"})
    return FileResponse(source, media_type="audio/wav", filename=source.name)


@app.get("/api/v1/callsigns", dependencies=[Depends(require_viewer)])
def callsign_directory(
    db: Annotated[Session, Depends(get_db)], q: str | None = None,
    cursor: str | None = None, limit: int = Query(default=50, ge=1, le=100),
    alphabetical: bool = False, review_status: str | None = None,
) -> dict[str, object]:
    try:
        items, next_cursor, has_more = list_callsigns(
            db, query=q, cursor=cursor, limit=limit, alphabetical=alphabetical,
            review_status=review_status,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_cursor", "message": str(error)},
        ) from error
    return {"items": items, "next_cursor": next_cursor, "has_more": has_more}


@app.get("/api/v1/callsigns/{callsign}/mentions", dependencies=[Depends(require_viewer)])
def callsign_mentions_history(
    db: Annotated[Session, Depends(get_db)], callsign: str,
    cursor: str | None = None, limit: int = Query(default=50, ge=1, le=100),
    from_at: Annotated[datetime | None, Query(alias="from")] = None,
    to_at: Annotated[datetime | None, Query(alias="to")] = None,
    review_status: str | None = None, audio_status: str | None = None,
) -> dict[str, object]:
    try:
        items, next_cursor, has_more = list_call_sign_mentions(
            db, callsign, cursor=cursor, limit=limit, from_at=from_at, to_at=to_at,
            review_status=review_status, audio_status=audio_status,
        )
    except ValueError as error:
        status = 422 if cursor else 400
        detail = {"code": "invalid_cursor", "message": str(error)} if cursor else str(error)
        raise HTTPException(status_code=status, detail=detail) from error
    return {"items": items, "next_cursor": next_cursor, "has_more": has_more}


@app.get("/api/v1/callsigns/last-heard", dependencies=[Depends(require_viewer)])
def last_heard_callsigns_route(
    limit: int | None = None,
    db: Annotated[Session | None, Depends(get_db)] = None,
) -> dict[str, object]:
    return last_heard_callsigns(limit=limit, db=db)


@app.get("/api/v1/callsigns/{callsign}", dependencies=[Depends(require_viewer)])
def callsign_history_profile(
    db: Annotated[Session, Depends(get_db)], callsign: str
) -> dict[str, object]:
    try:
        profile = callsign_profile(db, callsign)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if profile is None:
        raise HTTPException(status_code=404, detail="Callsign not found")
    return profile


def _apply_mention_review(
    db: Session, mention_id: str, payload: CallsignMentionReviewRequest, principal: Principal,
    request: Request,
) -> dict[str, object]:
    try:
        mention = review_mention(
            db, mention_id, action=payload.action,
            corrected_callsign=payload.corrected_callsign,
            reviewer_identity=principal.identity,
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    db.commit()
    audit_event(
        actor=principal.identity, auth_source=principal.auth_source,
        action="callsign_mention_review", outcome="success", request=request,
        detail=f"mention_id={mention.id};action={mention.review_status}",
    )
    return {"mention_id": mention.id, "canonical_callsign": mention.canonical_callsign,
            "review_status": mention.review_status, "reviewer_identity": mention.reviewer_identity,
            "reviewed_at": mention.reviewed_at.isoformat() if mention.reviewed_at else None}


@app.patch("/api/v1/callsign-mentions/{mention_id}", dependencies=[Depends(require_api_operator)])
def review_callsign_mention_api(
    db: Annotated[Session, Depends(get_db)], request: Request, mention_id: str,
    payload: CallsignMentionReviewRequest, principal: Annotated[Principal, Depends(require_api_operator)],
) -> dict[str, object]:
    return _apply_mention_review(db, mention_id, payload, principal, request)


@app.patch("/ui/callsign-mentions/{mention_id}", dependencies=[Depends(require_ui_operator)])
def review_callsign_mention_ui(
    db: Annotated[Session, Depends(get_db)], request: Request, mention_id: str,
    payload: CallsignMentionReviewRequest, principal: Annotated[Principal, Depends(require_ui_operator)],
) -> dict[str, object]:
    return _apply_mention_review(db, mention_id, payload, principal, request)


@app.post("/api/v1/callsigns/{callsign}/qrz-refresh", dependencies=[Depends(require_api_operator)])
def refresh_callsign_qrz_api(
    db: Annotated[Session, Depends(get_db)], request: Request, callsign: str,
    principal: Annotated[Principal, Depends(require_api_operator)],
) -> dict[str, object]:
    try:
        normalized = canonical_callsign(callsign)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    client = current_qrz_client()
    if client is None:
        raise HTTPException(status_code=503, detail="QRZ is not configured")
    try:
        details = client.lookup(normalized)
        stored = update_qrz_snapshot(
            db, normalized, details, cache_seconds=settings.qrz_cache_seconds
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except QrzError as error:
        raise HTTPException(status_code=502, detail="QRZ lookup failed") from error
    db.commit()
    audit_event(
        actor=principal.identity, auth_source=principal.auth_source,
        action="callsign_qrz_refresh", outcome="success", request=request,
        detail=f"callsign={stored.normalized_callsign};status={stored.qrz_status}",
    )
    return callsign_profile(db, stored.normalized_callsign) or {}


@app.post("/ui/callsigns/{callsign}/qrz-refresh", dependencies=[Depends(require_ui_operator)])
def refresh_callsign_qrz_ui(
    db: Annotated[Session, Depends(get_db)], request: Request, callsign: str,
    principal: Annotated[Principal, Depends(require_ui_operator)],
) -> dict[str, object]:
    return refresh_callsign_qrz_api(db, request, callsign, principal)


def last_heard_callsigns(
    limit: int | None = None,
    db: Annotated[Session | None, Depends(get_db)] = None,
) -> dict[str, object]:
    result_limit = max(1, min(limit or settings.qrz_last_heard_limit, 100))
    if db is not None and db.bind is not None and inspect(db.bind).has_table("callsign_mentions"):
        return _last_heard_from_database(db, result_limit)
    heard: dict[str, dict[str, object]] = {}
    heard_times: dict[str, datetime | None] = {}
    observations: dict[str, list[TranscriptCallsignMention | None]] = {}
    recording_sources: dict[str, set[str]] = {}
    sources: list[tuple[str, object]] = []
    active_runtime = current_runtime()
    sources.extend(
        (source_path, result)
        for source_path, result in active_runtime.live_results.items()
    )
    sources.extend(
        (job.source_path, active_runtime.results[job.id])
        for job in active_runtime.jobs()
        if job.id in active_runtime.results
    )

    for source_path, result in sources:
        started_at_value = recording_timestamp(source_path)
        started_at = datetime.fromisoformat(started_at_value) if started_at_value else None
        mentions = getattr(result, "callsign_mentions", None) or []
        candidates: list[
            tuple[
                str,
                datetime | None,
                float | None,
                str,
                TranscriptCallsignMention | None,
            ]
        ] = []
        if mentions:
            candidates.extend(
                (
                    mention.callsign,
                    started_at + timedelta(seconds=max(0.0, mention.end))
                    if started_at
                    else None,
                    max(0.0, mention.end),
                    "segment",
                    mention,
                )
                for mention in mentions
            )
        else:
            candidates.extend(
                (callsign, started_at, None, "recording", None)
                for callsign in extract_callsigns(str(getattr(result, "display_text", "")))
            )

        for callsign, last_heard_at, offset, precision, mention in candidates:
            observations.setdefault(callsign, []).append(mention)
            recording_sources.setdefault(callsign, set()).add(source_path)
            current_time = heard_times.get(callsign)
            if callsign in heard and (
                current_time is not None
                and (last_heard_at is None or current_time >= last_heard_at)
            ):
                continue
            item: dict[str, object] = {
                "callsign": callsign,
                "last_heard_at": last_heard_at.isoformat() if last_heard_at else None,
                "source_path": source_path,
            }
            if offset is not None:
                item["heard_offset_seconds"] = offset
                item["time_precision"] = precision
            heard[callsign] = item
            heard_times[callsign] = last_heard_at

    return _serialize_runtime_last_heard(heard, heard_times, observations, recording_sources, result_limit)


def _last_heard_from_database(db: Session, result_limit: int) -> dict[str, object]:
    snapshot_now = datetime.now(UTC)
    client = current_qrz_client()
    items: list[dict[str, object]] = []
    rejected = 0
    refresh_attempts = 0
    for database_item in last_heard_rows(db, result_limit):
        expires_at = database_item.pop("qrz_cache_expires_at")
        is_current = isinstance(expires_at, datetime) and (
            expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=UTC)
        ) >= snapshot_now
        stored_qrz_status = database_item.pop("qrz_status")
        if stored_qrz_status == "not_found":
            rejected += 1
            continue
        qrz_status = stored_qrz_status if is_current else None
        if (
            qrz_status is None
            and client is not None
            and refresh_attempts < settings.qrz_last_heard_refresh_limit
        ):
            try:
                refresh_attempts += 1
                snapshot = client.lookup(str(database_item["callsign"]))
                update_qrz_snapshot(
                    db, str(database_item["callsign"]), snapshot,
                    cache_seconds=settings.qrz_cache_seconds,
                )
                db.commit()
                if snapshot.status == "not_found":
                    rejected += 1
                    continue
                qrz_status = snapshot.status
                database_item["qrz_display_name"] = snapshot.name
                database_item["qrz_location"] = snapshot.location
                database_item["qrz_image_url"] = snapshot.image_url
                database_item["qrz_profile_url"] = snapshot.profile_url
            except QrzError as error:
                logger.warning("QRZ lookup failed for %s: %s", database_item["callsign"], error)
                client = None
        best_value = database_item.pop("_best_observation")
        observation_count = database_item["observation_count"]
        recording_count = database_item["recording_count"]
        assert isinstance(best_value, (float, int))
        assert isinstance(observation_count, int)
        assert isinstance(recording_count, int)
        best_observation = float(best_value)
        score = callsign_confidence_score(
            best_observation,
            observation_count,
            recording_count,
            qrz_confirmed=qrz_status == "found",
        )
        evidence = [str(value) for value in database_item["evidence"]] if isinstance(database_item["evidence"], list) else []
        if qrz_status == "found":
            evidence.insert(0, "QRZ confirms this callsign exists")
        items.append({
            **database_item,
            "status": qrz_status or "unavailable",
            "name": database_item.pop("qrz_display_name") if qrz_status else None,
            "location": database_item.pop("qrz_location") if qrz_status else None,
            "image_url": database_item.pop("qrz_image_url") if qrz_status else None,
            "profile_url": database_item.pop("qrz_profile_url") if qrz_status else None,
            "confidence": round(score, 3),
            "confidence_percent": round(score * 100),
            "confidence_label": callsign_confidence_label(score),
            "evidence": evidence[:6],
        })
    confirmed_callsigns = {str(item["callsign"]) for item in items if item["status"] == "found"}
    latest_recordings = {
        str(item["callsign"]): item["_recording_id"] for item in items if item["status"] == "found"
    }
    superseded = sum(
        1
        for item in items
        if any(
            longer in confirmed_callsigns
            and longer != str(item["callsign"])
            and longer.startswith(str(item["callsign"]))
            and len(longer) - len(str(item["callsign"])) <= 2
            and item["_recording_id"] == latest_recordings[longer]
            for longer in confirmed_callsigns
        )
    )
    items = [
        item for item in items
        if not any(
            longer in confirmed_callsigns
            and longer != str(item["callsign"])
            and longer.startswith(str(item["callsign"]))
            and len(longer) - len(str(item["callsign"])) <= 2
            and item["_recording_id"] == latest_recordings[longer]
            for longer in confirmed_callsigns
        )
    ][:result_limit]
    for item in items:
        item.pop("_recording_id", None)
        item.pop("qrz_display_name", None)
        item.pop("qrz_location", None)
        item.pop("qrz_image_url", None)
        item.pop("qrz_profile_url", None)
    return {
        "configured": client is not None,
        "total": len(items),
        "rejected": rejected,
        "superseded": superseded,
        "items": items,
    }


def _serialize_runtime_last_heard(
    heard: dict[str, dict[str, object]], heard_times: dict[str, datetime | None],
    observations: dict[str, list[TranscriptCallsignMention | None]],
    recording_sources: dict[str, set[str]], result_limit: int,
) -> dict[str, object]:
    for callsign, item in heard.items():
        mentions = observations[callsign]
        timed_mentions = [mention for mention in mentions if mention is not None]
        best_observation = max(
            (mention.confidence for mention in timed_mentions), default=0.45
        )
        observation_count = len(mentions)
        recording_count = len(recording_sources[callsign])
        score = callsign_confidence_score(
            best_observation,
            observation_count,
            recording_count,
        )
        evidence = list(
            dict.fromkeys(
                detail
                for mention in sorted(
                    timed_mentions, key=lambda value: value.confidence, reverse=True
                )
                for detail in mention.evidence
            )
        )
        if observation_count > 1:
            evidence.append(
                f"Heard {observation_count} times across {recording_count} recording"
                f"{'s' if recording_count != 1 else ''}"
            )
        if not timed_mentions:
            evidence.append("Older transcript without saved acoustic evidence")
        acoustic_values = [
            mention.acoustic_confidence
            for mention in timed_mentions
            if mention.acoustic_confidence is not None
        ]
        item.update(
            {
                "confidence": round(score, 3),
                "confidence_percent": round(score * 100),
                "confidence_label": callsign_confidence_label(score),
                "observation_count": observation_count,
                "recording_count": recording_count,
                "acoustic_quality_percent": (
                    round(max(acoustic_values) * 100) if acoustic_values else None
                ),
                "evidence": evidence[:6],
                "_best_observation": best_observation,
            }
        )

    possible_extensions: dict[str, list[str]] = {}
    for shorter, item in heard.items():
        extensions = [
            longer
            for longer in heard
            if longer != shorter
            and longer.startswith(shorter)
            and len(longer) - len(shorter) <= 2
            and recording_sources[shorter] & recording_sources[longer]
        ]
        if not extensions:
            continue
        possible_extensions[shorter] = sorted(extensions, key=len, reverse=True)
        raw_confidence = item["confidence"]
        raw_evidence = item["evidence"]
        assert isinstance(raw_confidence, (float, int))
        assert isinstance(raw_evidence, list)
        partial_score = min(float(raw_confidence), 0.59)
        item.update(
            {
                "confidence": round(partial_score, 3),
                "confidence_percent": round(partial_score * 100),
                "confidence_label": "Possible partial",
                "needs_review": True,
                "possible_extension": possible_extensions[shorter][0],
                "evidence": [
                    f"Later audio may extend this to {possible_extensions[shorter][0]}",
                    *raw_evidence,
                ][:6],
            }
        )

    sorted_heard = sorted(
        heard.values(),
        key=lambda item: heard_times[str(item["callsign"])]
        or datetime.min.replace(tzinfo=UTC),
        reverse=True,
    )
    client = current_qrz_client()
    if client is None:
        recent = sorted_heard[:result_limit]
        for item in recent:
            item.pop("_best_observation", None)
        return {"configured": False, "total": len(heard), "items": recent}

    items: list[dict[str, object]] = []
    rejected = 0
    lookup_error: str | None = None
    selected_callsigns = {
        str(item["callsign"]) for item in sorted_heard[:result_limit]
    }
    selected_callsigns.update(
        extension
        for callsign in tuple(selected_callsigns)
        for extension in possible_extensions.get(callsign, [])
    )
    for item in (
        candidate
        for candidate in sorted_heard[:100]
        if str(candidate["callsign"]) in selected_callsigns
    ):
        if lookup_error is not None:
            item.pop("_best_observation", None)
            items.append({**item, "status": "error", "error": lookup_error})
            continue
        try:
            details = client.lookup(str(item["callsign"])).serialize()
            if details["status"] == "not_found":
                rejected += 1
                continue
            best_value = item.pop("_best_observation")
            observation_value = item["observation_count"]
            recording_value = item["recording_count"]
            raw_evidence = item["evidence"]
            assert isinstance(best_value, (float, int))
            assert isinstance(observation_value, int)
            assert isinstance(recording_value, int)
            assert isinstance(raw_evidence, list)
            score = callsign_confidence_score(
                float(best_value),
                observation_value,
                recording_value,
                qrz_confirmed=True,
            )
            if item.get("needs_review") is True:
                score = min(score, 0.59)
            evidence = [str(value) for value in raw_evidence]
            evidence.insert(0, "QRZ confirms this callsign exists")
            items.append(
                {
                    **item,
                    **details,
                    "confidence": round(score, 3),
                    "confidence_percent": round(score * 100),
                    "confidence_label": callsign_confidence_label(score),
                    "evidence": evidence[:6],
                }
            )
        except QrzError as error:
            logger.warning("QRZ lookup failed for %s: %s", item["callsign"], error)
            lookup_error = str(error)
            item.pop("_best_observation", None)
            items.append({**item, "status": "error", "error": lookup_error})
    confirmed_callsigns = {
        str(item["callsign"]) for item in items if item.get("status") == "found"
    }
    superseded = sum(
        1
        for item in items
        if item.get("status") == "found"
        and any(
            extension in confirmed_callsigns
            for extension in possible_extensions.get(str(item["callsign"]), [])
        )
    )
    items = [
        item
        for item in items
        if not (
            item.get("status") == "found"
            and any(
                extension in confirmed_callsigns
                for extension in possible_extensions.get(str(item["callsign"]), [])
            )
        )
    ][:result_limit]
    return {
        "configured": True,
        "total": len(items),
        "rejected": rejected,
        "superseded": superseded,
        "items": items,
    }


@app.get("/api/v1/events")
async def events(request: Request, principal: Viewer) -> StreamingResponse:
    active_runtime = current_runtime()
    event_queue = active_runtime.subscribe()
    await sse_connections.acquire(principal.subject)

    async def stream() -> AsyncIterator[str]:
        try:
            yield "event: ready\ndata: {}\n\n"
            while True:
                if await request.is_disconnected():
                    return
                try:
                    payload = await asyncio.to_thread(event_queue.get, True, 15)
                    yield f"event: job\ndata: {json.dumps(payload)}\n\n"
                except Empty:
                    yield ": heartbeat\n\n"
        finally:
            active_runtime.unsubscribe(event_queue)
            await sse_connections.release(principal.subject)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
