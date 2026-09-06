# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added

- 0.9.0 Events workspace for live nets and historical reconstruction from Archive
  time ranges or explicitly selected recordings, with paginated transcript/audio
  navigation, latest-traffic following, dashboard active controls and source filters.
- Additive `events_sessions` migration: durable sessions, independent automatic
  membership/manual overrides, stable audio markers, shared tags and an
  operator-confirmed roster separate from detected callsign evidence.
- Authenticated `/api/v1/sessions` APIs with persisted creation idempotency keys,
  transactional active-source uniqueness, end/reopen retry behavior, source
  validation and existing viewer/operator/CSRF protections. Existing SSE is unchanged.
- Recorded-interval reconciliation on catalog/boundary changes and startup,
  including late discovery for ended events. Preserve history through missing audio,
  retranscription and changed membership; show crossing audio and evidence warnings.
- Events user/API/upgrade documentation and migration, concurrency, authorization,
  interval, restart and Chromium live/historical acceptance tests.


- Normalized callsign, transcript segment, and callsign mention persistence with
  deterministic current-transcript selection and indexed history queries.
- Viewer-protected callsign directory, profile, and mention-history workspace
  routes.
- Database-backed Last Heard results, operator review actions, explicit
  transmission attribution statistics, QRZ snapshots, and segment-aware audio
  seeking.

### Fixed

- Preserve Recording tags drafts, focus and cursor position during event refreshes,
  including in-flight saves and failed retries. Add spacing above the tag editor.

- Create historical events from selected recordings on the first Create click
  after filling out the form. Keep membership preview separate from submission,
  show saving progress and failures next to the button, and retain inputs for retry.

- In 0.9.1, display UTC recording, callsign, activity and event timestamps in the
  browser’s local timezone consistently. Label the UI timezone, convert calendar
  inputs to UTC, preserve instants in Archive/Event links, and apply Callsign
  date filters to local days. Restore explicit UTC offsets on SQLite API output
  and normalize offset-bearing search bounds before database queries.

- Let operators select missed or incorrect callsign text in saved transcripts and
  replace it from the dashboard or recording details, preserving raw text and
  recording a reviewed callsign mention. Reject stale edits and retain correction
  history across re-transcription; replay only matching text revisions.

- In 0.8.1, keep the live preview visible through final processing and retain it
  when the final pass is empty or loses most of the words. Attempt a recovery
  decode before rejecting a collapsed result.
- Add operator Re-transcribe actions in the transcription log and archive details,
  including failure feedback, duplicate-job protection, and preservation of saved
  transcripts when retries fail. Recover interrupted jobs on restart.

- Align the app and package version at 0.8.0 following callsign-intelligence
  acceptance.

- Restore successful callsign-history responses after error-handling edits; keep
  invalid callsign/cursor errors distinct without exposing exception details.
- Preserve legacy Archive mentions only when no current normalized transcript is
  selected, and batch transcript/mention/segment loading for Archive pages.
- Keep QRZ configuration status stable when a request stops lookups after failure.

- Preserve reviewed mention identity, evidence, confidence and original offsets
  together with heard time across same-transcript retranscription; retain
  unmatched reviews as non-current evidence without duplication.
- Traverse dated/undated callsign pages and skip cached QRZ-negative entries
  before bounding Last Heard candidates.
- Complete directory/profile/history evidence presentation, show unknown
  confidence honestly, and label raw Whisper log probabilities separately.
- Replace dynamic dashboard HTML-string rendering with DOM nodes, including
  topology and favorites; validate profile/image URLs before assignment.
- Bound history excerpts and include meaningful before/after review audit data.

### Verification

- Add permanent review/cache/pagination regressions, real-authorization callsign
  route tests, and Chromium acceptance tests using migrated temporary SQLite and
  generated audio. See `docs/verification-0.8.md` for commands and results.

## [0.7.0] - 2026-09-01

### Added

- A database-backed Radio Archive workspace with SQLite FTS5 transcript search,
  status/date/callsign filters, cursor pagination, recording details, saved
  callsign evidence, and retained-audio playback.
- Durable `Recording` catalog records, independent from ingestion workflow rows,
  with archive-relative identity, audio availability, and persisted transcripts.
- SQLite FTS5 archive search and cursor-paginated archive recording, detail, and
  ID-based audio APIs.

