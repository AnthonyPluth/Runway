# Configuration

Settings that belong to the app (bank connections, API keys, categories, rules) live in **Settings** and are stored in the database. Everything about how and where Runway runs is set with environment variables, in `.env` for Docker (start from [`.env.example`](../.env.example)) or in the environment for a source checkout.

| Variable | Default | What it does |
|---|---|---|
| `RUNWAY_PUBLIC_URL` | | The address you open Runway at, e.g. `https://runway.example.com`. Required with sign-in; also the OAuth issuer AI assistants connect to ([MCP](mcp.md)). |
| `OIDC_ISSUER` | | Your identity provider's issuer URL. Setting it turns sign-in on. |
| `OIDC_CLIENT_ID` / `OIDC_CLIENT_SECRET` | | The client registered with your provider. Leave the secret empty for a public (PKCE-only) client. |
| `OIDC_ALLOWED_EMAILS` | | Comma-separated emails allowed in. An email counts only if your provider marks it verified (`email_verified`). Taking an email off ends that person's sessions, the AI assistants they approved ([MCP](mcp.md)), the browser extension key they made and their devices' notifications. |
| `OIDC_TRUST_UNVERIFIED_EMAIL` | | `1` lets `OIDC_ALLOWED_EMAILS` match an email your provider doesn't mark verified. Only for a provider where nobody can register or change their own email (Microsoft Entra ID never sends `email_verified`). |
| `OIDC_ALLOWED_GROUPS` | | Comma-separated groups (from the `groups` claim) allowed in. Groups are checked at sign-in, so an AI assistant someone approved keeps working until `RUNWAY_SESSION_DAYS` after they last signed in. |
| `OIDC_ALLOW_ANY_USER` | | `1` lets in anyone your provider signs in. Only for a provider you fully control. |
| `OIDC_SCOPES` | `openid email profile` | Add `groups` if your provider needs it to send group membership. |
| `RUNWAY_SESSION_DAYS` | `14` | Days a session lasts unused. Using Runway keeps it going, for up to 90 days after signing in. |
| `RUNWAY_ALLOWED_HOSTS` | | Extra host names Runway answers to (local IPs, `*.local`, bare names and Tailscale names always work). |
| `RUNWAY_PUSH_HOSTS` | | Extra push-service hosts notifications may be sent to (Google, Mozilla, Apple and Windows push always work), e.g. a self-hosted UnifiedPush server. |
| `RUNWAY_SECRET_KEY` | | Encrypts the bank access and API keys Runway saves (at least 32 characters: `openssl rand -base64 32`). Without it, Runway makes `secret.key` in `RUNWAY_DATA`. |
| `RUNWAY_SECRET_KEY_OLD` | | The previous key, for one start after changing `RUNWAY_SECRET_KEY`; everything is re-encrypted with the new one. Also the way to restore a backup made under an earlier key: keep old keys as long as you keep backups made with them. |
| `RUNWAY_ALLOW_INSECURE_HTTP` | | `1` allows an `http://` `RUNWAY_PUBLIC_URL` on an internet address. Don't. |
| `RUNWAY_ALLOW_NO_AUTH` | | `1` runs without sign-in on the network, for when a proxy in front already handles it. |
| `DATABASE_URL` | | `postgresql://user:password@host:5432/db` to use Postgres instead of the built-in SQLite file. |
| `RUNWAY_DATA` | `./data` (`/data` in Docker) | Where the SQLite database lives. |
| `RUNWAY_HOST` / `RUNWAY_PORT` | `127.0.0.1` / `8765` | Address and port to listen on (`0.0.0.0` in Docker). |
| `RUNWAY_NO_SYNC` | | `1` turns off the automatic background sync. |
| `TZ` | system | Decides what "today" is for forecasts and budgets, and when the daily sync runs. |

Reporting to Sentry is off unless you set `SENTRY_DSN`: then errors, tracing, profiling, replays, logs, metrics, cron monitoring, feedback and Agent Tracing (with the AI's prompts), each of which you can turn off. See [DOCKER.md](../DOCKER.md#error-reports-optional) for the variables.

With no `OIDC_ISSUER`, Runway refuses to listen beyond `localhost` unless `RUNWAY_ALLOW_NO_AUTH` is set. See [Deployment](deployment.md) and [SECURITY.md](../SECURITY.md).
