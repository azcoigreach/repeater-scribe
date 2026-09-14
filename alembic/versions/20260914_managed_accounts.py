"""Managed identity and current authority; legacy browser sessions must sign in again."""

from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "managed_accounts"
down_revision = "events_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("issuer", sa.String(1024), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("identity", sa.String(255), nullable=False),
        sa.Column("email", sa.String(255)),
        sa.Column("preferred_username", sa.String(255)),
        sa.Column("display_name", sa.String(255)),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("updated_by", sa.String(255), nullable=False),
        sa.Column("first_sign_in_at", sa.DateTime(timezone=True)),
        sa.Column("last_sign_in_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("issuer", "subject", name="uq_account_oidc_identity"),
        sa.CheckConstraint("role IN ('viewer', 'user', 'admin')", name="ck_account_role"),
    )
    # Released sessions lack issuer evidence. Do not guess issuer/account links.
    op.execute("DELETE FROM auth_sessions")
    op.execute("DELETE FROM oidc_login_states")
    for table in ("auth_sessions", "api_tokens", "security_audits"):
        with op.batch_alter_table(table) as batch:
            batch.add_column(
                sa.Column("account_id", sa.String(36), nullable=table != "auth_sessions")
            )
            batch.create_foreign_key(f"fk_{table}_account", "accounts", ["account_id"], ["id"])
            batch.create_index(f"ix_{table}_account_id", ["account_id"])
    op.execute("UPDATE api_tokens SET role = 'user' WHERE role = 'operator'")
    audit = sa.table(
        "security_audits",
        sa.column("id"),
        sa.column("occurred_at"),
        sa.column("actor"),
        sa.column("auth_source"),
        sa.column("action"),
        sa.column("outcome"),
        sa.column("detail"),
    )
    op.bulk_insert(
        audit,
        [
            {
                "id": str(uuid4()),
                "occurred_at": datetime.now(UTC),
                "actor": "migration:managed_accounts",
                "auth_source": "migration",
                "action": "account_migration",
                "outcome": "allowed",
                "detail": "Legacy browser sessions and pending logins revoked; named token operator roles mapped to user",
            }
        ],
    )


def downgrade() -> None:
    # Rolling application code back must not restore stale session authority.
    op.execute("DELETE FROM auth_sessions")
    # Personal credentials cannot become independent credentials on downgrade.
    op.execute("UPDATE api_tokens SET enabled = 0 WHERE account_id IS NOT NULL")
    op.execute("UPDATE api_tokens SET role = 'operator' WHERE role = 'user'")
    for table in ("security_audits", "api_tokens", "auth_sessions"):
        with op.batch_alter_table(table) as batch:
            batch.drop_index(f"ix_{table}_account_id")
            batch.drop_constraint(f"fk_{table}_account", type_="foreignkey")
            batch.drop_column("account_id")
    op.drop_table("accounts")
