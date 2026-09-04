"""Persist normalized callsign history and transcript segments.

Revision ID: callsign_intelligence
Revises: archive_foundation
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "callsign_intelligence"
down_revision = "archive_foundation"
branch_labels = None
depends_on = None

logger = logging.getLogger(__name__)


def _new_id() -> str:
    return str(uuid4())


def _normalize(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    for choice in value.upper().split("/"):
        candidate = re.sub(r"[^A-Z0-9]", "", choice)
        if re.fullmatch(r"[A-Z0-9]{1,3}\d[A-Z]{1,4}", candidate) and any(
            symbol.isalpha() for symbol in candidate[:3]
        ):
            return candidate
    return None


def _datetime_value(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def _float_value(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _has_table(bind: sa.Connection, name: str) -> bool:
    return sa.inspect(bind).has_table(name)


def _has_index(bind: sa.Connection, table: str, name: str) -> bool:
    return any(index["name"] == name for index in sa.inspect(bind).get_indexes(table))


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        # SQLite batch migrations recreate recordings, which cannot be dropped
        # while its existing dependents are enforced. Re-enable before backfill.
        bind.exec_driver_sql("PRAGMA foreign_keys=OFF")
    recording_columns = {column["name"] for column in sa.inspect(bind).get_columns("recordings")}
    if "current_transcript_id" not in recording_columns:
        with op.batch_alter_table("recordings") as batch:
            batch.add_column(sa.Column("current_transcript_id", sa.String(36), nullable=True))
    if not _has_index(bind, "recordings", "ix_recordings_current_transcript_id"):
        op.create_index("ix_recordings_current_transcript_id", "recordings", ["current_transcript_id"])
    recording_fks = {foreign_key["name"] for foreign_key in sa.inspect(bind).get_foreign_keys("recordings")}
    if "fk_recordings_current_transcript" not in recording_fks:
        with op.batch_alter_table("recordings") as batch:
            batch.create_foreign_key(
                "fk_recordings_current_transcript", "transcripts", ["current_transcript_id"], ["id"]
            )

    if not _has_table(bind, "callsigns"):
        op.create_table(
            "callsigns",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("normalized_callsign", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("qrz_status", sa.String(32), nullable=True),
        sa.Column("qrz_display_name", sa.String(255), nullable=True),
        sa.Column("qrz_location", sa.String(255), nullable=True),
        sa.Column("qrz_image_url", sa.String(1024), nullable=True),
        sa.Column("qrz_profile_url", sa.String(1024), nullable=True),
        sa.Column("qrz_lookup_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("qrz_cache_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("normalized_callsign", name="uq_callsigns_normalized"),
        )
    if not _has_index(bind, "callsigns", "ix_callsigns_normalized_callsign"):
        op.create_index("ix_callsigns_normalized_callsign", "callsigns", ["normalized_callsign"])

    if not _has_table(bind, "transcript_segments"):
        op.create_table(
            "transcript_segments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("transcript_id", sa.String(36), nullable=False),
        sa.Column("recording_id", sa.String(36), nullable=True),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("start_offset", sa.Float(), nullable=False),
        sa.Column("end_offset", sa.Float(), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("display_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("language", sa.String(32), nullable=True),
        sa.Column("avg_logprob", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["transcript_id"], ["transcripts.id"]),
        sa.ForeignKeyConstraint(["recording_id"], ["recordings.id"]),
        sa.UniqueConstraint("transcript_id", "ordinal", name="uq_transcript_segments_ordinal"),
        )
    if not _has_index(bind, "transcript_segments", "ix_transcript_segments_transcript_id"):
        op.create_index("ix_transcript_segments_transcript_id", "transcript_segments", ["transcript_id"])
    if not _has_index(bind, "transcript_segments", "ix_transcript_segments_recording_id"):
        op.create_index("ix_transcript_segments_recording_id", "transcript_segments", ["recording_id"])

    if not _has_table(bind, "callsign_mentions"):
        op.create_table(
            "callsign_mentions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("callsign_id", sa.String(36), nullable=False),
        sa.Column("transcript_id", sa.String(36), nullable=False),
        sa.Column("recording_id", sa.String(36), nullable=False),
        sa.Column("segment_id", sa.String(36), nullable=True),
        sa.Column("raw_observed_value", sa.String(64), nullable=True),
        sa.Column("canonical_callsign", sa.String(32), nullable=False),
        sa.Column("start_offset", sa.Float(), nullable=True),
        sa.Column("end_offset", sa.Float(), nullable=True),
        sa.Column("heard_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("timing_precision", sa.String(16), nullable=False, server_default="recording"),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("acoustic_confidence", sa.Float(), nullable=True),
        sa.Column("recognition_confidence", sa.Float(), nullable=True),
        sa.Column("recognition_method", sa.String(32), nullable=False, server_default="legacy"),
        sa.Column("evidence_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("qrz_validation_status", sa.String(32), nullable=True),
        sa.Column("review_status", sa.String(16), nullable=False, server_default="detected"),
        sa.Column("reviewer_identity", sa.String(255), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["callsign_id"], ["callsigns.id"]),
        sa.ForeignKeyConstraint(["transcript_id"], ["transcripts.id"]),
        sa.ForeignKeyConstraint(["recording_id"], ["recordings.id"]),
        sa.ForeignKeyConstraint(["segment_id"], ["transcript_segments.id"]),
        )
    for name, columns in (
        ("ix_callsign_mentions_callsign_id", ["callsign_id"]),
        ("ix_callsign_mentions_recording", ["recording_id"]),
        ("ix_callsign_mentions_transcript", ["transcript_id"]),
        ("ix_callsign_mentions_segment", ["segment_id"]),
        ("ix_callsign_mentions_heard_at", ["heard_at"]),
        ("ix_callsign_mentions_review_status", ["review_status"]),
        ("ix_callsign_mentions_callsign_heard", ["callsign_id", "heard_at"]),
        ("ix_callsign_mentions_canonical_callsign", ["canonical_callsign"]),
    ):
        if not _has_index(bind, "callsign_mentions", name):
            op.create_index(name, "callsign_mentions", columns)
    if not _has_index(bind, "transmissions", "ix_transmissions_operator_callsign"):
        op.create_index("ix_transmissions_operator_callsign", "transmissions", ["operator_callsign"])

    if _has_table(bind, "callsign_mentions"):
        bind.execute(sa.text("DELETE FROM callsign_mentions"))
    recordings = bind.execute(sa.text("SELECT id FROM recordings")).all()
    for (recording_id,) in recordings:
        transcript = bind.execute(
            sa.text(
                "SELECT id FROM transcripts WHERE recording_id = :recording_id "
                "ORDER BY CASE WHEN job_id = :recording_id THEN 0 ELSE 1 END, "
                "updated_at DESC, id DESC LIMIT 1"
            ),
            {"recording_id": recording_id},
        ).first()
        if transcript is None:
            continue
        transcript_id = transcript[0]
        bind.execute(
            sa.text("UPDATE recordings SET current_transcript_id = :transcript_id WHERE id = :recording_id"),
            {"transcript_id": transcript_id, "recording_id": recording_id},
        )
        started = _datetime_value(bind.execute(
            sa.text("SELECT started_at FROM recordings WHERE id = :recording_id"),
            {"recording_id": recording_id},
        ).scalar())
        raw_json = bind.execute(
            sa.text("SELECT callsign_mentions_json FROM transcripts WHERE id = :transcript_id"),
            {"transcript_id": transcript_id},
        ).scalar()
        try:
            mentions = json.loads(raw_json or "[]")
            if not isinstance(mentions, list):
                raise TypeError("mention payload is not an array")
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            logger.warning("Skipping malformed callsign JSON for transcript %s: %s", transcript_id, error)
            continue
        for item in mentions:
            if not isinstance(item, dict):
                logger.warning("Skipping non-object callsign mention for transcript %s", transcript_id)
                continue
            callsign = _normalize(item.get("callsign"))
            if callsign is None:
                logger.warning("Skipping invalid callsign mention for transcript %s", transcript_id)
                continue
            callsign_id = bind.execute(
                sa.text("SELECT id FROM callsigns WHERE normalized_callsign = :callsign"),
                {"callsign": callsign},
            ).scalar()
            if callsign_id is None:
                callsign_id = _new_id()
                bind.execute(
                    sa.text(
                        "INSERT INTO callsigns (id, normalized_callsign, created_at, updated_at) "
                        "VALUES (:id, :callsign, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                    ),
                    {"id": callsign_id, "callsign": callsign},
                )
            start_value = _float_value(item.get("start"))
            end_value = _float_value(item.get("end"))
            heard_at = None
            precision = "recording"
            if started is not None and end_value is not None:
                heard_at = started + timedelta(seconds=max(0.0, end_value))
                precision = "segment"
            evidence = item.get("evidence", [])
            if not isinstance(evidence, list):
                evidence = []
            bind.execute(
                sa.text(
                    "INSERT INTO callsign_mentions (id, callsign_id, transcript_id, recording_id, "
                    "raw_observed_value, canonical_callsign, start_offset, end_offset, heard_at, "
                    "timing_precision, confidence, acoustic_confidence, recognition_confidence, "
                    "recognition_method, evidence_json, review_status, created_at, updated_at) "
                    "VALUES (:id, :callsign_id, :transcript_id, :recording_id, :raw_value, :callsign, "
                    ":start_offset, :end_offset, :heard_at, :precision, :confidence, :acoustic, "
                    ":recognition, 'legacy', :evidence, 'detected', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {
                    "id": _new_id(), "callsign_id": callsign_id, "transcript_id": transcript_id,
                    "recording_id": recording_id, "raw_value": item.get("callsign"), "callsign": callsign,
                    "start_offset": start_value, "end_offset": end_value, "heard_at": heard_at,
                    "precision": precision, "confidence": _float_value(item.get("confidence")),
                    "acoustic": _float_value(item.get("acoustic_confidence")),
                    "recognition": _float_value(item.get("recognition_confidence")),
                    "evidence": json.dumps(evidence),
                },
            )

    if bind.dialect.name == "sqlite":
        bind.exec_driver_sql("PRAGMA foreign_keys=ON")
    invalid_foreign_keys = bind.execute(sa.text("PRAGMA foreign_key_check")).all()
    if invalid_foreign_keys:
        raise RuntimeError(f"Foreign-key check failed after callsign backfill: {invalid_foreign_keys!r}")


def downgrade() -> None:
    op.drop_table("callsign_mentions")
    op.drop_table("transcript_segments")
    op.drop_index("ix_transmissions_operator_callsign", table_name="transmissions")
    op.drop_index("ix_callsigns_normalized_callsign", table_name="callsigns")
    op.drop_table("callsigns")
    with op.batch_alter_table("recordings") as batch:
        batch.drop_constraint("fk_recordings_current_transcript", type_="foreignkey")
        batch.drop_index("ix_recordings_current_transcript_id")
        batch.drop_column("current_transcript_id")