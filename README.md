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
  <img src="https://img.shields.io/badge/python-3.9%2B-3776AB?logo=python&amp;logoColor=white" alt="Python 3.9+">
  <img src="https://img.shields.io/badge/dependencies-standard%20library%20only-4cc38a" alt="Standard library only">
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
plain Python with no third-party packages.

## Features

### Cash-flow forecast
- **Day-by-day projection** of your primary account for 30 days to 6 months, with the lowest point called out in plain
  language ("Checking stays above $2,084 for the next 120 days").
- **Credit cards paid the way you pay them:** each card's statement balance comes out of checking on its due date.
  Statement balances are worked out from the card's transactions, or you can type in the real figure from your statement.
- **Future statements** are estimated from each card's average spending over its last three statements.
- **One-off edits:** click any upcoming amount to change it for that date only.

### Recurring money in and out
- Paychecks, mortgage, bills and subscriptions: weekly, every two weeks, twice a month, monthly, quarterly, twice a year,
  yearly, or on specific dates (property tax on April 15 and October 15, say).
- Transactions are **matched automatically** to their recurring item, and Runway **flags payments that didn't happen**.
- Suggestions for recurring items it spots in your history.

### Transactions and categorization
- **Rules** ("contains *whole foods*" → Groceries) plus built-in heuristics; tick "Remember for this merchant" and a
  category you pick becomes a rule.
- **Optional AI categorization** through OpenRouter, guided by examples of how you've categorized before and shown with
  confidence scores. Confident answers can be applied during sync; the rest wait for you, and it can propose new
  categories when nothing fits. Every AI call is logged so you can see what happened.
- Categories with one level of subcategories, a review queue for anything uncategorized, and search and filters.

### Budgets and reports
- Monthly budgets per category with pace markers; click any amount to jump to the transactions behind it.
- A **cash-flow Sankey chart** showing where each month's money went.

### Investments
- Holdings and performance for brokerage and retirement accounts, modeled on Ghostfolio: time-weighted return, a
  comparison with the S&P 500, gain per holding, allocation and a financial-independence calculator.
- **Near-real-time prices** while the market is open, with each holding's gain today.
- Positions from SimpleFIN, or optionally from Plaid for accounts SimpleFIN only knows the balance of. Accounts that
  neither can see into, like some 401(k)s, can be **tracked by hand**: enter shares and your contribution split, and
  Runway invests each new deposit accordingly.
- Editable cost basis, per share.

### Net worth
- Every account plus homes, vehicles and anything else you own, minus cards and loans, recorded daily.
- Optional automated home values through RentCast (it stays within the free tier).

### Everyday
- **Automatic sync** once a day, and whenever you open Runway if the data is more than an hour old.
- **Autosave everywhere:** there are no Save buttons.
- Dark interface that works on a phone as well as a desktop.
- **Backups** as a single file, restorable into either database.

## Screenshots

| Investments | Where money went |
|---|---|
| <img src="docs/screenshots/investments.png" alt="Investments page with value and return charts" width="440"> | <img src="docs/screenshots/reports.png" alt="Reports page with a cash-flow Sankey chart" width="440"> |

<p align="center"><img src="docs/screenshots/phone.png" alt="Runway's Overview on a phone" width="260"></p>

<sub>Screenshots use demo data; every dollar amount is a placeholder like $12,345.67.</sub>

## Quick start

You need Python 3.9 or newer. Nothing else to install.

```bash
git clone https://github.com/AnthonyPluth/Runway.git
cd Runway
python3 run.py
```

Open <http://localhost:8765>, then:

