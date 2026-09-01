"""Add authentication tokens, browser sessions, and security audits.

Revision ID: security_hardening
Revises: callsign_mention_timing
"""

from __future__ import annotations

from alembic import op

from asl_transcriber.models import Base

revision = "security_hardening"
down_revision = "callsign_mention_timing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    for table_name in ("security_audits", "api_tokens", "oidc_login_states", "auth_sessions"):
        op.drop_table(table_name)
