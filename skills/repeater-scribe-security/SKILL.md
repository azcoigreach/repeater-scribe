---
name: repeater-scribe-security
description: Audit, harden, deploy, or diagnose Repeater Scribe when internet exposure, OIDC, secrets, repeater controls, firewall rules, retention, backups, or security incidents are in scope. Do not use for ordinary feature work with no security or deployment impact.
---

# Repeater Scribe Security

Protect the application as a control surface for radio equipment operating under
an amateur callsign. Preserve operator intent and require explicit authorization
before changing external DNS, firewall, identity-provider, or live repeater state.

## Start with the deployed design

Read the files relevant to the request before acting:

- `docs/security.md` for the canonical internet deployment and recovery process.
- `compose.internet.yml`, `docker-compose.yml`, and `deploy/Caddyfile` for the
  actual network and container boundary.
- `src/asl_transcriber/config.py`, `auth.py`, and `security.py` when reviewing
  configuration, authentication, authorization, or middleware behavior.
- `SECURITY.md` for vulnerability and incident handling.

Inspect configuration by reporting only whether sensitive values exist, their
file modes, and their lengths when necessary. Never print secret contents,
container environments, OAuth tokens, session cookies, AMI credentials, QRZ
credentials, or API tokens. If a credential appears in output, stop repeating it
and tell the operator to rotate it.

## Non-negotiable boundaries

- Internet mode must fail closed with HTTPS, OIDC, an explicit hostname
  allowlist, and explicit subject or group admission.
- Anonymous access is limited to health and the OIDC login/callback flow.
- Viewers may read private operational data; operators may mutate favorites and
  use constrained controls; administrators may run ingestion and diagnostics.
- Browser writes require the session CSRF token and exact public Origin.
- Machine writes use named bearer tokens. Treat the legacy API key as temporary
  administrator access.
- Keep raw AllStar functions disabled unless the operator explicitly enables
  them after reviewing local function codes.
- Keep the ASL3 archive read-only. Retention removes derived visibility and
  database records, never source recordings.
- Expose only TCP 80 and TCP 443. UDP 443 is optional for HTTP/3. Never expose
  application port 8088, Caddy administration port 2019, AMI port 5038, SQLite,
  or the Docker daemon.

## Secret-file permissions

The application runs as UID/GID `10001`. Ordinary Docker Compose implements
file-backed secrets as bind mounts, so host-only mode `0600` prevents the
container from starting. Use this least-privilege layout:

- `secrets/` mode `0700` and owned by the deployment account.
- Secret files owned by the deployment account, group `10001`, mode `0640`.
- Recheck owner, group, mode, and non-empty size after every rotation.

The session secret keys stored session, OIDC-state, and named API-token digests.
Rotating it invalidates all three. Plan for browser sign-in and machine-token
replacement immediately after a rotation.

Do not make secret files world-readable. Prefer changing only the three exact
session, OIDC, and AMI files. Diagnose mounts and presence metadata before
changing permissions.

## Safe deployment workflow

1. Confirm the working tree, current image version, database location, and
   current Alembic revision without exposing environment values.
2. Create and verify an online SQLite backup before migration.
3. Run `alembic upgrade head`. Preserve the `callsign_mention_timing` migration
   lineage because released `0.6.0` images may already have recorded it.
4. Validate the combined Compose profile with `config --quiet`.
5. Recreate the services and wait for the application health check before
   evaluating Caddy.
6. Verify the loopback application health using an allowed Host value.
7. Verify public-host TLS and HTTP-to-HTTPS redirect behavior.
8. Inspect recent application and Caddy logs for validation, OIDC, ACME, proxy,
   or AMI failures. Do not dump the container environment.

When secrets are missing, schema migration may run through the base Compose
profile, but do not claim the internet deployment is ready until the combined
profile starts and passes its checks.

## Google OIDC bootstrap

For Google, use issuer `https://accounts.google.com` and callback
`https://PUBLIC_HOST/auth/callback`. The OAuth client ID is not an allowed
subject. The subject is the stable `sub` claim for the Google account.

When the subject is not yet known, configure a non-matching temporary subject,
attempt one login, and retrieve the denied subject from the application log.
Replace the temporary value with that exact subject and recreate the application.
Do not weaken admission to all Google accounts, and do not use an email address
as the durable identity key.

## Verification

Use checks proportional to the change. For a complete hardening or release run:

- `ruff check src tests alembic`
- `mypy src`
- `pytest -q`
- dependency vulnerability audit
- fresh and deployed-lineage Alembic upgrades
- Compose and Caddy configuration validation
- anonymous denial, role separation, CSRF/Origin denial, hostile Host denial,
  request/rate/SSE limits, retention, backup verification, and security headers

Do not treat a healthy container alone as proof of internet readiness. Confirm
TLS, authentication, admission, authorization, and the external port boundary.

## Incident posture

For suspected abuse, first disable AMI control while preserving evidence. Record
the time window, affected identities/tokens, audit events, proxy logs, and node
state. Revoke sessions and API tokens, rotate exposed credentials, and restore
from a verified backup only when evidence supports it. Do not delete logs or
rewrite history as a cleanup shortcut.
