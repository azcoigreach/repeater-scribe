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
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
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
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
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
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
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
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )
    job_id: Mapped[str] = mapped_column(String(36), ForeignKey("ingestion_jobs.id"), unique=True)
    raw_text: Mapped[str] = mapped_column(Text, default="")
    display_text: Mapped[str] = mapped_column(Text, default="")
    language: Mapped[str | None] = mapped_column(String(32), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    job: Mapped[IngestionJob] = relationship(back_populates="transcript")


class Transmission(Base):
    __tablename__ = "transmissions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )
    recording_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("recordings.id"), nullable=True)
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
    __table_args__ = (UniqueConstraint("home_node", "target_identifier", name="uq_favorite_home_target"),)

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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
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
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )


class LinkSession(Base):
    __tablename__ = "link_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    home_node: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    remote_identifier: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    link_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    direction: Mapped[str | None] = mapped_column(String(32), nullable=True)
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
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
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    ami_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    confirmation_result: Mapped[str | None] = mapped_column(String(32), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
