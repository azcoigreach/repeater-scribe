"""Preserve the migration lineage shipped by the 0.6.0 container image.

Revision ID: callsign_mention_timing
Revises: favorite_key_stats

The associated application feature was reverted, but deployed databases may
already identify this revision as their current Alembic head. Keeping the
idempotent migration in the chain allows those databases to upgrade safely.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "callsign_mention_timing"
down_revision = "favorite_key_stats"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("transcripts")}
    if "callsign_mentions_json" not in columns:
        op.add_column(
            "transcripts",
            sa.Column(
                "callsign_mentions_json",
                sa.Text(),
                nullable=False,
                server_default="[]",
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("transcripts")}
    if "callsign_mentions_json" in columns:
        op.drop_column("transcripts", "callsign_mentions_json")
