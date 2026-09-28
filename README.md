<p align="center">
  <img src="runway/static/logo.svg" width="72" height="72" alt="Runway logo">
</p>

<h1 align="center">Runway</h1>

<p align="center">
  <b>A self-hosted personal finance app that tells you how much runway your cash has.</b><br>
  Bank sync, a day-by-day cash-flow forecast, budgets, reports, investments and net worth, on your own hardware.
</p>

<p align="center">
  <a href="https://github.com/AnthonyPluth/Runway/actions/workflows/docker.yml"><img src="https://github.com/AnthonyPluth/Runway/actions/workflows/docker.yml/badge.svg" alt="Build"></a>
  <img src="https://img.shields.io/badge/python-3.14-3776AB?logo=python&amp;logoColor=white" alt="Python 3.14">
  <img src="https://img.shields.io/badge/packaging-Poetry-60A5FA?logo=poetry&amp;logoColor=white" alt="Poetry">
  <img src="https://img.shields.io/badge/database-SQLite%20%7C%20Postgres-4cc38a" alt="SQLite or Postgres">
</p>

<p align="center">
  <img src="docs/screenshots/overview.png" alt="Runway's Overview page: a headline saying checking stays above $2,084 for the next 120 days, summary tiles, and a projected balance chart" width="900">
</p>

---

## Contents

