"""Runway's MCP server: the tools that let an AI assistant (Claude and the like) read your Runway, and, only if you
switch it on in Runway and allowed it when connecting, make a short list of churning changes or pick categories.

Runway serves it at POST /mcp (runway/server/handler.py), to an assistant connected with OAuth (runway/mcp_oauth.py).
handle() answers one JSON-RPC message; every page it reads or change it makes goes through the `fetch` it's given
(mcp_http.fetch_for), which allows only the pages in mcp_access.READABLE and the changes in mcp_access.CHANGES, and
checks the connection's scope and that change's switch ("Let assistants change churning", "Let assistants
categorize") on every change, so turning a switch off takes effect at once. Nothing is ever deleted.
"""
from __future__ import annotations

import json
import urllib.parse
from collections.abc import Callable
from typing import Any

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
MAX_TEXT = 200_000   # characters in one reply: enough for a big month, not enough to flood the assistant's context

Fetch = Callable[..., Any]   # fetch(path, query) reads; fetch(path, query, body) makes a change


class ToolError(Exception):
    """Something to tell the assistant instead of a result (a bad argument, or Runway refusing)."""


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
BENEFIT_FIELDS = ("id", "name", "kind", "period", "amount", "guests", "used", "remaining", "period_end", "days_left", "expiring",
                  "used_count", "value_per_year", "counts")


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


def _perk(b: dict) -> bool:
    """Access and status: on all year, nothing to use up (as the Benefits tab groups them)."""
    return b.get("kind") in ("access", "status")


def _can_use(b: dict) -> bool:
    if b.get("kind") == "credit" and b.get("amount") is not None:
        return (b.get("remaining") or 0) > 0.005
    return not b.get("used_count")


def churning_benefits(fetch: Fetch, a: dict) -> Any:
    """Every open card's active benefits, sorted the way the Benefits tab does: expiring soon, still to use, used, and
    perks (lounges, status) that are simply on."""
    d = _churning(fetch)
    out: dict[str, list] = {"expiring": [], "available": [], "used": [], "perks": []}
    for c in _mine(d["cards"], a.get("owner")):
        if c.get("status") != "open":
            continue
        for b in c.get("benefits", []):
            if not b.get("active"):
                continue
            row = {"card": c["product"], "owner": c["owner"], **{k: b.get(k) for k in BENEFIT_FIELDS}}
            out["expiring" if b.get("expiring") else "perks" if _perk(b) else "available" if _can_use(b) else "used"].append(row)
    out["expiring"].sort(key=lambda r: r["days_left"] if r["days_left"] is not None else 1e9)
    show = a.get("show") or "all"
    return {"today": d["today"], **(out if show == "all" else {show: out[show]})}


def orders(fetch: Fetch, a: dict) -> Any:
    out = fetch("retail", {})
    recent = [o for o in out.get("recent", []) if not a.get("retailer") or o.get("retailer") == a["retailer"]]
    return {"orders": recent, "stores": {k: {f: v.get(f) for f in ("name", "orders", "matched", "unmatched", "last")} for k, v in (out.get("stores") or {}).items()}}


