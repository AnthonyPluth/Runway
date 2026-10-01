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
`feat:`) to bump the middle one, or `#major` for the first. The running version is shown under Settings → Advanced,
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

1. On the Mac: Settings → Advanced → Backup → **Download a backup** (or `poetry run python run.py backup`).
2. Start the container on the server, sign in, and go to Settings → Advanced → Backup → **Restore**, choosing that file.
   (Or copy the file into `./data` and run `docker compose run --rm runway python run.py restore /data/<file> --yes`.)

The backup holds your bank access and API keys encrypted with your key: set the same `RUNWAY_SECRET_KEY` on the
server (or copy `secret.key` into its `./data`) before restoring. Restored under another key, they can't be read (the
restore says which): put the key the backup was made with in `RUNWAY_SECRET_KEY_OLD` and restart once, and Runway
re-encrypts them with the current key; or enter them again in Settings. Delete stray copies of the file once you've
restored it.

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
   Turn on two-factor sign-in at your identity provider: it guards everything behind it. Everyone you let in shares
   one Runway: they see the same accounts and can change the same settings, connect and disconnect banks, download
   the backup and restore one (see [SECURITY.md](SECURITY.md)). Let in only people you'd hand your finances to.
3. **A secret key.** Set `RUNWAY_SECRET_KEY` (`openssl rand -base64 32`) and keep a copy in your password manager.
   It encrypts your saved bank access and API keys. (Without it, the key is `./data/secret.key`: back it up with the
   database.) Changing it: put the old one in `RUNWAY_SECRET_KEY_OLD` for one start, and keep it as long as you keep
   backups made with it (a backup's secrets are under the key of the time).
4. **Rate limiting at the proxy** (optional but good): Runway caps how many requests it handles at once, and the proxy
   can limit requests per address, e.g. Caddy's `rate_limit` or Traefik's `RateLimit` middleware.
5. **Backups kept private.** They contain your transactions, and your bank access and API keys encrypted with your
   `RUNWAY_SECRET_KEY` (restoring elsewhere needs the same key).
6. **Updates.** Pull new images regularly; each release is tested, and its dependencies are checked for known
   vulnerabilities.
7. **AI assistants** connect with OAuth at `<RUNWAY_PUBLIC_URL>/mcp` ([docs/mcp.md](docs/mcp.md)). If a forward-auth
   proxy signs you in (`RUNWAY_ALLOW_NO_AUTH=1`), exempt `/mcp`, `/oauth/register`, `/oauth/token`, `/oauth/revoke`
   and `/.well-known/oauth-*` from it, and keep `/oauth/authorize` behind it.

## Everyday

- Update to the newest image: `docker compose pull && docker compose up -d`
  (or let Watchtower do it automatically). If you pinned a version, change the tag first. Database changes are
  applied automatically when the new version starts; take a backup first if you like to be careful.
- Stop: `docker compose down` (data stays in `./data`)
- Back up: Settings → Advanced → Backup → Download a backup (works for either database)

## Error reports (optional)

Set `SENTRY_DSN` (Sentry → your project → Settings → Client Keys) and Runway sends its errors to Sentry, from the
server and from the web app, tagged with the version, and everything in the table below. Without it, nothing is sent
anywhere.

- A report has the error, its stack trace and the page or API path. It never has request bodies, cookies, headers,
  query strings, the values of variables, or anything on screen; addresses with a password in them (SimpleFIN's) and
  Plaid tokens are blanked.
- `SENTRY_BROWSER_DSN` sends the web app's reports to a separate Sentry project; `RUNWAY_SENTRY_BROWSER=0` keeps the
  web app from sending any. The browser only ever talks to the DSN's `https://` host (the page's
  Content-Security-Policy allows that one address).
- `SENTRY_ENVIRONMENT` (default `production`).
- An error page's "reference" code is on the Sentry event as the `ref` tag, so a reference from the app finds its report.

The rest of Sentry, all on with a DSN. Turn a rate down (`0` for none, the default is `1`: all) or a switch off
(`=0`):

| Variable | What it adds |
| --- | --- |
| `SENTRY_TRACES_SAMPLE_RATE` | Tracing, for that share of requests, syncs and page views: each API request by its route (`GET /api/transactions/{id}/category`, never the ids or what was searched), its database queries (without their values) and calls to banks and services; the daily sync; page loads and navigations with Web Vitals. A page's trace continues into the server's. |
| `SENTRY_PROFILE_SESSION_SAMPLE_RATE` | Profiling while tracing, for that share of server runs and browser visits: which functions the time goes to. The browser's profiler is Chrome's and Edge's. |
| `SENTRY_REPLAY_SAMPLE_RATE`, `SENTRY_REPLAY_ON_ERROR_SAMPLE_RATE` | Session Replay, for that share of visits (or of visits with an error, their last minute). Every piece of text, every input and every image is masked: a replay shows the layout, the clicks and the pages, not your data. |
| `SENTRY_LOGS` | Runway's log lines as Sentry Logs (one per request, with its route and timing but not your address), and the web app's console warnings and errors. |
| `SENTRY_METRICS` | Metrics: how long syncs take and whether they fail, new transactions per sync, and the AI's tokens. |
| `SENTRY_CRONS` | A Cron Monitor for the bank sync (`runway-bank-sync`), so Sentry tells you when a day goes by without one or one fails. Any bank sync counts, the Sync button's too. Sentry makes the monitor in Runway's time zone (`TZ`, or the system's); if `TZ` is a rule like `EST5EDT` rather than a name like `America/New_York`, or automatic syncing is off (`RUNWAY_NO_SYNC=1`), create the monitor in Sentry yourself with that slug. |
| `SENTRY_FEEDBACK` | "Send feedback" at the bottom of Settings → Advanced: a message to Sentry, without your name or email or a screenshot. |
| `SENTRY_AI_CONTENT` | With tracing, the AI's prompts and replies on its spans in Sentry's Agent Tracing (the merchants, amounts and dates it's asked about, which the AI provider sees anyway). With `SENTRY_AI_CONTENT=0`, Agent Tracing still has each categorizing run, its model, timings and tokens. |

Also in Sentry, without a variable:

- **Releases and health.** Everything is tagged with the version, and each request to the server and each visit counts
  toward a release's crash-free rate. Release builds (the published image) also upload the web app's source maps, so
  its stack traces are readable; the release workflow needs a `SENTRY_AUTH_TOKEN` secret and a `SENTRY_ORG` variable
  (plus `SENTRY_PROJECT` / `SENTRY_BROWSER_PROJECT` if your projects aren't `runway` and `runway-web`). To build your
  own image with them: `docker build --secret id=sentry_auth_token,env=SENTRY_AUTH_TOKEN --build-arg SENTRY_ORG=… .`
- **MCP.** Calls from an assistant through Runway's MCP server are spans named by the tool (never its arguments), in
  Sentry's MCP view.
- **Uptime.** Point a Sentry Uptime Monitor at `<RUNWAY_PUBLIC_URL>/healthz`, which answers without signing in.

## Notes

- Installing Runway on an iPhone and push notifications both need `RUNWAY_PUBLIC_URL` to be `https://`. Notifications
  go out through Apple's, Google's or Mozilla's push service, so the server needs outbound HTTPS to them.

- Plaid: banks that sign you in on their own site (Chase, Capital One, …) send you back to
  `<RUNWAY_PUBLIC_URL>/plaid/oauth`. Add that address under **Allowed redirect URIs** in the Plaid Dashboard (Runway
  shows it in Settings → Connections); it's what makes those banks work from a phone or the installed app.
- Only people in `OIDC_ALLOWED_EMAILS` / `OIDC_ALLOWED_GROUPS` get in, even if your provider lets others sign in.
  Everyone who gets in shares everything: it's one household's Runway, not one account per person.
- A session ends after 14 days without using Runway (`RUNWAY_SESSION_DAYS`); using it keeps you signed in, for up to
  90 days after you signed in. (If you're let in by `OIDC_ALLOWED_GROUPS` rather than by email, it ends 14 days after
  signing in, so leaving the group takes effect.) Signing out ends the Runway session and your provider session.
- Someone who isn't allowed in sees that, with a button to sign in with another account; the log says who was refused.
- Runway answers only to addresses that are yours: `RUNWAY_PUBLIC_URL`'s host, local IPs, `*.local`, plain names like
  `nas`, and Tailscale names. Add others to `RUNWAY_ALLOWED_HOSTS`.
- Use `https://` for `RUNWAY_PUBLIC_URL` (a reverse proxy like Caddy or Traefik, or Tailscale). Session cookies are
  then marked Secure. On an internet address Runway requires it.
- Already sign in through a proxy (Authelia forward-auth, Cloudflare Access, oauth2-proxy)? Leave `OIDC_ISSUER`
  empty and set `RUNWAY_ALLOW_NO_AUTH=1` instead, and don't expose port 8765 except through that proxy.
