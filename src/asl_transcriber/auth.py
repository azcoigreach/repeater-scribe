from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import Annotated, Any, Literal
from urllib.parse import urlencode, urlparse

import httpx
from fastapi import Depends, HTTPException, Request
from sqlalchemy import delete, select

from asl_transcriber.config import settings
from asl_transcriber.database import SessionLocal
from asl_transcriber.models import ApiToken, AuthSession, OidcLoginState, SecurityAudit

logger = logging.getLogger(__name__)
Role = Literal["viewer", "operator", "admin"]
ROLE_RANK: dict[str, int] = {"viewer": 1, "operator": 2, "admin": 3}


@dataclass(frozen=True)
class Principal:
    subject: str
    identity: str
    role: Role
    auth_source: str
    csrf_token: str | None = None
    session_hash: str | None = None


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client is not None else None


def audit_event(
    *,
    actor: str,
    auth_source: str,
    action: str,
    outcome: str,
    request: Request | None = None,
    detail: str | None = None,
) -> None:
    try:
        with SessionLocal() as session:
            session.add(
                SecurityAudit(
                    actor=actor[:255],
                    auth_source=auth_source[:32],
                    action=action[:128],
                    outcome=outcome[:32],
                    method=request.method[:16] if request is not None else None,
                    path=request.url.path[:1024] if request is not None else None,
                    client_ip=_client_ip(request) if request is not None else None,
                    detail=detail[:2000] if detail else None,
                )
            )
            session.commit()
    except Exception:
        logger.exception("Could not persist security audit event")


def _session_principal(raw_token: str) -> Principal | None:
    token_hash = _hash(raw_token)
    now = datetime.now(UTC)
    with SessionLocal() as session:
        stored = session.get(AuthSession, token_hash)
        if stored is None:
            return None
        idle_deadline = _utc(stored.last_seen_at) + timedelta(seconds=settings.session_idle_seconds)
        if _utc(stored.expires_at) <= now or idle_deadline <= now:
            session.delete(stored)
            session.commit()
            return None
        if (now - _utc(stored.last_seen_at)).total_seconds() >= 60:
            stored.last_seen_at = now
            session.commit()
        return Principal(
            subject=stored.subject,
            identity=stored.identity,
            role=stored.role,  # type: ignore[arg-type]
            auth_source="session",
            csrf_token=stored.csrf_token,
            session_hash=stored.token_hash,
        )


def _api_principal(raw_token: str) -> Principal | None:
    now = datetime.now(UTC)
    token_hash = _hash(raw_token)
    with SessionLocal() as session:
        stored = session.scalar(
            select(ApiToken).where(ApiToken.token_hash == token_hash, ApiToken.enabled.is_(True))
        )
        if stored is not None:
            if (
                stored.last_used_at is None
                or (now - _utc(stored.last_used_at)).total_seconds() >= 60
            ):
                stored.last_used_at = now
                session.commit()
            return Principal(
                subject=f"api-token:{stored.id}",
                identity=stored.name,
                role=stored.role,  # type: ignore[arg-type]
                auth_source="api_token",
            )
    legacy = settings.resolved_api_key
    if legacy and hmac.compare_digest(raw_token, legacy):
        return Principal(
            subject="legacy-api-key",
            identity="legacy-api-key",
            role="admin",
            auth_source="legacy_api_key",
        )
    return None


def authenticate_request(request: Request) -> Principal | None:
    cached = getattr(request.state, "principal", None)
    if isinstance(cached, Principal):
        return cached

    authorization = request.headers.get("authorization", "")
    principal: Principal | None = None
    if authorization.casefold().startswith("bearer "):
        principal = _api_principal(authorization[7:].strip())
    elif request.headers.get("x-api-key"):
        principal = _api_principal(request.headers["x-api-key"])
    elif raw_session := request.cookies.get(settings.session_cookie_name):
        principal = _session_principal(raw_session)
    elif settings.auth_mode == "off" and settings.deployment_mode == "local":
        principal = Principal(
            subject="local-admin",
            identity="local trusted network",
            role="admin",
            auth_source="local",
        )
    request.state.principal = principal
    return principal


