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

Segment timestamps and callsign-correction evidence are currently returned by
the engine but are not persisted. Manual transcript/callsign overrides and a
remote transcription backend are not implemented. See
[AI transcription](transcription.md) for the as-built design.

## Phase 4: API and UI

- Build the versioned REST API, search filters, and SSE streams.
- Add the built-in dashboard, detail pages, and audio access controls.
- Support API-key authentication and read-only operations.
- Add opt-in authenticated AMI status and AllStar function controls.
- Associate AMI station events with archive recordings and transcripts.

### Node-control foundation

- Persistent AMI transport with buffered framing, reconnect, and ActionID routing.
- Native app_rpt event normalization and baseline reconciliation.
- Protected named link controls with pending confirmation state and node SSE.
- Next milestones: favorites CRUD, durable transmission aggregation, station
	enrichment, recording correlation, and persisted statistics.

## Phase 5: Production hardening

- Add retention, authentication, CSRF, and security policy documents.
- Harden Docker and container behavior.
- Finish documentation, release automation, and final operational checks.
