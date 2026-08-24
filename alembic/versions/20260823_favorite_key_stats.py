"""Persist per-remote key-up totals used by the favorites list.

Revision ID: favorite_key_stats
Revises: node_control_foundation
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "favorite_key_stats"
down_revision = "node_control_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("remote_node_stats"):
        return
    op.create_table(
        "remote_node_stats",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("home_node", sa.String(length=32), nullable=False),
        sa.Column("remote_identifier", sa.String(length=64), nullable=False),
        sa.Column("keyup_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tx_milliseconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active_keyed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_keyed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_unkeyed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "home_node", "remote_identifier", name="uq_remote_stat_home_target"
        ),
    )
    op.create_index(
        op.f("ix_remote_node_stats_home_node"), "remote_node_stats", ["home_node"]
    )
    op.create_index(
        op.f("ix_remote_node_stats_remote_identifier"),
        "remote_node_stats",
        ["remote_identifier"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("remote_node_stats"):
        return
    op.drop_index(op.f("ix_remote_node_stats_remote_identifier"), table_name="remote_node_stats")
    op.drop_index(op.f("ix_remote_node_stats_home_node"), table_name="remote_node_stats")
    op.drop_table("remote_node_stats")
