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
- Deduplicate by content hash and source path.
- Parse ASL3 activity logs and correlate events with recordings.
- Create job lifecycle states and retry/dead-letter handling.

## Phase 3: Transcription

- Probe, normalize, and validate audio.
- Implement the `FasterWhisperEngine` abstraction.
- Persist raw and display transcripts with per-segment metadata.
- Add post-processing for callsign correction with manual overrides.

## Phase 4: API and UI

- Build the versioned REST API, search filters, and SSE streams.
- Add the built-in dashboard, detail pages, and audio access controls.
- Support API-key authentication and read-only operations.

## Phase 5: Production hardening

- Add retention, authentication, CSRF, and security policy documents.
- Harden Docker and container behavior.
- Finish documentation, release automation, and final operational checks.
