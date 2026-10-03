"""Admin account state API; no identity-provider configuration is exposed."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy import select

from asl_transcriber.accounts import change_account, serialize_account
from asl_transcriber.auth import Admin, Principal, require_api_admin
from asl_transcriber.database import SessionLocal
from asl_transcriber.models import Account
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


@router.get("")
def accounts(
    _: Admin, limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0)
) -> dict[str, Any]:
    with SessionLocal() as db:
        rows = db.scalars(
            select(Account).order_by(Account.created_at, Account.id).offset(offset).limit(limit)
        )
        return {"items": [serialize_account(row) for row in rows]}


@router.get("/{account_id}")
def account(account_id: str, _: Admin) -> dict[str, Any]:
    with SessionLocal() as db:
        row = db.get(Account, account_id)
        if row is None:
            raise HTTPException(404, "Account not found")
        return serialize_account(row)


@router.patch("/{account_id}")
def update_account(
    account_id: str,
    payload: AccountChange,
    principal: Annotated[Principal, Depends(require_api_admin)],
) -> dict[str, Any]:
    return change_account(account_id, principal, role=payload.role, enabled=payload.enabled)
