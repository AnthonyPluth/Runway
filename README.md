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
  <a href="https://codecov.io/gh/AnthonyPluth/Runway?flags[0]=backend"><img src="https://img.shields.io/codecov/c/github/AnthonyPluth/Runway/main?flag=backend&amp;label=backend%20coverage" alt="Backend test coverage"></a>
  <a href="https://codecov.io/gh/AnthonyPluth/Runway?flags[0]=frontend"><img src="https://img.shields.io/codecov/c/github/AnthonyPluth/Runway/main?flag=frontend&amp;label=frontend%20coverage" alt="Frontend test coverage"></a>
  <img src="https://img.shields.io/badge/python-3.14-3776AB?logo=python&amp;logoColor=white" alt="Python 3.14">
  <img src="https://img.shields.io/badge/packaging-Poetry-60A5FA?logo=poetry&amp;logoColor=white" alt="Poetry">
  <img src="https://img.shields.io/badge/database-SQLite%20%7C%20Postgres-4cc38a" alt="SQLite or Postgres">
</p>

<p align="center">
  <img src="docs/src/assets/screenshots/overview.png" alt="Runway's Overview page: an “On track” status for checking over the next 90 days, its $4,820.55 balance, and a projected balance chart" width="900">
</p>

---

## Why Runway

Most budgeting apps look backwards: what did I spend last month? Runway looks forward. It takes your checking balance, your paychecks and bills, and what each credit card will actually charge you on its due date, and draws the balance day by day for the next few months, so you can see the tightest moment before you get there.

It runs on your own computer or home server, keeps its data in one database you control (SQLite, or Postgres if you prefer), and is written in Python with a handful of well-known libraries.

## Features

- **Cash-flow forecast.** Your primary account, day by day for 30 days to 6 months, with the lowest point in plain language. Credit cards come out on their due dates using real statement balances from the issuer (through Plaid), and paydays and payments land on business days.
- **Bank sync, account by account.** SimpleFIN or Plaid for each account, once a day and on demand. Switching providers keeps your history.
- **Bills & income.** Paychecks, bills and subscriptions on any schedule, matched to transactions automatically, with a flag for payments that didn't happen.
- **Transactions and rules.** Categories with subcategories, split transactions, bulk editing, a review queue, and rules that categorize, rename or split by merchant, amount or account.
- **Optional AI categorization** through OpenRouter, with confidence scores and a log of every call.
- **Store orders.** A browser extension reads your Amazon, Target and Costco orders and splits each card charge by what you bought.
- **Budgets and reports.** Monthly budgets with rollover, a cash-flow Sankey, spending over time, merchants, income against spending, and a drill-down breakdown.
- **Net worth, investments and equity.** Accounts, homes and vehicles minus debts, recorded daily; brokerage performance with live prices; stock options, RSUs and Carta grants with vesting schedules.
- **Churning.** Sign-up bonuses, 5/24, annual fees, card benefits, bank bonuses and a plan for the cards you want next.
- **Notifications.** Web Push alerts for upcoming card payments, a low forecast, missed bills, large charges and more, with no third-party service involved.
- **An assistant-ready API.** An [MCP endpoint](https://anthonypluth.github.io/Runway/using/mcp/) lets Claude or another assistant, once you approve it, answer questions about your money.
- **Yours to keep.** Autosave everywhere, a dark interface that works on a phone (and installs as an app), and one-file backups that restore into SQLite or Postgres.

The full tour is in the [documentation](https://anthonypluth.github.io/Runway/using/features/). Runway's pages are Overview, Transactions, Budget (with a Bills & income tab), Reports, Net worth (Summary, Investments, Equity and Retirement), Churning and Settings.

## Quick start

```bash
git clone https://github.com/AnthonyPluth/Runway.git
cd Runway
cp .env.example .env      # set RUNWAY_PUBLIC_URL, OIDC_ISSUER, OIDC_CLIENT_ID/SECRET, OIDC_ALLOWED_EMAILS
docker compose up -d
```

The image is `ghcr.io/anthonypluth/runway:latest` (Intel/AMD and ARM). On a server, Runway requires sign-in through an OpenID Connect provider such as Authentik, Authelia, Keycloak, Pocket ID, Google or Microsoft Entra. Running from source, connecting your first bank and the rest are in the [quick start](https://anthonypluth.github.io/Runway/start/quick-start/).

## Documentation

**[anthonypluth.github.io/Runway](https://anthonypluth.github.io/Runway/)**, built from [`docs/`](docs/):

- [Quick start](https://anthonypluth.github.io/Runway/start/quick-start/), [Install with Docker](https://anthonypluth.github.io/Runway/start/docker/) and [Deployment](https://anthonypluth.github.io/Runway/start/deployment/): running on a server, your phone, Postgres, backups and migrations.
- [Features](https://anthonypluth.github.io/Runway/using/features/), the [browser extension](https://anthonypluth.github.io/Runway/using/browser-extension/) and [AI assistants (MCP)](https://anthonypluth.github.io/Runway/using/mcp/).
- [Configuration](https://anthonypluth.github.io/Runway/reference/configuration/) (every environment variable) and [Architecture](https://anthonypluth.github.io/Runway/reference/architecture/) (data sources, how the forecast works, the stack and its security model).
- [Development](https://anthonypluth.github.io/Runway/contributing/development/): running the tests, changing the database, the code layout and releases.

## How it's built

Runway is developed with AI coding assistants, under my direction. Every change goes through a pull request with review, and CI runs the full backend and frontend test suites (coverage in the badges above), type-checking, linting and a dependency audit before anything ships. Changes that touch bank connections, encryption or sign-in get extra scrutiny; see [SECURITY.md](SECURITY.md). The conventions the assistants follow are in [AGENTS.md](AGENTS.md).

## Security and privacy

Your data stays in your own database; outbound calls go only to the services you set up, and the AI (if enabled) sees only the date, amount, merchant text and account type. Sign-in through your OpenID Connect provider is mandatory beyond `localhost`, and bank access and API keys are encrypted at rest. The details are in [Architecture](https://anthonypluth.github.io/Runway/reference/architecture/#security-and-privacy); to report a vulnerability, see [SECURITY.md](SECURITY.md).

## Releases

Every push to `main` that passes the tests publishes a new image and a [GitHub Release](https://github.com/AnthonyPluth/Runway/releases). You can pin a version (`ghcr.io/anthonypluth/runway:1.2`) and upgrade when you choose. The running version is shown under **Settings → Advanced**; how versions are chosen is in [Development](https://anthonypluth.github.io/Runway/contributing/development/#releases).

## License

Runway is licensed under the [GNU Affero General Public License v3.0](LICENSE).

---

<sub>The Inter typeface is © The Inter Project Authors and the Geist typeface is © Vercel, both used under the SIL Open Font License (<a href="runway/static/fonts/Inter-LICENSE.txt">Inter</a>, <a href="runway/static/fonts/Geist-LICENSE.txt">Geist</a>).
Merchant, bank and card logos are from Plaid and <a href="https://logo.dev">Logo.dev</a>. They're trademarks of their owners.</sub>
