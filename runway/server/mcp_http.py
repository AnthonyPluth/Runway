"""How the MCP server reaches Runway's pages: the allowlist lookup and the call itself, shared by GET/POST /api/mcp/*
(the stdio proxy, over HTTP) and POST /mcp (Runway serving MCP itself, in process). Both go through resolve(), so
mcp_access.READABLE and WRITABLE and the "writes" switch apply the same way to each."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

import sqlalchemy.exc

from .. import db, mcp_access
from ..mcp_server import ToolError
from .common import ApiError, _current
from .routes import ROUTES, _match

# The ways to prove a caller may use the MCP server: each takes (conn, Authorization header) and says yes or no.
# A later credential (an OAuth-issued access token) is another entry here; the rest of the auth path doesn't change.
CREDENTIAL_CHECKS: tuple[Callable[[Any, str | None], bool], ...] = (mcp_access.check_token,)

WRITES_OFF = "Changes are switched off. Turn on \"Let assistants change churning\" in Runway under Settings → Connections."


def authorized(conn, authorization: str | None) -> bool:
    return any(check(conn, authorization) for check in CREDENTIAL_CHECKS)


def resolve(method: str, path: str) -> tuple[str, Callable, list] | None:
    """Which page `method path` (like GET /api/overview) is, if the key may use it: ("read" | "write", handler, path
    parameters). None for anything outside mcp_access.READABLE / WRITABLE."""
    if method == "GET":
        pattern = path if path in mcp_access.READABLE else next((p for p in mcp_access.READABLE_PATTERNS if _match(p, path) is not None), None)
        if pattern:
            return "read", next(fn for m, p, fn in ROUTES if m == "GET" and p == pattern), ([] if pattern == path else _match(pattern, path))
    elif method == "POST":
        hit = next(((p, _match(p, path)) for p in mcp_access.WRITABLE if _match(p, path) is not None), None)
        if hit:
            return "write", next(fn for m, pattern, fn in ROUTES if m == "POST" and pattern == hit[0]), hit[1]
    return None


def run(fn: Callable, query: dict, body: dict, params: list) -> Any:
    """Call a resolved page as the MCP key (no signed-in person). ApiError and the like are left for the caller."""
    _current.user = None
    with db.session() as conn:
        return fn(conn, query, body, *params)


def local_fetch(path: str, params: dict[str, Any], body: dict | None = None) -> Any:
    """mcp_server's fetch(path, query, body) answered in this process instead of over HTTP: the same lookup, the same
    switch checked on every change, the same replies (and refusals, as ToolError)."""
    full = "/api/" + path
    with db.session() as conn:
        writes = mcp_access.allow_writes(conn)
    if body is None and full == "/api/access":
        return {"writes": writes}
    hit = resolve("GET" if body is None else "POST", full)
    if hit is None:
        raise ToolError("Not found")
    kind, fn, args = hit
    if kind == "write" and not writes:
        raise ToolError(WRITES_OFF)
    query = {k: [str(v)] for k, v in params.items() if v not in (None, "")}
    try:
        return run(fn, query, body or {}, args)
    except ApiError as e:
        raise ToolError(str(e)) from None
    except sqlalchemy.exc.OperationalError as e:
        if "locked" in str(e):
            raise ToolError("Runway is busy saving a sync. Try again in a few seconds.") from None
        raise
    except (ValueError, TypeError, KeyError):
        raise ToolError("Runway couldn't read one of the values sent.") from None
