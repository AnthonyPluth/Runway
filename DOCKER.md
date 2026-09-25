# Running Runway in Docker

Runway signs you in through your OpenID Connect provider (Authentik, Authelia, Keycloak, Pocket ID, Google,
Microsoft Entra, ...). The image is plain Python plus the Postgres driver, which is only used if you set `DATABASE_URL`.

## 1. Register Runway with your provider

Create an OIDC / OAuth2 application ("confidential" client, authorization code flow):

| Setting | Value |
|---|---|
| Redirect URI | `<RUNWAY_PUBLIC_URL>/auth/callback` |
| Post-logout redirect URI | `<RUNWAY_PUBLIC_URL>/auth/signed-out` (optional) |
| Scopes | `openid email profile` (+ `groups` if you use group-based access) |
| ID token signing | RS256 (the usual default) |

Note the issuer URL, client ID and client secret.

## Where the image comes from

Every push to `main` runs the tests and publishes `ghcr.io/anthonypluth/runway:latest` (Intel/AMD and ARM) through
GitHub Actions (`.github/workflows/docker.yml`), tagged `latest` and with its version (`1.2.3`, `v1.2.3`, `1.2`, `1`).
To stay on a version instead of always taking the newest, set `image: ghcr.io/anthonypluth/runway:1.2` (or `:1.2.3`)
in `docker-compose.yml`; every released version stays in the registry.

Each of those pushes is also a release: it gets the next version tag (`v1.0.0`, `v1.0.1`, …) and a GitHub Release with
notes listing what changed. Every push bumps the last number; put `#minor` in a commit message (or start it with
`feat:`) to bump the middle one, or `#major` for the first. The running version is shown at the bottom of Settings,
and on the image as the `org.opencontainers.image.version` label.

The repository is private, so the image is too. On the server, log in once with a GitHub token that has the
`read:packages` scope (github.com/settings/tokens):

```
echo <token> | docker login ghcr.io -u AnthonyPluth --password-stdin
```

## 2. First start

1. Stop the Runway you run with `python3 run.py` (Ctrl-C) so the database is fully written.
2. In this folder:
   ```
   cp .env.example .env        # fill in RUNWAY_PUBLIC_URL, OIDC_* and OIDC_ALLOWED_EMAILS
   docker compose up -d
   docker compose logs -f runway
   ```
   Your existing data in `./data` is used as-is.
3. Open `RUNWAY_PUBLIC_URL`. You're sent to your provider to sign in, then back to Runway.
   Sign out with the arrow next to your name at the bottom of the sidebar.

If a setting is missing, the container stops with a message saying which one (see the logs).

## Moving your data from your Mac

1. On the Mac: Settings → Backup → **Download a backup** (or `python3 run.py backup`).
2. Start the container on the server, sign in, and go to Settings → Backup → **Restore**, choosing that file.
   (Or copy the file into `./data` and run `docker compose run --rm runway python run.py restore /data/<file> --yes`.)

The backup holds your bank access and API keys; delete stray copies once you've restored it.

## Using Postgres (optional)

Set `DATABASE_URL` in `.env` (e.g. `postgresql://runway:password@db-host:5432/runway`) and restart. Runway creates its
tables on first start. To bring your data along, restore a backup into it as above. There's a commented-out
`db` service in `docker-compose.yml` if you want Postgres alongside Runway. Without `DATABASE_URL`, Runway keeps
using its built-in database in `./data`.

## Everyday

- Update to the newest image: `docker compose pull && docker compose up -d`
  (or let Watchtower do it automatically). If you pinned a version, change the tag first.
- Stop: `docker compose down` (data stays in `./data`)
- Back up: Settings → Backup → Download a backup (works for either database)

## Notes

- Installing Runway on an iPhone and push notifications both need `RUNWAY_PUBLIC_URL` to be `https://`. Notifications
  go out through Apple's, Google's or Mozilla's push service, so the server needs outbound HTTPS to them.

- Only people in `OIDC_ALLOWED_EMAILS` / `OIDC_ALLOWED_GROUPS` get in, even if your provider lets others sign in.
- Sessions last 14 days (`RUNWAY_SESSION_DAYS`). Signing out ends the Runway session and your provider session.
- Runway answers only to addresses that are yours: `RUNWAY_PUBLIC_URL`'s host, local IPs, `*.local`, plain names like
  `nas`, and Tailscale names. Add others to `RUNWAY_ALLOWED_HOSTS`.
- Use `https://` for `RUNWAY_PUBLIC_URL` when you can (a reverse proxy like Caddy or Traefik, or Tailscale).
  Session cookies are then marked Secure.
- Already sign in through a proxy (Authelia forward-auth, Cloudflare Access, oauth2-proxy)? Leave `OIDC_ISSUER`
  empty and set `RUNWAY_ALLOW_NO_AUTH=1` instead, and don't expose port 8765 except through that proxy.
