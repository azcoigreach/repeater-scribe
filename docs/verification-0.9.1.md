# 0.9.1 verification

Verified on 2026-09-06 with Python 3.14.4, SQLite, and Playwright Chromium.
Application and package metadata report `0.9.1`; all 33 UI script/stylesheet
references use `?v=0.9.1` and resolve to existing files.

| Check | Result |
| --- | --- |
| `ruff check .` | Passed |
| `mypy src` | Passed, 39 source files |
| `pytest -q -W error::DeprecationWarning` | 304 passed |
| `pytest browser_tests -q -W error::DeprecationWarning` | 29 passed |
| `git diff --check` | Passed |

Browser tests use an isolated HTTPS server, a temporary migrated database,
generated audio, and stubbed external services. They cover:

- Phoenix, UTC, New York, and Kolkata time handling; local calendar boundaries,
  daylight-saving transitions, UTC links, and unchanged event/marker/check-in edits.
- Creating a historical event from two selected recordings with one click,
  retaining the form after failure, and retrying with the same idempotency key.
- Recording tag drafts, focus, and cursor selection during polling and explicit
  refreshes; typing during an in-flight save; failure/retry and saved persistence.
- Existing dashboard, Archive, callsign, Events, transcription correction/recovery,
  authorization, audio playback, and desktop/mobile workflows.

For this local environment Chromium was installed in
`/tmp/repeater-scribe-playwright`; browser commands set `PLAYWRIGHT_BROWSERS_PATH`
to that directory. Standard Playwright installations can use the command above.

No schema migration is added in 0.9.1. The required Alembic head remains
`events_sessions`; UTC persistence and container timezone settings are unchanged.
See [upgrade instructions](upgrade-0.9.md). These checks do not deploy the release.
