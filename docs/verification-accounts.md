# Issue #55 verification

Implementation branch: `feat/55-managed-accounts`, targeting `main` at
`68648a264ac613dc1c62ff2b082642ee16f63807`. This is account-foundation work for the
0.10.0 milestone; product versions remain 0.9.2 pending deliberate release work.

## Local automated evidence

Run on 2026-09-14 with the repository virtual environment, Python 3.14.4:

| Command | Result |
| --- | --- |
| `.venv/bin/ruff check .` | Passed |
| `.venv/bin/mypy src` | Passed, 43 source files |
| `.venv/bin/pytest -q -W error::DeprecationWarning` | 337 passed in 38.81s |
| `PLAYWRIGHT_BROWSERS_PATH=/tmp/issue55-playwright .venv/bin/pytest browser_tests -q -W error::DeprecationWarning` | 76 passed in 95.60s |
| `git diff --check` | Passed |

Python tests cover a fresh database and a populated supported prior
`events_sessions` revision. Upgrade preserves token credentials/IDs/history and
historical audits, revokes issuer-less sessions/pending logins, and authenticates
the migrated named token with User authority. Existing archive migration tests
continue checking transcript, callsign review, missing-audio and FTS preservation.

Account regressions cover current-state authorization, verified OIDC sign-in
precedence, immutable identity keys, metadata/audit snapshots, last-Admin races,
transactional caller revalidation, device-local logout, all-session disablement,
future token role caps, CLI recovery service, CSRF/exact Origin and rate limiting.
All three actual SSE handlers are exercised through their protected response
iterators; separate idle-source tests cover disablement, demotion, logout, expiry
and token revocation with subscription cleanup. Browser tests verify existing
User controls continue to work, including explicit `data-role=user` acceptance.

TestClient stalled in sandboxed asyncio thread wakeups; completing HTTP/browser
tests required running outside that sandbox with disposable fixture data.
Playwright's matching Chromium was absent and was downloaded to the named `/tmp`
directory; the successful browser run used the fixture TLS server directly.

## Boundaries and remaining acceptance

CI runs Python 3.12, Caddy-backed browser acceptance, proxy idle-connection
regression and dependency auditing. Those CI outcomes belong to the current PR
commit and are reported on GitHub; this local record does not claim their result.
No local dependency audit, separate proxy regression, container build, live OIDC,
ASL3, GPU, QRZ, release or deployment acceptance was performed. No live AMI commands
were issued. Review and relevant development-environment acceptance precede any
human-authorized merge, release or deployment.

Migration/admission/recovery decisions are in [accounts](accounts.md); the full
route inventory is in [permissions](permissions.md).
