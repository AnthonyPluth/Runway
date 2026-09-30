# AI assistants (MCP)

Runway includes a [Model Context Protocol](https://modelcontextprotocol.io) server, so an assistant like Claude can read your accounts, transactions, budget, reports, net worth and credit-card churning (cards, benefits, upcoming fees) and answer questions about them. It is **read-only**: it can't change anything, and it never sees your bank connections, settings, API keys or backups.

1. In Runway, open **Settings → Advanced → AI assistants (MCP)** and choose **Make a key**. It's shown once; a new key replaces the old one, and Remove switches the server off.
2. Add the server to your assistant. It runs on your computer from a checkout of Runway (Python 3.14, standard library only) and talks to Runway over HTTP. For Claude Code:

```bash
claude mcp add runway -e RUNWAY_URL=https://runway.example.com -e RUNWAY_MCP_KEY=rwm_... -- python -m runway.mcp_server
```

## Letting it change churning (optional)

By default the key only reads. To let an assistant mark a benefit used, add or update cards, benefits, to-dos and planned items, and check off a plan, turn on **Let assistants change churning** in the same card. It's off until you switch it on, and turning it off takes effect at once (Runway checks the switch on every change). The changes can't delete anything or touch accounts, transactions or settings (the list is `WRITABLE` in `runway/mcp_access.py`), and they're marked as changing data so assistants like Claude ask before running them.

## What the key can reach

The key opens only `/api/mcp/<page>`, and only for the pages listed in `runway/mcp_access.py` (GET requests). Like the rest of Runway's API, it needs your Runway to be reachable from the computer running the assistant, so on the internet put it behind HTTPS (see [Deployment](deployment.md)).
