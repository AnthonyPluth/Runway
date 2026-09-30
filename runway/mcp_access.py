"""The MCP server's access to Runway: a read-only key, an optional write key, and what each may do.

Keys are bearer tokens made under Settings → Connections (only a hash of each is kept), separate from the browser
extension's key and from signing in. The read key (rwm_) opens GET /api/mcp/<page> for the pages in READABLE and nothing
else: no settings, connections, bank credentials or backups, and nothing that changes data. The write key (rww_) is a
second, opt-in key that reads the same pages and can also POST the churning actions in WRITABLE: nothing else, and no
deletes. The read key can never write.
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
    "/api/investments", "/api/equity", "/api/churning", "/api/churning/best",
})


# The churning changes the write key may make (POST /api/mcp/<the same path>): adding and changing, checking things off,
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


def new_write_token(conn) -> str:
    """A new write key (replacing any earlier one). Only a hash of it is kept."""
    token = "rww_" + secrets.token_urlsafe(32)
    db.set_setting(conn, sk.MCP_WRITE_TOKEN_HASH, hashlib.sha256(token.encode()).hexdigest())
    db.set_setting(conn, sk.MCP_WRITE_TOKEN_CREATED, datetime.now().isoformat(timespec="seconds"))
    return token


def remove_write_token(conn) -> None:
    db.set_setting(conn, sk.MCP_WRITE_TOKEN_HASH, None)
    db.set_setting(conn, sk.MCP_WRITE_TOKEN_CREATED, None)


def _matches(conn, key: str, authorization: str | None) -> bool:
    want = db.get_setting(conn, key)
    m = re.match(r"Bearer\s+(\S+)$", (authorization or "").strip())
    if not want or not m:
        return False
    return hmac.compare_digest(hashlib.sha256(m.group(1).encode()).hexdigest(), want)


def access(conn, authorization: str | None) -> str | None:
    """What the key in this Authorization header may do: "write" (the write key), "read" (the read key), or None."""
    if _matches(conn, sk.MCP_WRITE_TOKEN_HASH, authorization):
        return "write"
    return "read" if _matches(conn, sk.MCP_TOKEN_HASH, authorization) else None


def check_token(conn, authorization: str | None) -> bool:
    """Whether this is the read key (the write key is checked with access())."""
    return _matches(conn, sk.MCP_TOKEN_HASH, authorization)


def status(conn) -> dict:
    return {"token": bool(db.get_setting(conn, sk.MCP_TOKEN_HASH)), "token_created": db.get_setting(conn, sk.MCP_TOKEN_CREATED),
            "write_token": bool(db.get_setting(conn, sk.MCP_WRITE_TOKEN_HASH)),
            "write_token_created": db.get_setting(conn, sk.MCP_WRITE_TOKEN_CREATED)}
