# Managed accounts and authorization

Issue [#55](https://github.com/azcoigreach/repeater-scribe/issues/55) introduces
server-side accounts. The Settings layout, account-management UI, personal-token
creation, and browser reconnect/session presentation remain separate work.

## Identity and admission

An account has a stable UUID and a unique, immutable verified OIDC **issuer +
subject** pair. Email, preferred username, display name and displayed identity
are mutable metadata; none can admit an identity or link two accounts. Accounts
are retained when disabled. Existing content attribution and session identity
snapshots are not rewritten when metadata changes. Security audits attach the
stable account ID and preserve their actor/detail snapshots. Denied sign-ins for
known disabled accounts retain that account ID; unknown identities remain
unlinked. Denial audits persist after the login transaction rolls back.

On successful, cryptographically verified login:

1. Look up the exact issuer and subject.
2. For an existing account, its enabled state and role win. Disabled accounts
   cannot sign in. Provider groups, configured role mappings and default roles
   never re-enable or promote a managed account. Existing enabled accounts can
   sign in even after their bootstrap mapping is removed.
3. For an unknown account, require an explicitly allowed subject or group using
   the existing configuration. Admin subjects/groups bootstrap Admin accounts;
   legacy Operator mappings bootstrap User accounts. The configured default
   role applies only after admission succeeds. An unmatched identity is denied.
4. Update identity metadata and first/last successful sign-in timestamps, record
   admission/sign-in audits, and create an account-linked browser session in
   one serialized transaction.

The `ASLT_OIDC_OPERATOR_SUBJECTS`, `ASLT_OIDC_OPERATOR_GROUPS` names and existing
`repeater-scribe-operators` group value remain compatibility configuration for
User authority. `ASLT_OIDC_DEFAULT_ROLE=user` is supported; `operator` normalizes
to `user`. OIDC configuration and secrets remain deployment configuration, never
runtime account API fields.

## Current access and API

Viewer can read operational workspaces and audio. User adds the former Operator
content, Favorites and permitted node-control authority. Admin adds ingestion,
diagnostics and account administration. These roles never bypass independent
AMI/control/raw-function enablement. See the [complete endpoint matrix](permissions.md).

Every protected request resolves the current account enabled state and role.
A session's stored role is historical data, not authority. Ownerless named
machine tokens remain independent credentials. Account-owned personal tokens
are always capped by the owner's current enabled state and role, in addition to
the scopes granted to that token.

All three SSE routes revalidate credentials before each emitted event and at
least every second while idle (subject to database/scheduler latency). Revocation,
expiry or any role change closes the subscription and releases its connection
slot. Streaming does not extend session idle lifetime. Periodic security cleanup
removes both idle-expired and absolutely expired sessions, including abandoned
sessions that never make another request. A rejected browser request also removes
its invalid session immediately. Already-authorized finite
responses or in-flight node commands cannot be recalled; future requests are
checked against current state.

- `GET /api/v1/accounts?limit=100&offset=0` lists accounts for Admins, with a
  maximum page size of 200.
- `GET /api/v1/accounts/{id}` returns one account to an Admin.
- `PATCH /api/v1/accounts/{id}` accepts `role` (`viewer`, `user`, `admin`) and/or
  boolean `enabled`. Empty, null, legacy-role and unknown fields are rejected.
  Issuer/subject and provider configuration cannot be changed here.
- `GET /api/v1/auth/me` includes the stable `account_id` when applicable.

Cookie-authenticated mutations require CSRF and exact public Origin. API writes
require a credential even in trusted local mode. Existing named Admin tokens
can administer accounts. Normal write rate limits and security audits apply.
Account write audits commit atomically with the change. Disabling an account
revokes **all** its browser sessions in the same transaction. Re-enabling never
restores them. Normal login creates a separate device session; logout revokes
only the presented session.

All account admission, access changes and CLI recovery reserve SQLite's writer
with `BEGIN IMMEDIATE` before state-dependent reads. Account changes also
revalidate the caller inside that transaction. Disabling or demoting the last
enabled managed Admin returns 409, including when independent processes submit
concurrent requests. A named Admin token does not count as an enabled account.

## Personal API tokens

Every enabled account can manage up to 25 active personal tokens from **Settings
→ API Access**. The secret is shown only once after creation; later list and Admin
responses include only the safe prefix and metadata. Token names are unique
within an account. Expiration is required and can be 30, 90 (default), or 365
days. To rotate, create and securely store a replacement, then revoke the old
token. Revocation and expiration take effect on the next request.

Available scopes are:

- `api:read`: Viewer reads, subject to existing endpoint access rules.
- `content:write`: User-level shared-content edits.
- `node:control`: User-level Favorites and permitted node controls; AMI and raw
   function settings still apply.
- `admin`: Admin endpoints. Only an Admin can grant it; the Settings page asks
   for confirmation.

The server intersects the granted scopes with the token's role snapshot and the
owner's current role. Demotion immediately caps existing token authority.
Disabling an account revokes its personal tokens and browser sessions in one
transaction. Re-enabling never restores either. API token lifecycle actions
require a browser session with CSRF and exact-Origin validation; a bearer token
cannot mint another bearer token. Legacy CLI-created ownerless machine tokens
keep their existing role behavior and are never converted to personal tokens.

Use the versioned API with `Authorization: Bearer`:

```bash
curl --fail-with-body https://radio.example.test/api/v1/recordings \
   -H "Authorization: Bearer $ASLT_TOKEN"
```

Create a token from an authenticated browser session; the response's `token`
field is the only full-secret reveal:

```bash
curl --fail-with-body https://radio.example.test/api/v1/personal-tokens \
   -X POST \
   -H "Cookie: __Host-aslt_session=$ASLT_SESSION" \
   -H "Origin: https://radio.example.test" \
   -H "X-CSRF-Token: $ASLT_CSRF_TOKEN" \
   -H "Content-Type: application/json" \
   --data '{"name":"daily archive reader","expires_in_days":90,"scopes":["api:read"]}'
```

The authenticated Swagger UI is at `/docs`; it supports Bearer **Authorize**
and **Try it out**. The OpenAPI JSON at `/openapi.json` is also protected and is
not served anonymously. The documentation page's browser session does not add
privileges to a token used by Swagger.

Admins can inspect safe token summaries and revoke one or all personal tokens
from a selected account in Settings → Users. Neither the Admin API nor UI ever
reveals another user's full token secret.

## Upgrade and recovery

Back up the database as described in [security operations](security.md), stop
the old application, and run `alembic upgrade head` before starting new code.
The supported prior revision is `events_sessions` (0.9.2); fresh creation is also
supported. Keep deployment OIDC configuration intact for initial admission.

UI assets use a shared `asset_version` template global, currently
`personal-api-tokens-1`, to invalidate cached scripts and styles independently of
the product version. Bump this revision whenever changed UI assets are shipped.

**Browser sessions and pending OIDC logins are revoked once on upgrade.** Old
session rows have no issuer and cannot safely establish verified account links.
Users must sign in again. Existing configured administrators bootstrap on that
first login. Account timestamps start with the new verified sign-in; older
sign-in times are not invented from unlinked sessions. Historical content,
security audits, token IDs/digests/names and last-used timestamps are preserved.
Existing Event request fingerprints are retained. New browser request keys use
the stable account ID. A retry matching a pre-account subject/key returns 409
with instructions to inspect the existing Event rather than guessing an issuer
link or creating a duplicate. Named-token request identities are unchanged.
Named token role `operator` becomes `user` without changing its authority or
secret. New CLI tokens default to `user`; `--role operator` remains accepted.

If role configuration is wrong before first login, correct the deployment
allowlists/groups. For an already managed account, changing provider claims
will not override account state. Another Admin can repair it through the API.
If no usable Admin remains, an authorized host administrator can run:

```bash
docker compose exec -T repeater-scribe asl-transcriber recover-admin EXACT_OIDC_SUBJECT
```

The command uses the configured issuer, creates or restores an enabled Admin
for that exact pair, records `cli:recovery` with the account ID, and revokes any
old sessions. It prints no credentials. The subject must still be proven by a
normal verified OIDC login; email is not a substitute. This host-level recovery
is intentional administrative authority, not an HTTP bypass. Check configuration
and recover a known subject after restoring lost account data. Never disable
internet authentication to recover access.

For rollback, restore a verified pre-upgrade backup into an isolated database
and use the matching prior image. Downgrading to `managed_accounts` removes
personal-token rows rather than converting them into independent service tokens;
ownerless machine tokens are preserved. It also drops managed account state and
revokes browser sessions. Do not downgrade the only copy of account data.
