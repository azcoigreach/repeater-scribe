"""Account-owned API token lifecycle; secrets are returned only at creation."""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from asl_transcriber.accounts import begin_account_write
from asl_transcriber.auth import (
    Principal,
    Viewer,
    audit_event,
    resolve_principal,
    token_digest,
    verify_csrf,
)
from asl_transcriber.database import SessionLocal
from asl_transcriber.models import Account, ApiToken
from asl_transcriber.roles import Role

router = APIRouter(prefix="/api/v1/personal-tokens", tags=["personal API tokens"])
TokenScope = Literal["api:read", "content:write", "node:control", "admin"]
ACTIVE_TOKEN_LIMIT = 25
TOKEN_EXPIRATIONS = (30, 90, 365)


def _default_scopes() -> list[TokenScope]:
    return ["api:read"]


class PersonalTokenCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=128)
    expires_in_days: Literal[30, 90, 365] = 90
    scopes: list[TokenScope] = Field(default_factory=_default_scopes)

    @field_validator("name")
    @classmethod
    def visible_name(cls, value: str) -> str:
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("Token name cannot contain control characters")
        return value

    @field_validator("scopes")
    @classmethod
    def unique_scopes(cls, value: list[TokenScope]) -> list[TokenScope]:
        if len(value) != len(set(value)):
            raise ValueError("Token scopes must be unique")
        return value


def _require_browser_account(principal: Principal, request: Request, *, mutation: bool) -> str:
    if principal.auth_source != "session" or principal.account_id is None:
        raise HTTPException(403, "An authenticated browser session is required")
    if mutation:
        verify_csrf(request, principal)
    return principal.account_id


def _current_session(principal: Principal, db: Session) -> tuple[Principal, Account]:
    current = resolve_principal(principal, db)
    if current is None or current.auth_source != "session" or current.account_id is None:
        raise HTTPException(401, "The browser session is no longer valid")
    account = db.get(Account, current.account_id)
    if account is None or not account.enabled:
        raise HTTPException(401, "The account is disabled")
    return current, account


def _allowed_scopes(role: Role) -> set[str]:
    if role == "viewer":
        return {"api:read"}
    if role == "user":
        return {"api:read", "content:write", "node:control"}
    return {"api:read", "content:write", "node:control", "admin"}


def token_status(token: ApiToken, *, owner_enabled: bool = True) -> str:
    if token.expires_at is not None:
        expiry = token.expires_at if token.expires_at.tzinfo else token.expires_at.replace(tzinfo=UTC)
        if expiry <= datetime.now(UTC):
            return "expired"
    if token.revoked_at is not None or not token.enabled:
        return "revoked"
    if not owner_enabled:
        return "owner_disabled"
    return "active"


def serialize_token(token: ApiToken, *, owner_enabled: bool = True) -> dict[str, object]:
    scopes: list[str] = []
    try:
        decoded = json.loads(token.scopes_json or "[]")
        if isinstance(decoded, list) and all(isinstance(scope, str) for scope in decoded):
            scopes = decoded
    except (TypeError, ValueError):
        pass
    return {
        "id": token.id,
        "name": token.name,
        "prefix": token.token_prefix,
        "role": token.role,
        "scopes": scopes,
        "created_at": token.created_at.isoformat(),
        "expires_at": token.expires_at.isoformat() if token.expires_at else None,
        "last_used_at": token.last_used_at.isoformat() if token.last_used_at else None,
        "revoked_at": token.revoked_at.isoformat() if token.revoked_at else None,
        "status": token_status(token, owner_enabled=owner_enabled),
    }


@router.get("", include_in_schema=False)
def list_personal_tokens(
    request: Request, principal: Viewer
) -> dict[str, list[dict[str, object]]]:
    account_id = _require_browser_account(principal, request, mutation=False)
    with SessionLocal() as db:
        current, account = _current_session(principal, db)
        if current.account_id != account_id:
            raise HTTPException(401, "The browser session changed")
        tokens = db.scalars(
            select(ApiToken)
            .where(ApiToken.account_id == account_id)
            .order_by(ApiToken.created_at.desc(), ApiToken.id)
        )
        return {"items": [serialize_token(token, owner_enabled=account.enabled) for token in tokens]}


