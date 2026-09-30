"""The MCP server's access to Runway: one key, and a switch for whether it may change churning.

The key is a bearer token made under Settings → Connections (only a hash of it is kept), separate from the browser
extension's key and from signing in. It opens GET /api/mcp/<page> for the pages in READABLE and nothing else: no settings,
connections, bank credentials or backups. Only while "Let assistants change churning" is switched on (allow_writes, off
unless you turn it on) can it also POST the churning actions in WRITABLE: nothing else, and no deletes.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime

from . import db
from . import settings_keys as sk

# The GET /api/... pages the key may read (each also has to be a route in server/routes.py).
READABLE = frozenset({
    "/api/overview", "/api/accounts", "/api/transactions", "/api/budget", "/api/categories", "/api/cashflow",
    "/api/month_pace", "/api/reports/spending", "/api/reports/income", "/api/reports/merchants",
    "/api/reports/merchant", "/api/reports/breakdown", "/api/reports/transactions", "/api/recurring", "/api/networth",
    "/api/investments", "/api/equity", "/api/churning", "/api/churning/best", "/api/retail",
})
# Readable pages with an id in the path (an order, with its items and their categories).
READABLE_PATTERNS = ("/api/retail/orders/{id}",)


# The churning changes the key may make while they're switched on (POST /api/mcp/<the same path>): adding and changing, checking things off,
# and undoing that; never removing anything, and nothing outside churning.
WRITABLE = (
    "/api/churning/cards", "/api/churning/cards/{id}", "/api/churning/cards/{id}/plan/done",
    "/api/churning/cards/{id}/plan/undo", "/api/churning/cards/{id}/benefits", "/api/churning/benefits/{id}",
    "/api/churning/benefits/{id}/use", "/api/churning/benefits/{id}/unuse", "/api/churning/tasks",
    "/api/churning/tasks/{id}", "/api/churning/tasks/{id}/snooze", "/api/churning/wishlist", "/api/churning/wishlist/{id}",
)


def new_token(conn) -> str:
    """A new key for the MCP server (replacing any earlier one). Only a hash of it is kept."""
    token = "rwm_" + secrets.token_urlsafe(32)
    db.set_setting(conn, sk.MCP_TOKEN_HASH, hashlib.sha256(token.encode()).hexdigest())
    db.set_setting(conn, sk.MCP_TOKEN_CREATED, datetime.now().isoformat(timespec="seconds"))
    return token


def remove_token(conn) -> None:
    db.set_setting(conn, sk.MCP_TOKEN_HASH, None)
    db.set_setting(conn, sk.MCP_TOKEN_CREATED, None)


def _matches(conn, key: str, authorization: str | None) -> bool:
    want = db.get_setting(conn, key)
    m = re.match(r"Bearer\s+(\S+)$", (authorization or "").strip())
    if not want or not m:
        return False
    return hmac.compare_digest(hashlib.sha256(m.group(1).encode()).hexdigest(), want)


def check_token(conn, authorization: str | None) -> bool:
    return _matches(conn, sk.MCP_TOKEN_HASH, authorization)


def allow_writes(conn) -> bool:
    """Whether the churning changes in WRITABLE are switched on (they're off until you turn them on)."""
    return db.get_setting(conn, sk.MCP_ALLOW_WRITES) == "1"


def set_allow_writes(conn, on: bool) -> None:
    db.set_setting(conn, sk.MCP_ALLOW_WRITES, "1" if on else "0")


def status(conn) -> dict:
    return {"token": bool(db.get_setting(conn, sk.MCP_TOKEN_HASH)), "token_created": db.get_setting(conn, sk.MCP_TOKEN_CREATED),
            "allow_writes": allow_writes(conn)}
