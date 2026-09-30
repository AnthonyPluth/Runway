"""The MCP server's access to Runway: a read-only key, and the pages it may read.

The key is a bearer token made under Settings → Connections (only a hash of it is kept), separate from the browser
extension's key and from signing in. It opens GET /api/mcp/<page> for the pages in READABLE and nothing else: no
settings, connections, bank credentials or backups, and nothing that changes data.
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


def new_token(conn) -> str:
    """A new key for the MCP server (replacing any earlier one). Only a hash of it is kept."""
    token = "rwm_" + secrets.token_urlsafe(32)
    db.set_setting(conn, sk.MCP_TOKEN_HASH, hashlib.sha256(token.encode()).hexdigest())
    db.set_setting(conn, sk.MCP_TOKEN_CREATED, datetime.now().isoformat(timespec="seconds"))
    return token


def remove_token(conn) -> None:
    db.set_setting(conn, sk.MCP_TOKEN_HASH, None)
    db.set_setting(conn, sk.MCP_TOKEN_CREATED, None)


def check_token(conn, authorization: str | None) -> bool:
    want = db.get_setting(conn, sk.MCP_TOKEN_HASH)
    m = re.match(r"Bearer\s+(\S+)$", (authorization or "").strip())
    if not want or not m:
        return False
    return hmac.compare_digest(hashlib.sha256(m.group(1).encode()).hexdigest(), want)


def status(conn) -> dict:
    return {"token": bool(db.get_setting(conn, sk.MCP_TOKEN_HASH)), "token_created": db.get_setting(conn, sk.MCP_TOKEN_CREATED)}
