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

To run the tests on Postgres as CI does (in parallel, where tests that share the database can trip over each other), start a throwaway one; Docker's default 64 MB of shared memory isn't enough for the schemas the tests make:

```bash
docker run -d --rm --name runway-test-pg --shm-size=1g -p 5432:5432 -e POSTGRES_USER=runway -e POSTGRES_PASSWORD=runway -e POSTGRES_DB=runway postgres:16
DATABASE_URL=postgresql://runway:runway@127.0.0.1:5432/runway poetry run unittest-parallel -t . -s tests -j 4
docker stop runway-test-pg
```

`make test-pg` runs the suite against the Postgres at `$DATABASE_URL` (the command above, with a check that the variable is set). Whatever Postgres you use must be UTF-8: a cluster you make yourself needs `initdb -E UTF8 --locale=C.UTF-8` (the `postgres` Docker image already is). Each test that writes settings or goes through the server makes and drops its own schema, so running the suite twice on the same database gives the same result.

To run one CI shard of the Postgres tests (CI splits them across three runners, each with its own Postgres), add its modules: `... unittest-parallel -t . -s tests -j 4 $(python tests/shard.py 2/3)`.

`make check` runs the checks to run before you push, each tool once: ruff, mypy and import-linter, Runway's own Semgrep rules, the Python tests (on SQLite, in parallel as CI runs them), and the web app's type-check, ESLint, Vitest tests and build, and the docs site's build. `make lint`, `make test` (serial, for a clearer failure), `make test-parallel` and `make frontend-check` run one part. The security scans run only in CI, each on the pull requests it can affect (see `.github/workflows/security.yml`): Semgrep, Trivy, zizmor, pip-audit, npm audit and CodeQL. So do the Postgres tests (see above to run them yourself). For the quick checks on every commit (ruff, trailing whitespace, YAML/TOML syntax, merge-conflict markers, large files), install [pre-commit](https://pre-commit.com) and run `pre-commit install` once.

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

## Changing the database

Edit `runway/storage/schema.py`, then generate a migration and check it over:

```bash
poetry run alembic revision --autogenerate -m "add a column"   # writes runway/storage/migrations/versions/…
poetry run alembic check                                       # the schema and migrations agree
```

A column that refers to another table's row (an account's id, above all) gets a foreign key, `schema.refers(table, column, "accounts.id", ondelete)`: `CASCADE` when the row belongs to what it refers to (an account's transactions), `SET NULL` when it only points at it (the account a card is paid from). Deleting an account (`deleted_accounts.remove`) relies on them, and `tests/test_deleted_accounts.py` fails for a column named like an account id without one, or a settings key named by an account id that isn't in `settings_keys.PER_ACCOUNT`. Ids kept on purpose after what they name is gone (the deleted accounts list, investment accounts' ids, Plaid's) have none. A backup restores with the keys checked when the restore commits, so rows may come in any order.

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

## Releases

Every push to `main` runs the tests and, at the same time, builds the image for `linux/amd64` and `linux/arm64` with the next version baked in (and checks that it starts). Nothing is published until the tests pass; then GitHub Actions:

1. pushes the image (already built, so this is quick) as `ghcr.io/anthonypluth/runway` tagged `latest`, `1.2.3` (and `v1.2.3`), `1.2` and `1`;
2. tags the version and publishes a [GitHub Release](https://github.com/AnthonyPluth/Runway/releases) with notes;
3. deletes untagged leftovers. Every released version stays available, so you can pin one (`image: ghcr.io/anthonypluth/runway:1.2`) and upgrade when you choose.

Versions follow `vMAJOR.MINOR.PATCH`, decided by what's been merged since the last release. Pull request titles start with a type (they end up in each merge commit's message): `feat: …` bumps the minor version, `fix: …` (or anything else) the patch, and a `!` before the colon (`feat!: …`, `fix!: …`) the major version, for a change that breaks an existing setup. `#minor` or `#major` at the end of a line, or a line starting with `BREAKING CHANGE:`, work too. (A message that only mentions them mid-sentence doesn't count.) The running version is shown under **Settings → Data**.

[Dependabot](https://github.com/AnthonyPluth/Runway/blob/main/.github/dependabot.yml) opens weekly pull requests to keep the GitHub Actions and the Python base image up to date; each one runs the tests before it can be merged.
