"""Repair legacy transmission duration schema.

Revision ID: transmission_duration_milliseconds
Revises: callsign_intelligence
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "transmission_duration_milliseconds"
down_revision = "callsign_intelligence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("transmissions")}
    if "duration_milliseconds" not in columns:
        op.add_column("transmissions", sa.Column("duration_milliseconds", sa.Integer(), nullable=True))


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("transmissions")}
    if "duration_milliseconds" in columns:
        op.drop_column("transmissions", "duration_milliseconds")