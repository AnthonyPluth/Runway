# AI assistants (MCP)

Runway serves a [Model Context Protocol](https://modelcontextprotocol.io) endpoint, so an assistant like Claude can read your accounts, transactions, budget, reports, net worth, orders and credit-card churning (cards, benefits, upcoming fees) and answer questions about them. It never sees your bank connections, settings, API keys or backups.

## Connecting

Add Runway's address, `<RUNWAY_PUBLIC_URL>/mcp`, to the assistant. It's shown with a copy button under **Settings → Advanced → AI assistants (MCP)**.

- Claude Code: `claude mcp add --transport http runway https://runway.example.com/mcp`
- Claude on the web or desktop: **Settings → Connectors → Add custom connector**, with the same address.

The assistant then sends you to Runway to approve it (sign in first if you aren't). The page names the app and the address it returns to; choose **Allow** or **Deny**. Each approval shows up under **Connected assistants** in the same card, with who approved it and when it was last used; **Revoke** disconnects it at once. An approval lasts only as long as its approver may sign in: taking them off `OIDC_ALLOWED_EMAILS` ends their assistants' connections too, and with `OIDC_ALLOWED_GROUPS` (checked only at sign-in) a connection ends `RUNWAY_SESSION_DAYS` after its approver last signed in to Runway.

Runway is its own OAuth authorization server (OAuth 2.1 with PKCE, dynamic client registration), and its issuer is `RUNWAY_PUBLIC_URL`. Without `RUNWAY_PUBLIC_URL`, assistants can connect only on a home-network address (`http://localhost:8765`, `http://nas.local:8765`, …).

Clients that run OAuth inside a web page aren't supported: Runway sends no CORS headers, and `/mcp` refuses requests from a page on another origin. Claude Code and Claude on the web or desktop work; test with those, not the MCP Inspector's browser mode.

An app that registers but is never approved is forgotten after a day (and at most 50 wait at once). If Runway says it doesn't know the app, remove Runway from the assistant and add it again.

## Letting it change churning (optional)

An assistant always gets **read**. It can also ask for **churning:write**: marking a benefit used, adding or updating cards, benefits, to-dos and planned items, and checking off a plan (the list is `WRITABLE` in `runway/mcp_access.py`; nothing is ever deleted, and accounts, transactions and settings are out of reach). A change needs both:

- **Change churning** ticked on the approval page (offered only when the assistant asks for it), and
- **Let assistants change churning** switched on in the card. It's off until you turn it on, applies to every connection, and is checked on every change, so turning it off takes effect at once without revoking anything.

A connection approved read-only that tries a change is told to reconnect. The changing tools are marked as such, so assistants like Claude ask before running them.

## Endpoints

| Path | What |
| --- | --- |
| `POST /mcp` | MCP (Streamable HTTP, JSON replies), with `Authorization: Bearer <access token>` |
| `GET /.well-known/oauth-protected-resource/mcp` | Protected-resource metadata (RFC 9728) |
| `GET /.well-known/oauth-authorization-server` | Authorization-server metadata (RFC 8414) |
| `POST /oauth/register` | Dynamic client registration (RFC 7591) |
| `GET/POST /oauth/authorize` | The approval page (needs you signed in) |
| `POST /oauth/token` | Codes and refresh tokens for tokens |
| `POST /oauth/revoke` | Revocation (RFC 7009) |

Access tokens last an hour and refresh tokens 90 days; refresh tokens rotate, and a used one sent again revokes the whole connection. Only hashes of tokens and codes are stored, and none of this goes into backups: reconnect assistants after a restore.

## Behind a proxy

Registration (`/oauth/register`) needs no sign-in, so anyone who can reach Runway can register apps (never more than 50 unapproved at once). Runway has no per-address rate limiting; put it at the reverse proxy (see [DOCKER.md](../DOCKER.md#putting-runway-on-the-internet)).

With `RUNWAY_ALLOW_NO_AUTH=1` and a forward-auth proxy (Authelia, Cloudflare Access, oauth2-proxy), exempt these paths from the proxy's sign-in, since assistants call them without a browser: `/mcp`, `/oauth/register`, `/oauth/token`, `/oauth/revoke` and `/.well-known/oauth-*`. Keep `/oauth/authorize` behind it: that's where you approve.
