# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Fixed

- Last-heard callsigns now use each callsign's final Whisper segment timestamp,
  so multiple callsigns in one recording are ordered by when they were actually
  heard instead of all inheriting the recording start time.

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
