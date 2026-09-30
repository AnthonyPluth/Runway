"""Runway's MCP server: lets an AI assistant (Claude Desktop, Claude Code and the like) read your Runway, and only read.

    RUNWAY_URL=https://runway.example.com RUNWAY_MCP_KEY=rwm_... python -m runway.mcp_server

It speaks MCP over stdio (one JSON message per line) and talks to a running Runway over HTTP with the read-only key made
under Settings → Connections. It never opens the database itself, and the key only opens the pages in
mcp_access.READABLE: nothing that changes data, and no settings, bank connections or backups. Standard library only.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
TIMEOUT = 60
MAX_TEXT = 200_000   # characters in one reply: enough for a big month, not enough to flood the assistant's context

Fetch = Callable[[str, dict[str, Any]], Any]


class ToolError(Exception):
    """Something to tell the assistant instead of a result (a bad argument, Runway refusing or unreachable)."""


# ------------------------------------------------------------------------------------------------ talking to Runway

def http_fetch(path: str, params: dict[str, Any]) -> Any:
    """GET /api/mcp/<path> on the Runway named by RUNWAY_URL, with the key in RUNWAY_MCP_KEY."""
    base = (os.environ.get("RUNWAY_URL") or "http://127.0.0.1:8765").rstrip("/")
    key = os.environ.get("RUNWAY_MCP_KEY") or ""
    if not key:
        raise ToolError("RUNWAY_MCP_KEY isn't set. Make a key under Settings → Connections in Runway and set it in the "
                        "server's environment.")
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
    req = urllib.request.Request(f"{base}/api/mcp/{path}" + (f"?{query}" if query else ""),
                                 headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            said = json.load(e).get("error")
        except Exception:
            said = None
        raise ToolError(said or f"Runway answered {e.code}.") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ToolError(f"Can't reach Runway at {base}: {getattr(e, 'reason', e)}") from None


# ------------------------------------------------------------------------------------------------ tools

def _schema(props: dict[str, dict] | None = None, required: list[str] | None = None) -> dict:
    out: dict[str, Any] = {"type": "object", "properties": props or {}, "additionalProperties": False}
    if required:
        out["required"] = required
    return out


_MONTH = {"type": "string", "description": "A month, like 2026-09 (default: this month)."}
_OWNER = {"type": "string", "description": "Only this person's cards (default: everyone's)."}
_DAY = {"type": "string", "description": "A day, like 2026-09-30."}


def _pass(path: str, *names: str) -> Callable[[Fetch, dict], Any]:
    """A tool that is one page of Runway with these arguments as its query."""
    return lambda fetch, a: fetch(path, {n: a.get(n) for n in names})


def _transactions(fetch: Fetch, a: dict) -> Any:
    limit = min(max(int(a.get("limit") or 50), 1), 200)
    out = fetch("transactions", {"month": a.get("month"), "account": a.get("account"), "category": a.get("category"),
                                 "q": a.get("search"), "limit": limit, "offset": a.get("offset")})
    items = out.get("items", out) if isinstance(out, dict) else out
    keep = ("id", "posted", "amount", "payee", "description", "category", "account_name", "pending", "needs_review", "recurring_name")
    return {"transactions": [{k: t.get(k) for k in keep if t.get(k) not in (None, "")} for t in items],
            **({"total": out["total"]} if isinstance(out, dict) and "total" in out else {})}


CARD_FIELDS = ("id", "owner", "issuer", "product", "status", "opened_on", "closed_on", "annual_fee", "fee_due", "authorized_user", "business",
               "currency_name", "bonus", "bonus_spend", "bonus_deadline", "bonus_state", "spent", "plan", "plan_target", "plan_due",
               "plan_done_on", "eligibility", "benefits_value", "net_fee", "counts_524", "falls_off")
BENEFIT_FIELDS = ("name", "kind", "period", "amount", "used", "remaining", "period_end", "days_left", "expiring", "used_count",
                  "value_per_year", "counts")


def _churning(fetch: Fetch) -> dict:
    return fetch("churning", {})


def _mine(items: list[dict], owner: str | None) -> list[dict]:
    return [x for x in items if not owner or x.get("owner") == owner]


def churning_cards(fetch: Fetch, a: dict) -> Any:
    d = _churning(fetch)
    cards = _mine(d["cards"], a.get("owner"))
    if not a.get("include_closed"):
        cards = [c for c in cards if c.get("status") == "open"]
    return {"today": d["today"], "cards": [{k: c.get(k) for k in CARD_FIELDS} for c in cards]}


def churning_upcoming(fetch: Fetch, a: dict) -> Any:
    d = _churning(fetch)
    return {"today": d["today"], "upcoming": _mine(d["upcoming"], a.get("owner"))}


def churning_five24(fetch: Fetch, a: dict) -> Any:
    d = _churning(fetch)
    return {"today": d["today"], "five24": {o: v for o, v in d["five24"].items() if not a.get("owner") or o == a["owner"]}}


def _can_use(b: dict) -> bool:
    if b.get("kind") == "credit" and b.get("amount") is not None:
        return (b.get("remaining") or 0) > 0.005
    return not b.get("used_count")


def churning_benefits(fetch: Fetch, a: dict) -> Any:
    """Every open card's active benefits, sorted the way the Benefits tab does: expiring soon, still to use, used."""
    d = _churning(fetch)
    out: dict[str, list] = {"expiring": [], "available": [], "used": []}
    for c in _mine(d["cards"], a.get("owner")):
        if c.get("status") != "open":
            continue
        for b in c.get("benefits", []):
            if not b.get("active"):
                continue
            row = {"card": c["product"], "owner": c["owner"], **{k: b.get(k) for k in BENEFIT_FIELDS}}
            out["expiring" if b.get("expiring") else "available" if _can_use(b) else "used"].append(row)
    out["expiring"].sort(key=lambda r: r["days_left"] if r["days_left"] is not None else 1e9)
    show = a.get("show") or "all"
    return {"today": d["today"], **(out if show == "all" else {show: out[show]})}


