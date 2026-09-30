"""The MCP server's key (Settings → Connections). The server itself is runway/mcp_server.py; what it may read is
mcp_access.READABLE."""
from __future__ import annotations

from ... import mcp_access


def api_mcp_key(conn, _q, _b):
    return mcp_access.status(conn)


def api_mcp_key_new(conn, _q, _b):
    """A new read-only key for the MCP server; shown once."""
    return {"token": mcp_access.new_token(conn)}


def api_mcp_key_remove(conn, _q, _b):
    mcp_access.remove_token(conn)
    return {"ok": True}
