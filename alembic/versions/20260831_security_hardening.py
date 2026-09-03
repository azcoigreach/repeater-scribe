"""Add authentication tokens, browser sessions, and security audits.

Revision ID: security_hardening
Revises: callsign_mention_timing
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "security_hardening"
down_revision = "callsign_mention_timing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "auth_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("identity", sa.String(255), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("csrf_token", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_auth_sessions_subject", "auth_sessions", ["subject"])
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])
    op.create_table(
        "oidc_login_states",
        sa.Column("state_hash", sa.String(64), primary_key=True),
        sa.Column("nonce", sa.String(128), nullable=False),
        sa.Column("code_verifier", sa.String(128), nullable=False),
        sa.Column("next_path", sa.String(1024), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_oidc_login_states_expires_at", "oidc_login_states", ["expires_at"])
    op.create_table(
        "api_tokens",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_api_tokens_token_hash", "api_tokens", ["token_hash"])
    op.create_table(
        "security_audits",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("auth_source", sa.String(32), nullable=False),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("method", sa.String(16)), sa.Column("path", sa.String(1024)),
        sa.Column("client_ip", sa.String(64)), sa.Column("detail", sa.Text()),
    )
    op.create_index("ix_security_audits_occurred_at", "security_audits", ["occurred_at"])
    op.create_index("ix_security_audits_action", "security_audits", ["action"])


def downgrade() -> None:
    for table_name in ("security_audits", "api_tokens", "oidc_login_states", "auth_sessions"):
        op.drop_table(table_name)
