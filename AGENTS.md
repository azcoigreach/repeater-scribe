# Repeater Scribe agent guidance

## Start a task

- Read the linked issue, its latest comments, CONTRIBUTING.md, and relevant docs.
- Read .github/copilot-instructions.md for the existing Git and repository safety rules.
- Follow docs/development-workflow.md for issue readiness, implementation, and review.
- Treat the issue as the durable specification. Record scope decisions there; do not assume another chat's history is available.
- Inspect branch and worktree state. Preserve unrelated changes and work on one focused feature/fix branch from main unless another base is specified.
- Use Python where practical and preserve discovery, ingestion, transcription, API, and web boundaries.
- Do not silently implement unapproved future ideas listed in docs/plan.md.

## Implementation and verification

- Link the issue in the PR. Use Closes #N only when merging fully completes that issue; use Refs #N for partial work.
- Read current issue/PR comments before revising work and address actionable findings on the existing branch.
- Add behavioral regression coverage for fixes; use browser acceptance coverage for meaningful UI behavior changes.
- Follow the quality gates in CONTRIBUTING.md and report exact commands, outcomes, and checks not run.
- Verify migration changes against both a fresh database and an upgrade from the supported prior schema using disposable data.
- Keep product version changes and release tags scoped to explicit release work.
- Open a draft PR when implementation or verification remains incomplete.
- Report the PR URL, source/base branches, latest commit, and CI status. Do not describe pending checks as passing.
- Do not merge, publish a release, deploy, or issue live node-control commands without authorization for that action.

## Code Review Rules

### Evidence and history

- A detected callsign mention is not proof of the transmitting station or confirmed Event attendance. Keep mention evidence, explicit operator attribution, and confirmed roster entries distinct.
- Preserve raw transcripts, operator corrections, audit evidence, and historical recording/Event identity across retranscription, restart, and missing audio. Surface unknown confidence honestly.
- Flag changes that lose manual Event membership overrides, break retry/idempotency contracts, or mix UTC persistence with browser-local display/filter boundaries.

### Authorization and external systems

- Preserve viewer/operator/admin boundaries, session CSRF and exact-origin checks, scoped API tokens, and fail-closed internet configuration.
- Keep the ASL3 archive read-only. AMI control remains opt-in; acceptance of an AMI command is not confirmation of node state.
- Keep QRZ credentials and other secrets out of logs, responses, fixtures, and commits. Use bounded external calls and existing authorization controls.

### Review evidence

- Review the current PR commit and relevant surrounding code against the issue's acceptance criteria.
- Report concrete defects with file/line context, impact, and reproduction or reasoning. Distinguish verified failures from risks and untested behavior.
- Re-check changed code after fixes; an earlier review does not cover later commits.
