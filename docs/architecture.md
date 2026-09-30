# Architecture

## Data sources

| Source | Used for | Needed? |
|---|---|---|
| [SimpleFIN Bridge](https://beta-bridge.simplefin.org) | Balances, transactions and investment positions from your bank and brokerages | SimpleFIN or Plaid |
| [Plaid](https://plaid.com) | Card statements and due dates (Liabilities), balances and transactions for accounts you set to Plaid (Transactions), investment holdings and trades (Investments) | For credit cards |
| [OpenRouter](https://openrouter.ai) | AI category suggestions (any model; defaults to Claude Haiku) | Optional |
| Yahoo Finance chart data | Daily and live prices, splits and fund names | Automatic |
| [Finnhub](https://finnhub.io) | Real-time stock and ETF trades for the live prices (WebSocket; the free plan covers 50 tickers, for personal use) | Optional |
| [Realie](https://www.realie.ai) | Automated home value estimates | Optional |
| [Logo.dev](https://logo.dev) | Merchant and bank logos Plaid doesn't have | Optional |

SimpleFIN and Plaid are configured under Settings → Bank connections; the optional services under Settings → Services.

## How it works

- **Forecast:** start from today's balance, add each recurring item on its dates, subtract each card's statement on its due date (both from the issuer through Plaid), and optionally spread average everyday spending across the days.
- **Investment history:** rebuilt from activity where Plaid provides it, the way Ghostfolio does; otherwise from the position snapshots Runway saves on every sync. Changes in positions between snapshots count as money added or withdrawn, not as gains, and returns are time-weighted.
- **Churning:** spending toward a card's bonus is counted the way Reports counts spending (split transactions by their parts, transfers and card payments left out, refunds lowering it). A bank bonus's direct deposits are deposits categorized as income, or that look like payroll. The banks' bonus rules live in `runway/churning.py` as data, and every estimate says it is one. Points values are community-consensus estimates with an as-of date (`VALUES_AS_OF`), not fetched from anywhere: no bank or aggregator (Plaid included) reports what a point is worth, so they're data you can override per program. Points estimated from spending use a card's normal rates, since a transaction doesn't say whether it was booked through the issuer's portal.

## Stack

Python with a handful of well-known libraries: [SQLAlchemy](https://www.sqlalchemy.org) and [Alembic](https://alembic.sqlalchemy.org) for SQLite/Postgres and migrations (psycopg 3 for Postgres), [PyJWT](https://pyjwt.readthedocs.io) for sign-in tokens, [pywebpush](https://github.com/web-push-libs/pywebpush) for notifications, [python-dateutil](https://dateutil.readthedocs.io) for recurring schedules and [holidays](https://github.com/vacanza/holidays) for bank holidays. The web server is the standard library's. The web app is Svelte 5 and TypeScript (Vite, Tailwind CSS, shadcn-svelte-style components on Bits UI, Lucide icons) with hand-drawn SVG charts and the Geist typeface, built into `runway/static/app/` and served at `/`. Dependencies are managed with [Poetry](https://python-poetry.org) (`pyproject.toml`, `poetry.lock`).

## Security and privacy

To report a vulnerability, or for running Runway safely on the internet, see [SECURITY.md](../SECURITY.md). In brief:

- **Your data stays with you.** Everything lives in your database. Outbound calls go only to the services above, and the AI only sees the date, amount, merchant text and account type of transactions you ask it about. Error reports to Sentry are off unless you set `SENTRY_DSN`, and carry the error without your data (see [DOCKER.md](../DOCKER.md#error-reports-optional)).
- **Sign-in on the network is mandatory.** Runway refuses to listen beyond `localhost` without OIDC, unless you explicitly say a proxy handles it.
- **OIDC done carefully:** authorization code flow with PKCE. ID tokens are verified with PyJWT against your provider's published keys (RSA, RSA-PSS, EC or EdDSA; `none` and unexpected algorithms are refused), with issuer, audience, authorized party, expiry, issued-at and nonce checks. Sessions are random tokens stored hashed, in `HttpOnly`, `SameSite=Lax` cookies (`Secure` over HTTPS).
- **Host checking and HTTPS:** Runway only answers to addresses you've configured or that are clearly local, and with sign-in on it won't start with an `http://` `RUNWAY_PUBLIC_URL` unless it's a home-network address.
- **Secrets encrypted at rest:** bank access (SimpleFIN and Plaid access tokens), API keys and the push signing key are stored encrypted (Fernet, with `RUNWAY_SECRET_KEY` or a generated key file), so a copy of the database alone doesn't give them away. Backup files hold them decrypted so they restore anywhere: keep backups private.
- **Browser protections:** a strict Content-Security-Policy (only Runway's own scripts, with a per-page nonce, plus Plaid Link), no framing, `Referrer-Policy: no-referrer`, HSTS over HTTPS. State-changing requests need Runway's own header and are refused from other sites; signing out is a POST.
- **The browser extension** never sees your store passwords: it reads orders with the sign-in in your browser and sends them only to your Runway, with a key you make under Settings → Browser extension (only its hash is kept; it opens nothing but the extension's own calls, which can only add orders). Item names and prices go to the AI only if you've turned it on. The [MCP](mcp.md) key is likewise stored hashed and is read-only by default.
- **Hardened server:** request size limits, a timeout for slow clients, a cap on requests handled at once, and errors that never show internals (they log a reference instead). One access-log line per request, without query strings. The container runs as an unprivileged user without extra capabilities, and secrets (`.env`, `data/`, backups) are excluded from Git.
