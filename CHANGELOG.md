# Changelog

All notable changes to this project will be documented in this file.

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
