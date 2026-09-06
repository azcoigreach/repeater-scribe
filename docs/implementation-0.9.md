# 0.9.0 implementation plan

Baseline: clean `main` at c7f98ef (merged 0.8.1 transcription recovery), package
and application 0.8.1, Alembic head `transcript_text_corrections`. No AGENTS.md
exists in this checkout. Existing `/api/v1/events` is the live SSE stream.

1. Add normalized sessions, membership overrides, markers, tags, and check-ins;
   migrate additively from the actual 0.8.1 head.
2. Reconcile recorded intervals within archive-root source scopes on catalog
   changes, boundary changes, and startup; preserve manual decisions and history.
3. Add paginated authenticated `/api/v1/sessions` APIs, persisted retry keys,
   transactional active-source uniqueness, and existing callsign evidence queries.
4. Build Events list/detail, dashboard active controls, Archive range/selection
   creation, playback, markers and an explicitly confirmed roster.
5. Exercise migration, domain/API/security and Chromium acceptance; update
   package versions, user/API/upgrade documentation and roadmap. No deployment,
   release publication, or release tags.
