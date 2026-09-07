# Contributing

Thank you for helping improve Repeater Scribe.

Read [the development workflow](docs/development-workflow.md) for planning,
issue readiness, Codex handoff, review, and release boundaries. Coding agents
must also read [AGENTS.md](AGENTS.md).

## Development setup

Use Python 3.12 and install FFmpeg, matching CI.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e '.[dev,browser]'
python -m playwright install --with-deps chromium
```

## Quality gates

The existing CI runs these checks plus dependency auditing:

```bash
ruff check .
mypy src
pytest -q -W error::DeprecationWarning
pytest browser_tests -q -W error::DeprecationWarning
```

Run relevant checks during implementation and report exact results in the PR.
The required CI must pass on the final commit before merge. For documentation
and template-only changes, inspect links, template metadata, and the diff;
state that application tests were not run locally rather than claiming a pass.

## Pull request expectations

- Start with a scoped issue and use a focused branch targeting main.
- Add or update behavioral tests for behavior changes.
- Map the change to acceptance criteria and document significant design decisions.
- Respect discovery, ingestion, transcription, API, and web boundaries.
- Keep archive data read-only and preserve transcript/correction/Event history.
- Follow .github/copilot-instructions.md for existing Git and safety conventions.
- Separate merge, release, and deployment authorization.
