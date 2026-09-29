# Agent guide

Instructions for AI coding agents working in Runway, a self-hosted personal finance app (Python backend in `runway/`, Svelte web app in `frontend/`, browser extension in `extension/`). Human contributors can use it too; the README's Development section has the long version.

## Layout

- `runway/`: the backend. `server/` holds the HTTP handlers, `migrations/` the Alembic migrations, `static/` the served assets. Domain modules (`forecast.py`, `reports.py`, `networth.py`, `plaid*.py`, and so on) sit at the top level.
- `frontend/`: the web app (Svelte, Vite, Vitest, ESLint).
- `extension/`: the browser extension.
- `tests/`: backend tests, run with `unittest`.
- `docs/`: documentation and screenshots.

## Commands

Python goes through Poetry (Python 3.14).

- `make check`: everything CI checks (lint, tests, frontend checks). Run it before pushing.
- `make lint`: ruff, mypy, and ESLint.
- `make test`: `poetry run python -m unittest discover tests`.
- `make frontend-check`: type-check, lint, Vitest and build for `frontend/`.
- `make fix`: apply ruff's safe fixes; review the diff afterward.

## Conventions

- Match the surrounding code's style, comment density and naming. Ruff and mypy config live in `pyproject.toml`; don't silence a rule to get green.
- Real typography (’ – −) in user-facing strings and comments is intentional; don't "fix" it to ASCII.
- Schema changes need an Alembic migration in `runway/migrations/`.
- Add or update tests with the change. Don't skip, disable or delete a test to get CI passing.
- Never commit secrets. `.env.example` lists configuration; real values stay in `.env`.
- Read `SECURITY.md` before touching auth, encryption or anything that handles bank credentials.

## Git and PRs

- Work on the branch you were given; don't push to `main`.
- Keep commits focused, with a clear message.
- Open a PR only when asked, and summarize what changed and why.

## Model routing

See [CLAUDE.md](CLAUDE.md) for which model to use for which kind of task.
