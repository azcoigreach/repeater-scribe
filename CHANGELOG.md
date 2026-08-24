# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added

- Fully local, provisional transcription of growing WAV recordings with rolling FFmpeg
  snapshots and SSE updates.
- Callsign hotwords, NATO-phonetic decoding, and allowlisted callsign correction while
  retaining the model's raw transcript.
- A 12 GB NVIDIA profile using `large-v3`, CUDA FP16, and separate low-latency and
  beam-5 final decoding passes.

### Changed

- The Docker image now includes the CUDA 12 cuBLAS/cuDNN libraries and Compose requests
  access to the host GPU.

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