TOOLS: list[dict[str, Any]] = [
    {"name": "get_overview", "description": "The cash-flow forecast: balances, upcoming bills and income, and what's left to spend.",
     "inputSchema": _schema(), "run": _pass("overview")},
    {"name": "list_accounts", "description": "Every account with its balance and kind.", "inputSchema": _schema(), "run": _pass("accounts")},
    {"name": "list_transactions", "description": "Recent transactions, newest first. Filter by month, account, category or a search "
     "of the payee or description.",
     "inputSchema": _schema({"month": _MONTH, "account": {"type": "string", "description": "An account id from list_accounts."},
                             "category": {"type": "string", "description": "A category name (includes its subcategories)."},
                             "search": {"type": "string", "description": "Text in the payee or description."},
                             "limit": {"type": "integer", "minimum": 1, "maximum": 200, "description": "How many (default 50)."},
                             "offset": {"type": "integer", "minimum": 0}}),
     "run": _transactions},
    {"name": "get_budget", "description": "A month's budget: what's budgeted and spent in each category.", "inputSchema": _schema({"month": _MONTH}),
     "run": _pass("budget", "month")},
    {"name": "list_categories", "description": "Spending and income categories.", "inputSchema": _schema(), "run": _pass("categories")},
    {"name": "get_cashflow", "description": "Where money came from and went in a month.", "inputSchema": _schema({"month": _MONTH}),
     "run": _pass("cashflow", "month")},
    {"name": "get_month_pace", "description": "How this month's spending compares to a usual month so far.", "inputSchema": _schema(), "run": _pass("month_pace")},
    {"name": "spending_report", "description": "Spending by month over time, grouped by category or otherwise.",
     "inputSchema": _schema({"end": _MONTH, "months": {"type": "integer", "minimum": 2, "maximum": 36, "description": "How many months back (default 12)."},
                             "group": {"type": "string", "description": "What to group by (default category)."}}),
     "run": _pass("reports/spending", "end", "months", "group")},
    {"name": "income_vs_spending", "description": "Income against spending by month.",
     "inputSchema": _schema({"end": _MONTH, "months": {"type": "integer", "minimum": 2, "maximum": 36}}),
     "run": _pass("reports/income", "end", "months")},
    {"name": "top_merchants", "description": "Where the money went, by merchant, over a span of days.",
     "inputSchema": _schema({"start": _DAY, "end": {**_DAY, "description": "The day after the last day (exclusive)."},
                             "category": {"type": "string"}}),
     "run": _pass("reports/merchants", "start", "end", "category")},
    {"name": "get_net_worth", "description": "Net worth now and over time, with assets and debts.", "inputSchema": _schema(), "run": _pass("networth")},
    {"name": "list_recurring", "description": "Recurring bills and income Runway tracks.", "inputSchema": _schema(), "run": _pass("recurring")},
    {"name": "get_investments", "description": "Investment accounts and holdings.",
     "inputSchema": _schema({"period": {"type": "string", "description": "1D, 1W, 1M, 3M, 6M, YTD, 1Y or ALL (default 1Y)."}}),
     "run": _pass("investments", "period")},
    {"name": "get_equity", "description": "Stock options and other equity grants.", "inputSchema": _schema(), "run": _pass("equity")},
    {"name": "churning_cards", "description": "The credit cards tracked for churning: annual fee, bonus progress, plan and eligibility.",
     "inputSchema": _schema({"owner": _OWNER, "include_closed": {"type": "boolean", "description": "Include closed and product-changed cards."}}),
     "run": churning_cards},
    {"name": "churning_upcoming", "description": "What's coming up for churning: annual fees, bonus deadlines, benefits about to reset, planned actions.",
     "inputSchema": _schema({"owner": _OWNER}), "run": churning_upcoming},
    {"name": "churning_benefits", "description": "Every open card's benefits: which are expiring soon, which are still to use this "
     "period, and which are used.",
     "inputSchema": _schema({"owner": _OWNER, "show": {"type": "string", "enum": ["all", "expiring", "available", "used"], "description": "Which group (default all)."}}),
     "run": churning_benefits},
    {"name": "churning_five24", "description": "Each person's 5/24 count and when it next drops.", "inputSchema": _schema({"owner": _OWNER}), "run": churning_five24},
    {"name": "churning_best_card", "description": "Which card to use for a purchase, by earning rate.",
     "inputSchema": _schema({"amount": {"type": "number", "description": "The purchase, in dollars."}, "category": {"type": "string"}, "owner": _OWNER},
                            ["amount"]),
     "run": _pass("churning/best", "amount", "category", "owner")},
]
BY_NAME = {t["name"]: t for t in TOOLS}


