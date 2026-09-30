"""What an AI assistant connected to Runway's MCP endpoint (/mcp) may reach, and the switch for whether it may change churning.

An assistant connects with OAuth (runway/mcp_oauth.py) and gets a token for the scopes you approved: "read" opens the
pages in READABLE and nothing else (no settings, connections, bank credentials or backups); "churning:write" also
allows the churning changes in WRITABLE (nothing else, and no deletes), but only while "Let assistants change
churning" is switched on (allow_writes, off unless you turn it on). The switch is read on every change, so turning it
off takes effect at once for every connection, without revoking any.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import db, mcp_oauth
from . import settings_keys as sk

# The GET /api/... pages an assistant may read (each also has to be a route in server/routes.py).
READABLE = frozenset({
    "/api/overview", "/api/accounts", "/api/transactions", "/api/budget", "/api/categories", "/api/cashflow",
    "/api/month_pace", "/api/reports/spending", "/api/reports/income", "/api/reports/merchants",
    "/api/reports/merchant", "/api/reports/breakdown", "/api/reports/transactions", "/api/recurring", "/api/networth",
    "/api/investments", "/api/equity", "/api/churning", "/api/churning/best", "/api/retail",
})
# Readable pages with an id in the path (an order, with its items and their categories).
READABLE_PATTERNS = ("/api/retail/orders/{id}",)


# The churning changes an assistant may make with churning:write while they're switched on: adding and changing,
# checking things off, and undoing that; never removing anything, and nothing outside churning.
WRITABLE = (
    "/api/churning/cards", "/api/churning/cards/{id}", "/api/churning/cards/{id}/plan/done",
    "/api/churning/cards/{id}/plan/undo", "/api/churning/cards/{id}/benefits", "/api/churning/benefits/{id}",
    "/api/churning/benefits/{id}/use", "/api/churning/benefits/{id}/unuse", "/api/churning/tasks",
    "/api/churning/tasks/{id}", "/api/churning/tasks/{id}/snooze", "/api/churning/wishlist", "/api/churning/wishlist/{id}",
)


@dataclass(frozen=True)
class Access:
    """What one caller of /mcp may do: its scopes ("read", "churning:write"), the grant it came from and who approved
    it. A change also needs allow_writes() on at that moment."""
    scopes: frozenset[str]
    grant_id: int | None
    who: str | None


def resolve_bearer(conn, authorization: str | None, resource: str | None) -> Access | None:
    """What an OAuth access token (Authorization: Bearer rwa_...) for this MCP endpoint (`resource`) may do: its grant's
    scopes. None for anything else: no token, an unknown, expired or revoked one, or one for another resource."""
    m = re.fullmatch(r"Bearer\s+(\S+)", (authorization or "").strip(), re.I)
    if not m or not resource:
        return None
    grant = mcp_oauth.access_grant(conn, m.group(1), resource)
    if grant is None:
        return None
    return Access(frozenset(grant["scope"].split()), grant["id"], grant["email"] or grant["sub"])


def allow_writes(conn) -> bool:
    """Whether the churning changes in WRITABLE are switched on (they're off until you turn them on)."""
    return db.get_setting(conn, sk.MCP_ALLOW_WRITES) == "1"


def set_allow_writes(conn, on: bool) -> None:
    db.set_setting(conn, sk.MCP_ALLOW_WRITES, "1" if on else "0")

