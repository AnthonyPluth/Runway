# Agent guide

Instructions for AI coding agents working in Runway, a self-hosted personal finance app (Python backend in `runway/`, Svelte web app in `frontend/`, browser extension in `extension/`). Human contributors can use it too; [Development](docs/src/content/docs/contributing/development.md) has the long version, and the published docs are at https://anthonypluth.github.io/Runway/.

## Layout

- `runway/`: the backend, in packages whose imports `make lint` checks (import-linter's contracts in `pyproject.toml`):
  - `server/`: the HTTP handlers (`api/`), the background sync and the MCP server. Nothing else in `runway/` imports it.
  - `domain/`: what Runway works out from your data (`forecast.py`, `recurring.py`, `categorize.py`, `reports.py`, `networth.py`, `churning.py`, `retail/`, and so on).
  - `providers/`: the outside services (`plaid*.py`, `simplefin.py`, `carta.py`, `prices.py`, `finnhub.py`, `realie.py`, `webpush.py`). A provider doesn't import another; what they share has a module of its own (`banktx.py`, `banks.py`).
  - `storage/`: the database (`db.py`, `schema.py`, `models.py`, `settings_keys.py`, `secretbox.py`, `backup.py`) and `migrations/` (Alembic). It imports nothing above it, and only it imports the database drivers and Alembic.
  - At the top, what everything shares: `tls.py` (the only module that opens outbound connections), `validate.py`, `money.py`, `dates.py`, `monitoring.py`, `oidc.py`.
  - `static/`: the served assets.

  A broken boundary fails `make lint`: move the code to where its import is allowed rather than adding an exception. A new provider module joins its provider's contract, or gets one of its own.
- `frontend/`: the web app (Svelte, Vite, Vitest, ESLint).
- `extension/`: the browser extension.
- `tests/`: backend tests, run with `unittest`.
- `tools/`: scripts for the repo itself; `feature_map.py` generates the feature map, `api_contract.py` the API contract, `pr_screenshots.py` a UI PR's inline screenshots.
- `docs/`: the documentation site (Starlight), published to GitHub Pages. Pages are Markdown in `docs/src/content/docs/`; screenshots are in `docs/src/assets/screenshots/`.

## Commands

Python goes through Poetry (Python 3.14).

- `make check`: each tool once: ruff, mypy, import-linter, Runway's own Semgrep rules, the Python tests (SQLite), the web app's type-check, ESLint, Vitest and build, and the docs site's build. Run it before pushing. The security scans (Semgrep's registry packs, Trivy, zizmor, pip-audit, npm audit, CodeQL) run only in CI.
- `make lint`: ruff, mypy, import-linter (the backend's import boundaries), ESLint and Runway's own Semgrep rules (`.semgrep/runway.yml`, run through `pipx`).
- `make test`: `poetry run python -m unittest discover tests`.
- `make test-parallel`: the same tests across 4 processes, as CI runs them (`make check` uses it); `make test-pg` runs them against `$DATABASE_URL` (Postgres).
- `make frontend-check`: type-check, lint, Vitest and build for `frontend/`.
- `make fix`: apply ruff's safe fixes; review the diff afterward.
- `make verify`: runs the real app on demo data in a browser at phone, tablet and desktop widths, saving screenshots and a report to `artifacts/verify/` (`PAGES="budget setup"` limits the pages). It also saves a viewport-only `<page>-<width>-top.png` beside each full-page screenshot. UI PRs show those screenshots inline: see "Git and PRs" (demo data only).
- `make pr-screenshots PR=<n>`: pushes `make verify`'s `*-top.png` screenshots to the `pr-screenshots` branch and prints the markdown for the PR comment that embeds them (`tools/pr_screenshots.py`).
- `make fleet-checks`: the checks that replaced instructions (`tools/fleet_checks.py`, part of `make check`): one Alembic head, a test in `tests/test_migrations.py` for each new migration, the workflows' conventions, and the `Co-Authored-By` trailer on your commits since `origin/main`.
- `make feature-map`: regenerate the feature map; `make feature-map-check` checks it is current and every route has a test (part of `make check`).
- `make api-contract`: regenerate the API contract (`docs/openapi.json`, `frontend/src/lib/api-types.ts`); `make api-contract-check` checks it is current (part of `make check`).
- `make pre-push`: about a minute: the feature-map, API-contract and fleet checks, and the tests that read shared fixtures (see "Before you push"). `make check` stays the one full run.
- `make docs`: the documentation site with live reload; `make docs-build` builds it and checks the links between pages.

## Starting work

- **One issue, one session.** Work starts from an issue with its scope, acceptance criteria, dependencies and the model to use (see [CLAUDE.md](CLAUDE.md)); start a fresh session for each one, on that model, rather than running many pieces of work through one long session.
- **Decisions belong in the issue.** If the issue doesn't settle something a user would notice, ask instead of choosing. Otherwise keep existing behaviour exactly; a change that isn't asked for is a regression.
- **Done means checked.** An issue says how its result is checked (`make check`, `make verify` for anything on screen, a test that fails before the change); the PR shows that output, not a description of it.
- **Verify in proportion.** Run the tests your change touches while working, and `make check` once before pushing. CI runs the Postgres suites on every pull request: run `make test-pg` yourself only for changes to the database, migrations or tests (see "Tests that share the database" below), on a database of your own that you drop afterwards.
- **A correction becomes a check.** When a mistake is pointed out, fix it and add what would have caught it (a lint rule in `.semgrep/` or `frontend/eslint.config.js`, a test, or a check in `tools/fleet_checks.py`). A sentence here is the last resort, for what can't be checked.
- **Parallel work stays apart.** Agents working at once take separate areas. Two pull requests that change the same files, or both add a migration, land one at a time: the second merges `main` in and re-runs its checks.

## Start from the feature map

[`docs/feature-map.json`](docs/feature-map.json) (readable as the [Feature map](docs/src/content/docs/contributing/feature-map.md) page) lists every API route with its handler, the web app files that call it, the tests that exercise it and the docs page that describes it. Start there to find where a feature lives. It is generated: after changing routes, `api(` calls, tests or docs, run `make feature-map` and commit the result (`make check` and CI fail when it is stale). A new route needs a test; `tools/feature_map_allowlist.txt` holds the routes that had none, and only shrinks.

## Conventions

- Match the surrounding code's style, comment density and naming. Ruff and mypy config live in `pyproject.toml`; don't silence a rule to get green.
- One paved path; lint fails on the old way (`.semgrep/runway.yml`, `frontend/eslint.config.js`). Fix a finding with the helper, or justify it with an inline ignore and a reason; don't switch the rule off. Python: HTTP only through `tls.urlopen` (no `urllib.request.urlopen` or `build_opener` outside `runway/tls.py`); request bodies in `runway/server/api/` through `runway/validate.py` (no `bool(body…)`, `float(body…)`, `int(body[…])`); no SQL built from strings (`sa.text(f"…")`); the half cent is `money.CENT` (no `0.005` outside `runway/money.py`); sync and provider modules take the `today` they're given (no bare `date.today()`); no `datetime.utcnow()`; month arithmetic only in `runway/dates.py`. Web app: `errMsg(e)` from `lib/act.ts`, not `(e as Error).message`; `fetch(` only in `lib/api.ts`; `.catch(() => {})` carries a comment saying why; account kinds come from `lib/accounts.ts`, not a hand-written list.
- Real typography (’ – −) in user-facing strings and comments is intentional; don't "fix" it to ASCII.
- Schema changes need an Alembic migration in `runway/storage/migrations/`, numbered after main's newest, with its own `test_<revision>_…` in `tests/test_migrations.py` (checked).
- The API contract: a route it covers has its reply and body types in `runway/server/contract.py`, on its handler's annotations, and the web app calls it with `apiCall<"METHOD /path">(…)` from `lib/contract.ts`. Change a covered reply or body there, run `make api-contract` and commit the generated files; cover a route the same way (see [Development](docs/src/content/docs/contributing/development.md), "The API contract").
- A change users or contributors would notice updates its page in `docs/src/content/docs/` in the same PR. Link between pages with absolute paths (`/Runway/start/docker/`); a broken one fails the build.
- Add or update tests with the change. Don't skip, disable or delete a test to get CI passing.
- Never commit secrets. `.env.example` lists configuration; real values stay in `.env`.
- Read `SECURITY.md` before touching auth, encryption or anything that handles bank credentials.

## Mistakes that keep coming back

Review findings on agents' pull requests here fall into the same few kinds. Check your change for each before you push. When one can be checked, it moves out of this list into a check (lint in `.semgrep/runway.yml` or `frontend/eslint.config.js`, or `tools/fleet_checks.py`); each item says which it is. The ones marked *human-judged* have no check: the independent review (`.github/workflows/agent-review.yml`) reads every agent's pull request for them, and a blocking finding fails the merge gate. The one push it doesn't read is one that only merges `main` in and leaves the pull request's own change byte for byte as last reviewed: that review's passing verdict carries over.

- **Failure paths** (human-judged). For every new error, rejection or early return, know what the user sees, and test it. Don't leave stale state that says things are fine: a sync that half-fails must not clear the last warning, a failed reload must not leave old numbers on screen looking current, and a job that couldn't start isn't a failed run.
- **Tests that share the database** (human-judged). On Postgres every test module shares one schema, and CI runs modules in parallel. A test that writes settings, or runs a sync, the AI categorizer or a request through the server, takes its own database with `tests/shared.py`'s `own_database(self)`. Run the Postgres tests before pushing a change to tests (docs/src/content/docs/contributing/development.md). In Vitest, use fake timers for anything debounced, so a save doesn't leak into the next test.
- **Private data in logs and reports** (human-judged). Sentry reports, logs and error pages never carry amounts, merchants, account ids, query strings or credentials; that includes exception text (a database error names the row it was writing). Go through `monitoring.scrub`, and test what's sent. The one exception is the AI's prompts and replies on its own spans (on with a Sentry DSN, which is your own project): nothing else may carry what they do.
- **Personal financial data in the repo** (human-judged). The repository is public. Never put data read from anyone's instance (balances, amounts, merchants or payees, card or account names, lenders, budgets, income, dates of real transactions, people's names) into PR titles or descriptions, review replies, issues, commit messages, code comments, tests, fixtures or docs, even to explain a bug. Describe the case in general terms and use made-up figures and names (as `tests/` does).
- **Access that outlives the person** (human-judged). A new kind of session, grant, token or key must end when its owner can no longer sign in (`oidc.access_lapsed`), like browser sessions, assistants' OAuth grants, the browser extension's key and devices' notifications do.
- **Time zones** (partly checked: lint rejects a bare `date.today()` in sync and provider modules and `datetime.utcnow()`; the rest is human-judged). "Today" and the daily sync's hour are the machine's local time (`TZ`). Anything that tells another system about a time (a schedule, a timestamp) carries the same zone, not UTC by default.
- **Undo and restore** (human-judged). Putting something back restores exactly what was there, without normalizing it on the way; test the round trip with awkward values.
- **GitHub Actions** (checked: `tools/fleet_checks.py` checks bash by default, SHA pins with a version comment and paginated `gh api` lists; zizmor checks `persist-credentials`, template injection and pinning; concurrency groups and agents' allowed commands are human-judged). Workflows run bash with `-eo pipefail` (each file's `defaults`); keep it. On a pull request's `closed` event `github.ref` is main's, so build concurrency groups from `github.event.pull_request.number`. Check out with `persist-credentials: false`, pin actions by SHA with the version in a comment, pass event values to scripts as environment variables, paginate `gh api` lists, and allow an agent only the exact commands it needs. Run zizmor on a workflow you change.
- **Layout** (human-judged). A layout change is checked at phone, tablet (768 px) and desktop widths, not only the one it was written at.

