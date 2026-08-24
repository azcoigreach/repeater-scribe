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

AMI control is disabled by default. Enabling it requires both AMI credentials
and an application API key; arbitrary AMI actions are never exposed through the
HTTP API.

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