### Fixed

- Historical Alembic revisions now use frozen schema definitions instead of live
  ORM metadata; container startup upgrades the database before serving traffic.
- Request body limits are enforced for streamed/chunked ASGI payloads and QRZ XML
  responses are bounded and parsed with `defusedxml`.

## [0.6.1] - 2026-09-01

### Added

- Fail-closed internet deployment mode with OIDC Authorization Code + PKCE,
  opaque server-side sessions, viewer/operator/admin roles, scoped hashed API
  tokens, CSRF and exact-Origin checks, security headers, request/rate/SSE
  limits, and security audit records.
- Configurable transcript/audio visibility retention with derived-data cleanup.
- Hardened Caddy/Compose profile using a private application network,
  file-mounted secrets, read-only filesystems, dropped capabilities,
  `no-new-privileges`, and process limits.
- Verified online SQLite backup and restore-check CLI commands.
- CodeQL, dependency auditing, Dependabot updates, container scanning, SBOMs,
  provenance attestations, and tagged GHCR releases.
- A reusable Repeater Scribe security skill covering deployment, OIDC bootstrap,
  incident response, migration compatibility, and production verification.
- Internet deployment, backup/restore, secret rotation, release verification,
  and incident-response documentation.
- Callsign cards now show an estimated confidence percentage, confidence band,
  acoustic quality, observation and recording counts, and expandable scoring
  evidence.
- Callsign confidence now improves as independent evidence accumulates from
  clearer audio, repeated hearings, additional recordings, and QRZ validation.

### Security

- Audio, transcripts, node status, topology, SSE streams, controls, ingestion,
  and diagnostics now require explicit role authorization in internet mode.
- Raw AllStar functions now require a separate default-off setting.
- System diagnostics no longer return archive or database paths.
- The AllStar statistics default now uses HTTPS.

### Fixed

- Prevented authenticated dashboard traffic from exhausting SQLite connections
  and blocking health checks.
- Kept archive discovery and token verification off the event loop.
- Prioritized the newest growing recording and limited background final passes so
  live transcription can keep pace under sustained traffic.
- Restored overlapping live and final transcription scheduling instead of
  withholding final passes until every growing recording had stopped.
- Exempted authenticated dashboard reads from generic HTTP throttling, separated
  OIDC login limits from anonymous API traffic, and raised the multi-tab SSE ceiling.
- Expanded callsign scoring evidence now remains open while live and scheduled
  dashboard refreshes rebuild the callsign cards.
- A QRZ-valid callsign prefix no longer prevents later phonetics from extending
  it; confirmed longer observations supersede partial callsign cards instead of
  allowing QRZ validity to lock in the incomplete result.
- Last-heard callsigns now use each callsign's final Whisper segment timestamp,
  so multiple callsigns in one recording are ordered by when they were actually
  heard instead of all inheriting the recording start time.
- QRZ-rejected transcript fragments are no longer presented as callsigns, and
  confirmed callsigns can be recovered from duplicated or run-together live
  decoder output on later passes.

## [0.6.0] - 2026-08-30

### Added

- A dockable last-heard callsign window that extracts unique callsigns from live
  and finalized conversations and orders them by their most recent appearance.
- Optional QRZ XML Logbook Data integration for operator names, locations,
  primary profile photos, and links to QRZ callsign pages.
- International and portable callsign recognition, repeated-symbol recovery,
  accented number variants, conservative phonetic typo handling, and reuse of
  QRZ-validated calls as local correction candidates.
- Bidirectional links between callsign mentions in transcripts and their
  last-heard QRZ cards, including source-recording highlighting.
- Server-side QRZ session reuse, automatic re-login, configurable lookup caching,
  result limits, timeouts, and graceful photo and service-error fallbacks.
- QRZ configuration through `ASLT_QRZ_USERNAME`, `ASLT_QRZ_PASSWORD`,
  `ASLT_QRZ_BASE_URL`, `ASLT_QRZ_TIMEOUT_SECONDS`, `ASLT_QRZ_CACHE_SECONDS`, and
  `ASLT_QRZ_LAST_HEARD_LIMIT`.

### Security

- QRZ credentials and session keys remain server-side. Only extracted callsigns
  are sent to QRZ; transcript text and audio remain local.

