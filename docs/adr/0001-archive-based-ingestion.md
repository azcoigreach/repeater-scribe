# ADR 0001: Archive-based ingestion for ASL3 recordings

- Status: Accepted
- Date: 2026-08-22

## Context

ASL3 can archive recordings to a configured `archivedir` and produce a mix of per-event WAV recordings, activity logs, and metadata. The application must monitor those recordings without modifying the running AllStar node or taking control of keying equipment.

The archive-based approach is a good match for a general-purpose companion application because it is:

- read-only and non-invasive,
- compatible with existing ASL3 installations,
- resilient to node restarts,
- easy to deploy in Docker,
- testable without Asterisk AMI access.

## Decision

The application will treat the configured ASL3 archive as the authoritative input source. It will recursively scan configured archive roots, wait for file stabilization, deduplicate by source-relative path and content hash, and queue the recording for audio processing and transcription.

The ingestion architecture will isolate ASL3-specific parsing behind interfaces so that future sources (RTP, external-media, or stream sources) can be added without changing the rest of the pipeline.

## Consequences

### Positive

- The application never keys, interrupts, or modifies the node.
- It works alongside dockerized or host-managed ASL3 deployments.
- The workload is naturally partitioned into scanning, processing, and transcription.
- Source-relative paths are stable and portable for storage and API responses.

### Negative

- Archive-ingestion is inherently delayed by the file lifecycle on disk.
- Some recordings may be incomplete or malformed and must be filtered.
- Correlation with ASL3 logs may remain ambiguous for certain edge cases.

## Implementation notes

- Source files are never renamed, moved, or modified.
- A file is processed only after it has reached a stable size and modification time.
- Duplicate files are identified by a content hash and source-relative path.
- `AudioSource` and `ActivityLogParser` interfaces will keep ASL3 parser logic separate from the rest of the system.
- This preserves a straightforward path to future adapters without redesigning the core pipeline.
