"""What an AI assistant connected to Runway's MCP endpoint (/mcp) may reach, and the switches for whether it may change
churning or categorize.

An assistant connects with OAuth (runway/mcp_oauth.py) and gets a token for the scopes you approved: "read" opens the
pages in READABLE and nothing else (no settings, connections, bank credentials or backups); "churning:write" also
allows the churning changes in WRITABLE (nothing else, and no deletes), but only while "Let assistants change
churning" is switched on (allow_writes); "categorize:write" allows picking the categories in CATEGORIZABLE, only while
"Let assistants categorize" is on (allow_categorize). Both switches are off unless you turn them on, and are read on
every change, so turning one off takes effect at once for every connection, without revoking any.
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
# The category changes an assistant may make with categorize:write while they're switched on: a transaction's category
# (or accepting the one Runway suggested) and an order item's. Not splits, rules on their own, new categories or payees.
CATEGORIZABLE = ("/api/transactions/{id}/category", "/api/transactions/{id}/accept", "/api/retail/items/{id}")

# Each changing scope: the changes it allows, and the switch (a settings key, "1" for on) they also need.
CHANGES = {"churning:write": (WRITABLE, sk.MCP_ALLOW_WRITES), "categorize:write": (CATEGORIZABLE, sk.MCP_ALLOW_CATEGORIZE)}


@dataclass(frozen=True)
class Access:
    """What one caller of /mcp may do: its scopes ("read", "churning:write", "categorize:write"), the grant it came from
    and who approved it. A change also needs its scope's switch on at that moment (switched_on)."""
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


def allow_categorize(conn) -> bool:
    """Whether the category changes in CATEGORIZABLE are switched on (they're off until you turn them on)."""
    return db.get_setting(conn, sk.MCP_ALLOW_CATEGORIZE) == "1"


def set_allow_categorize(conn, on: bool) -> None:
    db.set_setting(conn, sk.MCP_ALLOW_CATEGORIZE, "1" if on else "0")


def switched_on(conn, scope: str) -> bool:
    """Whether the changes `scope` allows are switched on right now. False for a scope that allows none."""
    return scope in CHANGES and db.get_setting(conn, CHANGES[scope][1]) == "1"

