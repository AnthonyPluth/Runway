---
title: Install with Docker
description: Register Runway with your sign-in provider, start it, and put it on the internet safely.
sidebar:
  order: 2
---

Runway signs you in through your OpenID Connect provider (Authentik, Authelia, Keycloak, Pocket ID, Google, Microsoft Entra, …). The image holds Python, Runway's dependencies and Runway itself; it keeps its data in SQLite under `/data`, or in Postgres if you set `DATABASE_URL`.

## 1. Register Runway with your provider

Create an OIDC / OAuth2 application (a confidential client, authorization code flow):

| Setting | Value |
|---|---|
| Redirect URI | `<RUNWAY_PUBLIC_URL>/auth/callback` |
| Post-logout redirect URI | `<RUNWAY_PUBLIC_URL>/auth/signed-out` (optional) |
| Scopes | `openid email profile` (plus `groups` for group-based access) |
| ID token signing | RS256 (the usual default); PS256, ES256 and EdDSA work too |

Note the issuer URL, client ID and client secret.

## 2. Start it

```bash
git clone https://github.com/AnthonyPluth/Runway.git
cd Runway
cp .env.example .env        # fill in RUNWAY_PUBLIC_URL, OIDC_* and OIDC_ALLOWED_EMAILS
docker compose up -d
docker compose logs -f runway
```

Open `RUNWAY_PUBLIC_URL`. You're sent to your provider to sign in, then back to Runway. If a setting is missing, the container stops with a message saying which one. Sign out with the arrow next to your name at the bottom of the sidebar.

The image is `ghcr.io/anthonypluth/runway:latest`, for Intel/AMD and ARM, and public. Every push to `main` that passes the tests publishes it, tagged `latest` and with its version (`1.2.3`, `1.2`, `1`). To stay on a version, set `image: ghcr.io/anthonypluth/runway:1.2` in `docker-compose.yml`; every released version stays in the registry. The running version is under **Settings → Data**.

## Moving your data from another machine

1. On the old machine: **Settings → Data → Backup → Download a backup** (or `poetry run python run.py backup`).
2. On the server, sign in and go to **Settings → Data → Backup → Restore**, choosing that file. Or copy it into `./data`, stop the container, and run `docker compose run --rm runway python run.py restore /data/<file> --yes`.

The backup holds your bank access and API keys encrypted with your `RUNWAY_SECRET_KEY`: set the same key on the server (or copy `secret.key` into its `./data`) before restoring. Under a different key they can't be read: put the old key in `RUNWAY_SECRET_KEY_OLD` for one start and Runway re-encrypts them, or enter them again in Settings. See [Backups](/Runway/start/deployment/#backups-and-restore).

## Postgres (optional)

Set `DATABASE_URL` in `.env` (`postgresql://runway:password@db-host:5432/runway`) and restart. Runway creates its tables on first start and applies schema migrations whenever a new version starts; restore a backup to bring your data along. `docker-compose.yml` has a commented-out `db` service if you want Postgres alongside Runway.

## Putting Runway on the internet

Runway is built to be reachable from anywhere, set up like this:

1. **HTTPS in front.** A reverse proxy with a certificate (Caddy, Traefik, nginx) or Tailscale Funnel, with `RUNWAY_PUBLIC_URL` set to that `https://` address. Runway refuses to start with a plain `http://` internet address. If the proxy runs on the same machine, publish the port on localhost only (`"127.0.0.1:8765:8765"` in `docker-compose.yml`).
2. **Sign-in limited to you.** `OIDC_ALLOWED_EMAILS` and/or `OIDC_ALLOWED_GROUPS`; avoid `OIDC_ALLOW_ANY_USER`. Turn on two-factor sign-in at your provider. Everyone you let in shares one Runway: the same accounts, settings, bank connections and backups. Let in only people you'd hand your finances to ([SECURITY.md](https://github.com/AnthonyPluth/Runway/blob/main/SECURITY.md)).
3. **A secret key.** `RUNWAY_SECRET_KEY` (`openssl rand -base64 32`), kept in your password manager. It encrypts your saved bank access and API keys. Without it, the key is `./data/secret.key`: back it up with the database. To change it, put the old one in `RUNWAY_SECRET_KEY_OLD` for one start, and keep it as long as you keep backups made with it.
4. **Rate limiting at the proxy** (optional): Runway caps how many requests it handles at once, and the proxy can limit requests per address.
5. **Backups kept private.** They hold your transactions, and your bank access and API keys encrypted with your key.
6. **Updates.** `docker compose pull && docker compose up -d` (or let Watchtower do it). Each release is tested and its dependencies checked for known vulnerabilities. Migrations run on start; take a backup first if you like to be careful.
7. **AI assistants** connect with OAuth at `<RUNWAY_PUBLIC_URL>/mcp` ([AI assistants](/Runway/using/mcp/)). Behind a forward-auth proxy (`RUNWAY_ALLOW_NO_AUTH=1`), exempt `/mcp`, `/oauth/register`, `/oauth/token`, `/oauth/revoke` and `/.well-known/oauth-*` from it, and keep `/oauth/authorize` behind it.

## Notes

- Installing Runway on an iPhone and push notifications both need an `https://` `RUNWAY_PUBLIC_URL`. Notifications go through Apple's, Google's or Mozilla's push service, so the server needs outbound HTTPS to them.
- **Plaid:** banks that sign you in on their own site send you back to `<RUNWAY_PUBLIC_URL>/plaid/oauth`. Add it under **Allowed redirect URIs** in the Plaid Dashboard (Runway shows it in Settings → Connections).
- A session ends after 14 days without using Runway (`RUNWAY_SESSION_DAYS`) and at most 90 days after signing in; someone let in by `OIDC_ALLOWED_GROUPS` signs in again after 14 days, so leaving the group takes effect. Signing out ends the Runway session and your provider session.
- Runway answers only to addresses that are yours: `RUNWAY_PUBLIC_URL`'s host, local IPs, `*.local`, plain names like `nas`, and Tailscale names. Add others with `RUNWAY_ALLOWED_HOSTS`.
- Already signing in through a proxy (Authelia forward-auth, Cloudflare Access, oauth2-proxy)? Leave `OIDC_ISSUER` empty, set `RUNWAY_ALLOW_NO_AUTH=1`, and don't expose port 8765 except through that proxy.
