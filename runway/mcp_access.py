"""What an AI assistant connected to Runway's MCP endpoint (/mcp) may reach, and the switches for whether it may change
churning, categorize, or change anything.

An assistant connects with OAuth (runway/mcp_oauth.py) and gets a token for the scopes you approved: "read" opens the
pages in READABLE and nothing else (no settings, connections, bank credentials or backups); "churning:write" also
allows the churning changes in WRITABLE (nothing else, and no deletes), but only while "Let assistants change
churning" is switched on (allow_writes); "categorize:write" allows picking the categories in CATEGORIZABLE, only while
"Let assistants categorize" is on (allow_categorize); "write" allows every change the web app makes outside BLOCKED
(writable_routes, deletes included) and the pages in WRITE_READABLE, only while "Let assistants change anything" is on
(allow_all), and implies the other two scopes. The switches are off unless you turn them on, and are read on every
change, so turning one off takes effect at once for every connection, without revoking any.

BLOCKED is the boundary: bank connections, credentials and API keys, notifications and these settings themselves are
never reachable from /mcp, whatever the scope or switch.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select

from . import db, mcp_oauth
from . import settings_keys as sk
from .models import Transaction

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
# One category for a split transaction removes its parts, and nothing an assistant can reach puts them back.
SPLIT_REFUSED = ("That transaction is split across categories, and one category would remove its parts. "
                 "Change it in Runway's Transactions page instead.")

# Each changing scope: the changes it allows, and the switch (a settings key, "1" for on) they also need.
CHANGES = {"churning:write": (WRITABLE, sk.MCP_ALLOW_WRITES), "categorize:write": (CATEGORIZABLE, sk.MCP_ALLOW_CATEGORIZE)}

# "write": any change the web app makes (every POST and DELETE route, writable_routes) except what's BLOCKED, while
# "Let assistants change anything" is on. It implies churning:write and categorize:write.
ANYTHING = "write"
SWITCHES = {**{scope: key for scope, (_paths, key) in CHANGES.items()}, ANYTHING: sk.MCP_ALLOW_ALL}

# Never reachable from /mcp, whatever the scope or switch: each blocks its own path and everything under it. The
# assistant settings (an assistant must never widen its own access or end a connection), API keys and other settings,
# bank connections and third-party credentials or tokens, notifications and devices, and logos (they fetch from
# Logo.dev with its key).
BLOCKED = (
    "/api/mcp-settings", "/api/settings", "/api/state", "/api/connect", "/api/plaid", "/api/carta", "/api/finnhub",
    "/api/logodev", "/api/realie", "/api/retail/token", "/api/retail/settings", "/api/push",
    "/api/accounts/{id}/logo", "/api/accounts/{id}/logo-options", "/api/merchants/logo", "/api/merchants/logo-options",
    "/api/investments/logo", "/api/investments/logo-options",
)
# Fields of an allowed change that are still refused: which bank connection an account's transactions come from.
REFUSED_FIELDS = {("POST", "/api/accounts/{id}"): ("provider",)}
FIELD_REFUSED = "Which bank connection an account comes from is changed in Runway itself (Settings → Accounts)."

# GET pages "write" also opens: what its changes need to find what to change. None holds a secret.
WRITE_READABLE = (
    "/api/rules", "/api/recurring/suggestions", "/api/recurring/missed", "/api/recurring/{id}/candidates", "/api/accounts/deleted",
    "/api/accounts/{id}/removal", "/api/churning/found", "/api/retail/charges/{id}/candidates", "/api/ai/log", "/api/tracked/{id}",
)
# Changes that touch many records at once (or drop an alert for good): destructive, like a delete or a remove.
MANY = frozenset({"/api/recategorize", "/api/transactions/bulk", "/api/rules/{id}/apply", "/api/ai/apply", "/api/recurring/dismiss"})


def blocked(template: str) -> bool:
    """Whether a route (its pattern, as in server/routes.py) is out of every assistant's reach."""
    return any(template == b or template.startswith(b + "/") for b in BLOCKED)


def destructive(method: str, template: str) -> bool:
    """Whether a change removes or deletes something, or changes many records at once."""
    return method == "DELETE" or "remove" in template.split("/") or template in MANY


def writable_routes(routes) -> list[tuple[str, str]]:
    """The changes "write" allows, as (method, pattern), from server/routes.py's ROUTES: every POST and DELETE that
    isn't BLOCKED. A new route is allowed unless it's added to BLOCKED (tests/test_mcp.py keeps a list to review)."""
    return [(m, p) for m, p, *_fn in routes if m in ("POST", "DELETE") and not blocked(p)]


@dataclass(frozen=True)
class Access:
    """What one caller of /mcp may do: its scopes ("read", "churning:write", "categorize:write", "write"), the grant it
    came from and who approved it. A change also needs its scope's switch on at that moment (switched_on)."""
    scopes: frozenset[str]
    grant_id: int | None
    who: str | None

    def has(self, scope: str) -> bool:
        """Whether the connection was allowed `scope` ("write" brings churning:write and categorize:write with it)."""
        return scope in self.scopes or (scope in CHANGES and ANYTHING in self.scopes)


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


def allow_all(conn) -> bool:
    """Whether any change "write" allows is switched on (it's off until you turn it on)."""
    return db.get_setting(conn, sk.MCP_ALLOW_ALL) == "1"


def set_allow_all(conn, on: bool) -> None:
    db.set_setting(conn, sk.MCP_ALLOW_ALL, "1" if on else "0")


def is_split(conn, tx_id: str) -> bool:
    row = conn.execute(select(Transaction.is_split).where(Transaction.id == tx_id)).fetchone()
    return bool(row and row["is_split"])


def switched_on(conn, scope: str) -> bool:
    """Whether the changes `scope` allows are switched on right now. False for a scope that allows none."""
    return scope in SWITCHES and db.get_setting(conn, SWITCHES[scope]) == "1"