- [Why Runway](#why-runway)
- [Features](#features)
- [Screenshots](#screenshots)
- [Quick start](#quick-start)
- [On your phone](#on-your-phone)
- [Running on a server](#running-on-a-server)
- [Configuration](#configuration)
- [Backups, migration and Postgres](#backups-migration-and-postgres)
- [How it works](#how-it-works)
- [Security and privacy](#security-and-privacy)
- [Development](#development)
- [Releases](#releases)

## Why Runway

Most budgeting apps look backwards: what did I spend last month? Runway looks forward. It takes your checking balance,
your paychecks and bills, and what each credit card will actually charge you on its due date, and draws the balance
day by day for the next few months, so you can see the tightest moment before you get there.

It runs on your own computer or home server, keeps its data in a single database file you control, and is written in
plain Python with a handful of well-known libraries.

## Features

### Cash-flow forecast
- **Day-by-day projection** of your primary account for 30 days to 6 months, with the lowest point called out in plain
  language ("Checking stays above $2,084 for the next 120 days").
- **Credit cards paid the way you pay them:** each card's statement balance comes out of checking on its due date.
  Statement balances, closing dates, due dates and minimum payments come straight from the card issuer through Plaid
  (Liabilities), so there's nothing to set up by hand. You can still correct a statement figure if you need to.
- **Future statements** are estimated from each card's average spending over its last three statements.
- **Business days:** money doesn't move on weekends or bank holidays, so a payment due then lands on the next business
  day, and a paycheck on the business day before (Federal Reserve holiday calendar).
- **One-off edits:** click any upcoming amount to change it for that date only.

### Your choice of bank connection, account by account
- **SimpleFIN or Plaid for each account.** Link a bank or card through Plaid, match its accounts to the ones you already
  have, and pick where each one's balance and transactions come from. Switching keeps your history, categories and
  recurring matches: transactions both providers have are matched up so nothing is counted twice.
- Accounts only Plaid can reach can be added on their own, and a card linked through Plaid gets its statements from the
  issuer whichever provider its transactions come from.

### Recurring money in and out
- Paychecks, mortgage, bills and subscriptions: weekly, every two weeks, twice a month, monthly, quarterly, twice a year,
  yearly, or on specific dates (property tax on April 15 and October 15, say).
- Transactions are **matched automatically** to their recurring item, and Runway **flags payments that didn't happen**.
- Suggestions for recurring items it spots in your history.

### Transactions and categorization
- **Rules** with conditions and actions: when the merchant contains, is or starts with some text, the amount is in a
  range, the money goes out or comes in, or it's in a particular account, then set a category, rename the merchant,
  split it by percentages ("Costco: 70% Groceries, 30% Household") or put it in Review. The most specific rule wins, and
  a preview shows what a rule would match before you save it. When you pick a category, Runway asks whether to use it
  for that merchant from now on (a rule) — including after you apply an AI suggestion, so nothing writes a rule
  behind your back; built-in heuristics handle card payments and sweeps.
- **Optional AI categorization** through OpenRouter, guided by examples of how you've categorized before and shown with
  confidence scores. Confident answers can be applied during sync; the rest wait for you, and it can propose new
  categories when nothing fits. Every AI call is logged so you can see what happened.
- **Split transactions:** a $100 run to Target can be $60 Groceries and $40 Shopping. Budgets, reports and category
  filters count each part on its own; pick a single category again and the transaction goes back together.
- **Amazon and Target orders:** a small browser extension reads your Amazon orders and your Target online orders and
  in-store purchases, with the sign-in already in your browser (neither store has an API for this), and Runway splits
  each card charge by what you bought: tax and shipping shared out, each Amazon shipment charged on its own matched to
  its own items. Items are categorized by the AI if you've set it up, and a category you pick for an item sticks for
  the next time you buy it. See [extension/README.md](extension/README.md).
- **Bulk editing:** tick transactions (shift-click for a range) to give them a category, rename their merchant or mark
  them reviewed all at once.
- **Merchant logos** for merchants Plaid knows, downloaded once from Plaid and served by Runway itself (nothing else is
  asked); SimpleFIN transactions from the same merchant get the logo too. With a free Logo.dev publishable key
  (Settings), merchants Plaid has no logo for get one from Logo.dev by their website (Plaid's, or for about 95 big names
  like Target, Amazon and Walmart, one Runway knows): downloaded during a sync, re-checked monthly, served by Runway.
- Categories with one level of subcategories, a review queue for anything uncategorized, and search and filters.

### Budgets and reports
- Monthly budgets per category with pace markers; click any amount to jump to the transactions behind it.
- A **cash-flow Sankey chart** showing where each month's money went.
- **Spending over time** by category, merchant or account (6, 12 or 24 months), with each one's change from last
  month and from a year ago; **merchants** ranked by what you spent, each with its months and transactions; **income
  against spending** with your savings rate; and a **breakdown** treemap you click into, from categories to
  subcategories to merchants to the transactions behind them.

### Investments
- Holdings and performance for brokerage and retirement accounts, modeled on Ghostfolio: time-weighted return, a
  comparison with the S&P 500, gain per holding, allocation and a financial-independence calculator whose
  assumptions (spending, saving, return, withdrawal rate) start from your own numbers and keep whatever you change.
- **Near-real-time prices** while the market is open, with each holding's gain today.
- Positions from SimpleFIN, or optionally from Plaid for accounts SimpleFIN only knows the balance of. Accounts that
  neither can see into, like some 401(k)s, can be **tracked by hand**: enter shares and your contribution split, and
  Runway invests each new deposit accordingly.
- Editable cost basis, per share.

### Net worth
- Every account plus homes, vehicles and anything else you own, minus cards and loans, recorded daily.
- Optional automated home values through Realie (each home is looked up once a week at most, within the free tier).
- **Equity compensation:** stock options (ISO/NSO), RSUs, restricted stock and shares, each with its vesting schedule
  (cliff, monthly or quarterly), exercise price and exercises. Runway shows what has vested and what's still to come at
  each company's latest share price, and counts the vested part in net worth. Enter grants by hand, or read them from
  **Carta** with the browser extension (with your Carta sign-in, like Amazon and Target) or with Carta's Portfolio API
  if Carta approves your app.

### Everyday
- **Automatic sync** once a day, and whenever you open Runway if the data is more than an hour old.
- **Autosave everywhere:** there are no Save buttons.
- Dark interface that works on a phone as well as a desktop, and **installs as an app** on your iPhone's Home Screen.
- **Push notifications** on your phone or computer: a card payment coming up, the forecast getting low, a recurring
  payment that didn't show up, a large charge, or syncing that keeps failing. Each alert is sent once.
- **Backups** as a single file, restorable into either database.

## Screenshots

| Investments | Where money went |
|---|---|
| <img src="docs/screenshots/investments.png" alt="Investments page with value and return charts" width="440"> | <img src="docs/screenshots/reports.png" alt="Reports page with a cash-flow Sankey chart" width="440"> |

<p align="center"><img src="docs/screenshots/phone.png" alt="Runway's Overview on a phone" width="260"></p>

<sub>Screenshots use demo data; every dollar amount is a placeholder like $12,345.67.</sub>

## Quick start

You need Python 3.14, [Poetry](https://python-poetry.org/docs/#installation) 2 (`pipx install poetry`) and Node 22
(to build the web app once). Or skip all three and use Docker (see [Running on a server](#running-on-a-server)).

```bash
git clone https://github.com/AnthonyPluth/Runway.git
cd Runway
poetry install --no-root     # the dependencies, into a virtualenv just for Runway
(cd frontend && npm ci && npm run build)   # the web app, into runway/static/app (again after each update)
poetry run python run.py
```

Open <http://localhost:8765>, then:

1. **Connect your bank.** In [SimpleFIN Bridge](https://beta-bridge.simplefin.org), create a setup token and paste it
   into **Settings → Connections**. The first sync pulls about six months of history. Or, with Plaid keys, use
   **Connect a bank or card** there (up to two years of history), and set those accounts to Plaid under
   **Settings → Accounts**. You can mix the two, account by account.
2. **Link your credit cards through Plaid** (**Connect a bank or card**) so Runway gets their statements and due dates,
   then pick your primary account and each card's paying account under **Settings → Accounts**.
3. **Add your paychecks and bills** on the **Recurring** page, or accept the ones Runway suggests.
4. Optionally add an OpenRouter key (AI categorization), Plaid keys (banks and cards account by account, card
   statements, more investment detail) and a Realie key
   (home values) under **Settings → Connections**.

Your data is stored in `data/runway.db` next to the code.

## On your phone

Open Runway in Safari on your iPhone, tap **Share → Add to Home Screen**, and open it from the new icon: it runs full
screen like an app. To get notifications (iOS 16.4 or later), go to **Settings → Notifications** inside the installed
app and tap **Turn on notifications**. On a computer, the same button works in Chrome, Edge, Firefox or Safari.
Notifications need Runway to be served over `https://`.

Notifications are sent with Web Push, signed (VAPID) and end-to-end encrypted (RFC 8291) by
[pywebpush](https://github.com/web-push-libs/pywebpush). They go straight to your browser's push service (Apple's,
Google's or Mozilla's), so no third-party notification service or account is involved.

## Running on a server

Runway ships as a Docker image built for Intel/AMD and ARM: `ghcr.io/anthonypluth/runway:latest`. On a server, it
requires sign-in through an OpenID Connect provider such as Authentik, Authelia, Keycloak, Pocket ID, Google or
Microsoft Entra.

```bash
cp .env.example .env      # set RUNWAY_PUBLIC_URL, OIDC_ISSUER, OIDC_CLIENT_ID/SECRET, OIDC_ALLOWED_EMAILS
docker compose up -d
```

**[DOCKER.md](DOCKER.md)** walks through registering Runway with your provider, the first start, moving your data
over from another machine, and day-to-day updates.

## Configuration

Settings that belong to the app (bank connection, API keys, categories, rules) live in **Settings** and are stored in
the database. Everything about how and where Runway runs is set with environment variables:

| Variable | Default | What it does |
|---|---|---|
| `RUNWAY_PUBLIC_URL` | | The address you open Runway at, e.g. `https://runway.example.com`. Required with sign-in. |
| `OIDC_ISSUER` | | Your identity provider's issuer URL. Setting it turns sign-in on. |
| `OIDC_CLIENT_ID` / `OIDC_CLIENT_SECRET` | | The client registered with your provider. Leave the secret empty for a public (PKCE-only) client. |
| `OIDC_ALLOWED_EMAILS` | | Comma-separated emails allowed in. An email counts only if your provider marks it verified (`email_verified`). Taking an email off ends that person's sessions. |
| `OIDC_TRUST_UNVERIFIED_EMAIL` | | `1` lets `OIDC_ALLOWED_EMAILS` match an email your provider doesn't mark verified. Only for a provider where nobody can register or change their own email (Microsoft Entra ID never sends `email_verified`). |
| `OIDC_ALLOWED_GROUPS` | | Comma-separated groups (from the `groups` claim) allowed in. |
| `OIDC_ALLOW_ANY_USER` | | `1` lets in anyone your provider signs in. Only for a provider you fully control. |
| `OIDC_SCOPES` | `openid email profile` | Add `groups` if your provider needs it to send group membership. |
| `RUNWAY_SESSION_DAYS` | `14` | How long you stay signed in. |
| `RUNWAY_ALLOWED_HOSTS` | | Extra host names Runway answers to (local IPs, `*.local`, bare names and Tailscale names always work). |
| `RUNWAY_PUSH_HOSTS` | | Extra push-service hosts notifications may be sent to (Google, Mozilla, Apple and Windows push always work), e.g. a self-hosted UnifiedPush server. |
| `RUNWAY_SECRET_KEY` | | Encrypts the bank access and API keys Runway saves (at least 32 characters: `openssl rand -base64 32`). Without it, Runway makes `secret.key` in `RUNWAY_DATA`. |
| `RUNWAY_SECRET_KEY_OLD` | | The previous key, for one start after changing `RUNWAY_SECRET_KEY`; everything is re-encrypted with the new one. |
| `RUNWAY_ALLOW_INSECURE_HTTP` | | `1` allows an `http://` `RUNWAY_PUBLIC_URL` on an internet address. Don't. |
| `RUNWAY_ALLOW_NO_AUTH` | | `1` runs without sign-in on the network, for when a proxy in front already handles it. |
| `DATABASE_URL` | | `postgresql://user:password@host:5432/db` to use Postgres instead of the built-in SQLite file. |
| `RUNWAY_DATA` | `./data` (`/data` in Docker) | Where the SQLite database lives. |
| `RUNWAY_HOST` / `RUNWAY_PORT` | `127.0.0.1` / `8765` | Address and port to listen on (`0.0.0.0` in Docker). |
| `RUNWAY_NO_SYNC` | | `1` turns off the automatic background sync. |
| `TZ` | system | Decides what "today" is for forecasts and budgets. |

## Backups, migration and Postgres

A backup is one gzip'd JSON file holding every table. It works across databases, so it's also how you move Runway
between machines or from SQLite to Postgres.

```bash
poetry run python run.py backup                  # writes runway-backup-YYYY-MM-DD.json.gz
poetry run python run.py restore <file> [--yes]  # replaces everything with the backup
```

The same is available in **Settings → Backup**. Backups include your bank access and API keys, so keep them private.
Sign-in sessions aren't included.

To use **Postgres**, set `DATABASE_URL` (the driver is already installed). Runway creates its tables on first start;
restore a backup to bring your data along.

Runway keeps its database schema up to date by itself: on every start it applies any new
[Alembic](https://alembic.sqlalchemy.org) migrations, on SQLite and Postgres alike. Databases from before migrations
were added are upgraded in place.

## How it works

| Source | Used for | Needed? |
|---|---|---|
| [SimpleFIN Bridge](https://beta-bridge.simplefin.org) | Balances, transactions and investment positions from your bank and brokerages | SimpleFIN or Plaid |
| [OpenRouter](https://openrouter.ai) | AI category suggestions (any model; defaults to Claude Haiku) | Optional |
| [Plaid](https://plaid.com) | Card statements and due dates (Liabilities), balances and transactions for accounts you set to Plaid (Transactions), investment holdings and trades (Investments) | For credit cards |
| Yahoo Finance chart data | Daily and live prices, splits and fund names | Automatic |
| [Realie](https://www.realie.ai) | Automated home value estimates | Optional |

- **Forecast:** start from today's balance, add each recurring item on its dates, subtract each card's statement on its
  due date (both from the issuer through Plaid), and optionally spread average everyday spending across the days.
- **Investment history:** rebuilt from activity where Plaid provides it, the way Ghostfolio does; otherwise from the
  position snapshots Runway saves on every sync. Changes in positions between snapshots count as money added or
  withdrawn, not as gains, and returns are time-weighted.
- **Stack:** Python with a handful of well-known libraries: [SQLAlchemy](https://www.sqlalchemy.org) and
  [Alembic](https://alembic.sqlalchemy.org) for SQLite/Postgres and migrations (psycopg 3 for Postgres),
  [PyJWT](https://pyjwt.readthedocs.io) for sign-in tokens, [pywebpush](https://github.com/web-push-libs/pywebpush)
  for notifications, [python-dateutil](https://dateutil.readthedocs.io) for recurring schedules and
  [holidays](https://github.com/vacanza/holidays) for bank holidays. The web server is
  the standard library's; the front end is plain HTML/CSS/JavaScript with hand-drawn SVG charts and the Geist typeface.
  Dependencies are managed with [Poetry](https://python-poetry.org) (`pyproject.toml`, `poetry.lock`).

## Security and privacy

- **Your data stays with you.** Everything lives in your database. Outbound calls go only to the services above, and
  the AI only sees the date, amount, merchant text and account type of transactions you ask it about.
- **Sign-in on the network is mandatory.** Runway refuses to listen beyond `localhost` without OIDC, unless you
  explicitly say a proxy handles it.
- **OIDC done carefully:** authorization code flow with PKCE. ID tokens are verified with PyJWT against your provider's
  published keys (RSA, RSA-PSS, EC or EdDSA; `none` and unexpected algorithms are refused), with issuer, audience,
  authorized party, expiry, issued-at and nonce checks. Sessions are random tokens stored hashed, in `HttpOnly`,
  `SameSite=Lax` cookies (`Secure` over HTTPS).
- **Host checking:** Runway only answers to addresses you've configured or that are clearly local.
- **Secrets encrypted at rest:** bank access (SimpleFIN, Plaid access tokens), API keys and the push signing key are
  stored encrypted (Fernet, with `RUNWAY_SECRET_KEY` or a generated key file), so a copy of the database alone doesn't
  give them away. Backup files hold them decrypted so they restore anywhere: keep backups private.
- **HTTPS on the internet:** with sign-in on, Runway won't start with an `http://` `RUNWAY_PUBLIC_URL` unless it's a
  home-network address.
- **Browser protections:** a strict Content-Security-Policy (only Runway's own scripts, with a per-page nonce, plus
  Plaid Link), no framing, `Referrer-Policy: no-referrer`, HSTS over HTTPS. State-changing requests need Runway's own
  header and are refused from other sites; signing out is a POST.
- **The browser extension** (Amazon and Target orders) never sees your store passwords: it reads orders with the
  sign-in in your browser and sends them only to your Runway, with a key you make in Settings (only its hash is kept;
  it opens nothing but the extension's own calls, which can only add orders). Item names and prices go to the AI only
  if you've turned it on.
- **Hardened server:** request size limits, a timeout for slow clients, a cap on requests handled at once, and errors
  that never show internals (they log a reference instead). One access-log line per request, without query strings.
- The container runs as an unprivileged user without extra capabilities, and secrets (`.env`, `data/`, backups) are
  excluded from Git. Found a problem? See [SECURITY.md](SECURITY.md).

## Development

```bash
poetry install --no-root                               # dependencies, into .venv
poetry run python run.py --no-sync                     # run without touching your bank
poetry run python -m unittest discover tests           # the test suite (SQLite)
DATABASE_URL=postgresql://... poetry run python -m unittest discover tests   # the same tests against Postgres
poetry add <package>                                   # add a dependency (updates pyproject.toml and poetry.lock)
```

The web app is Svelte 5 + TypeScript in `frontend/` (Tailwind CSS, components in the shadcn-svelte style on Bits UI,
Lucide icons). Runway serves its build at `/`, so build it once before running Runway from a checkout. It needs
Node 22:

```bash
cd frontend && npm ci                 # its packages, into frontend/node_modules
npm run dev                           # http://localhost:5173/ (reloads as you edit; start Runway on 8765 too)
npm run check                         # type-check it (CI runs this)
npm run build                         # into runway/static/app/, which Runway serves at / (the Docker image does this)
```

Changing the database: edit `runway/schema.py`, then generate a migration and check it over:

```bash
poetry run alembic revision --autogenerate -m "add a column"   # writes runway/migrations/versions/…
poetry run alembic check                                       # the schema and migrations agree
```

Runway applies it on its next start. Queries are plain SQL with `?` placeholders that both databases understand
(`INSERT … ON CONFLICT`, not `INSERT OR REPLACE`).

| Path | What |
|---|---|
| `run.py` | Starts the server; `backup` and `restore` commands |
| `runway/server.py` | Web server, API routes, background sync |
| `runway/simplefin.py`, `sfinvest.py` | Bank sync and SimpleFIN investment positions |
| `runway/plaid.py`, `plaidbank.py` | Plaid: investments; banks and cards (per-account provider, transactions, card statements) |
| `runway/tracked.py` | Hand-tracked holdings |
| `runway/categorize.py`, `categories.py` | Rules, history and AI categorization; the category tree |
| `runway/forecast.py`, `recurring.py` | Cash-flow forecast, card statements, recurring items and missed payments |
| `runway/portfolio.py`, `prices.py` | Investment performance and price data |
| `runway/networth.py`, `realie.py` | Net worth and home values |
| `runway/oidc.py` | OpenID Connect sign-in |
| `runway/notify.py`, `webpush.py` | Push notifications: what to alert about, and sending them |
| `runway/db.py`, `schema.py`, `backup.py` | Database connections (SQLite or Postgres), the schema, backups |
| `runway/migrations/`, `alembic.ini` | Alembic migrations, applied on start-up |
| `runway/brands.py`, `runway/static/banks/` | Which institution each account belongs to, and their logos |
| `frontend/` | The web app (Svelte): `src/pages/` one file per page, `src/lib/` the API client, formatting and components |
| `runway/static/` | Files Runway serves beside the app: the service worker (`sw.js`), manifest, fonts, icons, bank logos, and `page.css` for the sign-in pages |
| `tests/` | Unit and end-to-end tests, including a mock OIDC provider |
| `pyproject.toml`, `poetry.lock` | Dependencies (Poetry) |
| `data/` | Your database (not in Git) |

## Releases

Every push to `main` runs the tests, then GitHub Actions:

1. tags the next version and publishes a [GitHub Release](https://github.com/AnthonyPluth/Runway/releases) with notes;
2. builds the image for `linux/amd64` and `linux/arm64` with that version baked in, and pushes it as
   `ghcr.io/anthonypluth/runway` tagged `latest`, `1.2.3` (and `v1.2.3`), `1.2` and `1`;
3. deletes untagged leftovers. Every released version stays available, so you can pin one
   (`image: ghcr.io/anthonypluth/runway:1.2`) and upgrade when you choose.

Versions follow `vMAJOR.MINOR.PATCH`. Each push bumps the patch number; put `#minor` in a commit message (or start it
with `feat:`) to bump the minor version, or `#major` (or `BREAKING CHANGE`) for a major one. The running version is
shown at the bottom of **Settings**.

[Dependabot](.github/dependabot.yml) opens weekly pull requests to keep the GitHub Actions and the Python base image
up to date; each one runs the tests before it can be merged.

---

<sub>The Geist typeface is © Vercel, used under the <a href="runway/static/fonts/Geist-LICENSE.txt">SIL Open Font License</a>.
Bank and card logos are from <a href="https://github.com/selfhst/icons">selfh.st/icons</a> (CC BY 4.0); merchant logos from Plaid and <a href="https://logo.dev">Logo.dev</a>. They're trademarks of their owners.</sub>
