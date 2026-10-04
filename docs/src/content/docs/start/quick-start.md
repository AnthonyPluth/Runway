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

The image is `ghcr.io/anthonypluth/runway:latest` (Intel/AMD and ARM). On a server, Runway requires sign-in through an OpenID Connect provider such as Authentik, Authelia, Keycloak, Pocket ID, Google or Microsoft Entra. [Install with Docker](/Runway/start/docker/) walks through registering Runway with your provider, the first start and updates.

## From source

You need Python 3.14, [Poetry](https://python-poetry.org/docs/#installation) 2 (`pipx install poetry`) and Node 26 (to build the web app once).

```bash
git clone https://github.com/AnthonyPluth/Runway.git
cd Runway
poetry install --no-root                   # the dependencies, into a virtualenv just for Runway
(cd frontend && npm ci && npm run build)   # the web app, into runway/static/app (again after each update)
poetry run python run.py
```

Open `http://localhost:8765`. Your data is stored in `data/runway.db`. To look around with made-up data first, run `poetry run python run.py demo`.

## First steps

1. **Connect your bank.** **Settings → Connections** offers two ways, one open at a time. **SimpleFIN** is the easiest (about $15 a year): create a setup token in [SimpleFIN Bridge](https://beta-bridge.simplefin.org) and paste it in; the first sync pulls about six months of history. **Plaid** needs your own Plaid developer keys, saved with **Save**, and reaches back up to two years. You can mix the two account by account under **Settings → Accounts**, which opens once a bank is connected so you can see what came in (accounts from Plaid wait there for you to add them: one by one, or **Add all**).
2. **Give each credit card its statement**, so Runway knows what's due and when: link the card through Plaid where its bank shares statements, or enter its latest statement on the card under **Settings → Accounts**. Set each card's paying account there too. To choose your **forecast account** (the one Overview forecasts), click the account name above the balance on **Overview** (or “Choose” in the setup checklist), or open the account in **Settings → Accounts** and choose “Use for the forecast”; it's marked **Forecast** there.
3. **Add your paychecks and bills** on **Recurring**, or accept the ones Runway suggests.
4. Optionally add an OpenRouter, Realie, Finnhub or Logo.dev key under **Settings → Connections**.

Next: what each page does is in [Features](/Runway/using/features/), and every environment variable is in [Configuration](/Runway/reference/configuration/).
