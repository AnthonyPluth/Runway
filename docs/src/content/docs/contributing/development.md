---
title: Development
description: Setting up, running the checks, verifying in a browser, changing the database, the code layout and releases.
sidebar:
  order: 1
---

The conventions (style, tests, commit and pull request rules) are in [AGENTS.md](https://github.com/AnthonyPluth/Runway/blob/main/AGENTS.md), which every contributor and coding agent reads first. This page is the longer version: how the pieces fit and how to check a change.

## Setup and checks

```bash
make setup                 # Poetry's environment and the web app's packages
make check                 # everything CI runs that needs no browser or Postgres: run it before pushing
make pre-push              # the quick version (about a minute) for pushes before the last
make lint                  # ruff, mypy, import-linter, ESLint and Runway's own Semgrep rules
make test                  # the Python tests on SQLite (make test-parallel: across 4 processes, as CI runs them)
make frontend-check        # type-check, ESLint, Vitest and build for the web app
make docs-build            # the documentation site, failing on a broken link between pages
```

Python goes through Poetry (Python 3.14): `poetry run python run.py --no-sync` runs Runway without touching your bank, `poetry run python -m unittest tests.test_x` runs one test module, and `poetry add <package>` adds a dependency. For the quick checks on every commit (ruff, whitespace, YAML and TOML syntax, merge markers), install [pre-commit](https://pre-commit.com) and run `pre-commit install` once.

**Postgres.** CI runs every test on Postgres 16 too, in parallel, where tests that share the database can trip over each other. Run them yourself for a change to the database, the migrations or the tests, against a throwaway Postgres (its data in memory and nothing waiting for the disk; never settings for a real database):

```bash
docker run -d --rm --name runway-test-pg --shm-size=1g --tmpfs /var/lib/postgresql/data -p 5432:5432 \
  -e POSTGRES_USER=runway -e POSTGRES_PASSWORD=runway -e POSTGRES_DB=runway postgres:16 \
  -c fsync=off -c synchronous_commit=off -c full_page_writes=off -c shared_buffers=512MB -c max_connections=600
DATABASE_URL=postgresql://runway:runway@127.0.0.1:5432/runway make test-pg
docker stop runway-test-pg
```

A test that writes settings, runs a sync or goes through the server takes a database of its own with `own_database(self)` from `tests/shared.py`: on Postgres a schema borrowed from its process's pool, on SQLite a file. `make test-timings` prints where the tests' time goes.

**Only in CI:** the Postgres suites, the Docker image build, the Verify flows check (below), the security scans (Semgrep's registry packs, Trivy, zizmor, pip-audit, npm audit, CodeQL) and the independent agent review.

## One paved path

There is one way to do most things, and `make lint` fails on the old way. Use the helper; where a use is right for a reason, say why on the line (`# nosemgrep: <rule id> -- why`, `// eslint-disable-next-line no-restricted-syntax -- why`) rather than switching the rule off.

| Don't | Use |
| --- | --- |
| `urllib.request.urlopen` or `build_opener` outside `runway/tls.py` | `tls.urlopen` |
| `bool(body…)`, `float(body…)` or `int(body[…])` in `runway/server/api/` | `runway/validate.py` |
| `sa.text(f"…")`, or SQL built with `+`, `%` or `.format` | SQLAlchemy statements, or `sa.text(":name")` with bound parameters |
| a `0.005` literal outside `runway/money.py` | `money.CENT`, `money.is_zero`, `money.same_amount` |
| `date.today()` in the sync and provider modules | the `today` the caller passes |
| `datetime.utcnow()` | the local time, or `datetime.now(timezone.utc)` for another system |
| month arithmetic outside `runway/dates.py` | `dates.add_months`, `month_start`, `days_in_month`, `months_between` |
| `(e as Error).message` in the web app | `errMsg(e)` from `lib/act.ts` |
| `fetch(` outside `lib/api.ts` | `api()` |
| `.catch(() => {})` with nothing in the braces | a comment saying why the failure doesn't matter |
| a hand-written list of account kinds | `lib/accounts.ts` |

The Python rules are in `.semgrep/runway.yml`, each with a failing and a passing example in `.semgrep/examples/`; the web app's are in `frontend/eslint.config.js`, with their examples in `frontend/src/lint-rules.test.ts`.

## The web app

Svelte 5 and TypeScript in `frontend/` (Vite, Tailwind CSS, shadcn-svelte-style components on Bits UI, Lucide icons), built into `runway/static/app/`, which Runway serves at `/`. It needs Node 26 (`.nvmrc`).

```bash
cd frontend && npm ci
npm run dev        # http://localhost:5173/, reloading as you edit (start Runway on 8765 too)
npm run check      # type-check
npm run lint       # ESLint over the web app, the browser extension and the service worker
npm test           # Vitest
npm run build      # into runway/static/app/
```

## The documentation

[Astro Starlight](https://starlight.astro.build) in `docs/`, published to GitHub Pages by `.github/workflows/docs.yml` on every push to `main` that changes it. Each page is Markdown in `docs/src/content/docs/`, in a folder per sidebar group (`start/`, `using/`, `reference/`, `contributing/`), with its title, description and order in the front matter. `make docs` serves it with live reload; `make docs-build` fails on a broken link between pages.

Link to another page by its address, base included: `[Configuration](/Runway/reference/configuration/)`. Screenshots come from `screenshots/` (below), never from a picture taken by hand: `![alt](../../../../../screenshots/budget-phone.png)` from a page, and the same path imported in `index.mdx`. A change users would notice updates its page in the same pull request.

## Verifying a change in the real app

Unit tests don't show a layout or a picker working, so a change to the web app is also run in a browser. `make verify` makes a temporary database, fills it with the demo data (made-up, never yours), starts Runway and drives it with Playwright: each page at phone (440×956, an iPhone 17 Pro Max) and desktop (1280×800) widths, then the scripted flows. It saves a full-page screenshot of each page (`<page>-<width>.png`), the page's first screen (`<page>-top-<width>.png`, for the README and the docs) and each flow's steps, plus `report.json`, to `artifacts/verify/`, and exits non-zero on a console error, an uncaught error, a 5xx, a failed step or a page that never finished loading.

```bash
make verify                          # every page and flow
make verify PAGES="budget setup"     # only these pages, and the flows that start on them
make verify PAGES="budget-edit"      # only this flow (a flow's name is its file's), or device-cache
```

Verify the pages you changed with `PAGES`: a full run takes several minutes, a page a minute or two.

A flow is a small JSON file in `frontend/verify/flows/` (the format is in its `README.md`): open a route, click, fill, wait, screenshot. A pull request that changes the UI adds or edits one. A flow marked `signed_in` runs against a second demo server with sign-in on, with Chrome's virtual authenticator standing in for Face ID.

It runs on Linux (the dev container and CI) and needs `libfaketime` (`sudo apt-get install -y libfaketime`): the demo data is built relative to today, so the server and the seed run with the clock frozen at the day in `runway/verify.py`, and the browser's clock is set to the same day. Each shot also stops endless animations, waits for the page to settle, scrolls to the top, moves the pointer off the page and leaves toasts out, so two runs on the same tree write identical images. There is no tablet width: look at the rail layout (768 to 1023 px) by resizing a browser.

A shot is taken only once nothing on the page is still loading (no skeleton placeholder, nothing `aria-busy`, no transition still moving, the Investments page's live prices in) and is then taken a second time: it's kept when the two are byte for byte the same, retaken when not, and fails after four tries, so a half-loaded page is never kept. A flow's `expect_text` and `expect_focus` checks since its last action are made again after the shot. Transitions and the opening of dialogs, sheets and popovers take no time (a sheet that slid in was otherwise drawn a fraction of a pixel off, differently each run), and the app doesn't see the window resizing that Chromium does for a full-page capture (it switched a desktop page to the phone layout for a moment, closing a picker or collapsing a row). `make verify-twice` runs everything twice, on fresh data, and fails on any image that differs.

**On a busy machine** (several agents on one container), `make verify` takes care of itself:

- One run at a time per machine: a second one prints `another verify is running on this machine (…), waiting...` and starts when the first ends (an hour at most, then it exits 3; `RUNWAY_VERIFY_LOCK_WAIT` in seconds). The lock is `/tmp/runway-verify.lock` (`flock`), let go of when its process ends however it ends; a run started under `flock /tmp/runway-verify.lock …` counts it as its own.
- It waits up to ten minutes for the load to drop below 1.5 per CPU before it starts, and stretches every wait's limit by the load (up to 8 times), since each wait ends as soon as what it waits for is there.
- A page or flow that fails is run again, alone, on a fresh copy of the demo data, up to three times in all, more patiently each time. One that passed only on a retry is listed as `flaky` in the summary.
- All or nothing: only a run where every page and flow passed puts its images in `artifacts/verify/`. A failed run leaves its `report.json`, each failed step's `-FAILED-` image and, under `unverified/`, what it did capture, for looking at, not committing.
- The whole output also goes to its own log, `artifacts/verify-logs/verify-<time>-<pid>.log` (the path is printed first and last), so don't redirect it to a fixed file name another run could share.

A failed run ends with a `FAIL:` line per page or flow, with its first problem (a step that timed out, `never settled` and what was still loading, a check that `no longer held after the screenshot`, a console error or a 5xx); the log has each attempt's full message.

![A page make verify saved: the Budget page at phone width, with demo data](../../../../../screenshots/budget-phone.png)

### Screenshots in a pull request

`screenshots/` holds every image `make verify` writes, committed: the visual regression baselines, and the pictures the README and the docs show. A pull request that changes the UI runs

```bash
make screenshots
```

before its last push and commits what changed. It's `make verify` followed by `tools/sync_screenshots.py`, which copies the new images in, leaves identical files untouched and removes the ones whose page or flow is gone. It copies nothing unless the whole run passed; `make screenshots PAGES="budget budget-edit"` updates only those pages' and flows' images (removing those that a page or flow it ran no longer writes) and leaves every other page's and flow's alone. Never copy images into `screenshots/` by hand. The pull request's **Files changed** then shows each changed screenshot as an image diff, and the **Screenshot diff** workflow (`tools/screenshot_diff.py`) comments the changed pixels, with highlighted images in its artifact; `poetry run python tools/screenshot_diff.py --base origin/main --head HEAD --dry-run` prints the same locally.

Commit every change in the directory, including pages you didn't mean to touch: an unexpected diff is a leak, or a nondeterminism bug to report. The only images a change ever commits are these (`make fleet-checks` fails any other raster image, except the icons and logos in `extension/icons/`, `runway/static/` and `docs/src/assets/logo.png`), so a before and after is shown through their diff, never a hand-made comparison. `make verify` captures the dark theme only; check the light theme yourself for a change that depends on it, and say so.

## Changing the database

Edit `runway/storage/schema.py`, then generate a migration and check it over:

```bash
poetry run alembic revision --autogenerate -m "add a column"   # writes runway/storage/migrations/versions/…
poetry run alembic check                                       # the schema and migrations agree
```

- Number it after `main`'s newest (`0041_…py`, `revision = '0041'`) and give it a test in `tests/test_migrations.py`, `test_0041_…`, that downgrades, writes the data it changes, upgrades and checks the result. `make check` fails on two heads, a reused number or an untested migration.
- A new database is made from `schema.py` in one step, not by replaying migrations, so a column a migration adds goes last in its table in `schema.py` too (`tests/test_fresh_database.py` checks).
- A column that refers to another table's row gets a foreign key, `schema.refers(table, column, "accounts.id", ondelete)`: `CASCADE` when the row belongs to what it refers to, `SET NULL` when it only points at it. Deleting an account relies on them.
- A migration that removes data saves a backup first (see 0040). Repairs for data older versions saved are migrations too, run once.

### Queries

Every query, in the app and the tests, is a SQLAlchemy statement over the models in `runway/storage/models.py` (one class per table in `schema.py`), run with `conn.execute(statement)`, which compiles it for whichever database is in use. `conn.execute()` refuses SQL text, and `tests/test_orm_guard.py` fails on SQL text anywhere in `runway/` or `tests/`.

- `db.rows(conn.execute(select(...)))` gives a list of dicts, keyed by column name or `.label()`. `conn.orm` is an ORM Session on the same connection and transaction, for loading an object, changing a few attributes and adding one; not for building API replies from many rows. Always `conn.commit()`, never `conn.orm.commit()`, and keep every existing commit where it is (some sit before a slow network call so SQLite's lock isn't held through it).
- Shared fragments, never copied: `splits.parts()` (a split transaction as its parts), `db.not_investment()`, `db.account_label_expr()` (an account's name as lists show it), `db.instr()` (substring position on both databases), `db.upsert()` and `db.insert_ignore()` (`ON CONFLICT`, for either database).
- Portable only: no SQLite-only functions (`strftime`, `group_concat`, `instr`, `ifnull`), no `LIMIT -1`, every non-aggregate column in `GROUP BY`, values of the column's type (Postgres won't store letters in an integer column), rounding in Python. Dates are ISO text, compared as strings; booleans are 0/1 integers.
- When SQLAlchemy genuinely can't express something, `text()` with named parameters and a `# raw SQL: <why>` comment on the line above, still running on both databases. Migrations and the driver-level setup in `db.py` are exempt.
- A rewritten query keeps the same rows, order, keys and types: compare its output before and after on the demo data.

## Code layout

| Path | What |
|---|---|
| `run.py` | Starts the server; the `backup`, `restore`, `demo` and `verify` commands |
| `runway/server/` | The web server: `handler.py` (who may reach what, security headers, limits), `routes.py` (the route table, for the web app and `/mcp` alike), `api/` (one module per area), `oauth_http.py` and `mcp_*.py` (assistants), `static.py`, `sync.py` (the background sync) |
| `runway/domain/` | What Runway works out from your data: `forecast.py`, `statements.py`, `recurring.py`, `bankdays.py`; `categorize.py`, `rules.py`, `payees.py`, `splits.py`; `budgets.py`, `reports.py`; `networth.py`, `loans.py`, `equity.py`, `portfolio.py`, `planner.py`; `churning.py` and `churn_*.py`; `notify.py`, `deleted_accounts.py`, `demo.py`; `retail/` (store orders: the extension's key, one parser per store, matching and splitting) |
| `runway/providers/` | Each outside service: `simplefin.py`, `plaid*.py`, `carta*.py`, `prices.py`, `finnhub.py`, `realie.py`, `webpush.py`; `banktx.py` and `banks.py` are what the bank providers share |
| `runway/storage/` | The database: `db.py`, `schema.py`, `models.py`, `settings_keys.py`, `secretbox.py` (secrets encrypted at rest), `backup.py`, and `migrations/` |
| `runway/tls.py`, `validate.py`, `dates.py`, `money.py`, `monitoring.py`, `oidc.py`, `applock.py` | Shared by everything: the one module that opens outbound connections; checking input; month arithmetic; amounts to the cent; the log, scrubbed; sign-in; the app lock |
| `runway/static/` | The service worker, manifest, fonts and icons; the web app builds into `runway/static/app/` |
| `frontend/` | The web app: `src/pages/` one file per page, `src/lib/` the API client, formatting and components; `verify/` the browser check |
| `extension/` | The browser extension, plain scripts with no build step; its pure helpers are tested from `frontend/src/extension/` |
| `tests/` | The Python tests, including a mock OIDC provider |
| `tools/` | Scripts for the repository: the feature map, the API contract, the fleet checks, the screenshot sync and diff |

**Import boundaries** (`make lint`, through [import-linter](https://import-linter.readthedocs.io), contracts in `pyproject.toml`): only the server imports `runway/server/`; only `runway/tls.py` opens outbound connections; a provider doesn't import another provider; `runway/storage/` imports nothing above it, and only it imports the database drivers and Alembic. When a contract breaks, move the code rather than adding an exception.

## API handlers

A handler in `runway/server/api/` is listed in `routes.py`, takes `(conn, query, body, *ids)` and returns the JSON to answer, or a `common.Response` for a download or a stream. It checks everything it's sent (`runway/validate.py` for numbers, amounts and switches; `common.row_id`, `query_int` and `text` for ids and text) and refuses what it can't use with an `ApiError`, a 4xx with a message. Anything else it raises is a bug: `routes.dispatch` turns it into a 500 with only a reference and logs it without the message (`monitoring.report`), which could quote the request. A route an assistant shouldn't reach goes in `mcp_access.BLOCKED`.

- **Reads and 304s.** Every JSON `GET` reply carries an `ETag` of its bytes and `Cache-Control: no-store`; asked again with `If-None-Match`, an unchanged reply is a 304. So a route must never send a value that changes on every call. The web app keeps the last reply to each address in memory only and forgets them on locking, signing out, a 401 and a 423.
- **The feature map** ([Feature map](/Runway/contributing/feature-map/), `docs/feature-map.json`) lists each route with its handler, the web app files that call it, its tests and its docs page. It's generated: run `make feature-map` after changing routes, `api(` calls, tests or docs. A new route needs a test.
- **The API contract.** A covered route has its reply and body types in `runway/server/contract.py`, annotated on its handler, and the web app calls it with `apiCall<"METHOD /path">(…)` from `lib/contract.ts`. `make api-contract` generates `docs/openapi.json` and `frontend/src/lib/api-types.ts` from them; commit both. To cover a route, add its types, annotate the handler, run `make api-contract`, switch its callers to `apiCall` and add a call to `tests/test_api_contract.py`.
- **The app lock.** Anything kept for one device sits on its `app_locks` row (`runway/applock.py`) and ends with the row: signing out, the session ending, access lapsing, the lock turned off or on again. The row never travels in backups. Its one secret is the device cache's key share, handed out by `POST /api/lock/key-share` only to that session, unlocked, once after each passkey check. The web app's side is `lib/deviceCache.ts` (`lib/cacheCrypto.ts`, `lib/idb.ts`): only the lists `lib/swr.ts` already keeps go in, never a balance or anything worked out from today, and every failure is a miss, never an error. `make verify` runs it end to end.

## Releases

Every push to `main` runs the tests and builds the image for `linux/amd64` and `linux/arm64` with the next version baked in. Once the tests pass, GitHub Actions pushes the image as `ghcr.io/anthonypluth/runway` tagged `latest`, `1.2.3`, `1.2` and `1`, tags the version and publishes a [GitHub Release](https://github.com/AnthonyPluth/Runway/releases) with notes.

The version comes from the pull request titles merged since the last release: `feat: …` bumps the minor version, `fix: …` (or anything else) the patch, and `feat!: …` or a `BREAKING CHANGE:` line the major version, for a change that breaks an existing setup.

## Merging

A pull request is ready when its **Merge gate** passes: every check on its head commit green (`.github/scripts/merge-gate.sh`). Besides the tests and the security scans:

- **Fleet checks** (`tools/fleet_checks.py`, `make fleet-checks`): one Alembic head, a test for each new migration, the workflows' conventions, no raster image outside `screenshots/` and the icon folders, and a `Co-Authored-By: Claude <Model> <version> <noreply@anthropic.com>` trailer on each commit an AI agent wrote.
- **Verify flows** (`.github/workflows/verify.yml`): `make verify` in a real browser, when a pull request touches `frontend/`, the demo data, the verify runner or the workflow. It catches a flow whose selector or Tab order no longer matches.
- **Agent review** (`.github/workflows/agent-review.yml`): a pull request an agent wrote is reviewed against AGENTS.md by a fresh Claude Code session with no access to the one that wrote it. A blocking finding fails the gate until a new push is reviewed clean. A push that only merges `main` in, leaving the change byte for byte as reviewed, keeps the last pass.
- **Previews:** add the `needs_preview` label to a pull request for a temporary copy of Runway on its branch, with demo data, at a `trycloudflare.com` address posted in a comment and taken down after an hour (`.github/workflows/preview.yml`).