## [0.5.3] - 2026-08-27

### Fixed

- The default public API ceiling now matches the documented 30 requests per
  minute.
- Due favorite and home-node metadata refreshes can no longer indefinitely
  starve an open network map's queued discovery work.
- Favorite refreshes now expose their direct links without spending requests on
  neighbor nodes while the network map is closed.
- Closing or tabbing away from the network map immediately parks deeper crawl
  work once its event stream disconnects.

## [0.5.2] - 2026-08-24

### Added

- A hard per-minute ceiling on outbound AllStar statistics requests through
  `ASLT_ALLSTAR_MAX_REQUESTS_PER_MINUTE`.
- Viewer tracking for network maps through `ASLT_TOPOLOGY_VIEWER_TTL_SECONDS`,
  registered by the topology graph, crawl, and event-stream routes.

### Changed

- Node lookups are now prioritized: favorite roots refresh first, then the maps
  a dashboard viewer currently has open.
- Maps without a live viewer no longer walk their connections, so discovery on a
  focused map is no longer starved by background crawls.
- The dashboard opens the topology event stream only while the map panel is the
  active tab or an expanded floating window, and closes it when the panel is
  hidden, collapsed, or tabbed away.

## [0.5.1] - 2026-08-24

### Added

- Favorites now provide a split connect control for transceive, permanent,
  monitor, and local-monitor connection modes.

### Changed

- The README now documents Repeater Scribe as an AllStar node-operations,
  transcription, favorites, and topology application, including deployment and
  security boundaries.
- Package metadata now describes the node-control, network-mapping, and local
  transcription application rather than the original archive-only companion.
- Removed the unused `ASLT_AUDIO_API_MODE` and `ASLT_READ_ONLY_MODE` settings;
  they did not enforce authentication or disable AMI controls.
- Shipped callsign, AMI-secret, and API-key examples now default empty so each
  installation must supply its own site-specific values.

### Fixed

- Provisional rolling transcripts now replace a re-decoded trailing window instead of
  appending a near-duplicate copy of the same speech.

## [0.5.0] - 2026-08-24

### Added

- Fully local, provisional transcription of growing WAV recordings with rolling FFmpeg
  snapshots and SSE updates.
- NATO-phonetic decoding and local callsign correction while retaining the
  model's raw transcript.
- Dynamic callsign candidates from favorites, node activity, and topology, with weighted
  correction for fast-speech substitutions, split suffixes, and numeric-slot errors.
- A 12 GB NVIDIA profile using `large-v3`, CUDA FP16, and separate low-latency and
  beam-5 final decoding passes.

### Changed

- The Docker image now includes the CUDA 12 cuBLAS/cuDNN libraries and Compose requests
  access to the host GPU.
- Dynamic callsign hotword prompting now defaults off; candidates are applied after decode
  to prevent prompt-list hallucinations while preserving local callsign correction.
- Transcription documentation now describes the as-built provisional/final
  pipeline, callsign resolver, local privacy boundary, tuning, and limitations;
  obsolete single-pass and unimplemented persistence guidance was removed.
- Unused duration and silence-threshold settings were removed from the example
  transcription environment.
- The deployment example and application default no longer contain a site-specific
  AllStar node ID; operators must configure `ASLT_AMI_NODE_ID` explicitly.

## [0.4.0] - 2026-08-23

### Added

- Persistent AMI node control and live connected-node monitoring.
- Favorite node management with metadata, activity, keyup, and transmit-time statistics.
- Rate-limited AllStar topology discovery with a live, dockable network map.
- Interactive topology bubbles with pan, zoom, dragging, metadata, and rooted branch layout.

### Changed

- Disconnected favorites continue to receive activity and keyup monitoring.
- Topology bubbles resize horizontally to display their metadata without truncation.

## [0.1.0] - 2026-08-22

### Added

- Initial project structure and Python packaging.
- Unified configuration via environment variables with an `ASLT_` prefix.
- FastAPI application with a health endpoint and system info endpoint.
- SQLite-backed database scaffolding and Alembic config.
- Docker and Compose examples for a non-root deployment.
- Architecture and implementation plan documentation.
- ADR describing the archive-based ingestion approach.

### Notes

This is an early development release focused on the Phase 1 foundation and project structure.
