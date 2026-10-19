"""Admin account state API; no identity-provider configuration is exposed."""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import case, func, select

from asl_transcriber.accounts import (
    begin_account_write,
    change_account,
    provision_account,
    revoke_account_sessions,
    serialize_account,
)
from asl_transcriber.auth import (
    Admin,
    Principal,
    audit_event,
    require_api_admin,
    resolve_principal,
    verify_csrf,
)
from asl_transcriber.config import settings
from asl_transcriber.database import SessionLocal
from asl_transcriber.models import Account, ApiToken, AuthSession
from asl_transcriber.personal_token_api import serialize_token
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
    account_ids = [row.id for row in rows]
    session_counts = _session_counts(db, account_ids)
    token_counts: dict[str, tuple[int, int]] = {}
    if account_ids:
        now = datetime.now(UTC)
        token_rows = db.execute(
            select(
                ApiToken.account_id,
                func.count(ApiToken.id),
                func.sum(case((
                    ApiToken.enabled.is_(True)
                    & ApiToken.revoked_at.is_(None)
                    & ((ApiToken.expires_at.is_(None)) | (ApiToken.expires_at > now))
                    & Account.enabled.is_(True),
                    1,
                ), else_=0)),
            )
            .join(Account, Account.id == ApiToken.account_id)
            .where(ApiToken.account_id.in_(account_ids))
            .group_by(ApiToken.account_id)
        )
        token_counts = {
            account_id: (int(total), int(active or 0))
            for account_id, total, active in token_rows
        }
    return [
        serialize_account(row)
        | {
            "active_session_count": session_counts.get(row.id, 0),
            "personal_token_count": token_counts.get(row.id, (0, 0))[0],
            "active_personal_token_count": token_counts.get(row.id, (0, 0))[1],
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


def _current_admin(principal: Principal, db) -> Principal:
    current = resolve_principal(principal, db)
    if current is None or current.role != "admin":
        raise HTTPException(403, "Administrator access is required")
    if current.scopes is not None and "admin" not in current.scopes:
        raise HTTPException(403, "Token scope is insufficient")
    return current


@router.get("/{account_id}/tokens")
def account_tokens(account_id: str, _: Admin) -> dict[str, Any]:
    with SessionLocal() as db:
        account = db.get(Account, account_id)
        if account is None:
            raise HTTPException(404, "Account not found")
        tokens = db.scalars(
            select(ApiToken)
            .where(ApiToken.account_id == account_id)
            .order_by(ApiToken.created_at.desc(), ApiToken.id)
        )
        return {
            "items": [
                serialize_token(token, owner_enabled=account.enabled) for token in tokens
            ]
        }


@router.post("/{account_id}/tokens/{token_id}/revoke", include_in_schema=False)
def revoke_account_token(
    account_id: str,
    token_id: str,
    request: Request,
    principal: Admin,
) -> dict[str, str]:
    if principal.auth_source != "session" or principal.account_id is None:
        raise HTTPException(403, "A browser session is required for token lifecycle changes")
    verify_csrf(request, principal)
    now = datetime.now(UTC)
    with SessionLocal() as db:
        begin_account_write(db)
        current = _current_admin(principal, db)
        token = db.scalar(
            select(ApiToken).where(
                ApiToken.id == token_id,
                ApiToken.account_id == account_id,
            )
        )
        if token is None:
            raise HTTPException(404, "Personal token not found")
        if token.enabled and token.revoked_at is None:
            token.enabled = False
            token.revoked_at = now
        name = token.name
        db.commit()
    audit_event(
        actor=current.subject,
        account_id=account_id,
        auth_source=current.auth_source,
        action="admin_personal_token_revoked",
        outcome="allowed",
        request=request,
        detail=f"token_id={token_id};name={name}",
    )
    return {"id": token_id, "status": "revoked"}


@router.post("/{account_id}/tokens/revoke-all", include_in_schema=False)
def revoke_account_tokens(
    account_id: str,
    request: Request,
    principal: Admin,
) -> dict[str, int]:
    if principal.auth_source != "session" or principal.account_id is None:
        raise HTTPException(403, "A browser session is required for token lifecycle changes")
    verify_csrf(request, principal)
    now = datetime.now(UTC)
    with SessionLocal() as db:
        begin_account_write(db)
        current = _current_admin(principal, db)
        account = db.get(Account, account_id)
        if account is None:
            raise HTTPException(404, "Account not found")
        tokens = list(db.scalars(
            select(ApiToken).where(
                ApiToken.account_id == account_id,
                ApiToken.enabled.is_(True),
                ApiToken.revoked_at.is_(None),
            )
        ))
        for token in tokens:
            token.enabled = False
            token.revoked_at = now
        token_ids = [token.id for token in tokens]
        db.commit()
    audit_event(
        actor=current.subject,
        account_id=account_id,
        auth_source=current.auth_source,
        action="admin_personal_tokens_revoked_all",
        outcome="allowed",
        request=request,
        detail=f"token_ids={','.join(token_ids)}",
    )
    return {"revoked_tokens": len(token_ids)}
