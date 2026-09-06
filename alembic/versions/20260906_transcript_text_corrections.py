"""Retain operator corrections to transcript text.

Revision ID: transcript_text_corrections
Revises: current_callsign_mentions
"""

import sqlalchemy as sa
from alembic import op

revision = "transcript_text_corrections"
down_revision = "current_callsign_mentions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "transcripts",
        sa.Column("text_corrections_json", sa.Text(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("transcripts", "text_corrections_json")
