"""Settings → Data → AI assistants (MCP): the address to connect to, the assistants connected with OAuth, and the
switches for letting them change churning and categorize. The server is runway/mcp_server.py; OAuth is runway/mcp_oauth.py."""
from __future__ import annotations

from datetime import datetime

from ... import mcp_access, mcp_oauth
from ..common import ApiError, _current


def _when(t: float | None) -> str | None:
    return datetime.fromtimestamp(t).isoformat(timespec="seconds") if t else None


def api_mcp_settings(conn, _q, _b):
    iss = mcp_oauth.issuer(getattr(_current, "host", None))
    return {"allow_writes": mcp_access.allow_writes(conn), "allow_categorize": mcp_access.allow_categorize(conn), "oauth": iss is not None,
            "url": mcp_oauth.resource(iss) if iss else None, "reason": None if iss else mcp_oauth.unavailable_reason(),
            "connections": [{**c, "created": _when(c["created"]), "last_used": _when(c["last_used"])}
                            for c in mcp_oauth.connections(conn)]}


def api_mcp_writes(conn, _q, body):
    """Switch on or off letting assistants allowed churning:write make the changes in mcp_access.WRITABLE."""
    mcp_access.set_allow_writes(conn, body.get("allow") in (True, 1, "1", "true", "on"))
    return {"allow_writes": mcp_access.allow_writes(conn)}


def api_mcp_categorize(conn, _q, body):
    """Switch on or off letting assistants allowed categorize:write make the changes in mcp_access.CATEGORIZABLE."""
    mcp_access.set_allow_categorize(conn, body.get("allow") in (True, 1, "1", "true", "on"))
    return {"allow_categorize": mcp_access.allow_categorize(conn)}


def api_mcp_revoke(conn, _q, _b, grant_id):
    """Disconnect an assistant: its grant, and every token under it, end at once."""
    try:
        gid = int(grant_id)
    except ValueError:
        raise ApiError("Not found", 404) from None
    if not mcp_oauth.revoke_grant(conn, gid, "revoked_in_settings"):
        raise ApiError("That connection isn't there any more.", 404)
    return {"ok": True}
