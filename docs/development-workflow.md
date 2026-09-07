# Development workflow

Repeater Scribe uses GitHub as the durable record of planned work, decisions,
implementation, and verification. ChatGPT supports planning and independent
review; Codex implements bounded issues in a development environment; Eric
sets priorities, performs operational acceptance, and authorizes releases.

## One change from idea to release

1. Discuss the problem and desired operator behavior in ChatGPT.
2. Create or refine a GitHub issue with scope, acceptance criteria, examples,
   dependencies, and a verification plan. Copy the actual decisions into the
   issue; a link to a private chat alone is insufficient.
3. Mark the issue Ready once Eric accepts its scope and the requirements are
   actionable. Start with one implementation task at a time.
4. Hand the issue URL to Codex in the configured development environment.
   Codex reads the issue and comments, creates a branch from main, implements
   the change, and opens a linked PR with verification evidence.
5. CI checks the PR. Review the current diff in ChatGPT against the issue,
   inspect surrounding code and test results, and record actionable findings
   on the PR when asked to publish the review.
6. Codex addresses accepted findings on the same branch. Review subsequent
   commits and rerun affected checks until the change meets its criteria.
7. Eric validates relevant real ASL3/GPU/QRZ behavior in development, reviews
   the evidence, and authorizes merge. Close completed issues on merge.
8. Group merged work into a deliberate versioned release. A merged issue
   means implemented, not necessarily deployed. Record the released version.

Use Refs #N for partial work and Closes #N only for a fully completed issue.
Keep new feature requests discovered during review in separate issues unless
they are necessary to satisfy the current acceptance criteria.

## GitHub tracking setup

Recommended Project: **Repeater Scribe Development**.

| Field | Values / purpose |
| --- | --- |
| Status | Backlog, Ready, In progress, In review, Validation, Done |
| Priority | P0 critical, P1 next, P2 normal, P3 later |
| Area | Transcription, Callsigns, Archive, Events, Node control, QRZ, UI, Security, Infrastructure |
| Milestone | A planned release only after its scope is selected |
| Blocked | An explicit dependency/link and reason |

Use labels for issue kind (bug, enhancement, documentation, maintenance) and
area; use Project Status for progress so there are not two competing status
systems. A field or label by itself does not launch Codex.

Recommended views: a status board, a backlog table sorted by priority, and a
release view grouped by milestone. Enable available Project workflows for
adding repository issues and marking closed completed items Done. Review
closure reasons so cancelled work is not counted as shipped.

Projects, labels, milestones, repository rules, and Codex environment settings
are GitHub/account configuration. These documentation files do not provision
them. Track activation and verification in the workflow adoption issue.

Do not backfill every shipped feature as a new open issue. README.md,
CHANGELOG.md, and versioned verification docs describe the shipped baseline.
Audit docs/plan.md before migrating future ideas: it contains historical plans
as well as completed work. Create actionable backlog issues only after checking
the current implementation; discovery candidates are not release commitments.

## Development and release boundaries

Use short-lived feature/fix branches targeting main. A separate deployment
environment does not require a permanent develop branch.

Give development its own Compose project name, port bindings, data volumes,
database, credentials, and configuration. Use disposable or sanitized fixtures
for automated tests. Mount any authorized sample archive read-only. Do not
share a writable production database or enable live AMI commands in automated
tests. Real node, GPU, and QRZ acceptance requires a configured integration
environment and must be reported separately from fixture-based CI.

The existing CI runs Ruff, mypy, Python tests, Chromium browser acceptance, and
dependency auditing. CodeQL and Dependabot already provide additional checks.
Verify the active main ruleset requires the actual relevant passing check
contexts and resolved review conversations. Keep auto-merge disabled unless
Eric explicitly changes the policy. For a solo-maintained repository, do not
add a mandatory second human approval that the maintainer cannot supply.

The release workflow is triggered by version tags and publishes container
images. Before a release, verify checks on the release commit, align package
version and release notes, document migrations and backup/rollback procedures,
and perform development acceptance. Tagging, image publication, and deploying
that image are distinct actions. Never imply that merging a PR deployed it.

## Codex handoff

Start an implementation task in the chosen Codex environment with the issue
URL, repository, target branch, and instruction to follow AGENTS.md. Require
acceptance-criteria coverage, test evidence, and a linked PR.

For a configured Codex cloud repository, the documented GitHub PR triggers are
`@codex review` and follow-up instructions such as
`@codex fix the CI failures`. These run in Codex cloud; they do not dispatch
work to an existing local development server. Verify repository access,
environment setup, and review settings before relying on the integration.

Do not assume ordinary issue comments or every PR comment automatically start
implementation. Begin with explicit task handoff. If issue-triggered execution
on the development server is desired, specify and implement that separately:
trusted initiators, exact trigger, one task per issue, isolated worktrees,
credentials, task status, retry behavior, and no automatic production deploy.

## Review and completion

A review checks correctness, acceptance criteria, data preservation, security
boundaries, migration behavior, and relevant operator experience. It references
the reviewed commit and distinguishes tests run from code inspection.
Automatic Codex review is an additional pass; it is not operational acceptance.

Definition of done:

- Acceptance criteria are satisfied and evidence is attached to the PR.
- Relevant CI checks pass on the final commit; remaining limitations are explicit.
- Actionable review findings are resolved or have a recorded disposition.
- Relevant development acceptance, docs, and migration notes are complete.
- Eric authorizes merge; release/deployment status is recorded separately.

## References

- [GitHub Projects](https://docs.github.com/en/issues/planning-and-tracking-with-projects)
- [GitHub milestones](https://docs.github.com/issues/using-labels-and-milestones-to-track-work/about-milestones)
- [Codex GitHub review and task triggers](https://developers.openai.com/codex/third-party/github)
