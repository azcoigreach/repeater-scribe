"""Create the node-control foundation tables and current application schema.

Revision ID: node_control_foundation
Revises:
"""

from __future__ import annotations

from alembic import op

from asl_transcriber.models import Base

revision = "node_control_foundation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
