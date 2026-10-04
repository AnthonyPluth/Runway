"""How the MCP server (POST /mcp) reaches Runway's pages, in this process: who the caller is (authorized), the
allowlist lookup (kind_of) and the call itself. Every page is found by routes.match, in ROUTES only (so nothing outside
/api/'s routes, like sign-in or OAuth, can be reached), and answered by routes.dispatch, as the web app's are; it refuses
mcp_access.BLOCKED (backups and restores among them), and allows
mcp_access.READABLE, the changes in mcp_access.CHANGES and, for "write", ANYTHING and mcp_access.WRITE_READABLE; the
connection's scopes and each change's switch apply to every call. This is the gate: the tools are a convenience."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from typing import Any

from ..storage import db
from . import mcp_access
from .mcp_server import ToolError
from . import routes
from .common import ApiError, Response, _current

# The ways to prove a caller may use the MCP server: each takes (conn, Authorization header, this server's resource)
# and answers what the caller may do (mcp_access.Access), or None.
CREDENTIAL_CHECKS: tuple[Callable[[Any, str | None, str | None], mcp_access.Access | None], ...] = (mcp_access.resolve_bearer,)

# The changes "write" allows: (method, pattern), from ROUTES.
ANYTHING = frozenset(mcp_access.writable_routes(routes.ROUTES))

WRITES_OFF = "Changes are switched off. Turn on \"Let assistants change churning\" in Runway under Settings → Data."
READ_ONLY = ("This connection can only read. To let the assistant change churning, reconnect Runway in the assistant and "
             "allow \"Change churning\" when Runway asks.")
CATEGORIZE_OFF = "Categorizing is switched off. Turn on \"Let assistants categorize\" in Runway under Settings → Data."
CANT_CATEGORIZE = ("This connection can't categorize. To let the assistant pick categories, reconnect Runway in the assistant "
                   "and allow \"Categorize\" when Runway asks.")
ALL_OFF = "Changes are switched off. Turn on \"Let assistants change anything\" in Runway under Settings → Data."
CANT_CHANGE = ("This connection can't make that change. To let the assistant change anything, reconnect Runway in the "
               "assistant and allow \"Change anything\" when Runway asks.")
# Why a change of each scope can't be made: (the connection wasn't allowed it, the switch is off).
REFUSALS = {"churning:write": (READ_ONLY, WRITES_OFF), "categorize:write": (CANT_CATEGORIZE, CATEGORIZE_OFF),
            mcp_access.ANYTHING: (CANT_CHANGE, ALL_OFF)}
OUT_OF_REACH = ("Not found: assistants can't reach bank connections, API keys, notifications or the assistant settings. "
                "Change those in Runway itself.")
METHODS = ("GET", "POST", "DELETE")


def fetch_for(access: mcp_access.Access) -> Callable[..., Any]:
    """local_fetch as this caller: what mcp_server.handle() is given for one request to /mcp."""
    return lambda path, params, body=None, method=None: local_fetch(path, params, body, access, method)


def authorized(conn, authorization: str | None, resource: str | None = None) -> mcp_access.Access | None:
    for check in CREDENTIAL_CHECKS:
        access = check(conn, authorization, resource)
        if access is not None:
            return access
    return None


def kind_of(method: str, pattern: str) -> str | None:
    """What an assistant needs for a route: "read", or the scope a change (or a page only "write" opens) needs. None
    for anything an assistant may never use."""
    if mcp_access.blocked(pattern):
        return None
    if method == "GET":
        if pattern in mcp_access.READABLE or pattern in mcp_access.READABLE_PATTERNS:
            return "read"
        return mcp_access.ANYTHING if pattern in mcp_access.WRITE_READABLE else None
    scope = next((s for s, (paths, _key) in mcp_access.CHANGES.items() if pattern in paths), None)
    return scope or (mcp_access.ANYTHING if (method, pattern) in ANYTHING else None)


def endpoints() -> dict[str, list[str]]:
    """What "write" can reach with call_endpoint, by area (the path after /api/), destructive changes marked."""
    out: dict[str, list[str]] = defaultdict(list)
    for r in routes.TABLE.routes:
        m, pattern = r.method, r.pattern
        if kind_of(m, pattern):
            out[pattern.split("/")[2]].append(f"{m} {pattern}" + (" (destructive)" if m != "GET" and mcp_access.destructive(m, pattern) else ""))
    return dict(out)


def _why_not(access: mcp_access.Access, scope: str) -> str | None:
    """Why this connection can't make `scope`'s changes now (not allowed when it connected, or switched off), or None.
    "write", with its switch on, also allows the changes of the scopes it implies."""
    if scope not in REFUSALS:
        return "Not found"
    if not access.has(scope):
        return REFUSALS[scope][0]
    with db.session() as conn:
        if mcp_access.switched_on(conn, scope):
            return None
        if scope != mcp_access.ANYTHING and mcp_access.ANYTHING in access.scopes and mcp_access.switched_on(conn, mcp_access.ANYTHING):
            return None
    return REFUSALS[scope][1]


def local_fetch(path: str, params: dict[str, Any], body: dict | None, access: mcp_access.Access, method: str | None = None) -> Any:
    """mcp_server's fetch(path, query, body, method) as `access` may: the lookup, the scope and the switch checked on
    every change, and the page's own reply (or refusal, as ToolError). Without a method it's GET with no body and POST
    with one. Two pages aren't Runway's: fetch("access", {"scope": ...}) answers whether this connection may make that
    scope's changes right now (churning:write when no scope is given), and fetch("endpoints", {}) lists what "write"
    reaches (endpoints())."""
    method = (method or ("GET" if body is None else "POST")).upper()
    if method not in METHODS:
        raise ToolError("Not found")
    if body is not None and not isinstance(body, dict):
        raise ToolError("Send the body as a JSON object.")
    full = "/api/" + path
    if method == "GET" and full == "/api/access":
        why = _why_not(access, str(params.get("scope") or "churning:write"))
        return {"writes": why is None, **({"why": why} if why else {})}
    if method == "GET" and full == "/api/endpoints":
        why = _why_not(access, mcp_access.ANYTHING)
        if why:
            raise ToolError(why)
        return endpoints()
    found = routes.match(method, full)
    if found is None:
        raise ToolError("Not found")
    pattern, args = found.route.pattern, found.params
    if mcp_access.blocked(pattern):
        raise ToolError(OUT_OF_REACH)
    kind = kind_of(method, pattern)
    if kind is None:
        raise ToolError("Not found")
    if kind != "read":
        why = _why_not(access, kind)
        if why:
            raise ToolError(why)
    elif "read" not in access.scopes:
        raise ToolError("Not found")
    if any(f in (body or {}) for f in mcp_access.REFUSED_FIELDS.get((method, pattern), ())):
        raise ToolError(mcp_access.FIELD_REFUSED)
    if pattern == "/api/transactions/{id}/category":
        with db.session() as conn:
            if mcp_access.is_split(conn, args[0]):
                raise ToolError(mcp_access.SPLIT_REFUSED)
    query = {k: [str(v)] for k, v in params.items() if v not in (None, "")}
    _current.user = None   # an assistant: no signed-in person
    try:
        result = routes.dispatch(found, query, body or {})
    except ApiError as e:   # what was wrong, busy, or a server error's reference: as the web app is told
        raise ToolError(str(e)) from None
    if isinstance(result, Response):   # a download or a stream is the web app's (BLOCKED keeps them out anyway)
        raise ToolError("Not found")
    return result
