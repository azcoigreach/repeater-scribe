# 0.9.2 verification

The 0.9.2 rollup includes the post-0.9.1 stabilization milestone: refresh/menu and
transcript playback state (#29), exclusive audio (#30), Callsigns favicon and
headings (#31/#32), whole-second Event pickers (#33), Favorites recovery (#39),
transcript action spacing (#44), and proxy idle-lifetime alignment (#49).

Package and application source report `0.9.2`. All **37** versioned UI script and
stylesheet references use `?v=0.9.2` and resolve to existing assets. The built
`asl_transcriber-0.9.2-py3-none-any.whl` contains matching distribution metadata
and application version. Historical 0.9.1 changelog and verification records are
retained.

## Commands and environment

Local verification uses Python 3.14.4, Chromium 151.0.7922.34, Caddy 2.11.4,
Uvicorn 0.52.4, disposable SQLite databases, and generated media/TLS identities.
CI runs the repository's Python 3.12 gate and dependency auditing on the PR's
final commit.

```bash
.venv/bin/ruff check .
.venv/bin/mypy src
.venv/bin/pytest -q -W error::DeprecationWarning
PLAYWRIGHT_BROWSERS_PATH=/tmp/scribe-playwright ASLT_TEST_CADDY_BINARY=/tmp/scribe-49-caddy .venv/bin/pytest browser_tests -q -W error::DeprecationWarning
.venv/bin/python -m pip wheel --no-deps --no-build-isolation --wheel-dir /tmp/scribe-092-wheel .
git diff --check
```

| Local check | Result |
| --- | --- |
| Ruff | Passed |
| mypy | Passed, 39 source files |
| Python suite | 312 passed in 20.82s |
| Browser suite through Caddy | 76 passed in 91.97s |
| Wheel build and version inspection | Passed, 0.9.2 |
| Versioned UI assets | 37 matching references, all targets exist |
| Relative documentation links | 33 resolve |
| Diff check | Passed |

These results cover the final version and asset changes. The subsequent CI run
and its final commit/status are recorded on PR #54; prior-commit CI is not
substituted for that run.

Version checks compare `pyproject.toml` and `asl_transcriber.__version__`, inspect
wheel `METADATA` and packaged `__init__.py`, and verify every versioned template
asset URL and target. The wheel and generated build output are temporary files,
not committed artifacts.

The browser gate runs the real application behind the reference Caddy HTTPS
proxy. It includes Event/Favorites polling, failed-read recovery, menu/focus and
topology-drag preservation, exclusive playback and seek/draft state, authorization,
CSRF/exact Origin, audio byte ranges, and a continuing SSE heartbeat. The direct
HTTPS variant is not rerun locally. CI also runs a short proxy idle-connection
regression. See [the detailed transient-read report](verification-transient-reads.md)
for the longer before/after experiment and its limits.

## Acceptance and release boundaries

On 2026-09-13 (America/Phoenix), the maintainer accepted proceeding with the
unreproduced 502 and revisiting it if errors return; this disposition is recorded
on [issue #49](https://github.com/azcoigreach/repeater-scribe/issues/49#issuecomment-5658019610).
The timeout correction is verified, while the original deployed cause remains
an inference. Additional deployed reproduction is no longer a blocker for PR #54.

No migration is added: the supported schema head remains `events_sessions`.
Existing migration tests remain in the Python gate. No live ASL3, GPU, QRZ,
node-control, or deployment checks are performed. The new package version and
release notes prepare 0.9.2; they do not create a release tag, publish an image,
or deploy it. See [upgrade and rollback instructions](upgrade-0.9.md).
