# Contributing

Thank you for helping improve ASL Transcriber.

## Development setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e '.[dev]'
```

## Quality gates

Before opening a pull request, run:

```bash
ruff check .
mypy src
pytest -q
```

## Pull request expectations

- Keep changes focused and easy to review.
- Add or update tests for behavior changes.
- Document notable design decisions in the docs or ADRs.
- Respect the code boundaries separating discovery, ingestion, transcription, API, and web layers.
