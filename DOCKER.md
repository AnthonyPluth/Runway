# Running Runway in Docker

Runway signs you in through your OpenID Connect provider (Authentik, Authelia, Keycloak, Pocket ID, Google,
Microsoft Entra, ...). Nothing else is needed: the image is plain Python with no extra packages.

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
GitHub Actions (`.github/workflows/docker.yml`). Only the latest image is kept.

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
   "Sign out" is at the top right.

If a setting is missing, the container stops with a message saying which one (see the logs).

## Everyday

- Update to the newest image: `docker compose pull && docker compose up -d`
  (or let Watchtower do it automatically)
- Stop: `docker compose down` (data stays in `./data`)
- Back up: the `data` folder (stop the container first, or copy all three `runway.db*` files together)

## Notes

- Only people in `OIDC_ALLOWED_EMAILS` / `OIDC_ALLOWED_GROUPS` get in, even if your provider lets others sign in.
- Sessions last 14 days (`RUNWAY_SESSION_DAYS`). Signing out ends the Runway session and your provider session.
- Runway answers only to addresses that are yours: `RUNWAY_PUBLIC_URL`'s host, local IPs, `*.local`, plain names like
  `nas`, and Tailscale names. Add others to `RUNWAY_ALLOWED_HOSTS`.
- Use `https://` for `RUNWAY_PUBLIC_URL` when you can (a reverse proxy like Caddy or Traefik, or Tailscale).
  Session cookies are then marked Secure.
- Already sign in through a proxy (Authelia forward-auth, Cloudflare Access, oauth2-proxy)? Leave `OIDC_ISSUER`
  empty and set `RUNWAY_ALLOW_NO_AUTH=1` instead, and don't expose port 8765 except through that proxy.
