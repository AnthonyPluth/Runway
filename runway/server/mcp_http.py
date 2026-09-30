"""How the MCP server (POST /mcp) reaches Runway's pages, in this process: who the caller is (authorized), the
allowlist lookup (resolve) and the call itself. Every page goes through resolve(), so mcp_access.READABLE and the
changes in mcp_access.CHANGES, the connection's scopes and each change's switch apply to every call."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

import sqlalchemy.exc

from .. import db, mcp_access
from ..mcp_server import ToolError
from .common import ApiError, _current
from .routes import ROUTES, _match

# The ways to prove a caller may use the MCP server: each takes (conn, Authorization header, this server's resource)
# and answers what the caller may do (mcp_access.Access), or None.
CREDENTIAL_CHECKS: tuple[Callable[[Any, str | None, str | None], mcp_access.Access | None], ...] = (mcp_access.resolve_bearer,)

WRITES_OFF = "Changes are switched off. Turn on \"Let assistants change churning\" in Runway under Settings → Advanced."
READ_ONLY = ("This connection can only read. To let the assistant change churning, reconnect Runway in the assistant and "
             "allow \"Change churning\" when Runway asks.")
CATEGORIZE_OFF = "Categorizing is switched off. Turn on \"Let assistants categorize\" in Runway under Settings → Advanced."
CANT_CATEGORIZE = ("This connection can't categorize. To let the assistant pick categories, reconnect Runway in the assistant "
                   "and allow \"Categorize\" when Runway asks.")
# Why a change of each scope can't be made: (the connection wasn't allowed it, the switch is off).
REFUSALS = {"churning:write": (READ_ONLY, WRITES_OFF), "categorize:write": (CANT_CATEGORIZE, CATEGORIZE_OFF)}


def fetch_for(access: mcp_access.Access) -> Callable[..., Any]:
    """local_fetch as this caller: what mcp_server.handle() is given for one request to /mcp."""
    return lambda path, params, body=None: local_fetch(path, params, body, access)


def authorized(conn, authorization: str | None, resource: str | None = None) -> mcp_access.Access | None:
    for check in CREDENTIAL_CHECKS:
        access = check(conn, authorization, resource)
        if access is not None:
            return access
    return None


def resolve(method: str, path: str) -> tuple[str, Callable, list] | None:
    """Which page `method path` (like GET /api/overview) is, if an assistant may use it: ("read", or the scope a change
    needs, like "churning:write"; handler; path parameters). None for anything outside mcp_access.READABLE / CHANGES."""
    if method == "GET":
        pattern = path if path in mcp_access.READABLE else next((p for p in mcp_access.READABLE_PATTERNS if _match(p, path) is not None), None)
        if pattern:
            return "read", next(fn for m, p, fn in ROUTES if m == "GET" and p == pattern), ([] if pattern == path else _match(pattern, path))
    elif method == "POST":
        hit = next(((scope, p, _match(p, path)) for scope, (paths, _key) in mcp_access.CHANGES.items() for p in paths
                    if _match(p, path) is not None), None)
        if hit:
            return hit[0], next(fn for m, pattern, fn in ROUTES if m == "POST" and pattern == hit[1]), hit[2]
    return None


def run(fn: Callable, query: dict, body: dict, params: list) -> Any:
    """Call a resolved page as an assistant (no signed-in person). ApiError and the like are left for the caller."""
    _current.user = None
    with db.session() as conn:
        return fn(conn, query, body, *params)


def _why_not(access: mcp_access.Access, scope: str) -> str | None:
    """Why this connection can't make `scope`'s changes now (not allowed when it connected, or switched off), or None."""
    if scope not in REFUSALS:
        return "Not found"
    if scope not in access.scopes:
        return REFUSALS[scope][0]
    with db.session() as conn:
        on = mcp_access.switched_on(conn, scope)
    return None if on else REFUSALS[scope][1]


def local_fetch(path: str, params: dict[str, Any], body: dict | None, access: mcp_access.Access) -> Any:
    """mcp_server's fetch(path, query, body) as `access` may: the lookup, the scope and the switch checked on every
    change, and the page's own reply (or refusal, as ToolError). fetch("access", {"scope": ...}) answers whether this
    connection may make that scope's changes right now (churning:write when no scope is given)."""
    full = "/api/" + path
    if body is None and full == "/api/access":
        why = _why_not(access, str(params.get("scope") or "churning:write"))
        return {"writes": why is None, **({"why": why} if why else {})}
    hit = resolve("GET" if body is None else "POST", full)
    if hit is None:
        raise ToolError("Not found")
    kind, fn, args = hit
    if kind != "read":
        why = _why_not(access, kind)
        if why:
            raise ToolError(why)
    elif "read" not in access.scopes:
        raise ToolError("Not found")
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