@router.post("", status_code=status.HTTP_201_CREATED, include_in_schema=False)
def create_personal_token(
    payload: PersonalTokenCreate,
    request: Request,
    principal: Viewer,
) -> dict[str, object]:
    account_id = _require_browser_account(principal, request, mutation=True)
    if not payload.scopes:
        raise HTTPException(422, "Choose at least one API scope")
    raw_token = f"aslt_pat_{secrets.token_urlsafe(36)}"
    now = datetime.now(UTC)
    expires_at = now + timedelta(days=payload.expires_in_days)
    scopes = sorted(payload.scopes)

    try:
        with SessionLocal() as db:
            begin_account_write(db)
            current, _account = _current_session(principal, db)
            if current.account_id != account_id:
                raise HTTPException(401, "The browser session changed")
            if not set(scopes) <= _allowed_scopes(current.role):
                raise HTTPException(403, "Your current role cannot grant one or more selected scopes")
            duplicate = db.scalar(
                select(ApiToken.id).where(
                    ApiToken.account_id == account_id,
                    func.lower(ApiToken.name) == payload.name.lower(),
                )
            )
            if duplicate:
                raise HTTPException(409, "A token with this name already exists")
            active_count = db.scalar(
                select(func.count()).select_from(ApiToken).where(
                    ApiToken.account_id == account_id,
                    ApiToken.enabled.is_(True),
                    ApiToken.revoked_at.is_(None),
                    (ApiToken.expires_at.is_(None)) | (ApiToken.expires_at > now),
                )
            )
            if active_count is not None and active_count >= ACTIVE_TOKEN_LIMIT:
                raise HTTPException(409, f"An account can have at most {ACTIVE_TOKEN_LIMIT} active personal tokens")
            token = ApiToken(
                account_id=account_id,
                name=payload.name,
                token_hash=token_digest(raw_token),
                role=current.role,
                enabled=True,
                token_prefix=f"{raw_token[:18]}...",
                scopes_json=json.dumps(scopes, separators=(",", ":")),
                created_at=now,
                expires_at=expires_at,
            )
            db.add(token)
            db.flush()
            token_id = token.id
            db.commit()
    except IntegrityError as error:
        raise HTTPException(409, "A token with this name already exists") from error

    audit_event(
        actor=current.subject,
        account_id=account_id,
        auth_source=current.auth_source,
        action="personal_token_created",
        outcome="allowed",
        request=request,
        detail=f"token_id={token_id};name={payload.name};scopes={','.join(scopes)};expires_at={expires_at.isoformat()}",
    )
    return {
        "id": token_id,
        "name": payload.name,
        "token": raw_token,
        "prefix": f"{raw_token[:18]}...",
        "scopes": scopes,
        "created_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
    }


@router.delete("/{token_id}", include_in_schema=False)
def revoke_personal_token(token_id: str, request: Request, principal: Viewer) -> dict[str, str]:
    account_id = _require_browser_account(principal, request, mutation=True)
    now = datetime.now(UTC)
    with SessionLocal() as db:
        begin_account_write(db)
        current, _ = _current_session(principal, db)
        if current.account_id != account_id:
            raise HTTPException(401, "The browser session changed")
        token = db.scalar(
            select(ApiToken).where(ApiToken.id == token_id, ApiToken.account_id == account_id)
        )
        if token is None:
            raise HTTPException(404, "Personal token not found")
        if token.enabled and token.revoked_at is None:
            token.enabled = False
            token.revoked_at = now
        token_name = token.name
        db.commit()

    audit_event(
        actor=current.subject,
        account_id=account_id,
        auth_source=current.auth_source,
        action="personal_token_revoked",
        outcome="allowed",
        request=request,
        detail=f"token_id={token_id};name={token_name}",
    )
    return {"id": token_id, "status": "revoked"}