def call_tool(name: str, args: dict, fetch: Fetch) -> str:
    tool = BY_NAME.get(name)
    if not tool:
        raise ToolError(f"Unknown tool {name}.")
    text = json.dumps(tool["run"](fetch, args or {}), ensure_ascii=False, separators=(",", ":"))
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT] + f'… (cut at {MAX_TEXT:,} characters: narrow it with a month, account or category)'
    return text


# ------------------------------------------------------------------------------------------------ the protocol

def handle(msg: Any, fetch: Fetch = http_fetch) -> dict | None:
    """One JSON-RPC message in, its reply out (None for a notification)."""
    if not isinstance(msg, dict) or "method" not in msg:
        return None
    mid, method, params = msg.get("id"), msg["method"], msg.get("params") or {}

    def ok(result: dict) -> dict:
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def fail(code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}

    if "id" not in msg:   # notifications (notifications/initialized, /cancelled...) get no reply
        return None
    if method == "initialize":
        asked = params.get("protocolVersion")
        return ok({"protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                   "capabilities": {"tools": {"listChanged": False}}, "serverInfo": {"name": "runway", "version": "1"},
                   "instructions": "Read-only access to a Runway personal finance app: accounts, transactions, budget, reports, "
                                   "net worth and credit-card churning (cards, benefits, upcoming fees). Amounts are in dollars."})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": [{"name": t["name"], "description": t["description"], "inputSchema": t["inputSchema"],
                              "annotations": {"readOnlyHint": True, "openWorldHint": False}} for t in TOOLS]})
    if method == "tools/call":
        try:
            name = params.get("name")
            args = params.get("arguments") or {}
            if not isinstance(name, str) or not isinstance(args, dict):
                return fail(-32602, "tools/call needs a name and an arguments object")
            return ok({"content": [{"type": "text", "text": call_tool(name, args, fetch)}]})
        except ToolError as e:
            return ok({"content": [{"type": "text", "text": str(e)}], "isError": True})
        except (ValueError, TypeError, KeyError) as e:   # an argument of the wrong kind, or a reply that isn't Runway's
            return ok({"content": [{"type": "text", "text": f"That didn't work: {type(e).__name__}: {e}"}], "isError": True})
    return fail(-32601, f"Method not found: {method}")


def serve(stdin=None, stdout=None, fetch: Fetch = http_fetch) -> None:
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            reply = handle(json.loads(line), fetch)
        except json.JSONDecodeError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        if reply is not None:
            stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            stdout.flush()


if __name__ == "__main__":
    serve()
