# Repository Copilot Instructions

## Branch Management

- Never make feature or fix changes directly on `main`.
- Before editing, check the current branch and worktree status.
- Create a descriptive branch from the current base branch before implementation.
- Use prefixes such as `feat/`, `fix/`, `docs/`, `test/`, or `chore/`.
- Keep one cohesive change per branch.
- Do not create or switch branches if the worktree contains relevant uncommitted changes without first preserving and understanding them.
- Do not force-push, rebase shared branches, delete branches, or modify remote history unless explicitly requested.

## Commits

- Use the repository-local Git identity:
  - Name: `KM7GHS`
  - Email: `km7ghs@fallbackengineering.com`
- Confirm the identity with `git config --local` before committing.
- Make small, focused commits using Conventional Commit subjects, for example:
  - `feat: add recording search`
  - `fix: handle incomplete archive files`
  - `test: cover activity log parsing`
- Run the relevant tests and quality checks before committing.
- Review `git diff --check` and the staged file list before committing.
- Never commit `.env`, ASL3 recordings, runtime databases, caches, model files, or generated package metadata.

## Pull Requests

- Push the feature branch with upstream tracking before opening a PR.
- Open pull requests against `main` unless the user specifies another base.
- Include a concise summary, user-visible behavior, test results, and deployment notes when applicable.
- Do not merge the PR automatically unless explicitly requested.
- After opening or updating a PR, report its URL, source branch, target branch, and latest commit.
- Check CI status after pushing and address failures on the feature branch.

## Repository Safety

- Treat `asl-monitor/` as a read-only ASL3 archive mount.
- Preserve the `.gitkeep` placeholder while keeping archive contents ignored.
- Do not stage or modify live recordings, daily activity logs, `.env`, `data/`, or `tmp/`.
- Do not use destructive Git commands such as `git reset --hard` or `git checkout --` unless explicitly requested.
- Preserve unrelated user changes and inspect overlapping edits before modifying files.