def order(fetch: Fetch, a: dict) -> Any:
    oid = str(a.get("order_id") or "")
    if not oid:
        raise ToolError("Give an order_id (from list_orders).")
    o = fetch("retail/orders/" + urllib.parse.quote(oid, safe=""), {})
    keep = ("id", "title", "quantity", "amount", "category", "category_source")
    return {**{k: v for k, v in o.items() if k not in ("items", "raw")}, "items": [{k: i.get(k) for k in keep} for i in o.get("items", [])]}


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
    {"name": "spending_breakdown", "description": "Where the money went between two days, by category (an order's item categories included, "
     "since orders are split by item), with each category's share.",
     "inputSchema": _schema({"start": _DAY, "end": {**_DAY, "description": "The day after the last day (exclusive)."}}),
     "run": _pass("reports/breakdown", "start", "end")},
    {"name": "list_orders", "description": "Recent Amazon, Target and Costco orders Runway has read: date, total, item count and whether "
     "each is matched to a card transaction. Use get_order for one order's items and their categories.",
     "inputSchema": _schema({"retailer": {"type": "string", "enum": ["amazon", "target", "costco"]}}), "run": orders},
    {"name": "get_order", "description": "One order: each item with its price and category (and whether you picked it, the AI did, or "
     "it takes the transaction's), and the card charges it was paid with.",
     "inputSchema": _schema({"order_id": {"type": "string", "description": "An order's id from list_orders."}}, ["order_id"]), "run": order},
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
     "period, which are used, and the perks that are simply on (lounge networks with how many guests come in free, status).",
     "inputSchema": _schema({"owner": _OWNER, "show": {"type": "string", "enum": ["all", "expiring", "available", "used", "perks"], "description": "Which group (default all)."}}),
     "run": churning_benefits},
    {"name": "churning_five24", "description": "Each person's 5/24 count and when it next drops.", "inputSchema": _schema({"owner": _OWNER}), "run": churning_five24},
    {"name": "churning_best_card", "description": "Which card to use for a purchase, by earning rate.",
     "inputSchema": _schema({"amount": {"type": "number", "description": "The purchase, in dollars."}, "category": {"type": "string"}, "owner": _OWNER},
                            ["amount"]),
     "run": _pass("churning/best", "amount", "category", "owner")},
]

# ------------------------------------------------------------------------------------------------ changes (opt-in)

CHURNING, CATEGORIZE = "churning:write", "categorize:write"   # the scopes that allow changes (mcp_access.CHANGES)
_SWITCHES = {CHURNING: "Let assistants change churning", CATEGORIZE: "Let assistants categorize"}


def writes_allowed(fetch: Fetch, scope: str = CHURNING) -> bool:
    """Whether this connection may make `scope`'s changes right now (its scope, and the switch in Settings). Unsure: no."""
    try:
        return bool(fetch("access", {"scope": scope}).get("writes"))
    except (ToolError, AttributeError):
        return False


def _refusal(fetch: Fetch, scope: str = CHURNING) -> str:
    """Why a change can't be made: the switch is off, or this connection wasn't allowed it when it connected."""
    try:
        why = fetch("access", {"scope": scope}).get("why")
    except (ToolError, AttributeError):
        why = None
    return why or f"Changes are switched off. Turn on \"{_SWITCHES[scope]}\" in Runway under Settings → Advanced."


def _change(template: str, id_arg: str | None = None, fields: bool = False, extra: tuple[str, ...] = ()) -> Callable[[Fetch, dict], Any]:
    """A tool that makes one churning change: POSTs to `template` (its {id} from `id_arg`) the `fields` object and any of
    the `extra` arguments, as the web app's forms send them."""
    def run(fetch: Fetch, a: dict) -> Any:
        path = template.format(id=int(a[id_arg])) if id_arg else template
        body = dict(a.get("fields") or {}) if fields else {}
        body.update({k: a[k] for k in extra if a.get(k) is not None})
        return fetch(path, {}, body)
    return run


_ID = {"type": "integer", "minimum": 1}
_FIELDS = {"type": "object", "description": "The fields to set, named as in Runway's form for it (see churning_cards for a card's)."}
_CARD_ID = {**_ID, "description": "A card's id (from churning_cards)."}
_BENEFIT_ID = {**_ID, "description": "A benefit's id (from churning_benefits)."}
_TASK_ID = {**_ID, "description": "A to-do's id."}
_WISH_ID = {**_ID, "description": "A planned item's id."}


def _write_tool(name: str, description: str, run: Callable[[Fetch, dict], Any], props: dict, required: list[str], idempotent: bool = False,
                scope: str = CHURNING) -> dict:
    return {"name": name, "description": description, "inputSchema": _schema(props, required), "run": run, "write": scope,
            "idempotent": idempotent}


