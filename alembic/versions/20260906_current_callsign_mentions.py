"""Track superseded reviewed callsign mentions.

Revision ID: current_callsign_mentions
Revises: transmission_duration_milliseconds
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "current_callsign_mentions"
down_revision = "transmission_duration_milliseconds"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("callsign_mentions")}
    if "is_current" not in columns:
        op.add_column(
            "callsign_mentions",
            sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.true()),
        )
    if not any(index["name"] == "ix_callsign_mentions_is_current" for index in sa.inspect(op.get_bind()).get_indexes("callsign_mentions")):
        op.create_index("ix_callsign_mentions_is_current", "callsign_mentions", ["is_current"])


def downgrade() -> None:
    op.drop_index("ix_callsign_mentions_is_current", table_name="callsign_mentions")
    op.drop_column("callsign_mentions", "is_current")