## Before you push

Each push starts CI and an independent review, so find what you can locally and push once. The cloud container is a shared machine that can't run the whole suite, so run the targeted checks:

- The tests for what you changed, then `make pre-push`. It covers what most often fails CI because a change leaks into a shared fixture: new demo data in `runway/domain/demo.py` (`demo.seed` is read by `test_demo`, `test_api_contract`, `test_api_handlers`, `test_api_queries`, `test_api_validation`, `test_budget_*`, `test_churning_api` and `test_backup`; put data only one test needs in that test); a new route missing from `tests/test_api_contract.py`; a forecast change that moves `tests/fixtures/forecast_golden.json` (regenerate it only when the change is meant to); generated files that weren't regenerated (`make feature-map`, `make api-contract`); and a schema change's migration, its `test_<revision>_…` and the backup tests.
- Put a test method directly under its own decorators: one added between a `@unittest.skipIf` and its test takes the skip.
- For web app changes run `make lint` and `make frontend-check`. If ESLint or svelte-check won't start in a fresh container (`Could not find "svelte" in plugin "svelte"`, missing `esrap` types), `frontend/node_modules` is incomplete: `cd frontend && rm -rf node_modules && npm ci` (`.claude/hooks/session-start.sh` does this when it finds it). Don't push to let CI report the error.
- When something fails that you didn't expect, find the cause (run the one test, read the traceback) before changing anything, and put all the fixes in one push. A guess that needs CI to confirm costs a whole cycle.