1. **Connect your bank.** In [SimpleFIN Bridge](https://beta-bridge.simplefin.org), create a setup token and paste it
   into **Settings → Connections**. The first sync pulls about six months of history.
2. **Pick your primary account** and add each credit card's closing day, due day and paying account under
   **Settings → Accounts**.
3. **Add your paychecks and bills** on the **Recurring** page, or accept the ones Runway suggests.
4. Optionally add an OpenRouter key (AI categorization), Plaid keys (more investment detail) and a RentCast key
   (home values) under **Settings → Connections**.

Your data is stored in `data/runway.db` next to the code.

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
| `OIDC_ALLOWED_EMAILS` | | Comma-separated emails allowed in. |
| `OIDC_ALLOWED_GROUPS` | | Comma-separated groups (from the `groups` claim) allowed in. |
| `OIDC_ALLOW_ANY_USER` | | `1` lets in anyone your provider signs in. Only for a provider you fully control. |
| `OIDC_SCOPES` | `openid email profile` | Add `groups` if your provider needs it to send group membership. |
| `RUNWAY_SESSION_DAYS` | `14` | How long you stay signed in. |
| `RUNWAY_ALLOWED_HOSTS` | | Extra host names Runway answers to (local IPs, `*.local`, bare names and Tailscale names always work). |
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
python3 run.py backup                  # writes runway-backup-YYYY-MM-DD.json.gz
python3 run.py restore <file> [--yes]  # replaces everything with the backup
```

The same is available in **Settings → Backup**. Backups include your bank access and API keys, so keep them private.
Sign-in sessions aren't included.

To use **Postgres**, set `DATABASE_URL` and install the driver (`pip install "psycopg[binary]"`; the Docker image
already has it). Runway creates its tables on first start; restore a backup to bring your data along.

## How it works

| Source | Used for | Needed? |
|---|---|---|
| [SimpleFIN Bridge](https://beta-bridge.simplefin.org) | Balances, transactions and investment positions from your bank and brokerages | Yes |
| [OpenRouter](https://openrouter.ai) | AI category suggestions (any model; defaults to Claude Haiku) | Optional |
| [Plaid](https://plaid.com) | Investment holdings and trades for accounts SimpleFIN only has balances for | Optional |
| Yahoo Finance chart data | Daily and live prices, splits and fund names | Automatic |
| [RentCast](https://www.rentcast.io) | Automated home value estimates | Optional |

- **Forecast:** start from today's balance, add each recurring item on its dates, subtract each card's statement on its
  due date (statement balance = balance at the closing day, worked out from transactions after it), and optionally
  spread average everyday spending across the days.
- **Investment history:** rebuilt from activity where Plaid provides it, the way Ghostfolio does; otherwise from the
  position snapshots Runway saves on every sync. Changes in positions between snapshots count as money added or
  withdrawn, not as gains, and returns are time-weighted.
- **Stack:** Python standard library (`http.server`, `sqlite3`, `urllib`), plain HTML/CSS/JavaScript with hand-drawn SVG
  charts, the Geist typeface. The only optional dependency is `psycopg` for Postgres.

## Security and privacy

- **Your data stays with you.** Everything lives in your database. Outbound calls go only to the services above, and
  the AI only sees the date, amount, merchant text and account type of transactions you ask it about.
- **Sign-in on the network is mandatory.** Runway refuses to listen beyond `localhost` without OIDC, unless you
  explicitly say a proxy handles it.
- **OIDC done carefully:** authorization code flow with PKCE, ID token signature verified against your provider's keys,
  and issuer, audience, expiry and nonce checks. Sessions are random tokens stored hashed, in `HttpOnly`,
  `SameSite=Lax` cookies (`Secure` over HTTPS).
- **Host checking:** Runway only answers to addresses you've configured or that are clearly local.
- The container runs as an unprivileged user, and secrets (`.env`, `data/`, backups) are excluded from Git.

## Development

```bash
python3 run.py --no-sync               # run without touching your bank
python3 -m unittest discover tests     # the test suite (SQLite)
DATABASE_URL=postgresql://... python3 -m unittest discover tests   # the same tests against Postgres
```

| Path | What |
|---|---|
| `run.py` | Starts the server; `backup` and `restore` commands |
| `runway/server.py` | Web server, API routes, background sync |
| `runway/simplefin.py`, `sfinvest.py` | Bank sync and SimpleFIN investment positions |
| `runway/plaid.py`, `tracked.py` | Plaid investments and hand-tracked holdings |
| `runway/categorize.py`, `categories.py` | Rules, history and AI categorization; the category tree |
| `runway/forecast.py`, `recurring.py` | Cash-flow forecast, card statements, recurring items and missed payments |
| `runway/portfolio.py`, `prices.py` | Investment performance and price data |
| `runway/networth.py`, `rentcast.py` | Net worth and home values |
| `runway/oidc.py` | OpenID Connect sign-in |
| `runway/db.py`, `pg.py`, `backup.py` | Schema, SQLite/Postgres layer, backups |
| `runway/static/` | The web app: `index.html`, `app.js`, `app.css`, fonts and logo |
| `tests/` | Unit and end-to-end tests, including a mock OIDC provider |
| `data/` | Your database (not in Git) |

## Releases

Every push to `main` runs the tests, then GitHub Actions:

1. tags the next version and publishes a [GitHub Release](https://github.com/AnthonyPluth/Runway/releases) with notes;
2. builds the image for `linux/amd64` and `linux/arm64` with that version baked in, and pushes it as
   `ghcr.io/anthonypluth/runway:latest`;
3. removes older images, so only the latest is kept.

Versions follow `vMAJOR.MINOR.PATCH`. Each push bumps the patch number; put `#minor` in a commit message (or start it
with `feat:`) to bump the minor version, or `#major` (or `BREAKING CHANGE`) for a major one. The running version is
shown at the bottom of **Settings**.

[Dependabot](.github/dependabot.yml) opens weekly pull requests to keep the GitHub Actions and the Python base image
up to date; each one runs the tests before it can be merged.

---

<sub>The Geist typeface is © Vercel, used under the <a href="runway/static/fonts/Geist-LICENSE.txt">SIL Open Font License</a>.</sub>
