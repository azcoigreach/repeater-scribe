# 0.8 acceptance and migration verification

Version remains 0.7.0; changes are under Unreleased. Work continues on PR #20.

## Migration procedure

Stop application workers, back up SQLite using the supported backup procedure,
then use the application's configured database URL:

```bash
alembic upgrade head
alembic current
alembic check
```

Head is `current_callsign_mentions`. Startup checks the schema and fails with an
`alembic upgrade head` instruction when it is outdated. Test migrations against a
copy/temporary database before touching the live catalog. No schema changes or
historical migration edits are needed by this completion work.

A populated 0.7 (`archive_foundation`) database upgrades and backfills legacy
mentions. Current-transcript selection is deterministic. Downgrading below
`callsign_intelligence` removes normalized review/segment/canonical cache data;
re-upgrading reconstructs only what remains in legacy JSON, not human reviews.
Downgrading from head to `callsign_intelligence` also loses `is_current` flags;
re-upgrade defaults surviving rows to current. Do not use that cycle to preserve
superseded-review semantics; restore the backup instead.
Restore a backup to recover that richer evidence. The supported downgrade to
`callsign_intelligence` and re-upgrade retains predecessor-owned transmission
`duration_milliseconds`. The forward repair's downgrade intentionally keeps that
column, so it never destroys predecessor duration data.

## Automated commands

Use the project virtual environment on PATH. Unit/service/API/migration gates:

```bash
pytest -q -W error::DeprecationWarning
pytest --cov=asl_transcriber --cov-report=term-missing
ruff check .
mypy src
python -m pip check
git diff --check
```

Separate real-browser suite (requires OpenSSL and local HTTPS/socket access):

```bash
python -m pip install -e '.[dev,browser]'
python -m playwright install --with-deps chromium
pytest browser_tests -q -W error::DeprecationWarning
```

Set `PLAYWRIGHT_BROWSERS_PATH` consistently for installation and execution if
using a nondefault browser cache. Missing browser binaries/dependencies fail the
suite rather than silently skipping acceptance. It launches a temporary migrated
SQLite catalog and HTTPS Uvicorn server, seeds server-side operator/viewer
sessions, stubs QRZ, disables AMI/transcription/statistics networking, blocks
external browser requests, and generates a 12-second WAV. OIDC provider login is
covered separately by existing mocked protocol tests; these UI tests start from
valid sessions. Transient dashboard updates and topology use deterministic browser
fixtures through their actual renderers; catalog/history/review requests hit the
real application/database. No production credentials or real QRZ availability
are required.

## Execution evidence

Executed locally with Python 3.14.4 and Chromium 151.0.7922.34. The virtual
environment's executables were used (`.venv/bin/...`).

| Command | Result |
| --- | --- |
| `pytest -q -W error::DeprecationWarning` | 244 passed, 15.16s |
| `pytest --cov=asl_transcriber --cov-report=term-missing` | 244 passed, 15.71s; 86% total, 95% callsign service |
| `ruff check .` | Passed |
| `mypy src` | Passed, 35 source files |
| `python -m pip check` | No broken requirements |
| `alembic upgrade head` (fresh temporary SQLite) | Passed |
| `alembic current` | `current_callsign_mentions (head)` |
| `alembic check` | No new upgrade operations |
| `PRAGMA foreign_key_check` | No violations |
| `git diff --check` | Passed |
| `PLAYWRIGHT_BROWSERS_PATH=/tmp/repeater-scribe-browsers pytest browser_tests -q -W error::DeprecationWarning` | 9 passed, 5.99s |

The baseline at `496711e` was 218 passing tests with deprecations as errors;
Ruff, mypy and dependency checks passed. Fetch found no newer branch commits.
The initial sandboxed baseline failed on restricted local socket operations;
the unchanged suite passed outside that sandbox. Browser and full-suite execution
used the same permitted local-socket environment.

The coverage gate reports 17 unsuppressed Python 3.14 SQLite `ResourceWarning`s
for unclosed database resources. Migration fixture connections were closed where
touched; other test-created SQLAlchemy pools still have incomplete disposal.
These are warnings, not deprecation failures; no warning filters or assertions
were weakened. Coverage increased from the reviewed 85% baseline to 86%.

New permanent tests cover all reviewed-field/timing preservation for confirm,
correct and reject; repeated empty output; replacement of detected evidence;
exclusion of superseded evidence; corrected QRZ assignment; dated/undated cursor
termination; explicit rejected history; 1,000 unexpired negatives before a valid
Last Heard result; expired-negative refresh; lookup limits and first-failure
network stopping. Route tests use real authorization dependencies for anonymous,
viewer, operator-session and machine-token requests, CSRF/origin enforcement,
audit changes, refresh failure redaction, filters and structured invalid cursors.

The migration suite has 10 passing cases, including populated 0.7 upgrade,
downgrade/re-upgrade with predecessor duration preservation, foreign keys, fresh
head startup and actionable outdated-schema failure. Historical migrations were
not changed.

Nine browser cases cover workspace and callsign navigation; directory search,
sort and cursor paging; history filters, review actions and paging; unknown
confidence, missing audio and viewer controls; normalized segments and legacy
full text; generated-audio seeking; hostile transcript/QRZ text and unsafe URLs;
dashboard provisional/final polling; favorites/topology rendering; concurrent
Load More; and 1440px/390px layout overflow/control bounds. Browser JavaScript
errors fail the suite. CI now runs the browser suite separately.

## Verification boundaries

Implemented behavior and local automated verification are described above. No
manual human acceptance session, live repeater/AMI hardware check, real QRZ
lookup or production OIDC login was performed. External services are deliberately
stubbed in acceptance tests. Other browser engines and real mobile devices were
not tested. No release, tag, merge, or production migration was performed.

Historical-review/revision management UI, automatic speaker attribution and
complete attribution statistics remain outside 0.8 scope. Downgrade limitations
above require retaining a backup when human reviews matter.

## Follow-up review repair (2026-09-06)

Pulled five commits through `79686de`. The unchanged suite reproduced three
failures (241 passed): `66fe70f` had removed the history endpoint's successful
return. Ruff and mypy also detected this regression. New probes reproduced lost
legacy Archive mention display and query growth from 3 queries for one recording
to 17 for eight. The latter predated these five commits.

Restored the history response; kept callsign and cursor validation distinct using
public error constants; restored JSON fallback only without a selected normalized
transcript; batched Archive relationship loading; captured QRZ configuration once
per request independently of network failure. Kept the useful QRZ redaction and
duplicate-filter cleanup from the pulled changes. No schema or UI redesign.

Verification after repair:

- `pytest -q -W error::DeprecationWarning --cov=asl_transcriber --cov-report=term-missing`: **249 passed**, 16.31s, **86% coverage**; 13 unsuppressed SQLite resource warnings.
- `PLAYWRIGHT_BROWSERS_PATH=/tmp/repeater-scribe-browsers pytest browser_tests -q -W error::DeprecationWarning`: **9 passed**, 5.64s.
- `ruff check .`, `mypy src`, `python -m pip check`, `git diff --check`: passed.
- New behavior tests cover legacy/current serialization distinction, bounded query
  growth, invalid callsign versus cursor errors, internal error redaction, and QRZ
  configuration remaining true while further network attempts stop.
