# Deployment

## Running on a server

Runway ships as a Docker image built for Intel/AMD and ARM: `ghcr.io/anthonypluth/runway:latest`. On a server, it requires sign-in through an OpenID Connect provider such as Authentik, Authelia, Keycloak, Pocket ID, Google or Microsoft Entra.

```bash
cp .env.example .env      # set RUNWAY_PUBLIC_URL, OIDC_ISSUER, OIDC_CLIENT_ID/SECRET, OIDC_ALLOWED_EMAILS
docker compose up -d
```

**[DOCKER.md](../DOCKER.md)** is the walkthrough: registering Runway with your provider, the first start, moving your data over from another machine, putting it on the internet (HTTPS, sign-in limits, secret key, backups), day-to-day updates and optional error reports. Every environment variable is in [Configuration](configuration.md); for reporting a vulnerability and safe setup, see [SECURITY.md](../SECURITY.md).

## On your phone

Open Runway in Safari on your iPhone, tap **Share → Add to Home Screen**, and open it from the new icon: it runs full screen like an app. To get notifications (iOS 16.4 or later), go to **Settings → Notifications** inside the installed app and tap **Turn on notifications**. On a computer, the same button works in Chrome, Edge, Firefox or Safari. Installing and notifications need Runway to be served over `https://`.

Notifications are sent with Web Push, signed (VAPID) and end-to-end encrypted (RFC 8291) by [pywebpush](https://github.com/web-push-libs/pywebpush). They go straight to your browser's push service (Apple's, Google's or Mozilla's), so no third-party notification service or account is involved, and the server needs outbound HTTPS to those services.

## Backups, migration and Postgres

A backup is one gzip'd JSON file holding every table. It works across databases, so it's also how you move Runway between machines or from SQLite to Postgres.

```bash
poetry run python run.py backup                  # writes runway-backup-YYYY-MM-DD.json.gz
poetry run python run.py restore <file> [--yes]  # replaces everything with the backup
```

The same is available under **Settings → Advanced** (Backup & restore). Backups include your bank access and API keys, encrypted with `RUNWAY_SECRET_KEY` (restoring elsewhere needs the same key, or you enter them again). Sign-in sessions aren't included.

In Settings, choosing a file first shows what it holds (when it was made, from which database, and how many accounts, transactions, recurring items and budgets), and **Restore** says how many transactions it replaces and asks you to type `RESTORE`. Before replacing anything, the server saves what was there as `runway-before-restore-<date-time>.json.gz` in the data directory (`./data`, or `/data` in Docker), readable only by Runway's user; restore that file the same way to go back. The copy isn't made when there's nothing to keep, or by `run.py restore`. It holds your bank access too, so delete it once you no longer need it.

To use **Postgres**, set `DATABASE_URL` (the driver is already installed). Runway creates its tables on first start; restore a backup to bring your data along.

Runway keeps its schema up to date by itself: on every start it applies any new [Alembic](https://alembic.sqlalchemy.org) migrations, on SQLite and Postgres alike. Databases from before migrations were added are upgraded in place.
