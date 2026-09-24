# Runway

A small, self-hosted personal finance app: bank sync through SimpleFIN, AI categorization (OpenRouter),
a cash-flow forecast that pays credit cards by statement balance, budgets, reports, investments and net worth.
Python standard library only; data lives in a local SQLite database.

## Run on your computer

```
python3 run.py            # then open http://localhost:8765
```

Needs Python 3.9 or newer. Connect SimpleFIN and the rest under Setup.

## Run in Docker (home server)

See [DOCKER.md](DOCKER.md). Sign-in uses your OpenID Connect provider.

## Tests

```
python3 -m unittest discover tests
```

## Where things live

| Path | What |
|---|---|
| `runway/server.py` | web server and API |
| `runway/simplefin.py`, `sfinvest.py` | bank and investment sync |
| `runway/categorize.py` | rules, history and AI categorization |
| `runway/forecast.py`, `recurring.py` | cash-flow forecast and recurring items |
| `runway/portfolio.py`, `prices.py` | investment performance and prices |
| `runway/networth.py`, `rentcast.py` | net worth and home values |
| `runway/oidc.py` | sign-in |
| `runway/static/` | the web app (plain HTML, CSS, JavaScript) |
| `data/` | your database (not in Git) |