## Git and PRs

- Work on the branch you were given; don't push to `main`.
- Keep commits focused, with a clear message. An agent's commit ends with a `Co-Authored-By: Claude <Model> <version> <noreply@anthropic.com>` trailer naming the model that wrote it (checked on every pull request).
- Before pushing to a PR, run `make check` (and `make pre-push` before each earlier push).
- A UI PR shows its `make verify` screenshots inline. The screenshots are gitignored and can't be attached through the API, so once the PR is open (the script needs its number): run `make verify`, then `PR_SCREENSHOTS_TRAILERS=$'Co-Authored-By: …\nClaude-Session: …' make pr-screenshots PR=<n>` (add `FILES="artifacts/verify/budget-phone-top.png …"` to show only some pages; the trailers are the ones your commits carry). It pushes the `*-top.png` files to the orphan `pr-screenshots` branch under `pr-<n>/` (never part of a PR's diff, never forced) and prints markdown with a heading per width; post that exactly as a PR comment with `mcp__github__add_issue_comment` (its `---` and "Generated by Claude Code" footer are already in it). Only ever publish `make verify`'s made-up demo data: the repository is public, so nothing from a real instance, and the PR description links to nothing private. `make verify` captures the dark theme only: the comment says the light theme wasn't checked, so check it yourself (and say so, with `--theme light` if you publish its screenshots) when the change is theme-sensitive. Skip it for a PR with no UI change, and say so in the description.
- Open a PR only when asked, and summarize what changed and why. Keep the description short and professional: a few bullets on the change and how it was tested, not an essay. Don't say who asked for it or reported it.
- A PR's title decides the next version when it's merged: `feat: …` releases a minor version, `fix: …` or anything else a patch, and `feat!: …` (or a `BREAKING CHANGE:` line) a major one, for a change that breaks an existing setup.
- A PR is ready when its "Merge gate" check passes: every check green (`.github/scripts/merge-gate.sh`), including, on an agent's PR, the independent "Agent review" with no blocking finding (reviewed again on each push, except one that only merges `main` in with the PR's own change unchanged, which keeps the last pass). Dependabot's updates merge themselves through it.
- Don't hard-wrap lines in PR descriptions, comments, issues or the body of a commit message: write each paragraph or list item as one line, and let GitHub wrap it to the screen. Only code and repo files keep their own wrapping. Keep the commit subject to one short line.

## Model routing

See [CLAUDE.md](CLAUDE.md) for which model to use for which kind of task.
