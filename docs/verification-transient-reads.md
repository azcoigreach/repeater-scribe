# Transient Favorites and Event reads: issues #39 and #49

This stabilization work is rolled into package/application version 0.9.2 at the
maintainer's request. No live deployment, restart, node command, archive access, or credential access
was performed during implementation. All test databases, media, identities,
TLS certificates, and services were disposable fixtures on loopback.

## Findings and scope

**Verified Favorites defect (#39):** `loadFavorites()` wrote failures into the
Node Controls output, then left that output unchanged after successful reads.
Network/JSON exceptions were not handled, and a fixed interval could overlap
slow requests. The new dedicated Favorites status retains the last successful
list, distinguishes an unloaded list from an empty list, and clears only on a
successful current-home read. One in-flight request is shared by refresh
triggers, times out after eight seconds, and schedules the next read ten seconds
after completion. Home changes abort and invalidate old requests. There is no
automatic Connect, Disconnect, Function, or Event mutation retry.

**Verified configuration mismatch (#49):** the old Caddy transport inherited a
120-second upstream idle lifetime while Uvicorn used five seconds, matching the
Event poll interval. The reference configuration now explicitly uses two seconds
in Caddy and five in Uvicorn. These settings govern idle connection reuse; they
do not terminate active audio or SSE responses. Caddy documents this mismatch
as a potential source of upstream resets and 502s:
[HTTP transport reference](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy#the-http-transport).

**Cause remains an inference:** issue #49 contains correlated evidence of a
healthy-runtime markers read reset and subsequent successful reads without an
application restart. That is distinct from the issue's earlier startup-window
connection refusals. Local baseline runs have not reproduced the reset. The
timeout correction removes the documented mismatch but does not prove that it
caused that deployed failure. No failed Favorites response or corresponding
deployment logs were available for #39. Its GET handler reads local stored
Favorites/statistics; it does not synchronously query the public statistics
service. A shared transport cause for Favorites is plausible, not established.

## Transport experiment

`scripts/verify_proxy_keepalive.py` runs the reference Caddy binary and Uvicorn
with synthetic Event/Favorites read paths. Chromium connects to Caddy over
HTTPS/HTTP2; Caddy connects to Uvicorn over HTTP/1.1. It compares the old 120s
setting against the checked-in Caddyfile's 2s setting, with 64 concurrent reads
per burst and 24 bursts separated by 4.99, 5.00, and 5.01 seconds. It also checks
the actual upstream TCP peer port across a 2.5-second idle gap. Forwarded-header
rewriting is disabled only in this synthetic diagnostic server so that the TCP
port is observable; the real application's proxy-header behavior is unchanged.

The script saves separate proxy logs, an application log, and `results.json`.
Only post-readiness traffic contributes to the reported bursts. A passing
corrected run requires no non-200 response and a new upstream connection after
2.5 seconds. The baseline need not fail: a finite successful run cannot establish
the absence of an intermittent race.

The final experiment uses Caddy **2.11.4**, Uvicorn **0.52.4**, and Chromium
**151.0.7922.34** on 2026-09-13:

| Configuration | Burst reads | Non-200 | Duration | Reused after 2.5s |
| --- | ---: | ---: | ---: | --- |
| Baseline 120s / 5s | 1,536 | 0 | 119.24s | Yes |
| Corrected 2s / 5s | 1,536 | 0 | 119.30s | No |

Preliminary runs also returned no 502s; their peer-port check was invalid because
Uvicorn replaced the port with zero while processing forwarded headers. That
instrumentation was corrected before the final experiment.

## Verification commands

Local application tests use the existing Python 3.14 environment; CI uses the
repository's required Python 3.12 environment. With the development/browser
dependencies installed and the pinned Caddy binary extracted to
`/tmp/scribe-49-caddy`:

```bash
.venv/bin/ruff check .
.venv/bin/mypy src
.venv/bin/pytest -q -W error::DeprecationWarning
PLAYWRIGHT_BROWSERS_PATH=/tmp/scribe-playwright ASLT_TEST_CADDY_BINARY=/tmp/scribe-49-caddy .venv/bin/pytest browser_tests -q -W error::DeprecationWarning
PLAYWRIGHT_BROWSERS_PATH=/tmp/scribe-playwright .venv/bin/python scripts/verify_proxy_keepalive.py --caddy /tmp/scribe-49-caddy --output /tmp/scribe-49-proxy-final
git diff --check
```

The browser fixture accepts `ASLT_TEST_CADDY_BINARY` to run the real application
behind the reference proxy using generated TLS certificates. Omitting it runs
the existing direct HTTPS fixture. CI extracts the image pinned in
`compose.internet.yml`, runs browser acceptance through it, and executes a
shorter four-burst transport regression.

Browser coverage includes 502/network/malformed-response recovery, persistent
and first-load failures, coalescing/timeout, obsolete success and failure,
separate command-result ownership, retained list order, open menus/focus, and
an active topology drag. An Event failed-read/success case preserves its loaded
recordings, exclusive audio player identity, seek position, and unsaved marker
note. Real application requests additionally exercise six five-second polling
bursts across Event collections and Favorites, anonymous/viewer/operator
boundaries, CSRF and exact Origin rejection, audio `206` byte ranges, and an SSE
heartbeat after both configured idle durations.

The first browser attempt could not start because matching Chromium was absent;
it was installed into `/tmp/scribe-playwright`. Early topology drag test failures
were fixture targeting/timing errors corrected before the final suite.
Final local results: Ruff passed; mypy passed for 39 source files; **312 Python
tests passed in 21.21s**; **76 browser/boundary tests passed in 96.44s** through
Caddy; the transport experiment and `git diff --check` passed. Dependency
auditing and Python 3.12 execution are delegated to CI. Direct HTTPS browser
acceptance was not rerun locally; this run used the production-shaped proxy
boundary. Current CI status belongs in the PR; pending checks are not passes.

## Maintainer acceptance and rollout

On 2026-09-13, the maintainer accepted the remaining reproduction limitation:
"we are going to go ahead and run with it. We can circle back around if the errors pop back up."
PR #54 may therefore close #49 on merge together with #39. This records an
acceptance decision; it does not turn the inferred root cause into a verified
diagnosis. Further deployed experimentation is deferred unless errors recur.

If investigation resumes, a deployed observation requires separate authorization.
Capture the image commit, effective timeout
configuration, exact route/home node, request protocol, UTC timestamps, proxy
error/response, and matching application logs; omit credentials and private
identifiers. Compare a documented pre/post observation window around the
five-second polling boundary. Do not infer resolution from a cleared banner.

No schema migration or data rewrite is included. A later authorized rollout must
apply the Caddyfile and application image together; custom startup commands must
retain a larger Uvicorn idle timeout than the proxy's. Rollback restores the prior
image/configuration without changing stored data, but reintroduces the old idle
lifetime mismatch. Merge, release, image publication, and deployment remain
separate actions.
