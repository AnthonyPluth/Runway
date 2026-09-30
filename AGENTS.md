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

## Frontend changes need before/after screenshots

Any change that alters how the app looks or behaves in the browser (`frontend/`, CSS, the pages Runway serves) needs
before and after screenshots in its PR, in the **Screenshots** section of the PR template. Only backend, test or docs
changes can say "N/A: why".

- Use the made-up demo data: `python run.py demo` seeds a throwaway database; never screenshot real accounts.
- Capture with Playwright and the Chromium that's already installed (`PLAYWRIGHT_BROWSERS_PATH` is set; don't run
  `playwright install`). Take *before* on `main` and *after* on your branch, the same page, data, viewport and scroll.
  Desktop (about 1280px wide) always; a phone viewport (about 390px) too when the change touches layout or spacing.
- Show the state that changed (an opened dialog, the expanded list, the empty state), not just the page at rest.
- Keep images small (PNG, a few hundred KB at most; crop to the area that matters). Commit them under
  `docs/pr-screenshots/<branch-name>/` and link them from the PR with a commit permalink
  (`https://github.com/<owner>/<repo>/blob/<commit sha>/docs/pr-screenshots/<branch-name>/after.png?raw=true`), which keeps
  working after the branch is deleted. Delete that folder in a later cleanup if the repo gets heavy.
- If something can't be captured (a device-only thing like the iPhone status bar, a live service), say exactly what
  wasn't checked and why, rather than leaving the section empty.

## Git and PRs

- Work on the branch you were given; don't push to `main`.
- Keep commits focused, with a clear message.
- Open a PR only when asked, and summarize what changed and why.
- Don't hard-wrap lines in PR descriptions, comments or issues: write each paragraph or list item as one line and let GitHub wrap it to the screen. (Code, commit messages and repo files keep their own wrapping.)

## Model routing

See [CLAUDE.md](CLAUDE.md) for which model to use for which kind of task.
