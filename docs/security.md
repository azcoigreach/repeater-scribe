# Internet security and operations

Repeater Scribe is a control surface for radio equipment operating under an
amateur callsign. Internet mode therefore treats audio, transcripts, station
activity, and node-control authority as private operational data.

## Trust and roles

The public boundary is the TLS proxy. Port 8088 is loopback-only in the base
Compose file, while the internet profile exposes Caddy on ports 80 and 443. The
application connects inward to AMI and outward to the configured OIDC, QRZ, and
AllStar services. The archive is mounted read-only; `/data` and `/tmp` are the
only application write locations.

| Role | Authority |
| --- | --- |
| Anonymous | Minimal health response and OIDC login/callback |
| Viewer | Dashboard, audio, transcripts, activity, topology, and event streams |
| Operator | Viewer authority plus favorites and constrained node controls |
| Administrator | Operator authority plus ingestion and diagnostics |

Use OIDC subject allowlists for a private installation. Map administrative and
operator access through either subject lists or the configured group claim. MFA
is enforced at the identity provider and should be mandatory for both roles.

## First internet deployment

1. Create an OIDC confidential web client. Register only
   `https://RADIO_HOST/auth/callback` as its redirect URI.
2. Copy `.env.example` to `.env`. Set `ASLT_PUBLIC_HOST` for Compose and configure
   the OIDC issuer/client ID plus an explicit allowed-subject or group mapping.
   A valid identity that matches none of the allowed viewer/operator/admin
   subjects or groups is denied.
3. Create `secrets/session_secret` with at least 32 random bytes and
   `secrets/oidc_client_secret` with the provider-issued client secret. Restrict
   both files to the deployment account. Put the dedicated AMI account's secret
   in `secrets/ami_secret`; the internet Compose profile mounts all three as
   read-only Docker secrets instead of environment variables.

   The application runs as UID/GID `10001`. With ordinary Docker Compose,
   file-backed secrets are bind mounts, so keep the directory private and grant
   only the application group read access:

   ```bash
   chmod 700 secrets
   sudo chgrp 10001 secrets/session_secret secrets/oidc_client_secret secrets/ami_secret
   chmod 640 secrets/session_secret secrets/oidc_client_secret secrets/ami_secret
   ```

   Recheck these permissions after rotating or replacing a secret file.
4. Keep `ASLT_AMI_RAW_FUNCTION_ENABLED=false` unless every local function code
   has been reviewed. Use a dedicated least-privilege AMI account.
5. Start the profile with:

   ```bash
   docker compose -f docker-compose.yml -f compose.internet.yml run --rm \
     repeater-scribe alembic upgrade head
   docker compose -f docker-compose.yml -f compose.internet.yml up -d --build
   ```

6. Confirm that the public host redirects to OIDC, anonymous API/audio requests
   return 401, TLS is valid, and direct remote access to port 8088 fails.
7. Sign in as each role and verify the route boundaries before enabling AMI
   control.

The application trusts forwarded client and scheme headers in this profile
because only the private Caddy network may reach it. Do not publish the app
container port beyond loopback. The supplied application and Caddy images are
pinned to their Linux AMD64 digests; review Dependabot's digest updates and
repin deliberately when upgrading.

## Machine API tokens

Create a token inside the application container. Its secret is printed once and
only its SHA-256 digest is stored.

```bash
docker compose exec -T repeater-scribe \
  asl-transcriber create-api-token automation --role operator
```

Send it as `Authorization: Bearer TOKEN`. Revoke it by name:

```bash
docker compose exec -T repeater-scribe \
  asl-transcriber revoke-api-token automation
```

`ASLT_API_KEY` and `X-API-Key` remain temporarily supported for migration but
grant administrator authority. Replace them with named tokens.

## Retention and backups

`ASLT_RETENTION_DAYS=0` retains derived data indefinitely. A positive value
prevents older archive recordings from being discovered or served and removes
their ingestion/transcript rows during daily housekeeping. It never deletes the
source archive. Configure ASL3 retention separately and apply equivalent expiry
to backups.

Back up SQLite with its online backup API rather than copying a live database
file. The destination is created mode `0600` and is not replaced unless
`--force` is explicit:

```bash
mkdir -p data/backups
docker compose exec -T repeater-scribe \
  asl-transcriber backup-db /data/backups/asl-transcriber-$(date +%F).db
docker compose exec -T repeater-scribe \
  asl-transcriber verify-db /data/backups/asl-transcriber-$(date +%F).db
```

Periodically stop a test instance, place a verified backup at its configured
database path, start it without AMI control, and check recordings, transcripts,
favorites, topology, API tokens, and audit records. Secret files are backed up
separately with tighter permissions; never place them in the database archive.

## Upgrades and rollback

Before an upgrade, create and verify a database backup, retain the currently
running image digest or Git tag, review the changelog, and run `alembic upgrade
head` with the same two Compose files before recreating the service. Confirm
anonymous denial and each role after the health check passes.

For an application rollback, disable AMI control first and redeploy the retained
image or tag. Do not run an Alembic downgrade against the only copy of production
data: restore the pre-upgrade verified database into an isolated path, validate
it, and then intentionally replace the stopped service's database if schema
rollback is actually required.

## Release verification

- Run `ruff check .`, `mypy src`, and `pytest -q`.
- Run the dependency audit and container vulnerability scan in CI.
- Build the exact production Compose profile and confirm its health check.
- Verify authentication denial, role separation, CSRF rejection, Host rejection,
  request-size/rate limits, and SSE connection limits.
- Inspect response headers for CSP, HSTS, no-sniff, frame denial, referrer policy,
  and no-store caching.
- Confirm AMI control and raw functions remain disabled after a clean install.
- Exercise backup/restore, secret rotation, rollback, and the emergency
  `ASLT_AMI_CONTROL_ENABLED=false` procedure.
