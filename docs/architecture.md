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
- `Callsign`, `TranscriptSegment`, and `CallsignMention` normalized history
   entities maintained by the callsign service
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

Dashboard `/api/v1/recordings` rows include the opaque archive `source_id`, including
waiting files that have no job ID yet. Playback uses `(source_id, source_path)` so
identical filenames in different roots do not share controls. Generated
`/api/v1/audio` URLs pass `source_id` to select only that configured root; a missing
file or unknown source returns 404. Older URLs without `source_id` retain their
existing lookup behavior. Viewer authorization and path containment apply to both.

Live previews retain `(archive_root, source_path)` through growing-file processing,
lookup, and cleanup. An explicit-root miss never falls back to a different root's
preview. Runtime Last Heard uses the same identity for recording counts and partial
callsign comparisons. Runtime and database Last Heard responses carry `source_id`
to reveal the correct dashboard transcript; ambiguous legacy path-only links do
not select an arbitrary root.

Events, Archive detail, Dashboard and Callsign history register their audio with
the page's shared playback coordinator. Starting a native player or selecting a
transcript/mention offset pauses the previous player without resetting its position.
Pending seeks are scoped to the latest selection; metadata callbacks never start
playback. Removed native players and page navigation cancel playback. Dashboard's
persistent player survives card filtering and refresh; Callsign history stops its
player when replacing the history list. Archive and Callsign directory lists link
to these playback surfaces and do not create players themselves.

## Callsign intelligence

A callsign mention is a callsign decoded or reconstructed in transcript audio;
it is not proof that the station transmitted. Explicit attribution is separate
and is counted only from `Transmission.operator_callsign` rows with a meaningful
attribution level. QRZ validation confirms a public callsign record exists; it
is not human confirmation. Authorized operators can confirm, reject, or correct
mentions while the original observed value and evidence remain unchanged.

Migration `callsign_intelligence` backfills valid legacy mention JSON into
indexed normalized rows. Malformed or invalid entries are logged and skipped;
the compatibility JSON remains available. `Recording.current_transcript_id`
selects the transcript whose job ID matches the recording ID, falling back to
the most recently updated transcript and then UUID for deterministic ties.
Normal history totals use only that current transcript. Segments and mentions
remain when source audio becomes missing or expired.

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

## Reviewed evidence lifecycle

A recording owns transcription results; its `current_transcript_id` selects the
current result. The migration backfill uses the legacy selection rule described
above; a newly persisted final result becomes current. A transcript owns ordered
`TranscriptSegment` rows and `CallsignMention` rows. Each mention points to a
canonical `Callsign`, its recording, transcript, and optionally an overlapping
segment. Segment boundaries are not word alignment. `heard_at` uses recording
start plus the mention's end offset; playback starts at its start offset.

When details of the same transcript are replaced, detected mentions are replaced
by the new output. Confirmed, corrected and rejected rows are matched once each
by original observed value and start/end offsets within 0.25 seconds. Matched
reviews retain UUID, reviewer, review/create/update timestamps, canonical
assignment, original evidence, recognition method, confidence, timing precision,
and **both original offsets together with original heard_at**. A replacement
segment association can change; its excerpt and raw segment score describe the
current segment, while saved review evidence remains original. Corrected mentions
retain the QRZ state for their canonical assignment. Later canonical QRZ refreshes
update that validation state independently of human review.

Unmatched reviewed rows retain their original evidence and timing as
`is_current=false`, with no replacement segment. Repeated empty retranscription
does not duplicate or delete them. Ordinary current APIs exclude those rows:
directory, profile totals/review counts, Last Heard, Archive filters/serialized
mentions and mention history. Historical review retrieval/management is not a
supported HTTP or UI feature; rows remain in database backups. Selecting a new
transcript also excludes all mentions of the previous transcript from current
queries; review matching is scoped to replacement of the same transcript.

Source-audio rotation and ordinary retention preserve catalog/evidence and
change availability. An intentional catalog purge is different: it deletes the
selected recordings and dependent transcription/mention data, including retained
reviews. No public catalog-purge endpoint or historical-review management UI
is provided; administrative deletion must explicitly handle dependent records.
Back up the catalog before a deliberate purge. The application never
requires deleting the read-only source archive to purge its derived catalog.

QRZ public-record validation and operator confirmation are independent. A mention
can be human-confirmed while QRZ-negative. Only explicit transmission attribution
contributes transmission counts/airtime; mention review never writes that
attribution. See the [callsign API contract](callsign-api.md) for cache states,
expiration, lookup limits, failure behavior, permissions and write examples.

Legacy Archive recordings without a selected `current_transcript_id` can still
serialize their compatibility mention JSON when normalized mentions are absent.
Once a current transcript is selected, its normalized mentions are authoritative,
including an empty result; stale JSON must not revive detections. Archive list
queries eagerly load transcripts, mentions, segments and ingestion state in
batches, avoiding additional queries per serialized recording.


## Events and membership (0.9.0)

`RadioSession` is the durable operator event, distinct from authentication and
AMI link sessions. Its source root reuses Recording's archive-root identity;
clients use a deterministic SHA-256 source ID, not filesystem paths. An optional
end defines state; database checks enforce valid windows and a partial unique
index permits only one active event per source. Historical windows may overlap.

`SessionRecording` stores computed interval membership independently of nullable
manual inclusion/exclusion decisions. SQLAlchemy flush hooks collect only new or
changed recording source/start/duration and session boundaries. Reconciliation
uses source/window queries with bounded batches; changing one recording queries
affected sessions, not the entire recording catalog. Startup performs bounded
catch-up for all saved events, including ended ones. Whole-recording membership
never depends on transcription completion, provisional jobs or source mtime.

Session markers reference stable recording IDs and audio offsets. Check-ins
reference existing canonical Callsign rows, carry independent confirmation actor
and time, and are never rewritten by the transcript/evidence lifecycle. Shared
Tag rows have separate normalized session/recording associations. Recorded audio
availability remains independent of all event relationships.

The dedicated session router exposes `/api/v1/sessions` and existing-style `/ui`
mutations; `/api/v1/events` remains untouched. Write transactions reserve SQLite's
writer before state-dependent reads; persisted subject-scoped request fingerprints
make creation retries safe. The plain JavaScript Events UI uses bounded five-second
page refreshes, current saved transcript selection and separately labeled
provisional fallback. It does not consume or change existing SSE clients.

See [membership/user rules](events.md) and [API contracts](sessions-api.md).