def _require(request: Request, role: Role) -> Principal:
    principal = authenticate_request(request)
    if principal is None:
        audit_event(
            actor="anonymous",
            auth_source="none",
            action="authorization",
            outcome="denied",
            request=request,
            detail=f"required_role={role}",
        )
        raise HTTPException(status_code=401, detail="Authentication is required")
    if ROLE_RANK.get(principal.role, 0) < ROLE_RANK[role]:
        audit_event(
            actor=principal.identity,
            auth_source=principal.auth_source,
            action="authorization",
            outcome="denied",
            request=request,
            detail=f"required_role={role};actual_role={principal.role}",
        )
        raise HTTPException(status_code=403, detail="Insufficient permission")
    return principal


def require_viewer(request: Request) -> Principal:
    return _require(request, "viewer")


def require_operator(request: Request) -> Principal:
    return _require(request, "operator")


def require_admin(request: Request) -> Principal:
    return _require(request, "admin")


def verify_csrf(request: Request, principal: Principal) -> None:
    if principal.auth_source != "session":
        return
    supplied = request.headers.get("x-csrf-token", "")
    if not principal.csrf_token or not hmac.compare_digest(supplied, principal.csrf_token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
    origin = request.headers.get("origin")
    if origin != settings.public_origin:
        raise HTTPException(status_code=403, detail="Invalid request origin")


def require_ui_operator(request: Request) -> Principal:
    principal = require_operator(request)
    if settings.deployment_mode == "internet":
        if principal.auth_source != "session":
            raise HTTPException(
                status_code=403, detail="Browser session authentication is required"
            )
        verify_csrf(request, principal)
    return principal


def require_ui_admin(request: Request) -> Principal:
    principal = require_admin(request)
    if settings.deployment_mode == "internet":
        if principal.auth_source != "session":
            raise HTTPException(
                status_code=403, detail="Browser session authentication is required"
            )
        verify_csrf(request, principal)
    return principal


def require_api_operator(request: Request) -> Principal:
    principal = require_operator(request)
    if principal.auth_source == "local":
        raise HTTPException(status_code=401, detail="An API token is required")
    verify_csrf(request, principal)
    return principal


def require_api_admin(request: Request) -> Principal:
    principal = require_admin(request)
    if principal.auth_source == "local":
        raise HTTPException(status_code=401, detail="An API token is required")
    verify_csrf(request, principal)
    return principal


Viewer = Annotated[Principal, Depends(require_viewer)]
Operator = Annotated[Principal, Depends(require_operator)]
Admin = Annotated[Principal, Depends(require_admin)]


_discovery_cache: tuple[float, dict[str, Any]] | None = None


async def _oidc_discovery() -> dict[str, Any]:
    global _discovery_cache
    if _discovery_cache is not None and monotonic() - _discovery_cache[0] < 3600:
        return _discovery_cache[1]
    issuer = settings.oidc_issuer_url.rstrip("/")
    async with httpx.AsyncClient(timeout=settings.oidc_http_timeout_seconds) as client:
        response = await client.get(f"{issuer}/.well-known/openid-configuration")
        response.raise_for_status()
        metadata = response.json()
    if metadata.get("issuer", "").rstrip("/") != issuer:
        raise RuntimeError("OIDC discovery returned an unexpected issuer")
    for field in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        value = str(metadata.get(field, ""))
        if urlparse(value).scheme != "https":
            raise RuntimeError(f"OIDC discovery returned an insecure {field}")
    _discovery_cache = (monotonic(), metadata)
    return metadata


def _safe_next_path(value: str | None) -> str:
    if not value or not value.startswith("/") or value.startswith("//"):
        return "/"
    return value[:1024]


async def oidc_authorization_url(next_path: str | None = None) -> str:
    if settings.auth_mode != "oidc":
        raise HTTPException(status_code=404, detail="OIDC authentication is disabled")
    metadata = await _oidc_discovery()
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    now = datetime.now(UTC)
    with SessionLocal() as session:
        session.execute(delete(OidcLoginState).where(OidcLoginState.expires_at <= now))
        session.add(
            OidcLoginState(
                state_hash=_hash(state),
                nonce=nonce,
                code_verifier=verifier,
                next_path=_safe_next_path(next_path),
                expires_at=now + timedelta(minutes=10),
            )
        )
        session.commit()
    parameters = {
        "response_type": "code",
        "client_id": settings.oidc_client_id,
        "redirect_uri": f"{settings.public_base_url.rstrip('/')}/auth/callback",
        "scope": settings.oidc_scopes,
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{metadata['authorization_endpoint']}?{urlencode(parameters)}"


def _role_from_claims(claims: dict[str, Any]) -> Role:
    subject = str(claims.get("sub", ""))
    raw_groups = claims.get(settings.oidc_role_claim, [])
    if isinstance(raw_groups, str):
        groups = {raw_groups}
    elif isinstance(raw_groups, list):
        groups = {str(item) for item in raw_groups}
    else:
        groups = set()
    if subject in settings.oidc_admin_subject_list or groups.intersection(
        settings.oidc_admin_group_list
    ):
        return "admin"
    if subject in settings.oidc_operator_subject_list or groups.intersection(
        settings.oidc_operator_group_list
    ):
        return "operator"
    return settings.oidc_default_role


def _identity_is_allowed(claims: dict[str, Any]) -> bool:
    subject = str(claims.get("sub", ""))
    raw_groups = claims.get(settings.oidc_role_claim, [])
    if isinstance(raw_groups, str):
        groups = {raw_groups}
    elif isinstance(raw_groups, list):
        groups = {str(item) for item in raw_groups}
    else:
        groups = set()
    admitted_subjects = {
        *settings.oidc_allowed_subject_list,
        *settings.oidc_operator_subject_list,
        *settings.oidc_admin_subject_list,
    }
    admitted_groups = {
        *settings.oidc_allowed_group_list,
        *settings.oidc_operator_group_list,
        *settings.oidc_admin_group_list,
    }
    return subject in admitted_subjects or bool(groups.intersection(admitted_groups))


async def complete_oidc_login(code: str, state: str) -> tuple[str, Principal, str]:
    now = datetime.now(UTC)
    with SessionLocal() as session:
        login = session.get(OidcLoginState, _hash(state))
        if login is None or _utc(login.expires_at) <= now:
            raise HTTPException(status_code=400, detail="Invalid or expired login state")
        nonce = login.nonce
        verifier = login.code_verifier
        next_path = login.next_path
        session.delete(login)
        session.commit()

    metadata = await _oidc_discovery()
    token_data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": f"{settings.public_base_url.rstrip('/')}/auth/callback",
        "code_verifier": verifier,
    }
    async with httpx.AsyncClient(timeout=settings.oidc_http_timeout_seconds) as client:
        token_response = await client.post(
            metadata["token_endpoint"],
            data=token_data,
            auth=(settings.oidc_client_id, settings.resolved_oidc_client_secret),
            headers={"Accept": "application/json"},
        )
        if token_response.is_error:
            raise HTTPException(status_code=502, detail="Identity provider rejected the login")
        token = token_response.json()
        id_token = str(token.get("id_token", ""))
        jwks_response = await client.get(metadata["jwks_uri"])
        jwks_response.raise_for_status()
        jwks = jwks_response.json()
    if not id_token:
        raise HTTPException(status_code=502, detail="Identity provider did not return an ID token")
    try:
        from joserfc import jwt
        from joserfc.errors import JoseError
        from joserfc.jwk import KeySet

        token = jwt.decode(
            id_token,
            KeySet.import_key_set(jwks),
            algorithms=["RS256", "RS384", "RS512", "ES256", "ES384"],
        )
        jwt.JWTClaimsRegistry(
            leeway=60,
            iss={"essential": True, "value": settings.oidc_issuer_url.rstrip("/")},
            aud={"essential": True, "value": settings.oidc_client_id},
            sub={"essential": True},
            exp={"essential": True},
            iat={"essential": True},
            nonce={"essential": True, "value": nonce},
        ).validate(token.claims)
        claims = dict(token.claims)
    except (ImportError, JoseError, ValueError) as error:
        logger.warning("OIDC ID token validation failed: %s", error)
        raise HTTPException(status_code=502, detail="Identity token validation failed") from error
    issuer = str(claims.get("iss", "")).rstrip("/")
    audience = claims.get("aud")
    audiences = (
        {str(value) for value in audience} if isinstance(audience, list) else {str(audience)}
    )
    if issuer != settings.oidc_issuer_url.rstrip("/") or settings.oidc_client_id not in audiences:
        raise HTTPException(status_code=502, detail="Identity token claims are invalid")
    if (
        isinstance(audience, list)
        and len(audience) > 1
        and claims.get("azp") != settings.oidc_client_id
    ):
        raise HTTPException(status_code=502, detail="Identity token presenter is invalid")
    if not hmac.compare_digest(str(claims.get("nonce", "")), nonce):
        raise HTTPException(status_code=502, detail="Identity token nonce is invalid")
    subject = str(claims.get("sub", ""))
    if not subject:
        raise HTTPException(status_code=502, detail="Identity token has no subject")
    if not _identity_is_allowed(claims):
        logger.warning(
            "OIDC login denied for subject=%s; add it to ASLT_OIDC_ALLOWED_SUBJECTS",
            subject,
        )
        raise HTTPException(status_code=403, detail="This identity is not allowed")
    identity = str(claims.get("email") or claims.get("preferred_username") or subject)
    role = _role_from_claims(claims)
    raw_session = secrets.token_urlsafe(48)
    csrf_token = secrets.token_urlsafe(32)
    expires_at = now + timedelta(seconds=settings.session_absolute_seconds)
    with SessionLocal() as session:
        session.add(
            AuthSession(
                token_hash=_hash(raw_session),
                subject=subject,
                identity=identity[:255],
                role=role,
                csrf_token=csrf_token,
                created_at=now,
                last_seen_at=now,
                expires_at=expires_at,
            )
        )
        session.commit()
    return raw_session, Principal(subject, identity, role, "session", csrf_token), next_path


def revoke_session(raw_token: str | None) -> None:
    if not raw_token:
        return
    with SessionLocal() as session:
        stored = session.get(AuthSession, _hash(raw_token))
        if stored is not None:
            session.delete(stored)
            session.commit()


def create_api_token(name: str, role: Role) -> str:
    raw_token = f"aslt_{secrets.token_urlsafe(36)}"
    with SessionLocal() as session:
        session.add(ApiToken(name=name, token_hash=_hash(raw_token), role=role))
        session.commit()
    return raw_token


def revoke_api_token(name: str) -> bool:
    with SessionLocal() as session:
        stored = session.scalar(select(ApiToken).where(ApiToken.name == name))
        if stored is None:
            return False
        stored.enabled = False
        session.commit()
        return True


def purge_security_state() -> dict[str, int]:
    now = datetime.now(UTC)
    audit_cutoff = now - timedelta(days=settings.audit_retention_days)
    with SessionLocal() as session:
        expired_sessions_result = session.execute(
            delete(AuthSession).where(AuthSession.expires_at <= now)
        )
        expired_logins_result = session.execute(
            delete(OidcLoginState).where(OidcLoginState.expires_at <= now)
        )
        expired_audits_result = session.execute(
            delete(SecurityAudit).where(SecurityAudit.occurred_at < audit_cutoff)
        )
        session.commit()
    return {
        "sessions": int(getattr(expired_sessions_result, "rowcount", 0) or 0),
        "login_states": int(getattr(expired_logins_result, "rowcount", 0) or 0),
        "audits": int(getattr(expired_audits_result, "rowcount", 0) or 0),
    }
