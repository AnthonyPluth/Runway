# Running Runway in Docker

Runway signs you in through your OpenID Connect provider (Authentik, Authelia, Keycloak, Pocket ID, Google,
Microsoft Entra, ...). The image holds Python, Runway's dependencies (installed with Poetry from `poetry.lock`) and
Runway itself. It uses SQLite in `/data`, or Postgres if you set `DATABASE_URL`.

## 1. Register Runway with your provider

Create an OIDC / OAuth2 application ("confidential" client, authorization code flow):

| Setting | Value |
|---|---|
| Redirect URI | `<RUNWAY_PUBLIC_URL>/auth/callback` |
| Post-logout redirect URI | `<RUNWAY_PUBLIC_URL>/auth/signed-out` (optional) |
| Scopes | `openid email profile` (+ `groups` if you use group-based access) |
| ID token signing | RS256 (the usual default); PS256, ES256 and EdDSA work too |

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

To try a pull request before merging it, add the `needs_preview` label to it. A comment on the pull request soon
gives a temporary `https://….trycloudflare.com` address and a password, and the label comes off again (add it again
for a fresh preview). That copy of Runway holds made-up sample data only (`python run.py demo`), and it's taken down
after an hour (`.github/workflows/preview.yml`).

The repository is private, so the image is too. On the server, log in once with a GitHub token that has the
`read:packages` scope (github.com/settings/tokens):

```
echo <token> | docker login ghcr.io -u AnthonyPluth --password-stdin
```

## 2. First start

1. Stop the Runway you run with `poetry run python run.py` (Ctrl-C) so the database is fully written.
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

1. On the Mac: Settings → Backup → **Download a backup** (or `poetry run python run.py backup`).
2. Start the container on the server, sign in, and go to Settings → Backup → **Restore**, choosing that file.
   (Or copy the file into `./data` and run `docker compose run --rm runway python run.py restore /data/<file> --yes`.)

The backup holds your bank access and API keys; delete stray copies once you've restored it. The server encrypts them
again with its own key as they're restored.

## Using Postgres (optional)

Set `DATABASE_URL` in `.env` (e.g. `postgresql://runway:password@db-host:5432/runway`) and restart. Runway creates its
tables on first start, and applies any schema migrations each time a new version starts. To bring your data along, restore a backup into it as above. There's a commented-out
`db` service in `docker-compose.yml` if you want Postgres alongside Runway. Without `DATABASE_URL`, Runway keeps
using its built-in database in `./data`.

## Putting Runway on the internet

Runway is built to be reachable from anywhere, as long as it's set up like this:

1. **HTTPS in front.** Run it behind a reverse proxy with a certificate (Caddy, Traefik, nginx) or Tailscale Funnel,
   and set `RUNWAY_PUBLIC_URL` to that `https://` address. Runway refuses to start with a plain `http://` internet
   address. If the proxy runs on the same machine, publish the port on localhost only
   (`"127.0.0.1:8765:8765"` in `docker-compose.yml`) so nothing reaches Runway around it.
2. **Sign-in limited to you.** `OIDC_ALLOWED_EMAILS` and/or `OIDC_ALLOWED_GROUPS`; avoid `OIDC_ALLOW_ANY_USER`.
   Turn on two-factor sign-in at your identity provider: it guards everything behind it.
3. **A secret key.** Set `RUNWAY_SECRET_KEY` (`openssl rand -base64 32`) and keep a copy in your password manager.
   It encrypts your saved bank access and API keys. (Without it, the key is `./data/secret.key`: back it up with the
   database.)
4. **Rate limiting at the proxy** (optional but good): Runway caps how many requests it handles at once, and the proxy
   can limit requests per address, e.g. Caddy's `rate_limit` or Traefik's `RateLimit` middleware.
5. **Backups kept private.** They contain your bank access in the clear so they restore anywhere.
6. **Updates.** Pull new images regularly; each release is tested, and its dependencies are checked for known
   vulnerabilities.

## Everyday

- Update to the newest image: `docker compose pull && docker compose up -d`
  (or let Watchtower do it automatically). If you pinned a version, change the tag first. Database changes are
  applied automatically when the new version starts; take a backup first if you like to be careful.
- Stop: `docker compose down` (data stays in `./data`)
- Back up: Settings → Backup → Download a backup (works for either database)

## Error reports (optional)

Set `SENTRY_DSN` (Sentry → your project → Settings → Client Keys) and Runway sends its errors to Sentry, from the
server and from the web app, tagged with the version. Without it, nothing is sent anywhere.

- A report has the error, its stack trace and the page or API path. It never has request bodies, cookies, headers,
  query strings, the values of variables, or anything on screen; addresses with a password in them (SimpleFIN's) and
  Plaid tokens are blanked.
- `SENTRY_BROWSER_DSN` sends the web app's errors to a separate Sentry project; `RUNWAY_SENTRY_BROWSER=0` keeps the web
  app from sending any. The browser only ever talks to the DSN's `https://` host (the page's Content-Security-Policy
  allows that one address).
- `SENTRY_ENVIRONMENT` (default `production`) and `SENTRY_TRACES_SAMPLE_RATE` (performance tracing, default `0`).
- An error page's "reference" code is on the Sentry event as the `ref` tag, so a reference from the app finds its report.

## Notes

- Installing Runway on an iPhone and push notifications both need `RUNWAY_PUBLIC_URL` to be `https://`. Notifications
  go out through Apple's, Google's or Mozilla's push service, so the server needs outbound HTTPS to them.

- Plaid: banks that sign you in on their own site (Chase, Capital One, …) send you back to
  `<RUNWAY_PUBLIC_URL>/plaid/oauth`. Add that address under **Allowed redirect URIs** in the Plaid Dashboard (Runway
  shows it in Settings → Connections); it's what makes those banks work from a phone or the installed app.
- Only people in `OIDC_ALLOWED_EMAILS` / `OIDC_ALLOWED_GROUPS` get in, even if your provider lets others sign in.
- Sessions last 14 days (`RUNWAY_SESSION_DAYS`). Signing out ends the Runway session and your provider session.
- Runway answers only to addresses that are yours: `RUNWAY_PUBLIC_URL`'s host, local IPs, `*.local`, plain names like
  `nas`, and Tailscale names. Add others to `RUNWAY_ALLOWED_HOSTS`.
- Use `https://` for `RUNWAY_PUBLIC_URL` (a reverse proxy like Caddy or Traefik, or Tailscale). Session cookies are
  then marked Secure. On an internet address Runway requires it.
- Already sign in through a proxy (Authelia forward-auth, Cloudflare Access, oauth2-proxy)? Leave `OIDC_ISSUER`
  empty and set `RUNWAY_ALLOW_NO_AUTH=1` instead, and don't expose port 8765 except through that proxy.
