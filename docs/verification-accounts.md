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


## PR #57 review fixes (2026-09-28)

The follow-up restores invalid-session deletion on rejected browser requests and
includes idle deadlines in periodic cleanup. Denied admission for a known disabled
account carries the verified account ID past transaction rollback into its audit;
unknown identities remain unlinked. A shared `managed-accounts-1` asset revision
replaces the product-version cache token across all workspace scripts/styles.
Product version and migration revision are unchanged.

New regressions failed before their corresponding fixes: idle purge left two
expired rows, rejected requests retained both idle/absolute-expired sessions,
disabled-account admission audits had no account ID, and all six workspaces
still requested the shipped asset URLs. Coverage also checks expiry boundaries,
active-session/account/audit preservation, repeated cleanup, same-subject identities
at different issuers, unknown-identity denial, and CLI recovery dispatch, issuer
normalization, output and invalid inputs. Browser acceptance substitutes stale
JavaScript at the old URL and verifies the User can open the Event form through
the revised asset URL.

| Command | Follow-up result |
| --- | --- |
| `.venv/bin/ruff check .` | Passed |
| `.venv/bin/mypy src` | Passed, 43 source files |
| `.venv/bin/pytest -q -W error::DeprecationWarning` | 349 passed in 40.40s |
| `PLAYWRIGHT_BROWSERS_PATH=/tmp/issue55-playwright .venv/bin/pytest browser_tests -q -W error::DeprecationWarning` | 77 passed in 96.72s |
| `git diff --check` | Passed |

The same disposable databases, mocked external services and direct fixture TLS
server boundaries apply. The full Python suite includes fresh/prior-schema
migration coverage, although this follow-up does not change migrations. GitHub
CI and CodeQL results are recorded on the PR for the pushed commit.
