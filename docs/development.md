# Development

Contributor conventions (style, tests, commit and PR rules) are in [AGENTS.md](../AGENTS.md).

## Setup and checks

```bash
poetry install --no-root                               # dependencies and the tools CI runs (ruff, mypy, ...), into .venv
poetry run python run.py --no-sync                     # run without touching your bank
poetry run python -m unittest discover tests           # the test suite (SQLite)
DATABASE_URL=postgresql://... poetry run python -m unittest discover tests   # the same tests against Postgres
poetry run coverage run -m unittest discover tests && poetry run coverage report   # how much they cover
poetry run unittest-parallel -t . -s tests -j 4   # the same suite across 4 processes (what CI does): about 3x faster
poetry run mypy                                        # type-check the Python (settings in pyproject.toml)
poetry add <package>                                   # add a dependency (updates pyproject.toml and poetry.lock)
```

To run the tests on Postgres as CI does (in parallel, where tests that share the database can trip over each other), start a throwaway one; Docker's default 64 MB of shared memory isn't enough for the schemas the tests make:

```bash
docker run -d --rm --name runway-test-pg --shm-size=1g -p 5432:5432 -e POSTGRES_USER=runway -e POSTGRES_PASSWORD=runway -e POSTGRES_DB=runway postgres:16
DATABASE_URL=postgresql://runway:runway@127.0.0.1:5432/runway poetry run unittest-parallel -t . -s tests -j 4
docker stop runway-test-pg
```

To run one CI shard of the Postgres tests (CI splits them across three runners, each with its own Postgres), add its modules: `... unittest-parallel -t . -s tests -j 4 $(python tests/shard.py 2/3)`.

`make check` runs what CI checks before you push, each tool once: ruff and mypy, the Python tests (on SQLite), the web app's type-check, ESLint, Vitest tests and build, Bandit over `runway/` and zizmor over the workflows (offline). `make lint`, `make test`, `make frontend-check` and `make security` run one part. `make audit` checks the Python and web app dependencies for known vulnerabilities (pip-audit and npm audit, as CI does); it needs the network, so it isn't part of `make check`. Semgrep, Trivy and CodeQL run only in CI, as do the Postgres tests (see above to run them yourself). For the quick checks on every commit (ruff, trailing whitespace, YAML/TOML syntax, merge-conflict markers, large files), install [pre-commit](https://pre-commit.com) and run `pre-commit install` once.

## The web app

The web app is Svelte 5 + TypeScript in `frontend/` (Tailwind CSS, components in the shadcn-svelte style on Bits UI, Lucide icons). Runway serves its build at `/`, so build it once before running Runway from a checkout. It needs Node 22:

```bash
cd frontend && npm ci                 # its packages, into frontend/node_modules
npm run dev                           # http://localhost:5173/ (reloads as you edit; start Runway on 8765 too)
npm run check                         # type-check it (CI runs this)
npm run lint                          # ESLint over it, the browser extension and the service worker (CI runs this)
npm test                              # unit tests for its pure logic, with Vitest (CI runs these too)
npm run coverage                      # the same, measuring how much of the web app they run (the frontend badge)
npm run build                         # into runway/static/app/, which Runway serves at / (the Docker image does this)
```

## Changing the database

Edit `runway/schema.py`, then generate a migration and check it over:

```bash
poetry run alembic revision --autogenerate -m "add a column"   # writes runway/migrations/versions/…
poetry run alembic check                                       # the schema and migrations agree
```

Runway applies it on its next start. Queries are plain SQL with `?` placeholders that both databases understand (`INSERT … ON CONFLICT`, not `INSERT OR REPLACE`), or SQLAlchemy statements over the ORM models; [orm.md](orm.md) is the guide for converting a module.

## Code layout

| Path | What |
|---|---|
| `run.py` | Starts the server; `backup`, `restore` and `demo` commands |
| `runway/server/` | Web server: `handler.py` (requests, sign-in, security headers, static files, `serve()`), `routes.py` and `api/` (the API, one module per area), `sync.py` (background sync) |
| `runway/simplefin.py`, `sfinvest.py` | Bank sync and SimpleFIN investment positions |
| `runway/plaid.py`, `plaidbank.py` | Plaid: investments; banks and cards (per-account provider, transactions, card statements) |
| `runway/tracked.py` | Hand-tracked holdings |
| `runway/categorize.py`, `categories.py` | Rules, history and AI categorization; the category tree |
| `runway/forecast.py`, `recurring.py` | Cash-flow forecast, card statements, recurring items and missed payments |
| `runway/portfolio.py`, `prices.py` | Investment performance and price data |
| `runway/networth.py`, `realie.py` | Net worth and home values |
| `runway/oidc.py` | OpenID Connect sign-in |
| `runway/notify.py`, `webpush.py` | Push notifications: what to alert about, and sending them |
| `runway/mcp_server.py`, `mcp_access.py`, `mcp_oauth.py` | The MCP server's tools, what an assistant may reach, and OAuth for connecting one |
| `runway/db.py`, `schema.py`, `models.py`, `backup.py` | Database connections (SQLite or Postgres), the schema and its ORM models, backups |
| `runway/migrations/`, `alembic.ini` | Alembic migrations, applied on start-up |
| `runway/brands.py` | Which institution each account belongs to, and their logos (Logo.dev, by name) |
| `frontend/` | The web app (Svelte): `src/pages/` one file per page, `src/lib/` the API client, formatting and components (`components/settings/` holds Settings' tabs) |
| `extension/` | The browser extension (Amazon, Target, Costco and Carta) |
| `runway/static/` | Files Runway serves beside the app: the service worker (`sw.js`), manifest, fonts, icons, and `page.css` for the sign-in pages; the web app builds into `runway/static/app/` |
| `tests/` | Unit and end-to-end tests, including a mock OIDC provider |
| `pyproject.toml`, `poetry.lock` | Dependencies (Poetry) |
| `data/` | Your database (not in Git) |

## Releases

Every push to `main` runs the tests and, at the same time, builds the image for `linux/amd64` and `linux/arm64` with the next version baked in (and checks that it starts). Nothing is published until the tests pass; then GitHub Actions:

1. pushes the image (already built, so this is quick) as `ghcr.io/anthonypluth/runway` tagged `latest`, `1.2.3` (and `v1.2.3`), `1.2` and `1`;
2. tags the version and publishes a [GitHub Release](https://github.com/AnthonyPluth/Runway/releases) with notes;
3. deletes untagged leftovers. Every released version stays available, so you can pin one (`image: ghcr.io/anthonypluth/runway:1.2`) and upgrade when you choose.

Versions follow `vMAJOR.MINOR.PATCH`, decided by what's been merged since the last release. Pull request titles start with a type (they end up in each merge commit's message): `feat: …` bumps the minor version, `fix: …` (or anything else) the patch, and a `!` before the colon (`feat!: …`, `fix!: …`) the major version, for a change that breaks an existing setup. `#minor` or `#major` at the end of a line, or a line starting with `BREAKING CHANGE:`, work too. (A message that only mentions them mid-sentence doesn't count.) The running version is shown under **Settings → Advanced**.

[Dependabot](../.github/dependabot.yml) opens weekly pull requests to keep the GitHub Actions and the Python base image up to date; each one runs the tests before it can be merged.
