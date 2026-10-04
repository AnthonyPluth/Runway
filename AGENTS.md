# Agent guide

Instructions for AI coding agents working in Runway, a self-hosted personal finance app (Python backend in `runway/`, Svelte web app in `frontend/`, browser extension in `extension/`). Human contributors can use it too; [Development](docs/src/content/docs/contributing/development.md) has the long version, and the published docs are at https://anthonypluth.github.io/Runway/.

## Layout

- `runway/`: the backend. `server/` holds the HTTP handlers, `migrations/` the Alembic migrations, `static/` the served assets. Domain modules (`forecast.py`, `reports.py`, `networth.py`, `plaid*.py`, and so on) sit at the top level.
- `frontend/`: the web app (Svelte, Vite, Vitest, ESLint).
- `extension/`: the browser extension.
- `tests/`: backend tests, run with `unittest`.
- `docs/`: the documentation site (Starlight), published to GitHub Pages. Pages are Markdown in `docs/src/content/docs/`; screenshots are in `docs/src/assets/screenshots/`.

## Commands

Python goes through Poetry (Python 3.14).

- `make check`: each tool once: ruff, mypy, the Python tests (SQLite), the web app's type-check, ESLint, Vitest and build, and the docs site's build. Run it before pushing. The security scans (Semgrep, Trivy, zizmor, pip-audit, npm audit, CodeQL) run only in CI.
- `make lint`: ruff, mypy, and ESLint.
- `make test`: `poetry run python -m unittest discover tests`.
- `make test-parallel`: the same tests across 4 processes, as CI runs them (`make check` uses it); `make test-pg` runs them against `$DATABASE_URL` (Postgres).
- `make frontend-check`: type-check, lint, Vitest and build for `frontend/`.
- `make fix`: apply ruff's safe fixes; review the diff afterward.
- `make docs`: the documentation site with live reload; `make docs-build` builds it and checks the links between pages.

## Conventions

- Match the surrounding code's style, comment density and naming. Ruff and mypy config live in `pyproject.toml`; don't silence a rule to get green.
- Real typography (’ – −) in user-facing strings and comments is intentional; don't "fix" it to ASCII.
- Schema changes need an Alembic migration in `runway/migrations/`.
- A change users or contributors would notice updates its page in `docs/src/content/docs/` in the same PR. Link between pages with absolute paths (`/Runway/start/docker/`); a broken one fails the build.
- Add or update tests with the change. Don't skip, disable or delete a test to get CI passing.
- Never commit secrets. `.env.example` lists configuration; real values stay in `.env`.
- Read `SECURITY.md` before touching auth, encryption or anything that handles bank credentials.

## Mistakes that keep coming back

Review findings on agents' pull requests here fall into the same few kinds. Check your change for each before you push.

- **Failure paths.** For every new error, rejection or early return, know what the user sees, and test it. Don't leave stale state that says things are fine: a sync that half-fails must not clear the last warning, a failed reload must not leave old numbers on screen looking current, and a job that couldn't start isn't a failed run.
- **Tests that share the database.** On Postgres every test module shares one schema, and CI runs modules in parallel. A test that writes settings, or runs a sync, the AI categorizer or a request through the server, takes its own database with `tests/shared.py`'s `own_database(self)`. Run the Postgres tests before pushing a change to tests (docs/src/content/docs/contributing/development.md). In Vitest, use fake timers for anything debounced, so a save doesn't leak into the next test.
- **Private data in logs and reports.** Sentry reports, logs and error pages never carry amounts, merchants, account ids, query strings or credentials; that includes exception text (a database error names the row it was writing). Go through `monitoring.scrub`, and test what's sent. The one exception is the AI's prompts and replies on its own spans (on with a Sentry DSN, which is your own project): nothing else may carry what they do.
- **Personal financial data in the repo.** The repository is public. Never put data read from anyone's instance (balances, amounts, merchants or payees, card or account names, lenders, budgets, income, dates of real transactions, people's names) into PR titles or descriptions, review replies, issues, commit messages, code comments, tests, fixtures or docs, even to explain a bug. Describe the case in general terms and use made-up figures and names (as `tests/` does).
- **Access that outlives the person.** A new kind of session, grant, token or key must end when its owner can no longer sign in (`oidc.access_lapsed`), like browser sessions, assistants' OAuth grants, the browser extension's key and devices' notifications do.
- **Time zones.** "Today" and the daily sync's hour are the machine's local time (`TZ`). Anything that tells another system about a time (a schedule, a timestamp) carries the same zone, not UTC by default.
- **Undo and restore.** Putting something back restores exactly what was there, without normalizing it on the way; test the round trip with awkward values.
- **GitHub Actions.** Workflows run bash with `-eo pipefail` (each file's `defaults`); keep it. On a pull request's `closed` event `github.ref` is main's, so build concurrency groups from `github.event.pull_request.number`. Check out with `persist-credentials: false`, pin actions by SHA with the version in a comment, pass event values to scripts as environment variables, paginate `gh api` lists, and allow an agent only the exact commands it needs. Run zizmor on a workflow you change.
- **Layout.** A layout change is checked at phone, tablet (768 px) and desktop widths, not only the one it was written at.

## Git and PRs

- Work on the branch you were given; don't push to `main`.
- Keep commits focused, with a clear message.
- Before pushing to a PR, run `make check`.
- Open a PR only when asked, and summarize what changed and why. Keep the description short and professional: a few bullets on the change and how it was tested, not an essay. Don't say who asked for it or reported it.
- A PR's title decides the next version when it's merged: `feat: …` releases a minor version, `fix: …` or anything else a patch, and `feat!: …` (or a `BREAKING CHANGE:` line) a major one, for a change that breaks an existing setup.
- A PR is ready when its "Merge gate" check passes: every check green (`.github/scripts/merge-gate.sh`). Dependabot's updates merge themselves through it. Labelling an issue `claude` has an agent open a PR for it (`.github/workflows/claude-issue.yml`).
- Don't hard-wrap lines in PR descriptions, comments, issues or the body of a commit message: write each paragraph or list item as one line, and let GitHub wrap it to the screen. Only code and repo files keep their own wrapping. Keep the commit subject to one short line.

## Model routing

See [CLAUDE.md](CLAUDE.md) for which model to use for which kind of task.
