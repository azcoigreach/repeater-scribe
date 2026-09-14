"""Managed account admission and serialized access changes for the SQLite backend."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from asl_transcriber.database import SessionLocal
from asl_transcriber.models import Account, AuthSession, SecurityAudit
from asl_transcriber.roles import Role, normalize_role
from asl_transcriber.time_utils import iso_utc

if TYPE_CHECKING:
    from asl_transcriber.auth import Principal


def begin_account_write(db: Session) -> None:
    # Reserve the database writer before reads, across processes, as Event writes do.
    # Login, recovery and account changes all participate in the same serialization.
    db.execute(text("BEGIN IMMEDIATE"))


def account_audit(
    db: Session, account: Account, actor: str, source: str, action: str, detail: dict[str, Any]
) -> None:
    db.add(
        SecurityAudit(
            account_id=account.id,
            actor=actor[:255],
            auth_source=source,
            action=action,
            outcome="allowed",
            detail=json.dumps(detail),
        )
    )


def admit_account(db: Session, claims: dict[str, Any], *, allowed: bool, role: Role) -> Account:
    """Call only with verified OIDC claims, inside an account write transaction."""
    issuer, subject = claims["iss"], claims["sub"]
    account = db.scalar(select(Account).where(Account.issuer == issuer, Account.subject == subject))
    now = datetime.now(UTC)
    if account is not None and not account.enabled:
        raise HTTPException(403, "This account is disabled")
    if account is None:
        if not allowed:
            raise HTTPException(403, "This identity is not allowed")
        account = Account(
            issuer=issuer,
            subject=subject,
            identity=subject,
            role=role,
            enabled=True,
            created_by="oidc:configuration",
            updated_by="oidc:configuration",
        )
        db.add(account)
        db.flush()
        account_audit(
            db,
            account,
            f"account:{account.id}",
            "oidc",
            "account_admission",
            {"role": role, "bootstrap_admin": role == "admin"},
        )
    previous_identity = account.identity
    account.email = _metadata(claims.get("email"))
    account.preferred_username = _metadata(claims.get("preferred_username"))
    account.display_name = _metadata(claims.get("name"))
    account.identity = (
        account.email or account.preferred_username or account.display_name or subject
    )
    account.first_sign_in_at = account.first_sign_in_at or now
    account.last_sign_in_at = now
    account.updated_at = now
    account.updated_by = f"account:{account.id}"
    account_audit(
        db,
        account,
        f"account:{account.id}",
        "oidc",
        "account_sign_in",
        {"previous_identity": previous_identity, "identity": account.identity},
    )
    return account


def _metadata(value: Any) -> str | None:
    return value[:255] if isinstance(value, str) and value else None


def serialize_account(account: Account) -> dict[str, Any]:
    return {
        name: getattr(account, name)
        for name in (
            "id",
            "issuer",
            "subject",
            "identity",
            "email",
            "preferred_username",
            "display_name",
            "role",
            "enabled",
            "created_by",
            "updated_by",
        )
    } | {
        name: iso_utc(getattr(account, name))
        for name in (
            "created_at",
            "updated_at",
            "first_sign_in_at",
            "last_sign_in_at",
        )
    }


def change_account(
    account_id: str, principal: Principal, *, role: Role | None, enabled: bool | None
) -> dict[str, Any]:
    from asl_transcriber.auth import resolve_principal

    with SessionLocal() as db:
        begin_account_write(db)
        # Check again under the writer lock: another administrator may have just
        # demoted/revoked this caller while the HTTP request was in flight.
        current = resolve_principal(principal, db)
        if current is None or current.role != "admin":
            raise HTTPException(403, "Administrator access is required")
        account = db.get(Account, account_id)
        if account is None:
            raise HTTPException(404, "Account not found")
        previous = {"role": account.role, "enabled": account.enabled, "identity": account.identity}
        next_role = role if role is not None else normalize_role(account.role)
        next_enabled = enabled if enabled is not None else account.enabled
        if (
            account.enabled
            and account.role == "admin"
            and (not next_enabled or next_role != "admin")
        ):
            admins = db.scalar(
                select(func.count())
                .select_from(Account)
                .where(Account.enabled.is_(True), Account.role == "admin")
            )
            if admins == 1:
                raise HTTPException(409, "Cannot disable or demote the last enabled Admin")
        account.role, account.enabled = next_role, next_enabled
        account.updated_at = datetime.now(UTC)
        account.updated_by = current.subject
        if not next_enabled:
            db.execute(delete(AuthSession).where(AuthSession.account_id == account.id))
        account_audit(
            db,
            account,
            current.subject,
            current.auth_source,
            "account_access_changed",
            {"before": previous, "after": {"role": next_role, "enabled": next_enabled}},
        )
        db.flush()
        result = serialize_account(account)
        db.commit()
        return result


def recover_admin(issuer: str, subject: str) -> str:
    """Host-admin recovery; exact issuer/subject must still be proven on OIDC login."""
    if not issuer.startswith("https://") or not subject or len(subject) > 255 or len(issuer) > 1024:
        raise ValueError(
            "Recovery requires a configured HTTPS issuer and a subject of 1 to 255 characters"
        )
    with SessionLocal() as db:
        begin_account_write(db)
        account = db.scalar(
            select(Account).where(Account.issuer == issuer, Account.subject == subject)
        )
        if account is None:
            account = Account(
                issuer=issuer,
                subject=subject,
                identity=subject,
                role="admin",
                enabled=True,
                created_by="cli:recovery",
                updated_by="cli:recovery",
            )
            db.add(account)
            db.flush()
        previous = {"role": account.role, "enabled": account.enabled}
        account.role, account.enabled = "admin", True
        account.updated_at, account.updated_by = datetime.now(UTC), "cli:recovery"
        db.execute(delete(AuthSession).where(AuthSession.account_id == account.id))
        account_audit(db, account, "cli:recovery", "cli", "account_recovery", {"before": previous})
        account_id = account.id
        db.commit()
        return account_id
