# Implementation plan

## Phase 1: Foundation

This phase establishes the application skeleton and operating model needed to support the archive ingestion pipeline safely.

- Set up the Python project metadata, tooling, and CI entrypoints.
- Define environment-driven configuration with an `ASLT_` prefix.
- Create the database layer and SQLAlchemy models for the core domain.
- Expose a basic FastAPI application with `/api/v1/health`.
- Document the archive-based ingestion architecture decision.
- Add Docker and Compose scaffolding for a removable, non-root deployment.

## Phase 2: Ingestion

- Watch archive directories recursively and wait for files to stop growing.
- Stabilize files before processing.
- Deduplicate durable jobs by archive root and source-relative path.
- Parse ASL3 activity logs and correlate events with recordings.
- Create job lifecycle states and retry/dead-letter handling.

## Phase 3: Transcription (implemented)

- Use one local `FasterWhisperEngine` for rolling provisional and full-file final
  decoding.
- Persist final raw model text and callsign-corrected display text.
- Build ranked callsign context from configured calls, favorites, node activity,
  and topology data.
- Keep dynamic hotword prompting off by default and retain raw text for review.
- Benchmark model speed and exact callsign accuracy against real repeater audio.

Segment timestamps and callsign-correction evidence are persisted in
`callsign_mentions_json`. Manual transcript/callsign overrides and a
remote transcription backend are not implemented. See
[AI transcription](transcription.md) for the as-built design.

## Phase 4: API and UI

- Build the versioned REST API, search filters, and SSE streams.
- Add the built-in dashboard, detail pages, and audio access controls.
- Support API-key authentication and read-only operations.
- Add opt-in authenticated AMI status and AllStar function controls.
- Associate AMI station events with archive recordings and transcripts.

### Archive foundation (0.7.0 in progress)

- Persist archive-root/source-relative `Recording` catalog rows separately from
  ingestion jobs and retain them when read-only source audio rotates away.
- Provide `GET /api/v1/archive/recordings`,
  `GET /api/v1/archive/recordings/{recording_id}`, and
  `GET /api/v1/archive/recordings/{recording_id}/audio` for viewer clients.
- Use SQLite FTS5 and opaque descending cursors for historical transcript search.

### Archive workspace (0.7.0 in progress)

- Provide a standalone historical browser with database-backed filters, cursor
  pagination, permanent recording detail pages, audio availability states, and
  saved callsign mention evidence.

### Node-control foundation

- Persistent AMI transport with buffered framing, reconnect, and ActionID routing.
- Native app_rpt event normalization and baseline reconciliation.
- Protected named link controls with pending confirmation state and node SSE.
- Next milestones: favorites CRUD, durable transmission aggregation, station
	enrichment, recording correlation, and persisted statistics.

## Phase 5: Production hardening (implemented)

- OIDC Authorization Code + PKCE login with opaque server-side sessions.
- Viewer, operator, and administrator authorization on every dashboard, API,
  audio, control, and event-stream route.
- Scoped, hashed API tokens with a one-time-secret CLI and a legacy API-key
  migration path.
- Session-bound CSRF tokens, exact-origin checks, strict Host validation,
  restrictive browser security headers, request limits, rate limits, and SSE
  connection budgets.
- Security audit records for authentication, authorization, throttling, and
  state-changing requests.
- Retention enforcement that expires derived transcripts and audio visibility
  without modifying the read-only ASL3 archive.
- A fail-closed internet deployment mode with HTTPS/OIDC configuration
  validation, Caddy TLS termination, file-mounted secrets, and a hardened
  non-root/read-only container profile.
- Security regression tests, dependency scanning, deployment guidance, backup
  and recovery checks, and release-time operational validation.
