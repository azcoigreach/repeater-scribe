# Architecture overview

Repeater Scribe is organized around a pipeline that begins with the ASL3 archive and ends in searchable, API-accessible transcripts.

## Core responsibilities

- Source discovery: identify ASL3 archive files that are ready for processing.
- Ingestion: stabilize files and persist source-scoped jobs in the database.
- Audio processing: snapshot growing recordings for provisional transcription
  and decode stable recordings from beginning to end.
- Correlation: combine recordings with nearby ASL3 activity events.
- Persistence: store metadata, transcripts, and service state in SQLite by default.
- Delivery: expose events via SSE and resources via a FastAPI API and dashboard.
- Node integration: optionally read status and issue constrained AllStar function commands through authenticated AMI.

## Internal boundaries

The implemented runtime is separated into concrete modules with a protocol at
the transcription boundary:

- `ArchiveScanner` and `ArchiveIngestionService` for read-only archive discovery
  and stabilization
- `ActivityLogParser` for ASL3 event parsing
- the `TranscriptionEngine` protocol with one shared `FasterWhisperEngine` for
  local provisional and final transcription
- `LiveTranscriptionService` for FFmpeg tail snapshots and provisional text merging
- `DatabaseCallsignProvider` and `CallsignResolver` for local post-decode correction
- `ArchiveRuntime` and `ProcessingWorker` for job state and transcript persistence
- SQLite-backed persistence with Alembic migrations
- `AmiClient` for authenticated AMI status and constrained `rpt fun` control

No cloud transcription adapter is currently implemented. The engine interface
leaves room for an explicitly configured backend in the future.

## Internet security boundary

Internet mode is explicitly enabled with `ASLT_DEPLOYMENT_MODE=internet` and
fails startup unless HTTPS, OIDC, a strong session secret, and an explicit Host
allowlist are configured. Caddy is the only public listener in the reference
deployment; the FastAPI container remains on the private Compose network.

OIDC uses Authorization Code flow with PKCE, issuer discovery, signed ID-token
verification, nonce/state validation, and optional subject allowlisting. The
browser receives only an opaque `Secure`, `HttpOnly`, `SameSite=Lax` session
cookie. Server-side sessions carry viewer, operator, or administrator authority.
Cookie-authenticated writes additionally require a session CSRF token and the
exact configured public Origin. Machine clients use separately generated,
hashed bearer tokens.

Only minimal health and login/callback routes are anonymous. Audio, transcript,
node state, topology, SSE, and API resources require a viewer. Favorites and AMI
controls require an operator. Ingestion and diagnostics require an administrator.
Raw AllStar functions have a separate default-off control because their meaning
depends on the local `app_rpt` configuration.

AMI control is disabled by default. Enabling it requires AMI credentials and an
authenticated operator (a named API token, browser session, or legacy API key).
Arbitrary AMI actions are never exposed through the HTTP API.

## Archive catalog

`Recording` is the durable historical catalog entity, identified by archive root
and source-relative path. `IngestionJob` remains processing workflow state, and
`Transcript` is a transcription result associated with both during the migration
period. Source audio can be `missing`, `expired`, `archived`, or `protected`
while recording metadata and text remain available. SQLite FTS5 indexes raw and
corrected display text for the supported 0.7 archive backend.

Archive results sort by derived recording start time descending, then recording
UUID descending. A source name without an ASL timestamp uses `created_at` as its
deterministic ordering value. Routine source rotation changes only audio status
to `missing`; configured retention changes audio status to `expired` while
retaining catalog and transcript history. Routine refreshes never replace
intentional `expired`, `protected`, or `archived` states.

The Dashboard is the live operations workspace. The separate Archive workspace
uses only the archive APIs and database catalog: it does not read the runtime
job list or poll on the live dashboard interval. It applies SQLite FTS5 and
cursor pagination for historical searches, and can present transcript evidence
when retained source audio is unavailable.

## Runtime lifecycle

1. The application starts up and validates configuration.
2. The initial archive scan enumerates known recordings.
3. Growing files can be copied into temporary 16 kHz tail snapshots for a
   low-latency decode; merged provisional text is published through SSE.
4. Files whose size and modification time remain stable for the configured
   interval are queued for a full-file final decode.
5. Both passes apply the same dynamic local callsign resolver. Hotwords are
   disabled for live decoding and default off for final decoding.
6. Final raw and corrected display text are persisted and published. Live text
   remains in memory and is replaced by the final result.
7. The API and web UI read from the same database state.

The complete transcription contract and configuration are documented in
[AI transcription](transcription.md).
