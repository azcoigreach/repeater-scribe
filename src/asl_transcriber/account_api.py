"""Admin account state API; no identity-provider configuration is exposed."""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select

from asl_transcriber.accounts import (
    change_account,
    provision_account,
    revoke_account_sessions,
    serialize_account,
)
from asl_transcriber.auth import Admin, Principal, require_api_admin
from asl_transcriber.config import settings
from asl_transcriber.database import SessionLocal
from asl_transcriber.models import Account, AuthSession
from asl_transcriber.roles import Role

router = APIRouter(prefix="/api/v1/accounts", tags=["accounts"])


class AccountChange(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    role: Role | None = None
    enabled: bool | None = None

    @model_validator(mode="after")
    def nonempty(self) -> "AccountChange":
        if not self.model_fields_set or any(
            getattr(self, key) is None for key in self.model_fields_set
        ):
            raise ValueError("Supply role and/or enabled with non-null values")
        return self


class AccountProvision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    subject: str = Field(min_length=1, max_length=255)
    role: Role = "viewer"


def _session_counts(db, account_ids: list[str]) -> dict[str, int]:
    if not account_ids:
        return {}
    now = datetime.now(UTC)
    idle_cutoff = now - timedelta(seconds=settings.session_idle_seconds)
    rows = db.execute(
        select(AuthSession.account_id, func.count())
        .where(
            AuthSession.account_id.in_(account_ids),
            AuthSession.expires_at > now,
            AuthSession.last_seen_at > idle_cutoff,
        )
        .group_by(AuthSession.account_id)
    )
    return dict(rows.all())


def _serialize_accounts(db, rows: list[Account]) -> list[dict[str, Any]]:
    session_counts = _session_counts(db, [row.id for row in rows])
    return [
        serialize_account(row)
        | {
            "active_session_count": session_counts.get(row.id, 0),
            "provisioned_pending": row.first_sign_in_at is None,
        }
        for row in rows
    ]


@router.get("")
def accounts(
    _: Admin, limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0)
) -> dict[str, Any]:
    with SessionLocal() as db:
        rows = list(db.scalars(
            select(Account).order_by(Account.created_at, Account.id).offset(offset).limit(limit)
        ))
        return {"items": _serialize_accounts(db, rows)}


@router.post("")
def create_account(
    payload: AccountProvision,
    principal: Annotated[Principal, Depends(require_api_admin)],
) -> dict[str, Any]:
    result = provision_account(payload.subject, payload.role, principal)
    return result | {"active_session_count": 0, "provisioned_pending": True}


@router.get("/{account_id}")
def account(account_id: str, _: Admin) -> dict[str, Any]:
    with SessionLocal() as db:
        row = db.get(Account, account_id)
        if row is None:
            raise HTTPException(404, "Account not found")
        return _serialize_accounts(db, [row])[0]


@router.patch("/{account_id}")
def update_account(
    account_id: str,
    payload: AccountChange,
    principal: Annotated[Principal, Depends(require_api_admin)],
) -> dict[str, Any]:
    return change_account(account_id, principal, role=payload.role, enabled=payload.enabled)


@router.post("/{account_id}/sessions/revoke")
def revoke_sessions(
    account_id: str,
    principal: Annotated[Principal, Depends(require_api_admin)],
) -> dict[str, Any]:
    return revoke_account_sessions(account_id, principal)
