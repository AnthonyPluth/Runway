---
title: Deployment
description: Running on a server, on your phone, backups, migrations and Postgres.
sidebar:
  order: 3
---

## Running on a server

Runway ships as a Docker image built for Intel/AMD and ARM: `ghcr.io/anthonypluth/runway:latest`. On a server, it requires sign-in through an OpenID Connect provider such as Authentik, Authelia, Keycloak, Pocket ID, Google or Microsoft Entra.

```bash
cp .env.example .env      # set RUNWAY_PUBLIC_URL, OIDC_ISSUER, OIDC_CLIENT_ID/SECRET, OIDC_ALLOWED_EMAILS
docker compose up -d
```

**[Install with Docker](/Runway/start/docker/)** is the walkthrough: registering Runway with your provider, the first start, moving your data over from another machine, putting it on the internet (HTTPS, sign-in limits, secret key, backups), day-to-day updates and optional error reports. Every environment variable is in [Configuration](/Runway/reference/configuration/); for reporting a vulnerability and safe setup, see [SECURITY.md](https://github.com/AnthonyPluth/Runway/blob/main/SECURITY.md).

## On your phone

Open Runway in Safari on your iPhone, tap **Share → Add to Home Screen**, and open it from the new icon: it runs full screen like an app, and the page doesn't pinch-zoom (text follows your phone's text size). To get notifications (iOS 16.4 or later), go to **Settings → Notifications** inside the installed app and tap **Turn on notifications**. On a computer, the same button works in Chrome, Edge, Firefox or Safari. Installing and notifications need Runway to be served over `https://`.

Notifications are sent with Web Push, signed (VAPID) and end-to-end encrypted (RFC 8291) by [pywebpush](https://github.com/web-push-libs/pywebpush). They go straight to your browser's push service (Apple's, Google's or Mozilla's), so no third-party notification service or account is involved, and the server needs outbound HTTPS to those services.

### App lock (Face ID / Touch ID)

With sign-in on, each device can ask for Face ID, Touch ID or its passcode before Runway shows anything: **Settings → Data → App lock → Turn on app lock**, inside the installed app on an iPhone (it has a sign-in of its own, apart from Safari's), or in the browser on a computer with Touch ID or Windows Hello. It locks when you open Runway and when you come back after it's been in the background for the time you choose (immediately, or after 1, 5 or 15 minutes; 1 minute to start with), and asks again 12 hours after an unlock however much you use it. It covers the page while Runway is in the background, so the app switcher shows nothing of yours.

What it is: an app lock like a banking app's, on top of your sign-in, not instead of it. It uses WebAuthn with the device's own authenticator: turning it on makes a passkey on the device, and each unlock is that passkey signing a one-time challenge from your Runway, which checks it. While it's locked, your Runway refuses that sign-in's requests for data (and approving an AI assistant), so it holds even for someone who opens Runway's addresses in the phone's browser, not just in the app's screens. What it protects against: someone picking up your phone or computer while it's unlocked. What it doesn't: someone who has your session cookie on another machine while Runway is unlocked here, a malicious script running in Runway's own pages, data the app has already loaded into memory, or the browser extension's key and AI assistants' tokens, which have their own access.

It's this device's only: other devices, and Safari beside the installed app, are unaffected. It ends with the sign-in: signing out turns it off (that's also the way out if Face ID stops working: **Can’t unlock? Sign out** on the lock screen), and so do a session running out and being taken off the sign-in list (`OIDC_ALLOWED_EMAILS`, or a group's sign-in lapsing), when the device's record on the server is deleted along with the session. Turn it back on after signing in again. It needs `https://` (or `localhost`), Runway opened at its `RUNWAY_PUBLIC_URL`, and a browser that can hand over a passkey's public key (Safari 16 / iOS 16, Chrome or Edge 85, Firefox 119 and later). On iPhone the passkey is saved in your iCloud Keychain (as “Runway app lock”); if you delete it there, unlocking fails and signing out is the way back. A reverse proxy should pass the `X-Runway-Hidden` header on, as it does `X-Runway`: it marks what the app asks for from the background, which doesn't keep an unlock going.

For each device with the lock on, your Runway also keeps a random key share, encrypted with `RUNWAY_SECRET_KEY` like the other secrets: half of the key for an encrypted copy of your data kept on the device (the passkey supplies the other half; the web app doesn't keep that copy yet). It's handed only to that device's sign-in, right after Face ID, Touch ID or the passcode, and is deleted with the device's record (signing out, the session ending, being taken off the sign-in list, turning the lock off or on again), so the device's copy can't be opened once its own copy of the share runs out, 72 hours at most. It isn't in backups, and AI assistants and the browser extension can't ask for it. What it doesn't protect against: someone with the phone unlocked and Runway signed in, or a malicious script running in Runway's own pages.

## Backups, migration and Postgres

A backup is one gzip'd JSON file holding every table. It works across databases, so it's also how you move Runway between machines or from SQLite to Postgres.

```bash
poetry run python run.py backup                  # writes runway-backup-YYYY-MM-DD.json.gz
poetry run python run.py restore <file> [--yes]  # replaces everything with the backup (stop Runway first)
```

The same is available under **Settings → Data** (Backup & restore; the tab used to be called Advanced), which also shows when a backup was last downloaded from there. Backups include your bank access and API keys, encrypted with `RUNWAY_SECRET_KEY` (restoring elsewhere needs the same key, or you enter them again). Sign-in sessions aren't included.

In Settings, choosing a file first shows what it holds (when it was made, from which database, and how many accounts, transactions, recurring items and budgets), and **Restore** says how many transactions it replaces and asks you to type `RESTORE`. Before replacing anything, Runway saves what was there as `runway-before-restore-<date-time>.json.gz` in the data directory (`./data`, or `/data` in Docker), readable only by Runway's user; restore that file the same way to go back. Where it is stays shown under Restore (with any keys the backup's secret key couldn't read) until you dismiss it. The copy isn't made when there's nothing to keep. It holds your bank access too, so delete it once you no longer need it.

`run.py restore` does the same as Settings (the copy, and saying which keys it couldn't read), except that it can't hold off a running Runway's syncs the way Settings does: stop Runway first (`docker compose stop runway`, then `docker compose run --rm runway python run.py restore /data/<file>`), or a sync running at the same time could mix its rows in with the backup's.

**Backups from older versions.** A backup records the version of Runway's database it was made with. Restoring one from an older version brings its data up to date the way updating Runway would have: its rows go into a throwaway database at that version (in memory on SQLite; on Postgres a schema made inside a transaction that's rolled back, so the database user needs permission to create schemas), the migrations since then run over them, and the result replaces what's here. A backup made before backups recorded their version restores as before, with the newer migrations applied, and says that some of its older data may not have been brought up to date: check your accounts, payees and budgets afterwards. A backup from a newer version of Runway than the one you're running is refused: update Runway first.

To use **Postgres**, set `DATABASE_URL` (the driver is already installed). Runway creates its tables on first start; restore a backup to bring your data along.

Runway keeps its schema up to date by itself: on every start it applies any new [Alembic](https://alembic.sqlalchemy.org) migrations, on SQLite and Postgres alike. A migration that has to remove data saves a backup of the whole database in the data directory first (`runway-before-migration-<number>-<date-time>.json.gz`), and logs how many rows it removed from each table. The first is 0040, which adds foreign keys: rows that refer to an account (or an order, a card, a home) that's no longer there, left by older versions, are removed or let go of, as deleting that account would have done. That copy holds your bank access too (encrypted), so delete it once you're happy with the result.
