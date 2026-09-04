"""Persist normalized callsign history and transcript segments.

Revision ID: callsign_intelligence
Revises: archive_foundation
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta
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
    from asl_transcriber.transcription.callsigns import normalize_callsigns

    if not isinstance(value, str):
        return None
    normalized = normalize_callsigns((value,))
    return normalized[0] if normalized else None


def upgrade() -> None:
    op.add_column(
        "recordings",
        sa.Column("current_transcript_id", sa.String(36), nullable=True),
    )
    op.create_index(
        "ix_recordings_current_transcript_id", "recordings", ["current_transcript_id"]
    )
    with op.batch_alter_table("recordings") as batch:
        batch.create_foreign_key(
            "fk_recordings_current_transcript",
            "transcripts",
            ["current_transcript_id"],
            ["id"],
        )

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
    op.create_index("ix_callsigns_normalized_callsign", "callsigns", ["normalized_callsign"])

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
    op.create_index("ix_transcript_segments_transcript_id", "transcript_segments", ["transcript_id"])
    op.create_index("ix_transcript_segments_recording", "transcript_segments", ["recording_id"])

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
    ):
        op.create_index(name, "callsign_mentions", columns)

    bind = op.get_bind()
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
        started = bind.execute(
            sa.text("SELECT started_at FROM recordings WHERE id = :recording_id"),
            {"recording_id": recording_id},
        ).scalar()
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
            end_offset = item.get("end")
            try:
                end_value = float(end_offset) if end_offset is not None else None
            except (TypeError, ValueError):
                end_value = None
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
                    "start_offset": item.get("start"), "end_offset": end_value, "heard_at": heard_at,
                    "precision": precision, "confidence": item.get("confidence"),
                    "acoustic": item.get("acoustic_confidence"), "recognition": item.get("recognition_confidence"),
                    "evidence": json.dumps(evidence),
                },
            )


def downgrade() -> None:
    op.drop_table("callsign_mentions")
    op.drop_table("transcript_segments")
    op.drop_index("ix_callsigns_normalized_callsign", table_name="callsigns")
    op.drop_table("callsigns")
    with op.batch_alter_table("recordings") as batch:
        batch.drop_constraint("fk_recordings_current_transcript", type_="foreignkey")
        batch.drop_index("ix_recordings_current_transcript_id")
        batch.drop_column("current_transcript_id")