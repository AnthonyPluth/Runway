---
title: Development
description: Setting up, running the checks, changing the database, the code layout and releases.
sidebar:
  order: 1
---

Contributor conventions (style, tests, commit and PR rules) are in [AGENTS.md](https://github.com/AnthonyPluth/Runway/blob/main/AGENTS.md).

## Setup and checks

```bash
poetry install --no-root                               # dependencies and the tools CI runs (ruff, mypy, ...), into .venv
poetry run python run.py --no-sync                     # run without touching your bank
poetry run python -m unittest discover tests           # the test suite (SQLite)
DATABASE_URL=postgresql://... poetry run python -m unittest discover tests   # the same tests against Postgres
poetry run coverage run -m unittest discover tests && poetry run coverage report   # how much they cover
poetry run unittest-parallel -t . -s tests -j 4   # the same suite across 4 processes (what CI does): about 3x faster; `make test-parallel`
poetry run mypy                                        # type-check the Python (settings in pyproject.toml)
poetry add <package>                                   # add a dependency (updates pyproject.toml and poetry.lock)
```

To run the tests on Postgres as CI does (in parallel, where tests that share the database can trip over each other), start a throwaway one, as CI does: its data in memory (`--tmpfs`) and nothing waiting for the disk (`fsync`, `synchronous_commit` and `full_page_writes` off), so a crash loses it. That's fine for a database that lives for one test run, and never for Runway's own database, the image or the Compose files. Docker's default 64 MB of shared memory isn't enough for the schemas the tests make, and `max_connections` needs at least 30 per test process plus 100:

```bash
docker run -d --rm --name runway-test-pg --shm-size=1g --tmpfs /var/lib/postgresql/data -p 5432:5432 \
  -e POSTGRES_USER=runway -e POSTGRES_PASSWORD=runway -e POSTGRES_DB=runway postgres:16 \
  -c fsync=off -c synchronous_commit=off -c full_page_writes=off -c shared_buffers=512MB -c max_connections=600
DATABASE_URL=postgresql://runway:runway@127.0.0.1:5432/runway make test-pg
docker stop runway-test-pg
```

`make test-pg` runs the suite against the Postgres at `$DATABASE_URL`, in one process per CPU (at least 4, at most 16: `tools/test_jobs.sh`), after checking that the variable is set. Whatever Postgres you use must be UTF-8: a cluster you make yourself needs `initdb -E UTF8 --locale=C.UTF-8` (the `postgres` Docker image already is). A test that writes settings or goes through the server takes a database of its own (`own_database` in `tests/shared.py`): on Postgres a schema borrowed from its test process's pool, made once and emptied when the test gives it back (the pool drops its schemas left by an earlier run on the same machine when it starts), so running the suite twice on the same database gives the same result. Drop the database when you're done with it.

A new database, in the tests or a first start, is made from `runway/storage/schema.py` in one step (`db.create_fresh`) rather than by replaying every migration, and `tests/test_fresh_database.py` checks both give the same tables, column order included. So a migration that adds a column puts it last in `schema.py` too, where the migration leaves it.

`make test-timings` prints where the tests' time goes: the slowest tests and classes, and the time spent making and giving back databases (on SQLite, or on Postgres with `DATABASE_URL` set; `TEST_ARGS="-k tests.test_backup."` runs part of it). A manual run of the Docker image workflow with "timings" ticked prints the same for each CI shard of the Postgres tests in the run's summary.

To run one CI shard of the Postgres tests (CI splits them across three runners, each with its own Postgres), add its modules: `... unittest-parallel -t . -s tests -j 4 $(python tests/shard.py 2/3)`.

CI's Postgres is the same official `postgres:16` image, with the settings above, but pulled through Google's Docker Hub mirror (`mirror.gcr.io/library/postgres:16`, set in `.github/workflows/docker.yml`): GitHub's runners share addresses, so Docker Hub's limit on anonymous pulls used to fail the test jobs before any test ran. The tag follows Postgres 16's minor releases, so CI picks them up without a change here; to pull from Docker Hub instead, set the image back to `postgres:16`. Locally, `postgres:16` from Docker Hub is fine.

`make check` runs the checks to run before you push, each tool once: ruff, mypy and import-linter, Runway's own Semgrep rules, the fleet checks (see [Merging](#merging)), the feature map and API contract checks, the Python tests (on SQLite, in parallel as CI runs them), and the web app's type-check, ESLint, Vitest tests and build, and the docs site's build. `make pre-push` is the quick version for the pushes before the last (about a minute): the feature map, API contract and fleet checks, and the tests that read shared fixtures (the demo seed, the golden forecast, the contract's sample data, migrations and backups). `make setup` installs the backend's and the web app's dependencies on a fresh checkout. `make lint`, `make test` (serial, for a clearer failure), `make test-parallel` and `make frontend-check` run one part. The security scans run only in CI, each on the pull requests it can affect (see `.github/workflows/security.yml`): Semgrep, Trivy, zizmor, pip-audit, npm audit and CodeQL. So do the Postgres tests (see above to run them yourself). For the quick checks on every commit (ruff, trailing whitespace, YAML/TOML syntax, merge-conflict markers, large files), install [pre-commit](https://pre-commit.com) and run `pre-commit install` once.

## One paved path

Earlier clean-ups left one way to do most things, and `make lint` (so `make check`, and CI) fails on a copy of the old way. Use the helper instead; where a use is right for a reason, say why on the line (`# nosemgrep: <rule id> -- why` in Python, `// eslint-disable-next-line no-restricted-syntax -- why` in the web app) rather than switching the rule off.

| Don't | Use |
| --- | --- |
| `urllib.request.urlopen` or `build_opener` outside `runway/tls.py` | `tls.urlopen` |
| `bool(body…)`, `float(body…)` or `int(body[…])` in `runway/server/api/` | `runway/validate.py` |
| `sa.text(f"…")`, or SQL built with `+`, `%` or `.format` | SQLAlchemy expressions, or `sa.text(":name")` with bound parameters |
| a `0.005` literal outside `runway/money.py` | `money.CENT`, `money.is_zero`, `money.same_amount` |
| `date.today()` in the sync and provider modules | the `today` the caller passes (`today or date.today()` as its default is fine) |
| `datetime.utcnow()` | the local time, or `datetime.now(timezone.utc)` for another system |
| month arithmetic (`relativedelta(months=…)`, `calendar.monthrange`, `year * 12 + month`) outside `runway/dates.py` | `dates.add_months`, `month_start`, `days_in_month`, `months_between` |
| `(e as Error).message` in the web app | `errMsg(e)` from `lib/act.ts` |
| `fetch(` outside `lib/api.ts` | `api()` |
| `.catch(() => {})` with nothing in the braces | a comment saying why the failure doesn't matter |
| a hand-written list of account kinds (`["checking", "savings"]`) | `lib/accounts.ts` |

The Python rules are in `.semgrep/runway.yml`, with a failing and a passing example each in `.semgrep/examples/` (`make semgrep` runs the examples, then the code; CI's Semgrep job does too). It runs through `pipx`, at the version `.github/workflows/security.yml` pins. The web app's rules are in `frontend/eslint.config.js`, with their examples in `frontend/src/lint-rules.test.ts`.

## The web app

The web app is Svelte 5 + TypeScript in `frontend/` (Tailwind CSS, components in the shadcn-svelte style on Bits UI, Lucide icons). Runway serves its build at `/`, so build it once before running Runway from a checkout. It needs Node 26 (`.nvmrc` names it, for `nvm use` or `fnm use`; CI reads the same file):

```bash
cd frontend && npm ci                 # its packages, into frontend/node_modules
npm run dev                           # http://localhost:5173/ (reloads as you edit; start Runway on 8765 too)
npm run check                         # type-check it (CI runs this)
npm run lint                          # ESLint over it, the browser extension and the service worker (CI runs this)
npm test                              # unit tests for its pure logic and the extension's helpers, with Vitest (CI runs these too)
npm run coverage                      # the same, measuring how much of the web app they run (the frontend badge)
npm run build                         # into runway/static/app/, which Runway serves at / (the Docker image does this)
```

## The documentation

This site is [Astro Starlight](https://starlight.astro.build) in `docs/`, published to GitHub Pages by `.github/workflows/docs.yml` on every push to `main` that changes it. Each page is a Markdown file in `docs/src/content/docs/`, in a folder per sidebar group (`start/`, `using/`, `reference/`, `contributing/`); its front matter sets the title, the description and its place in the group (`sidebar.order`). Images go in `docs/src/assets/`.

```bash
make docs          # http://localhost:4321/Runway/, reloading as you edit
make docs-build    # builds it into docs/dist and fails on a broken link between pages
```

Link to another page by its address, base included: `[Configuration](/Runway/reference/configuration/)`, or `/Runway/start/docker/#error-reports-optional` for a heading. Link to files in the repository with their GitHub address.

## Verifying a change in the real app

Unit tests don't show a layout or a picker working, so a change to the web app is also run in a browser. `make verify` (or `python run.py verify [page…]`) makes a temporary database, fills it with the demo data (the made-up data `python run.py demo` adds, never yours), starts Runway on a free port, and drives it with Playwright: each page (`overview`, `transactions`, `budget`, `recurring`, `networth`, `reports`, `churning`, `setup`) at phone (440×956 px, an iPhone 17 Pro Max) and desktop (1280×800 px) widths, and saves a full-page screenshot of each (`<page>-<width>.png`). There is no tablet width: a change to the tablet rail (768 to 1023 px) is looked at by resizing a browser, and nothing of that is committed. The screenshots and `report.json` (console errors, failed requests) go to `artifacts/verify/`, which is not committed. It exits non-zero on a console error, an uncaught error in the page, a 5xx response or a failed scripted step, and says which page, width and request.

```bash
make verify                          # every page, and the scripted flows
make verify PAGES="budget setup"     # only these pages (and the flows that start on them)
```

It uses the Chromium that is already installed under `PLAYWRIGHT_BROWSERS_PATH` (`/opt/pw-browsers` by default); without one, run `npx playwright install chromium` in `frontend/` once. A scripted flow is a small JSON file in `frontend/verify/flows/`: a list of steps such as opening Settings → Categories and tapping a category's emoji. A pull request that changes the UI adds or edits a flow for it; the format is in `frontend/verify/flows/README.md`. A flow that needs a sign-in (`"signed_in": true`, the app lock) runs against a second demo server with sign-in on and a session made for it, with Chrome's virtual WebAuthn authenticator standing in for Face ID or Touch ID. Pull requests that change the UI commit the updated screenshots (below).

### The phone tab bar

On a phone `frontend/src/lib/components/MobileNav.svelte` draws the navigation as a floating pill, inset 12px from the screen's sides and lifted 12px (plus the safe-area inset) off the bottom edge, as frosted glass: a 4% white tint over a 28px backdrop blur with `saturate(2)` and `brightness(1.1)`, no outline (the border is kept, for the layout, but transparent), a faint inner highlight along the top edge and a soft drop shadow. A browser without `backdrop-filter` gets the solid popover surface. `--tabbar-h` in `app.css` is the distance from the screen's bottom to the pill's top (70px plus the inset); the page's bottom padding, the toasts and a sticky form footer are measured from it.

The glass costs contrast, so the inactive labels and icons are `#b9bec7` (relative luminance 0.51), lighter than the shared `muted-foreground` (`#9aa0aa`, 0.35), set in `MobileNav.svelte` only. For 4.5:1 the label needs a backdrop no brighter than a luminance of about 0.07 at `#b9bec7` (about 0.039 at `muted-foreground`). Measured in headless Chromium at 440×956 with the GPU path on (`--use-gl=angle --use-angle=swiftshader --enable-unsafe-swiftshader --ignore-gpu-blocklist`, so `backdrop-filter` renders), over every page of the demo data scrolled to six positions, with the labels hidden so only the glass was sampled: the labels measure at least 5.3:1 on average (lowest over the Overview's chart) and 4.7:1 at the brightest pixel behind any label. With `muted-foreground` they measured 3.7:1 and 3.3:1, which is why it isn't used. The current tab is white on a lighter pill, and well above the limit. Bright content the demo pages don't have (a white card, say) would still lower it; for a guarantee over any content, raise the tint (a `bg-popover/60` over the blur) and drop the `brightness-110`, at the cost of the glass look.

### Screenshots in a pull request

`screenshots/` holds one full-page screenshot of every page and scripted flow at phone and desktop widths (`<name>-<width>.png`), all in the one directory and committed. They are the visual regression baselines, and the only images a change commits are the ones `make verify` writes: show a before and after through the changed screenshots' diff in the pull request, never a hand-made comparison image (`make fleet-checks` fails a raster image that is not a page or flow image `make verify` can write into `screenshots/`, or an icon or logo in `extension/icons/`, `runway/static/` or `docs/src/assets/`). A pull request that changes the UI runs

```bash
make screenshots
```

before its last push and commits what changed. `make screenshots` is a full `make verify` followed by `tools/sync_screenshots.py` (standard library only), which copies the new screenshots in, leaves identical files untouched and removes the ones whose page or flow is gone. The pull request's **Files changed** then shows each changed screenshot as an image diff (2-up, swipe or onion skin), so a reviewer sees what the change did to the page.

Commit every change in the directory, including pages you didn't mean to touch: an unexpected diff is how a change that leaks into another page gets noticed. The demo data's dates follow the day `make verify` runs, so a date or a chart's axis can differ for that reason alone. The repository is public: only `make verify`'s made-up demo data is ever committed, and the independent review opens every added or changed image to check it. `make verify` captures the dark theme only; check the light theme yourself for a change that depends on it, and say so. A pull request with no UI change has nothing to update, and says so.

## Changing the database

Edit `runway/storage/schema.py`, then generate a migration and check it over:

```bash
poetry run alembic revision --autogenerate -m "add a column"   # writes runway/storage/migrations/versions/…
poetry run alembic check                                       # the schema and migrations agree
```

A column that refers to another table's row (an account's id, above all) gets a foreign key, `schema.refers(table, column, "accounts.id", ondelete)`: `CASCADE` when the row belongs to what it refers to (an account's transactions), `SET NULL` when it only points at it (the account a card is paid from). Deleting an account (`deleted_accounts.remove`) relies on them, and `tests/test_deleted_accounts.py` fails for a column named like an account id without one, or a settings key named by an account id that isn't in `settings_keys.PER_ACCOUNT`. Ids kept on purpose after what they name is gone (the deleted accounts list, investment accounts' ids, Plaid's) have none. A backup restores with the keys checked when the restore commits, so rows may come in any order.

Give each migration the next four-digit number after main's newest (`0041_…py`, `revision = '0041'`) and a test of its own in `tests/test_migrations.py`, a method named `test_0041_…` that downgrades to the revision before it, writes the data it changes, upgrades and checks the result. `make check` and CI fail when there is more than one Alembic head (two branches that each added the next number), a number is used twice, or a migration has no test (`tools/fleet_checks.py`).

Runway applies it on its next start. Queries, in the app and in the tests, are SQLAlchemy statements over the ORM models (`Connection.execute()` doesn't take SQL text), compiled for whichever database is in use; [Queries with SQLAlchemy](/Runway/contributing/orm/) is the guide.

## Code layout

| Path | What |
|---|---|
| `run.py` | Starts the server; `backup`, `restore` and `demo` commands |
| `runway/server/` | Web server: `handler.py` (who may reach what, security headers, request limits, reading bodies and sending answers, `serve()`), `routes.py` (the API's route table: finding a request's route and answering it, for the web app and `/mcp` alike) and `api/` (the API, one module per area), `oauth_http.py` (OAuth for `/mcp` and its consent page), `static.py` (the web app's files), `sync.py` (background sync) |
| `runway/server/mcp_server.py`, `mcp_access.py`, `mcp_oauth.py` | The MCP server's tools, what an assistant may reach, and OAuth for connecting one |
| `runway/providers/` | Each outside service Runway reads from or sends to. Providers don't import each other; what two of them share sits in a module of its own |
| `runway/providers/simplefin.py`, `sfinvest.py` | Bank sync and SimpleFIN investment positions |
| `runway/providers/plaid.py`, `plaidapi.py`, `plaidbank.py` | Plaid: investments; its API client; banks and cards (per-account provider, transactions, card statements) |
| `runway/providers/banktx.py`, `banks.py` | Shared by the bank providers: what storing a bank transaction works the same way for with either (a posted one taking over its pending version, and matching up the history when an account switches); whether any bank is connected |
| `runway/providers/carta.py`, `carta_web.py`, `prices.py`, `finnhub.py`, `realie.py`, `webpush.py` | Carta equity; price data and Finnhub's live prices; home values; sending push notifications |
| `runway/domain/` | What Runway works out from your data. It doesn't import the server |
| `runway/domain/forecast.py`, `recurring.py`, `statements.py`, `bankdays.py` | Cash-flow forecast, card statements (Plaid's and entered ones), recurring items and missed payments, bank holidays |
| `runway/domain/categorize.py`, `categories.py`, `rules.py`, `payees.py`, `splits.py` | Rules, history and AI categorization; the category tree; merchant names shortened from the bank’s text; split transactions |
| `runway/domain/brands.py`, `merchants.py` | Which institution each account belongs to, and their logos (Logo.dev, by name) |
| `runway/domain/budgets.py`, `reports.py` | Budgets and reports |
| `runway/domain/networth.py`, `loans.py`, `equity.py`, `portfolio.py`, `planner.py`, `tracked.py` | Net worth, loans, equity grants, investment performance, the retirement planner and hand-tracked holdings |
| `runway/domain/churning.py`, `churn_*.py`, `bank_bonuses.py` | Credit card churning: cards, benefits, cards found on your accounts, plans, bank bonuses |
| `runway/domain/notify.py`, `deleted_accounts.py`, `demo.py` | What to send a push notification about; deleting an account and keeping it deleted; the sample data |
| `runway/domain/retail/` | Amazon, Target and Costco orders from the browser extension: `token.py` (the extension's key), `parsers/` (one module per store), `items.py` (categorizing items), `match.py` (pairing charges with bank transactions), `split.py` (splitting a transaction by its order), `undo.py` and `view.py` |
| `runway/storage/` | The database, at the bottom: it imports nothing from the domain, the providers or the server, and only it imports the database drivers and Alembic |
| `runway/storage/db.py`, `schema.py`, `models.py`, `settings_keys.py`, `secretbox.py`, `backup.py` | Database connections (SQLite or Postgres), the schema and its ORM models, the settings' names, secrets encrypted at rest, backups |
| `runway/storage/migrations/`, `alembic.ini` | Alembic migrations, applied on start-up (with foreign keys off on SQLite while they run: batch mode remakes tables). Repairs for data saved by older versions are migrations too, run once, not code run at every start. A migration that removes data saves a backup first (see 0040) |
| `runway/tls.py`, `validate.py` | The only module that opens outbound connections (every https request goes through it); checking numbers and other input, including numbers in a provider's reply |
| `runway/dates.py`, `money.py`, `monitoring.py`, `oidc.py` | Month arithmetic (a day a month doesn't have is its last) and month keys; amounts to the cent, and splitting a total into whole cents; logs and Sentry; OpenID Connect sign-in |
| `frontend/` | The web app (Svelte): `src/pages/` one file per page, `src/lib/` the API client, formatting and components (`components/settings/` holds Settings' tabs) |
| `extension/` | The browser extension (Amazon, Target, Costco and Carta): `background.js` runs the imports, with one file each for Runway's API, the store address allow-list, hidden frames and tabs, and each store. Plain scripts with no build step; their pure helpers are tested by `frontend/src/extension/` |
| `runway/static/` | Files Runway serves beside the app: the service worker (`sw.js`), manifest, fonts, icons, and `page.css` for the sign-in pages; the web app builds into `runway/static/app/` |
| `tests/` | Unit and end-to-end tests, including a mock OIDC provider |
| `pyproject.toml`, `poetry.lock` | Dependencies (Poetry) |
| `data/` | Your database (not in Git) |

### Import boundaries

`make lint` (so `make check`, and CI) runs [import-linter](https://import-linter.readthedocs.io) over `runway/`, with the contracts in `pyproject.toml`:

- Only the server imports `runway/server/`: the domain, the providers, storage and the shared modules don't.
- Only `runway/tls.py` opens outbound connections (`ssl`, `socket`, `http`, `websocket`, `requests`). The few modules that need one of these for another reason are listed, each with why. urllib can't be split this way, so Semgrep keeps `urlopen` in `tls.py`.
- A provider doesn't import another provider. What two of them share goes in a module of its own (`banktx.py`, `banks.py`) or in the domain.
- `runway/storage/` imports nothing from the domain, the providers or the server, and only it imports the database drivers and Alembic. Everything else reaches the database through `db.session()` and `db.connect()`.

When a contract breaks, move the code to where its import is allowed rather than adding an exception. A new provider module joins its provider's contract, or gets one of its own.

## API handlers

A handler in `runway/server/api/` is listed in `routes.py`'s `ROUTES`, takes `(conn, query, body, *ids)` and returns the JSON to answer, or a `common.Response` for anything else (a download, a logo, a stream: `common.download` writes a download's headers). A route that takes a file instead of JSON is marked `@common.upload(limit)`, and one that opens its own database sessions (a sync, a restore) `@common.own_session`. A route that shouldn't be an assistant's goes in `mcp_access.BLOCKED`. It checks everything it's sent before using it: numbers, amounts of money, whole numbers, days and on/off switches with `runway/validate.py` (a switch sent as `"false"` is off; an amount is under `validate.MAX_AMOUNT`), and ids, query-string numbers and text fields with `server/common.py`'s `row_id`, `query_int` and `text`. What it can't use, it refuses with `ApiError`: a 4xx and a message saying what's wrong.

Anything else a handler raises is a bug. `routes.dispatch`, which answers the web app's calls (`/api/…`) and the assistants' (`/mcp`) alike, turns it into a 500 with only a reference, and logs and reports it (`monitoring.report(values=False)`: the error's type and where it was raised, never what it said, which can quote the request or name a row). A database busy with something else (SQLite's lock, or a Postgres lock timeout, deadlock or serialization failure: `db.is_busy`) is a 503, to try again.

The [feature map](/Runway/contributing/feature-map/) lists each route with its handler, the web app files that call it, its tests and its docs page. It is generated by `tools/feature_map.py`: run `make feature-map` after changing routes, `api(` calls, tests or docs. `make check` and CI fail when it is out of date, or when a route has no test and isn't on `tools/feature_map_allowlist.txt`, a list of the routes that had none, which only shrinks.

### The API contract

Some routes have a typed contract with the web app, so a field renamed on one side fails a check instead of drifting silently. So far: the accounts list, the Budget page's month and setting a budget, and the transactions list, adding a transaction and deleting one (`Endpoints` in `frontend/src/lib/api-types.ts` lists them); the rest follow the same way.

- **The backend.** The reply and request body types are TypedDicts in `runway/server/contract.py`. A covered handler is annotated with them: its return type is its reply, and the annotation on its body (third) parameter is what it takes (`body: BudgetSet` and `-> BudgetSaved` on the handler that saves a budget). mypy then holds the handler to them. A body is what the web app sends: the handler still checks every value with `runway/validate.py`. Where a reply is built from database rows (plain dicts to mypy), `tests/test_api_contract.py` calls the handler on sample data and checks the real reply against the contract.
- **The generated files.** `make api-contract` (`tools/api_contract.py`) reads the route table and those annotations and writes `docs/openapi.json`, an OpenAPI description of the covered routes, and from it `frontend/src/lib/api-types.ts`. Commit both; `make check` and CI fail when they are out of date. Don't edit them by hand.
- **The web app.** Call a covered route with `apiCall` from `lib/contract.ts` (`api()` underneath), naming the route as `Endpoints` does (`"METHOD /path"`), with the address to call: `apiCall<"GET …">(address)`. The address, the method, the body and the reply are type-checked against the contract, so `npm run check` fails on a call or a field that no longer matches. Types the pages share (`Tx`, `BudgetMonth`, ...) are the generated ones, re-exported.

To cover another route: add its types to `contract.py`, annotate its handler, run `make api-contract`, switch its callers to `apiCall`, and add a call to `tests/test_api_contract.py` (which fails until every covered route is checked there).

### Reads and 304s

Every JSON reply to a `GET` under `/api/` carries an `ETag`, a SHA-256 of the reply's bytes (`common.body_etag`), and stays `Cache-Control: no-store`. Asked again with `If-None-Match` naming it (weak `W/"…"` or strong, alone or in a list), an unchanged reply is a `304` with no body. Because the ETag is the reply itself, anything that changes what a route says (an edit, a sync, the day rolling over) is a new one: a route needs nothing of its own to get this, and must never send a value that changes on every call (a timestamp of the request) or every read becomes a full reply again. `api()` in `lib/api.ts` keeps the last reply to each address in memory only, never on disk, sends its ETag and uses it on a 304; it forgets them on locking, signing out, a 401 and a 423, together with the lists `lib/swr.ts` remembers for instant paint, and a reload starts empty. Unlike those lists, a change made through the API keeps them, since the server checks each one again; so a list painted from memory is confirmed by its refresh, often with a 304.

After a change to some transactions, the Transactions page loads only those rows again (`GET /api/transactions?id=…&id=…`, at most 100: each of them as the list now has it, or none when it has left the filters, with the whole list's `total` and `sum`) instead of the whole list (`TxListing.reloadRows`). It falls back to the whole list whenever a few rows can't show the change: a row that's new to the list or has a new date, a transfer category, which can change another row, or a list painted from memory and not yet confirmed.

### The app lock's device rows

Anything Runway keeps for one device goes on that device's `app_locks` row (`runway/applock.py`), so it ends with the row: signing out on that device, the session running out or being deleted, someone taken off `OIDC_ALLOWED_EMAILS` or a group's sign-in lapsing (the session's fixed end), turning the lock off, and turning it on again (a new row). The row is removed, never marked, and `app_locks` doesn't travel in backups, so a restore can't bring a device back. A write to a row is conditional on the state the request was let through in (`unlocked_until > now`, and the row's own `id`): a Lock now landing in between leaves it locked, the request gets the 423 a locked session gets, and nothing that extends or recreates access is written.

The one secret on the row is the device's cache key share (`key_share`, encrypted with `secretbox`, re-encrypted with the rest when `RUNWAY_SECRET_KEY` changes). `POST /api/lock/key-share` hands it out (`LockKeyShare` in `runway/server/contract.py`: the share as base64url and an `expires` of server time plus `applock.SHARE_WINDOW`, 72 hours) only to that session, unlocked, once after each passkey check (an unlock or turning the lock on, within `applock.SHARE_FRESH` seconds), at most `applock.SHARE_LIMIT` times an hour. It answers 403 without a fresh check, 423 when locked, 429 when asked too often and 400 when the lock is off. It's made the first time it's asked for and then stays the same until the row ends, since a new one makes the device's whole cache unreadable. It's never logged or reported (the request log and Sentry carry the route and status only, and a failure while handing it out is reported without what the error said), and `/api/lock` and everything under it is out of the assistants' reach (`mcp_access.BLOCKED`); the browser extension's key only reaches `/api/ext/`. `tests/test_applock.py` checks each of these.

### The encrypted cache on the device

`lib/deviceCache.ts` keeps an encrypted copy of what `lib/swr.ts` remembers, so a cold start paints those lists right after the unlock (#379). It registers itself with `persistWith`: `remember` also writes there, `recall` reads there when memory has nothing, and `dataChanged` (what `lib/api.ts` calls for a change) drops its records. `clearCache` alone (locking) only empties memory; the lock module closes the device cache (`closeCache`, dropping the key) on every lock and the re-lock timers, and deletes it (`wipe`) on `forget` (signing out, a 401, the lock off), on a 423, and `sweep` checks it at launch. After an unlock whose passkey gave a PRF secret, `lock.opening` holds App's page back (its loading placeholder) until the cache is open, `OPENING_MS` (600 ms) at most: the page reads what's kept when it's first drawn, so without that it would often be drawn before the share's request and the read had finished, and paint nothing from the device. The cryptography is in `lib/cacheCrypto.ts` (WebCrypto: HKDF-SHA-256 to non-extractable AES-GCM-256 keys) and the storage in `lib/idb.ts` (IndexedDB); there's no dependency for either.

Rules for changing it:

- Only names `persistable` allows are kept, only what `swr.ts`'s callers already stripped, and never a value `carriesExcluded` finds an `EXCLUDED` field in. Don't widen any of the three to keep a balance, a statement, a due date, an expected amount or anything else worked out from today or the forecast; `deviceCache.test.ts` reads the stored plaintext back to check.
- A record's authenticated data is its name, `SCHEMA` and data version (`dataVersionOf` Runway’s state reply: the release and the last sync). Changing what's stored or how means bumping `SCHEMA`, so old records are misses.
- Every failure is a miss, never an error: nothing in it throws, shows anything, or holds up the app or the lock. A record that doesn't open is deleted; IndexedDB failing means no cache.
- Painting from it is the same as painting from memory: marked as not yet current, always revalidated, and taken down if that fails.

The tests run it with the real lock, lists and API client over a fake fetch and the fake IndexedDB in `src/test/fakeIndexedDB.ts` (microtask-driven, so it works with Vitest's fake timers; it can fail to open or fail writes). Install a fresh one per test (`installFakeIndexedDB()`) and wait for the cache's background work with `settled()`. Fake only `Date` while it opens (`vi.useFakeTimers({ toFake: ["Date"] })`): WebCrypto answers on its own, outside the timers. `make verify` runs it end to end with a virtual authenticator that has PRF (`cacheCheck` in `frontend/verify/verify.mjs`: kept sealed, painted on a cold start before the server's list arrives, not past the 72 hours, nothing left after signing out). It runs last because it signs the shared demo session out.

## Releases

Every push to `main` runs the tests and, at the same time, builds the image for `linux/amd64` and `linux/arm64` with the next version baked in (and checks that it starts). Nothing is published until the tests pass; then GitHub Actions:

1. pushes the image (already built, so this is quick) as `ghcr.io/anthonypluth/runway` tagged `latest`, `1.2.3` (and `v1.2.3`), `1.2` and `1`;
2. tags the version and publishes a [GitHub Release](https://github.com/AnthonyPluth/Runway/releases) with notes;
3. deletes untagged leftovers. Every released version stays available, so you can pin one (`image: ghcr.io/anthonypluth/runway:1.2`) and upgrade when you choose.

Versions follow `vMAJOR.MINOR.PATCH`, decided by what's been merged since the last release. Pull request titles start with a type (they end up in each merge commit's message): `feat: …` bumps the minor version, `fix: …` (or anything else) the patch, and a `!` before the colon (`feat!: …`, `fix!: …`) the major version, for a change that breaks an existing setup. `#minor` or `#major` at the end of a line, or a line starting with `BREAKING CHANGE:`, work too. (A message that only mentions them mid-sentence doesn't count.) The running version is shown under **Settings → Data**.

## Merging

A pull request is ready when its **Merge gate** status passes: every check on its head commit green (`.github/scripts/merge-gate.sh`). Besides the tests and the security scans, that includes:

- **Fleet checks** (`tools/fleet_checks.py`, `make fleet-checks`): one Alembic head, a test for each new migration, the workflows' conventions (bash by default, actions pinned by SHA with their version in a comment, `gh api` lists paginated), and that each commit an AI agent wrote names its model in a trailer, `Co-Authored-By: Claude <Model> <version> <noreply@anthropic.com>`.
- **Verify flows** (`.github/workflows/verify.yml`): runs `python run.py verify` (the pages and the scripted flows in `frontend/verify/flows/`, in a real browser on the demo data) when a pull request, or a push to `main`, touches `frontend/`, `runway/domain/demo.py`, `runway/verify.py` or the workflow itself; otherwise it doesn't run at all, and the merge gate has nothing to wait for. It is what catches a flow whose selector no longer matches, or whose Tab order has changed, before it sits broken on `main`. A static check couldn't: the labels are built in the Svelte components and the focus order only exists in a browser. It is not part of `make check` (which needs no browser, so its time doesn't change); run `make verify PAGES="budget"` for the same check on one page. It installs Playwright's Chromium for the version `frontend/package-lock.json` pins and the run takes a few minutes in all. A failing run prints a `FAIL:` line per flow and width: fix the flow when the UI change was meant, the UI when it wasn't.
- **Agent review** (`.github/workflows/agent-review.yml`): a pull request an agent wrote (its commits carry a `Claude-Session:` or Claude `Co-Authored-By:` trailer) is reviewed by a fresh Claude Code session that has no access to the one that wrote it. It reads the change against `AGENTS.md` with only Read, Grep and Glob, confined to the review's directory, and loads no settings, hooks, skills, plugins, MCP servers or `CLAUDE.md` (`.github/scripts/agent-review-run.sh`). It posts its findings as a comment and sets the **Agent review** status: a blocking finding fails it, and with it the merge gate, until a new push is reviewed clean. It runs main's copy of the workflow and prompt, so a pull request can't change how it is reviewed. Anyone else's pull request gets a passing status saying it isn't an agent's.

  A push after a review is re-reviewed from where the last review left off: the comment records the commit it read, and when the new head descends from it the reviewer also gets that review's findings and the diff since. It checks each earlier finding is fixed and reads the new lines closely, with the whole change still in front of it; its findings replace the earlier ones. When there is no earlier commit to start from (the first review, a rebase, or anything going wrong), it reviews the whole change. A push that only merges `main` in isn't reviewed again when the last review passed: if every commit since is a merge and the pull request's own change (its diff against where it meets `main`) is byte for byte the one reviewed, the passing status is copied to the new head with a description saying so. A blocking or failed review is never carried forward, and anything that can't be shown to be identical (a conflict resolved by hand, a file `main` changed too) is reviewed.

  The diff the reviewer reads leaves out generated files and fixtures that deterministic checks cover (`docs/openapi.json`, `frontend/src/lib/api-types.ts`, `docs/feature-map.json`, the Feature map page, `tests/fixtures/forecast_golden.json`), lockfiles and images. They are still listed as changed and the reviewer can still open them; it flags a generated file that looks stale beside its source, and it opens every changed image (a diff can't show one) to check screenshots for private data. The list is `GENERATED` in `.github/scripts/agent_review.py`; nothing under `.github/` is ever left out. Documentation is always reviewed: the review is the only check for personal financial data in the repository, which is public.

  It needs one of two repository secrets (**Settings → Secrets and variables → Actions**): `CLAUDE_CODE_OAUTH_TOKEN`, for a Claude Pro or Max subscription (run `claude setup-token` and paste the token it prints), or `ANTHROPIC_API_KEY`, an API key from the Claude Console, which is used when both are set. The review always uses Sonnet, never Opus, whatever the change (security-sensitive ones included): the repository owner decided that, so the workflow fixes the model and no variable changes it. The `AGENT_REVIEW_BUDGET_USD` variable sets the most one review may spend, as Claude Code estimates it (3 US dollars), and `AGENT_REVIEW_MAX_TURNS` the most round trips to the model it may take (60). Both sit well above what a review takes (each run logs its turns and estimated cost); a review that hits either fails as an error rather than passing, so raise the limit and re-run it.

[Dependabot](https://github.com/AnthonyPluth/Runway/blob/main/.github/dependabot.yml) opens weekly pull requests to keep the GitHub Actions and the Python base image up to date; each one runs the tests before it can be merged.
