# Architecture overview

ASL Transcriber is organized around a pipeline that begins with the ASL3 archive and ends in searchable, API-accessible transcripts.

## Core responsibilities

- Source discovery: identify ASL3 archive files that are ready for processing.
- Ingestion: stabilize files, compute hashes, and persist jobs in the database.
- Audio processing: validate, normalize, and transcribe supported recordings.
- Correlation: combine recordings with nearby ASL3 activity events.
- Persistence: store metadata, transcripts, and service state in SQLite by default.
- Delivery: expose events via SSE and resources via a FastAPI API and dashboard.

## Internal boundaries

The architecture uses interfaces so ASL3-specific parsing and transcription implementations remain isolated: `AudioSource`, `ActivityLogParser`, `TranscriptionEngine`, `TranscriptPostProcessor`, and `EventPublisher`.

The first implementation is deliberately narrow:

- `ASL3ArchiveSource` for archive-based discovery
- `FasterWhisperEngine` for local transcription
- SQLite-backed persistence with Alembic migrations

This keeps the first release focused on reliability while leaving room for future adapters such as RTP media, broadcast stream sources, or a cloud transcription engine.

## Runtime lifecycle

1. The application starts up and validates configuration.
2. The initial archive scan enumerates known recordings.
3. Stable files are deduplicated and queued for processing.
4. Audio is probed and normalized before a transcription job runs.
5. Results are persisted and published through the event channel.
6. The API and web UI read from the same database state.
