"""Add personal token metadata while preserving ownerless service credentials."""

import sqlalchemy as sa
from alembic import op

revision = "personal_api_tokens"
down_revision = "managed_accounts"
branch_labels = None
depends_on = None


def _create_api_tokens_table(name: str, *, legacy: bool = False) -> None:
    columns = [
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("account_id", sa.String(36), nullable=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
    ]
    if not legacy:
        columns.extend([
            sa.Column("token_prefix", sa.String(24)),
            sa.Column("scopes_json", sa.Text()),
            sa.Column("expires_at", sa.DateTime(timezone=True)),
            sa.Column("revoked_at", sa.DateTime(timezone=True)),
        ])
    op.create_table(
        name,
        *columns,
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name="fk_api_tokens_account"
        ),
        sa.UniqueConstraint("token_hash", name="uq_api_token_token_hash"),
        sa.UniqueConstraint("name", name="uq_api_token_service_name")
        if legacy
        else sa.UniqueConstraint(
            "account_id", "name", name="uq_api_token_account_name"
        ),
    )


def _copy_service_tokens(source: str, target: str, *, legacy: bool = False) -> None:
    target_columns = (
        "id, account_id, name, token_hash, role, enabled, created_at, last_used_at"
    )
    source_columns = target_columns
    if not legacy:
        target_columns += ", token_prefix, scopes_json, expires_at, revoked_at"
        source_columns += ", NULL, NULL, NULL, NULL"
    op.execute(
        f"INSERT INTO {target} ({target_columns}) "
        f"SELECT {source_columns} FROM {source} WHERE account_id IS NULL"
    )


def _replace_api_tokens_table(*, legacy: bool) -> None:
    temporary = "api_tokens_legacy" if legacy else "api_tokens_personal"
    _create_api_tokens_table(temporary, legacy=legacy)
    _copy_service_tokens("api_tokens", temporary, legacy=legacy)
    op.drop_table("api_tokens")
    op.rename_table(temporary, "api_tokens")
    op.create_index("ix_api_tokens_account_id", "api_tokens", ["account_id"])
    op.create_index("ix_api_tokens_token_hash", "api_tokens", ["token_hash"])
    if not legacy:
        op.create_index(
            "uq_api_token_service_name",
            "api_tokens",
            ["name"],
            unique=True,
            sqlite_where=sa.text("account_id IS NULL"),
        )


def upgrade() -> None:
    _replace_api_tokens_table(legacy=False)


def downgrade() -> None:
    # Personal credentials cannot be safely converted into ownerless service tokens.
    _replace_api_tokens_table(legacy=True)