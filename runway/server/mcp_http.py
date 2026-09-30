"""How the MCP server (POST /mcp) reaches Runway's pages, in this process: who the caller is (authorized), the
allowlist lookup (resolve) and the call itself. Every page goes through resolve(), so mcp_access.READABLE and
WRITABLE, the connection's scope and the "writes" switch apply to every call."""
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
    """Which page `method path` (like GET /api/overview) is, if an assistant may use it: ("read" | "write", handler, path
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
    """Call a resolved page as an assistant (no signed-in person). ApiError and the like are left for the caller."""
    _current.user = None
    with db.session() as conn:
        return fn(conn, query, body, *params)


def local_fetch(path: str, params: dict[str, Any], body: dict | None, access: mcp_access.Access) -> Any:
    """mcp_server's fetch(path, query, body) as `access` may: the lookup, the scope and the switch checked on every
    change, and the page's own reply (or refusal, as ToolError)."""
    full = "/api/" + path
    with db.session() as conn:
        switch = mcp_access.allow_writes(conn)
    scoped = "churning:write" in access.scopes
    why = None if scoped and switch else READ_ONLY if not scoped else WRITES_OFF
    if body is None and full == "/api/access":
        return {"writes": why is None, **({"why": why} if why else {})}
    hit = resolve("GET" if body is None else "POST", full)
    if hit is None:
        raise ToolError("Not found")
    kind, fn, args = hit
    if kind == "write" and why:
        raise ToolError(why)
    if kind == "read" and "read" not in access.scopes:
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
