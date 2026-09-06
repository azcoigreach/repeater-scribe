# 0.9.0 verification

Verified on 2026-09-06, using the existing virtual environment (Python 3.14.4),
SQLite and Chromium. Baseline: clean 0.8.1 checkout at `c7f98ef`; implementation
branch: `feat/0.9.0-events`. Application/package version: 0.9.0. Required Alembic
head: `events_sessions`.

## Final commands actually run

| Command | Result |
| --- | --- |
| `.venv/bin/ruff check .` | Passed |
| `.venv/bin/mypy src` | Passed, 38 source files |
| `.venv/bin/pytest -q -W error::DeprecationWarning` | 300 passed (21.39 seconds) |
| `PLAYWRIGHT_BROWSERS_PATH=/tmp/repeater-scribe-browsers .venv/bin/pytest browser_tests -q -W error::DeprecationWarning` | 18 passed (21.63 seconds) |
| `git diff --check` | Passed |

The default sandbox stalled AnyIO/TestClient thread wakeups. Tests were rerun
outside that sandbox using isolated temporary SQLite databases and the existing
local HTTPS browser fixture. Chromium was made available through the repository's
Playwright tooling with its browser path under `/tmp`. External browser requests
are blocked by the fixture; no live repeater, identity provider or QRZ is used.

## Coverage

- Fresh migration lineage and additive upgrade from the actual 0.8.1
  `transcript_text_corrections` schema with retained recording, transcript,
  current-revision selection, reviewed mention, text correction and FTS search.
  Existing migration downgrade/re-upgrade, foreign-key checks and Alembic metadata
  comparison also pass.
- Live/historical creation, source isolation, concurrent active-source conflicts,
  overlapping historical sessions, creation retries across restart and equivalent
  UTC-offset input, retry-safe end/reopen and canonical roster uniqueness.
- Half-open intervals, crossing recordings, unknown/zero duration, unknown time,
  duration changes, late discovery into ended events, startup catch-up,
  inclusion/exclusion/reset, selection previews, tags and cursor traversal.
- Current evidence versus attendance, spelling review, retranscription,
  retained markers/roster, excluded evidence flags, missing audio, manually
  confirmed callsigns, editable confirmation data and exact submillisecond markers.
- Viewer/operator separation, CSRF/Origin failures, machine tokens and retained
  SSE route identity. Existing security/node-control regression tests remain green.
- Latest traffic advancing beyond a page while returning bounded chronological
  results; attributed metrics use existing explicit transmission evidence.

## Browser acceptance

The existing Callsigns, Archive, dashboard, review and transcript-correction
acceptance tests pass alongside four new Events tests:

1. Start Groovy Late Shift; accumulate a newly cataloged recording and transcript;
   add a recording/offset marker; confirm a detected check-in; end from the live
   dashboard; reopen the saved event; revisit roster and marker; seek its audio.
2. Reconstruct from Archive source/time filters; adjust boundaries; exclude a
   crossing recording; reload and inspect the decision; add manual attendance;
   verify 390-pixel layout and readable missing-audio history.
3. Create from selected Archive recordings with an automatic-membership preview;
   change boundaries and verify that explicit inclusion survives.
4. View saved Events as a read-only user without mutation controls or API access.

Browser fixtures use UTC for deterministic input. They exercise actual session
APIs, authentication, CSRF, SQLite, audio playback and catalog reconciliation;
transcripts and source discovery data are deterministic fixtures. Screenshots
were captured and mobile layout visually inspected.

## Limits and release status

No live ASL hardware, real Whisper decoding, Docker image build, production
migration, deployment, release publication or release tag was exercised by this
implementation task. Existing recording timestamp conventions are preserved;
`ASLT_SOURCE_TIMEZONE` is not currently applied by the parser. Browser input uses
browser local time; use explicit-offset API timestamps for ambiguous DST hours.
Attribution remains partial when explicit transmission evidence exists and
unavailable otherwise. No automatic attendance confirmation is introduced.

See [Events](events.md), [API/retry semantics](sessions-api.md) and
[upgrade steps](upgrade-0.9.md). The 0.9.0 changes are prepared on the feature
branch for human pull-request review and merge.
