---
title: Quick start
description: Run Runway with Docker or from source, and connect your first bank.
sidebar:
  order: 1
---

## Docker

```bash
git clone https://github.com/AnthonyPluth/Runway.git
cd Runway
cp .env.example .env      # set RUNWAY_PUBLIC_URL, OIDC_ISSUER, OIDC_CLIENT_ID/SECRET, OIDC_ALLOWED_EMAILS
docker compose up -d
```

The image is `ghcr.io/anthonypluth/runway:latest` (Intel/AMD and ARM). Beyond `localhost`, Runway requires sign-in through an OpenID Connect provider such as Authentik, Authelia, Keycloak, Pocket ID, Google or Microsoft Entra. [Install with Docker](/Runway/start/docker/) walks through registering Runway with your provider, the first start, HTTPS and updates.

## From source

You need Python 3.14, [Poetry](https://python-poetry.org/docs/#installation) 2 (`pipx install poetry`) and Node 26 (to build the web app).

```bash
git clone https://github.com/AnthonyPluth/Runway.git
cd Runway
poetry install --no-root                   # the dependencies, into a virtualenv just for Runway
(cd frontend && npm ci && npm run build)   # the web app, into runway/static/app (again after each update)
poetry run python run.py
```

Open `http://localhost:8765`. Your data is in `data/runway.db`. To look around with made-up data first, run `poetry run python run.py demo`.

## First steps

1. **Connect your bank** under **Settings → Connections**. **SimpleFIN** is the easiest (about $15 a year): create a setup token in [SimpleFIN Bridge](https://beta-bridge.simplefin.org) and paste it in. **Plaid** needs your own developer keys and reaches further back. Accounts from Plaid wait under **Settings → Accounts** for you to add them.
2. **Give each credit card its statement**, so Runway knows what's due and when: through Plaid where the bank shares statements, or by entering the latest one on the card under **Settings → Accounts**. Set each card's paying account there too.
3. **Choose your forecast account:** click the account name above the balance on **Overview**, or mark the account **Use for the forecast** under Settings → Accounts.
4. **Add your paychecks and bills** on **Recurring**, or accept the ones Runway spots in your history.
5. Optionally add an OpenRouter key (or an Ollama address), and Realie, Finnhub or Logo.dev keys, under **Settings → Connections**.

Next: what each page does, starting with the [Overview](/Runway/using/overview/); every environment variable is in [Configuration](/Runway/reference/configuration/).
