"""Create the durable recording catalog and SQLite transcript search index.

Revision ID: archive_foundation
Revises: security_hardening
"""

from __future__ import annotations

import re
import wave
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "archive_foundation"
down_revision = "security_hardening"
branch_labels = None
depends_on = None


def _started_at(source_path: str) -> datetime | None:
    match = re.match(r"^(\d{14})(\d{2})", Path(source_path).name)
    if match is None:
        return None
    return datetime.strptime(match.group(1), "%Y%m%d%H%M%S").replace(
        tzinfo=UTC, microsecond=int(match.group(2)) * 10000
    )


def upgrade() -> None:
    bind = op.get_bind()
    topology_columns = {
        column["name"] for column in sa.inspect(bind).get_columns("topology_node_snapshots")
    }
    if "total_kerchunks" not in topology_columns:
        with op.batch_alter_table("topology_node_snapshots") as batch:
            batch.add_column(
                sa.Column("total_kerchunks", sa.Integer(), nullable=False, server_default="0")
            )
    job_columns = {column["name"] for column in sa.inspect(bind).get_columns("ingestion_jobs")}
    if "archive_root" not in job_columns:
        with op.batch_alter_table("ingestion_jobs") as batch:
            batch.add_column(sa.Column("archive_root", sa.String(1024), nullable=True))
    job_indexes = {index["name"] for index in sa.inspect(bind).get_indexes("ingestion_jobs")}
    if "ix_ingestion_jobs_archive_root" not in job_indexes:
        op.create_index("ix_ingestion_jobs_archive_root", "ingestion_jobs", ["archive_root"])
    if "ix_ingestion_jobs_root_path" not in job_indexes:
        op.create_index(
            "ix_ingestion_jobs_root_path", "ingestion_jobs", ["archive_root", "source_path"]
        )
    with op.batch_alter_table("recordings") as batch:
        batch.add_column(sa.Column("archive_root", sa.String(1024), nullable=True))
        batch.add_column(sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("source_modified_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("audio_status", sa.String(16), nullable=False, server_default="available"))
        batch.add_column(sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_unique_constraint("uq_recording_root_path", ["archive_root", "source_path"])
    op.create_index("ix_recordings_archive_root", "recordings", ["archive_root"])
    op.create_index("ix_recordings_started_at", "recordings", ["started_at"])
    op.create_index("ix_recordings_audio_status", "recordings", ["audio_status"])
    with op.batch_alter_table("ingestion_jobs") as batch:
        batch.add_column(sa.Column("recording_id", sa.String(36), nullable=True))
        batch.create_foreign_key("fk_ingestion_jobs_recording", "recordings", ["recording_id"], ["id"])
        batch.create_index("ix_ingestion_jobs_recording_id", ["recording_id"])
    with op.batch_alter_table("transcripts") as batch:
        batch.add_column(sa.Column("recording_id", sa.String(36), nullable=True))
        batch.create_foreign_key("fk_transcripts_recording", "recordings", ["recording_id"], ["id"])
        batch.create_index("ix_transcripts_recording_id", ["recording_id"])
        batch.create_unique_constraint("uq_transcript_job", ["job_id"])

    rows = bind.execute(sa.text("SELECT id, source_path, archive_root, status, created_at FROM ingestion_jobs")).mappings()
    for row in rows:
        root = row["archive_root"] or ""
        source = Path(root) / row["source_path"]
        stat = source.stat() if root and source.is_file() else None
        duration = None
        if stat is not None:
            try:
                with wave.open(str(source), "rb") as audio:
                    duration = audio.getnframes() / audio.getframerate()
            except (EOFError, OSError, wave.Error, ZeroDivisionError):
                pass
        bind.execute(
            sa.text("INSERT INTO recordings (id, created_at, updated_at, source_path, archive_root, started_at, source_modified_at, status, file_size, duration_seconds, audio_status) VALUES (:id, :created, :created, :path, :root, :started, :modified, :status, :size, :duration, :audio_status) ON CONFLICT(id) DO UPDATE SET source_path = excluded.source_path, archive_root = excluded.archive_root, started_at = excluded.started_at, source_modified_at = excluded.source_modified_at, status = excluded.status, file_size = excluded.file_size, duration_seconds = excluded.duration_seconds, audio_status = excluded.audio_status"),
            {"id": row["id"], "created": row["created_at"], "path": row["source_path"], "root": root, "started": _started_at(row["source_path"]), "modified": datetime.fromtimestamp(stat.st_mtime, UTC) if stat else None, "status": row["status"], "size": stat.st_size if stat else None, "duration": duration, "audio_status": "available" if stat else "missing"},
        )
        bind.execute(sa.text("UPDATE ingestion_jobs SET recording_id = :id WHERE id = :id"), {"id": row["id"]})
        bind.execute(sa.text("UPDATE transcripts SET recording_id = :id WHERE job_id = :id"), {"id": row["id"]})
    op.execute("CREATE VIRTUAL TABLE transcript_fts USING fts5(raw_text, display_text, content='transcripts', content_rowid='rowid')")
    op.execute("INSERT INTO transcript_fts(rowid, raw_text, display_text) SELECT rowid, raw_text, display_text FROM transcripts")
    op.execute("CREATE TRIGGER transcript_fts_insert AFTER INSERT ON transcripts BEGIN INSERT INTO transcript_fts(rowid, raw_text, display_text) VALUES (new.rowid, new.raw_text, new.display_text); END")
    op.execute("CREATE TRIGGER transcript_fts_delete AFTER DELETE ON transcripts BEGIN INSERT INTO transcript_fts(transcript_fts, rowid, raw_text, display_text) VALUES ('delete', old.rowid, old.raw_text, old.display_text); END")
    op.execute("CREATE TRIGGER transcript_fts_update AFTER UPDATE OF raw_text, display_text ON transcripts BEGIN INSERT INTO transcript_fts(transcript_fts, rowid, raw_text, display_text) VALUES ('delete', old.rowid, old.raw_text, old.display_text); INSERT INTO transcript_fts(rowid, raw_text, display_text) VALUES (new.rowid, new.raw_text, new.display_text); END")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS transcript_fts_update")
    op.execute("DROP TRIGGER IF EXISTS transcript_fts_delete")
    op.execute("DROP TRIGGER IF EXISTS transcript_fts_insert")
    op.execute("DROP TABLE IF EXISTS transcript_fts")
    with op.batch_alter_table("transcripts") as batch:
        batch.drop_index("ix_transcripts_recording_id")
        batch.drop_constraint("fk_transcripts_recording", type_="foreignkey")
        batch.drop_column("recording_id")
    with op.batch_alter_table("ingestion_jobs") as batch:
        batch.drop_index("ix_ingestion_jobs_recording_id")
        batch.drop_index("ix_ingestion_jobs_root_path")
        batch.drop_constraint("fk_ingestion_jobs_recording", type_="foreignkey")
        batch.drop_column("recording_id")
    with op.batch_alter_table("recordings") as batch:
        batch.drop_index("ix_recordings_audio_status")
        batch.drop_index("ix_recordings_started_at")
        batch.drop_index("ix_recordings_archive_root")
        batch.drop_constraint("uq_recording_root_path", type_="unique")
        batch.drop_column("expired_at")
        batch.drop_column("audio_status")
        batch.drop_column("source_modified_at")
        batch.drop_column("started_at")
        batch.drop_column("archive_root")