WRITE_TOOLS: list[dict[str, Any]] = [
    _write_tool("mark_benefit_used", "Mark a card benefit used this period: `amount` dollars of a credit (the rest of it if left out), or just used.",
                _change("churning/benefits/{id}/use", "benefit_id", extra=("amount", "used_on")),
                {"benefit_id": _BENEFIT_ID, "amount": {"type": "number", "minimum": 0}, "used_on": _DAY}, ["benefit_id"]),
    _write_tool("undo_benefit_use", "Undo a benefit's last use this period (or a particular one with use_id).",
                _change("churning/benefits/{id}/unuse", "benefit_id", extra=("use_id",)),
                {"benefit_id": _BENEFIT_ID, "use_id": _ID}, ["benefit_id"]),
    _write_tool("add_benefit", "Add a benefit to a card (name, kind, amount, period...).", _change("churning/cards/{id}/benefits", "card_id", True),
                {"card_id": _CARD_ID, "fields": _FIELDS}, ["card_id", "fields"]),
    _write_tool("update_benefit", "Change fields of a benefit.", _change("churning/benefits/{id}", "benefit_id", True),
                {"benefit_id": _BENEFIT_ID, "fields": _FIELDS}, ["benefit_id", "fields"], True),
    _write_tool("add_card", "Add a credit card to churning (owner, issuer, product, opened_on...).", _change("churning/cards", None, True),
                {"fields": _FIELDS}, ["fields"]),
    _write_tool("update_card", "Change fields of a card (its plan, notes, annual fee, bonus, closed date...). Never removes it.",
                _change("churning/cards/{id}", "card_id", True), {"card_id": _CARD_ID, "fields": _FIELDS}, ["card_id", "fields"], True),
    _write_tool("complete_card_plan", "Check off a card's plan (close it, or product change it) as done, on a day (today unless given).",
                _change("churning/cards/{id}/plan/done", "card_id", extra=("on",)), {"card_id": _CARD_ID, "on": _DAY}, ["card_id"]),
    _write_tool("undo_card_plan", "Undo checking off a card's plan.", _change("churning/cards/{id}/plan/undo", "card_id"),
                {"card_id": _CARD_ID}, ["card_id"], True),
    _write_tool("add_task", "Add a to-do for a card (card_id, due_on, action).", _change("churning/tasks", None, True), {"fields": _FIELDS}, ["fields"]),
    _write_tool("update_task", "Change fields of a to-do (mark it done, move its date...).", _change("churning/tasks/{id}", "task_id", True),
                {"task_id": _TASK_ID, "fields": _FIELDS}, ["task_id", "fields"], True),
    _write_tool("snooze_task", "Leave a to-do out of Upcoming until a day, or for a number of days.", _change("churning/tasks/{id}/snooze", "task_id", extra=("until", "days")),
                {"task_id": _TASK_ID, "until": _DAY, "days": {"type": "integer", "minimum": 1, "maximum": 365}}, ["task_id"], True),
    _write_tool("add_planned_item", "Plan a card or bank bonus to apply for (kind, owner, issuer, product, bonus, apply_url...).",
                _change("churning/wishlist", None, True), {"fields": _FIELDS}, ["fields"]),
    _write_tool("update_planned_item", "Change fields of a planned item (its priority, notes, status wanted or dropped, apply_url...).",
                _change("churning/wishlist/{id}", "wish_id", True), {"wish_id": _WISH_ID, "fields": _FIELDS}, ["wish_id", "fields"], True),
]


def _categorize(template: str, id_arg: str, text_id: bool = False) -> Callable[[Fetch, dict], Any]:
    """A tool that picks a category: POSTs {category, remember} to `template`, as the web app does (remember only when
    asked: it changes other transactions or items too)."""
    def run(fetch: Fetch, a: dict) -> Any:
        raw = a.get(id_arg)
        if raw in (None, ""):
            raise ToolError(f"Give a {id_arg}.")
        rid = urllib.parse.quote(str(raw), safe="") if text_id else str(int(raw))
        category = a.get("category")
        if not isinstance(category, str) or not category.strip():
            raise ToolError("Give a category (a name from list_categories).")
        return fetch(template.format(id=rid), {}, {"category": category.strip(), "remember": a.get("remember") is True})
    return run


