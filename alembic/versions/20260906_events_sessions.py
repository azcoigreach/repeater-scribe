"""Durable events, membership, markers, tags and confirmed attendance.

Revision ID: events_sessions
Revises: transcript_text_corrections
"""

import sqlalchemy as sa
from alembic import op

revision = "events_sessions"
down_revision = "transcript_text_corrections"
branch_labels = None
depends_on = None


def ident(name="id", target=None, primary=False, nullable=False):
    args = [sa.ForeignKey(target)] if target else []
    return sa.Column(name, sa.String(36), *args, primary_key=primary, nullable=nullable)


def timestamp(name, nullable=False):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "radio_sessions",
        ident(primary=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("source_root", sa.String(1024), nullable=False),
        timestamp("started_at"),
        timestamp("ended_at", True),
        sa.Column("net_control", sa.String(32)),
        timestamp("created_at"),
        timestamp("updated_at"),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("updated_by", sa.String(255), nullable=False),
        sa.CheckConstraint("ended_at IS NULL OR ended_at > started_at", name="ck_session_window"),
    )
    op.create_index(
        "uq_session_active_source",
        "radio_sessions",
        ["source_root"],
        unique=True,
        sqlite_where=sa.text("ended_at IS NULL"),
    )
    op.create_index(
        "ix_session_source_window", "radio_sessions", ["source_root", "started_at", "ended_at"]
    )
    op.create_index("ix_session_started", "radio_sessions", ["started_at", "id"])
    op.create_index(
        "ix_recording_source_started", "recordings", ["archive_root", "started_at", "id"]
    )
    op.create_table(
        "session_requests",
        sa.Column("actor", sa.String(255), primary_key=True),
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        ident("session_id", "radio_sessions.id"),
    )
    op.create_index("ix_session_requests_session_id", "session_requests", ["session_id"])
    op.create_table(
        "session_recordings",
        ident("session_id", "radio_sessions.id", True),
        ident("recording_id", "recordings.id", True),
        sa.Column("automatic", sa.Boolean(), nullable=False),
        sa.Column("decision", sa.String(16)),
        timestamp("updated_at"),
        sa.Column("updated_by", sa.String(255)),
        sa.CheckConstraint(
            "decision IS NULL OR decision IN ('include', 'exclude')", name="ck_membership_decision"
        ),
    )
    op.create_index("ix_membership_recording", "session_recordings", ["recording_id", "session_id"])
    op.create_table(
        "session_markers",
        ident(primary=True),
        ident("session_id", "radio_sessions.id"),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("callsign", sa.String(32)),
        timestamp("at"),
        ident("recording_id", "recordings.id", nullable=True),
        sa.Column("audio_offset", sa.Float()),
        timestamp("created_at"),
        timestamp("updated_at"),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("updated_by", sa.String(255), nullable=False),
        sa.CheckConstraint(
            "audio_offset IS NULL OR (recording_id IS NOT NULL AND audio_offset >= 0)",
            name="ck_marker_offset",
        ),
    )
    op.create_index("ix_marker_session_time", "session_markers", ["session_id", "at", "id"])
    op.create_table(
        "session_checkins",
        ident(primary=True),
        ident("session_id", "radio_sessions.id"),
        ident("callsign_id", "callsigns.id"),
        timestamp("at"),
        sa.Column("note", sa.Text(), nullable=False),
        ident("recording_id", "recordings.id", nullable=True),
        sa.Column("audio_offset", sa.Float()),
        timestamp("confirmed_at"),
        sa.Column("confirmed_by", sa.String(255), nullable=False),
        timestamp("updated_at"),
        sa.Column("updated_by", sa.String(255), nullable=False),
        sa.UniqueConstraint("session_id", "callsign_id", name="uq_checkin_station"),
        sa.CheckConstraint(
            "audio_offset IS NULL OR (recording_id IS NOT NULL AND audio_offset >= 0)",
            name="ck_checkin_offset",
        ),
    )
    op.create_index("ix_checkin_session_time", "session_checkins", ["session_id", "at", "id"])
    op.create_index("ix_session_checkins_callsign_id", "session_checkins", ["callsign_id"])
    op.create_table("tags", sa.Column("name", sa.String(64), primary_key=True))
    for prefix, target in (("session", "radio_sessions"), ("recording", "recordings")):
        op.create_table(
            f"{prefix}_tags",
            ident(f"{prefix}_id", f"{target}.id", True),
            sa.Column("tag", sa.String(64), sa.ForeignKey("tags.name"), primary_key=True),
        )
        op.create_index(f"ix_{prefix}_tag_name", f"{prefix}_tags", ["tag", f"{prefix}_id"])


def downgrade() -> None:
    for table in (
        "recording_tags",
        "session_tags",
        "tags",
        "session_checkins",
        "session_markers",
        "session_recordings",
        "session_requests",
        "radio_sessions",
    ):
        op.drop_table(table)
    op.drop_index("ix_recording_source_started", "recordings")
