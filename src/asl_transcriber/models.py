from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from asl_transcriber.database import Base


class Node(Base):
    __tablename__ = "nodes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    local_node: Mapped[int | None] = mapped_column(Integer, nullable=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    recordings: Mapped[list[Recording]] = relationship(back_populates="node")


class Recording(Base):
    __tablename__ = "recordings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    source_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(64), default="pending", index=True)
    local_node: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_node: Mapped[int | None] = mapped_column(Integer, nullable=True)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    node_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("nodes.id"), nullable=True)

    node: Mapped[Node | None] = relationship(back_populates="recordings")
    transmissions: Mapped[list[Transmission]] = relationship(back_populates="recording")


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    source_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    archive_root: Mapped[str | None] = mapped_column(String(1024), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(64), default="pending", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    dead_letter: Mapped[bool] = mapped_column(Boolean, default=False)
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    transcript: Mapped[Transcript | None] = relationship(back_populates="job", uselist=False)


class Transcript(Base):
    __tablename__ = "transcripts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    job_id: Mapped[str] = mapped_column(String(36), ForeignKey("ingestion_jobs.id"), unique=True)
    raw_text: Mapped[str] = mapped_column(Text, default="")
    display_text: Mapped[str] = mapped_column(Text, default="")
    language: Mapped[str | None] = mapped_column(String(32), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    callsign_mentions_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)

    job: Mapped[IngestionJob] = relationship(back_populates="transcript")


class Transmission(Base):
    __tablename__ = "transmissions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    recording_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("recordings.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(64), default="discovered", index=True)
    source_node: Mapped[int | None] = mapped_column(Integer, nullable=True)
    local_node: Mapped[int | None] = mapped_column(Integer, nullable=True)
    start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    home_node: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    source_identifier: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    associated_node_callsign: Mapped[str | None] = mapped_column(String(32), nullable=True)
    operator_callsign: Mapped[str | None] = mapped_column(String(32), nullable=True)
    attribution_level: Mapped[str] = mapped_column(String(32), default="unknown")
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_milliseconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    close_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    collision: Mapped[bool] = mapped_column(Boolean, default=False)
    raw_event_metadata: Mapped[str | None] = mapped_column(Text, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    recording: Mapped[Recording | None] = relationship(back_populates="transmissions")


class Favorite(Base):
    __tablename__ = "favorites"
    __table_args__ = (
        UniqueConstraint("home_node", "target_identifier", name="uq_favorite_home_target"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    home_node: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    target_identifier: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    callsign_override: Mapped[str | None] = mapped_column(String(32), nullable=True)
    description_override: Mapped[str | None] = mapped_column(String(255), nullable=True)
    location_override: Mapped[str | None] = mapped_column(String(255), nullable=True)
    group_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    default_connection_mode: Mapped[str] = mapped_column(String(32), default="transceive")
    permanent: Mapped[bool] = mapped_column(Boolean, default=False)
    exclusive_connect: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class RemoteNodeStat(Base):
    __tablename__ = "remote_node_stats"
    __table_args__ = (
        UniqueConstraint("home_node", "remote_identifier", name="uq_remote_stat_home_target"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    home_node: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    remote_identifier: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    keyup_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_tx_milliseconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    active_keyed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_keyed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_unkeyed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class FavoriteStatsSnapshot(Base):
    __tablename__ = "favorite_stats_snapshots"
    __table_args__ = (
        UniqueConstraint("home_node", "remote_identifier", name="uq_favorite_snapshot_home_target"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    home_node: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    remote_identifier: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    callsign: Mapped[str | None] = mapped_column(String(32), nullable=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    keyed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    total_keyups: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_tx_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_kerchunks: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    uptime_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    link_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    topology_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    source_reported_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_activity_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


class TopologyNodeSnapshot(Base):
    __tablename__ = "topology_node_snapshots"
    __table_args__ = (
        UniqueConstraint("home_node", "identifier", name="uq_topology_node_home_identifier"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    home_node: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    identifier: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    neighbors_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    keyed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    total_keyups: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_tx_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_kerchunks: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    uptime_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    source_reported_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class TopologyEdgeSnapshot(Base):
    __tablename__ = "topology_edge_snapshots"
    __table_args__ = (
        UniqueConstraint("home_node", "node_a", "node_b", name="uq_topology_edge_home_pair"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    home_node: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    node_a: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    node_b: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    reporters_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    modes_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    stale_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TopologyCrawl(Base):
    __tablename__ = "topology_crawls"
    __table_args__ = (
        UniqueConstraint("home_node", "root_identifier", name="uq_topology_crawl_home_root"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    home_node: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    root_identifier: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False, index=True)
    queue_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    seen_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    processed_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    queried_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_nodes: Mapped[int] = mapped_column(Integer, default=200, nullable=False)
    max_depth: Mapped[int] = mapped_column(Integer, default=12, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_refresh_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    limit_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class LinkSession(Base):
    __tablename__ = "link_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    home_node: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    remote_identifier: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    link_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    direction: Mapped[str | None] = mapped_column(String(32), nullable=True)
    connected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    disconnect_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)


class ControlAudit(Base):
    __tablename__ = "control_audits"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    identity: Mapped[str] = mapped_column(String(255), nullable=False)
    home_node: Mapped[str] = mapped_column(String(32), nullable=False)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    target: Mapped[str | None] = mapped_column(String(64), nullable=True)
    requested_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    ami_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    confirmation_result: Mapped[str | None] = mapped_column(String(32), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    subject: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    identity: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    csrf_token: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )


class OidcLoginState(Base):
    __tablename__ = "oidc_login_states"

    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    nonce: Mapped[str] = mapped_column(String(128), nullable=False)
    code_verifier: Mapped[str] = mapped_column(String(128), nullable=False)
    next_path: Mapped[str] = mapped_column(String(1024), default="/", nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )


class ApiToken(Base):
    __tablename__ = "api_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SecurityAudit(Base):
    __tablename__ = "security_audits"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False, index=True
    )
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    auth_source: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    method: Mapped[str | None] = mapped_column(String(16), nullable=True)
    path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    client_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