def _accept(fetch: Fetch, a: dict) -> Any:
    tid = a.get("transaction_id")
    if tid in (None, ""):
        raise ToolError("Give a transaction_id.")
    return fetch("transactions/" + urllib.parse.quote(str(tid), safe="") + "/accept", {}, {})


_TX_ID = {"type": "string", "description": "A transaction's id (from list_transactions)."}
_CATEGORY = {"type": "string", "description": "An existing category's name, exactly as list_categories gives it."}

CATEGORIZE_TOOLS: list[dict[str, Any]] = [
    _write_tool("set_transaction_category", "Set a transaction's category (it's then marked reviewed). With remember, also use "
                "it for this merchant from now on, and for its other transactions still to review. A split transaction is "
                "refused: it's changed in Runway itself. The reply's `was` is what it had before (for telling the person, "
                "not a full undo).",
                _categorize("transactions/{id}/category", "transaction_id", text_id=True),
                {"transaction_id": _TX_ID, "category": _CATEGORY,
                 "remember": {"type": "boolean", "description": "Make a rule for this merchant (default false)."}},
                ["transaction_id", "category"], True, CATEGORIZE),
    _write_tool("accept_transaction_category", "Accept the category Runway gave a transaction that needs review, as it is.",
                _accept, {"transaction_id": _TX_ID}, ["transaction_id"], True, CATEGORIZE),
    _write_tool("set_order_item_category", "Set an order item's category (the transactions its order paid for are split again "
                "by item). With remember, also for the same item in other orders.",
                _categorize("retail/items/{id}", "item_id"),
                {"item_id": {**_ID, "description": "An item's id (from get_order)."}, "category": _CATEGORY,
                 "remember": {"type": "boolean", "description": "Use it for this item in other orders too (default false)."}},
                ["item_id", "category"], True, CATEGORIZE),
]
ALL_TOOLS = TOOLS + WRITE_TOOLS + CATEGORIZE_TOOLS
BY_NAME = {t["name"]: t for t in ALL_TOOLS}


def offered(fetch: Fetch) -> list[dict[str, Any]]:
    """The tools this server offers: the reading ones, plus each scope's changing ones only while this connection may
    use them."""
    return (TOOLS + (WRITE_TOOLS if writes_allowed(fetch, CHURNING) else [])
            + (CATEGORIZE_TOOLS if writes_allowed(fetch, CATEGORIZE) else []))


def _annotations(t: dict) -> dict:
    if not t.get("write"):
        return {"readOnlyHint": True, "openWorldHint": False}
    # A change, but never a delete: the assistant asks you first
    return {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": bool(t.get("idempotent")), "openWorldHint": False}


def call_tool(name: str, args: dict, fetch: Fetch) -> str:
    tool = BY_NAME.get(name)
    if not tool:
        raise ToolError(f"Unknown tool {name}.")
    if tool.get("write") and not writes_allowed(fetch, tool["write"]):
        raise ToolError(_refusal(fetch, tool["write"]))
    text = json.dumps(tool["run"](fetch, args or {}), ensure_ascii=False, separators=(",", ":"))
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT] + f'… (cut at {MAX_TEXT:,} characters: narrow it with a month, account or category)'
    return text


# ------------------------------------------------------------------------------------------------ the protocol

def handle(msg: Any, fetch: Fetch) -> dict | None:
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
        can = [what for scope, what in ((CHURNING, "the churning tools that add or change things (never delete)"),
                                        (CATEGORIZE, "the tools that set a transaction's or order item's category"))
               if writes_allowed(fetch, scope)]
        return ok({"protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                   "capabilities": {"tools": {"listChanged": False}}, "serverInfo": {"name": "runway", "version": "1"},
                   "instructions": "Access to a Runway personal finance app: accounts, transactions, budget, reports, net worth and "
                                   "credit-card churning (cards, benefits, upcoming fees). Amounts are in dollars. Everything is read-only"
                                   + (f" except {' and '.join(can)}, which need the person's go-ahead." if can else ".")})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": [{"name": t["name"], "description": t["description"], "inputSchema": t["inputSchema"],
                              "annotations": _annotations(t)} for t in offered(fetch)]})
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

