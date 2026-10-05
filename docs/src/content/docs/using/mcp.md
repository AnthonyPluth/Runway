---
title: AI assistants (MCP)
description: Connect Claude or another assistant to Runway.
sidebar:
  order: 3
---

Runway serves a [Model Context Protocol](https://modelcontextprotocol.io) endpoint, so an assistant like Claude can read your accounts, transactions, budget, reports, net worth, orders and credit-card churning (cards, benefits, upcoming fees) and answer questions about them, and, if you allow it, make changes. It never sees your bank connections, settings, API keys or backups.

## Connecting

Add Runway's address, `<RUNWAY_PUBLIC_URL>/mcp`, to the assistant. It's shown with a copy button under **Settings → Data → AI assistants (MCP)**.

- Claude Code: `claude mcp add --transport http runway https://runway.example.com/mcp`
- Claude on the web or desktop: **Settings → Connectors → Add custom connector**, with the same address.

The assistant then sends you to Runway to approve it (sign in first if you aren't). The page names the app and the address it returns to; choose **Allow** or **Deny**. Each approval shows up under **Connected assistants** on the same tab, with who approved it and when it was last used; **Disconnect** ends it at once. An approval lasts only as long as its approver may sign in: taking them off `OIDC_ALLOWED_EMAILS` ends their assistants' connections too, and with `OIDC_ALLOWED_GROUPS` (checked only at sign-in) a connection ends `RUNWAY_SESSION_DAYS` after its approver last signed in to Runway.

Runway is its own OAuth authorization server (OAuth 2.1 with PKCE, dynamic client registration), and its issuer is `RUNWAY_PUBLIC_URL`. Without `RUNWAY_PUBLIC_URL`, assistants can connect only on a home-network address (`http://localhost:8765`, `http://nas.local:8765`, …).

Clients that run OAuth inside a web page aren't supported: Runway sends no CORS headers, and `/mcp` refuses requests from a page on another origin. Claude Code and Claude on the web or desktop work; test with those, not the MCP Inspector's browser mode.

An app that registers but is never approved is forgotten after a day (and at most 50 wait at once). If Runway says it doesn't know the app, remove Runway from the assistant and add it again.

## Letting it change churning (optional)

An assistant always gets **read**. It can also ask for **churning:write**: marking a benefit used, adding or updating cards, benefits, to-dos and planned items, and checking off a plan (the list is `WRITABLE` in `runway/server/mcp_access.py`; nothing is ever deleted, and accounts, transactions and settings are out of reach). A change needs both:

- **Change churning** ticked on the approval page (offered only when the assistant asks for it), and
- **Let assistants change churning** switched on in the card. It's off until you turn it on, applies to every connection, and is checked on every change, so turning it off takes effect at once without revoking anything.

A connection approved read-only that tries a change is told to reconnect. The changing tools are marked as such, so assistants like Claude ask before running them.

## Letting it categorize (optional)

An assistant can also ask for **categorize:write**: setting a transaction's category (`set_transaction_category`), accepting the category Runway suggested for one that needs review (`accept_transaction_category`), and setting an order item's category (`set_order_item_category`). Only existing categories can be used (the assistant finds them with `list_categories`); it can't split transactions, rename payees, or add, rename or remove categories (the list is `CATEGORIZABLE` in `runway/server/mcp_access.py`). With `remember`, a transaction's category also becomes a rule for its merchant, and an item's is used for the same item in other orders; that's off unless the assistant asks for it. Like churning, a change needs both:

- **Categorize** ticked on the approval page (offered only when the assistant asks for it), and
- **Let assistants categorize** switched on in the card. It's separate from the churning switch, off until you turn it on, and checked on every change.

A connection approved without it that tries to categorize is told to reconnect. A transaction that's split across categories is refused (one category would remove its parts); change those in Runway. Setting a category marks the transaction reviewed and yours; `set_transaction_category` replies with what it had before (`was`), but there's no undo over MCP: setting the old category again leaves it reviewed, and a remembered rule stays until you change it under Settings → Rules.

## Letting it change anything (optional)

An assistant can also ask for **write**: adding, changing and removing your financial data, as the web app does. That's transactions, budgets, categories, rules, recurring items, forecast amounts, accounts, net worth items, equity, investments, churning and orders, deletes included. It never reaches bank connections (Plaid, SimpleFIN, Carta), API keys and other settings, notifications, logos, backups, or these assistant settings themselves (the blocked list is `BLOCKED` in `runway/server/mcp_access.py`), and it can't change which bank connection an account comes from. It brings the churning and categorize changes with it. A change needs both:

- **Change anything** ticked on the approval page (offered only when the assistant asks for it, and never ticked for you), and
- **Let assistants change anything** switched on in the card. It's separate from the other two, off until you turn it on, and checked on every change.

Runway tells the assistant to describe every change and wait for your yes, and for anything destructive (removing or deleting something, or changing many records at once, marked as such) to say what will be lost and ask again, one change at a time. Most areas have their own tools; `list_endpoints` lists everything else it can reach and `call_endpoint` calls it, through the same checks.

The worked example: paste or upload a statement and ask the assistant to enter it. It reads the rows, checks the account with you, shows you the dates, payees and amounts, and on your yes sends them in one call (`add_transactions`, up to 500 rows). A row already in that account (same day, amount and payee) or repeated in the statement is skipped and reported; your rules categorize the rest, and what they don't goes to Review. This is for accounts Runway doesn't sync: a statement for an account a bank sync also feeds would duplicate what the sync brings in later, since only what's already there is caught.

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

Registration (`/oauth/register`) needs no sign-in, so anyone who can reach Runway can register apps (never more than 50 unapproved at once). Runway has no per-address rate limiting; put it at the reverse proxy (see [Putting Runway on the internet](/Runway/start/docker/#putting-runway-on-the-internet)).

With `RUNWAY_ALLOW_NO_AUTH=1` and a forward-auth proxy (Authelia, Cloudflare Access, oauth2-proxy), exempt these paths from the proxy's sign-in, since assistants call them without a browser: `/mcp`, `/oauth/register`, `/oauth/token`, `/oauth/revoke` and `/.well-known/oauth-*`. Keep `/oauth/authorize` behind it: that's where you approve.
