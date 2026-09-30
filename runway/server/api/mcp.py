"""The MCP server's key (Settings → Advanced). The server itself is runway/mcp_server.py; what it may read is
mcp_access.READABLE."""
from __future__ import annotations

from ... import mcp_access


def api_mcp_key(conn, _q, _b):
    return mcp_access.status(conn)


def api_mcp_key_new(conn, _q, _b):
    """A new read-only key for the MCP server; shown once."""
    return {"token": mcp_access.new_token(conn)}


def api_mcp_writes(conn, _q, body):
    """Switch on or off letting the key make the churning changes in mcp_access.WRITABLE."""
    mcp_access.set_allow_writes(conn, body.get("allow") in (True, 1, "1", "true", "on"))
    return {"allow_writes": mcp_access.allow_writes(conn)}


def api_mcp_key_remove(conn, _q, _b):
    mcp_access.remove_token(conn)
    return {"ok": True}
