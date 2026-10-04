"""Runway's MCP server: the tools that let an AI assistant (Claude and the like) read your Runway, and, only if you
switch it on in Runway and allowed it when connecting, make a short list of churning changes, pick categories, or
change anything the web app can (outside bank connections, API keys, notifications and these settings).

Runway serves it at POST /mcp (runway/server/handler.py), to an assistant connected with OAuth (runway/mcp_oauth.py).
handle() answers one JSON-RPC message; every page it reads or change it makes goes through the `fetch` it's given
(mcp_http.fetch_for), which allows only the pages and changes mcp_access opens, and checks the connection's scope and
that change's switch ("Let assistants change churning", "Let assistants categorize", "Let assistants change anything")
on every change, so turning a switch off takes effect at once. Every changing tool tells the assistant to ask first.
"""
from __future__ import annotations

import json
import urllib.parse
from collections.abc import Callable
from typing import Any

from . import mcp_access

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
MAX_TEXT = 200_000   # characters in one reply: enough for a big month, not enough to flood the assistant's context

Fetch = Callable[..., Any]   # fetch(path, query) reads; fetch(path, query, body) POSTs a change; fetch(path, query, body, "DELETE") deletes


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
    {"name": "get_budget", "description": "A month's budget: what's budgeted and spent in each category, and the income expected and received (income_rows).", "inputSchema": _schema({"month": _MONTH}),
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

CHURNING, CATEGORIZE, WRITE = "churning:write", "categorize:write", "write"   # the scopes that allow changes (mcp_access.SWITCHES)
_SWITCHES = {CHURNING: "Let assistants change churning", CATEGORIZE: "Let assistants categorize", WRITE: "Let assistants change anything"}
# What an assistant allowed "write" is told when it connects: it reads these, the person doesn't.
ANYTHING_RULES = (
    "You can change the person's data: the changing tools, and call_endpoint (list_endpoints) for anything else. Rules: "
    "1) Before every change, say in plain words what you'll change and wait for the person's explicit yes. "
    "2) A destructive change (deleting or removing anything, or changing many records at once; such tools are marked "
    "destructive) also needs the person to confirm what will be lost. Ask for each change on its own, never under one "
    "blanket yes for several. "
    "3) To enter a statement the person shares, use add_transactions: confirm the account, show the parsed rows (date, "
    "payee, signed amount), get a yes, send them in one call, then report what was added and skipped. "
    "Bank connections, API keys, notifications and these assistant settings are out of reach.")
# How every changing tool's description ends: the assistant asks first, and for a destructive one, says what goes.
ASK = "Ask the person before calling this."
DESTRUCTIVE = "Destructive: tell the person exactly what will be removed and get a clear yes first."


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
    return why or f"Changes are switched off. Turn on \"{_SWITCHES[scope]}\" in Runway under Settings → Data."


def _id_of(a: dict, name: str, text: bool) -> str:
    raw = a.get(name)
    if raw in (None, "") or isinstance(raw, bool):
        raise ToolError(f"Give a {name}.")
    return urllib.parse.quote(str(raw), safe="") if text else str(int(raw))


def _change(template: str, id_arg: str | tuple[str, ...] | None = None, fields: bool = False, extra: tuple[str, ...] = (),
            method: str = "POST", text_id: bool = False) -> Callable[[Fetch, dict], Any]:
    """A tool that makes one change: POSTs (or DELETEs) to `template` (each {id} in turn from `id_arg`, a number, or text
    with `text_id`) the `fields` object and any of the `extra` arguments, as the web app's forms send them."""
    names = (id_arg,) if isinstance(id_arg, str) else id_arg or ()

    def run(fetch: Fetch, a: dict) -> Any:
        path = template
        for name in names:
            path = path.replace("{id}", _id_of(a, name, text_id), 1)
        body = dict(a.get("fields") or {}) if fields else {}
        body.update({k: a[k] for k in extra if a.get(k) is not None})
        return fetch(path, {}, body) if method == "POST" else fetch(path, {}, body, method)
    return run


_ID = {"type": "integer", "minimum": 1}
_FIELDS = {"type": "object", "description": "The fields to set, named as in Runway's form for it (see churning_cards for a card's)."}
_CARD_ID = {**_ID, "description": "A card's id (from churning_cards)."}
_BENEFIT_ID = {**_ID, "description": "A benefit's id (from churning_benefits)."}
_TASK_ID = {**_ID, "description": "A to-do's id."}
_WISH_ID = {**_ID, "description": "A planned item's id."}


def _write_tool(name: str, description: str, run: Callable[[Fetch, dict], Any], props: dict, required: list[str], idempotent: bool = False,
                scope: str = CHURNING, destructive: bool = False) -> dict:
    """A tool that changes something: offered, and run, only while this connection may make `scope`'s changes."""
    return {"name": name, "description": f"{description} {ASK}" + (f" {DESTRUCTIVE}" if destructive else ""),
            "inputSchema": _schema(props, required), "run": run, "write": scope, "idempotent": idempotent, "destructive": destructive}


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
                "it for this merchant from now on, and for its other transactions you didn't categorize yourself. If it paid for an order, every item of the order gets the category too. A split transaction is "
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

# ------------------------------------------------------------------------------------------------ any change ("write")

def _endpoint(name: str, description: str, method: str, template: str, props: dict, required: list[str], *,
              ids: str | tuple[str, ...] | None = None, fields: bool = False, extra: tuple[str, ...] = (), text_id: bool = False,
              idempotent: bool = False, destructive: bool | None = None, run: Callable[[Fetch, dict], Any] | None = None,
              reads: bool = False, route: bool = True) -> dict:
    """A "write" tool for one route of Runway (`method` /api/`template`; `route` False for one that isn't one route);
    destructive as mcp_access.destructive says unless told. One that `reads` changes nothing (and doesn't ask)."""
    where = (method, "/api/" + template)
    harm = False if reads else mcp_access.destructive(*where) if destructive is None else destructive
    tool = _write_tool(name, description, run or _change(template, ids, fields, extra, method, text_id), props, required,
                       idempotent or reads, WRITE, harm)
    if reads:
        tool.update(description=description, reads=True)
    return {**tool, "route": where if route else None}


def _fields(description: str) -> dict:
    return {"type": "object", "description": description}


def _text(description: str) -> dict:
    return {"type": "string", "description": description}


def _num(description: str) -> dict:
    return {"type": "number", "description": description}


def _budget_off(fetch: Fetch, a: dict) -> Any:
    return fetch("budget", {}, {"category": a.get("category") or "", "amount": 0})


def _list_rules(fetch: Fetch, _a: dict) -> Any:
    return fetch("rules", {})


def _call_endpoint(fetch: Fetch, a: dict) -> Any:
    method = str(a.get("method") or "").upper()
    path = str(a.get("path") or "").strip().lstrip("/")
    path = path[4:] if path.startswith("api/") else path
    if method not in ("GET", "POST", "DELETE") or not path:
        raise ToolError("Give a method (GET, POST or DELETE) and a path from list_endpoints, like /api/rules/12.")
    query = a.get("query") or {}
    if not isinstance(query, dict):
        raise ToolError("Send the query as an object.")
    if method == "GET":
        return fetch(path, query)
    body = a.get("body") or {}
    return fetch(path, query, body) if method == "POST" else fetch(path, query, body, "DELETE")


_ACCOUNT = _text("An account's id (from list_accounts).")
_RULE_ID = {**_ID, "description": "A rule's id (from list_rules)."}
_REC_ID = {**_ID, "description": "A recurring item's id (from list_recurring)."}
_ASSET_ID = {**_ID, "description": "An asset's id (from get_net_worth's assets_list)."}
_COMPANY_ID = _text("An equity company's id (from get_equity).")
_GRANT_ID = _text("A grant's id (from get_equity).")
_CHARGE_ID = _text("An order charge's id (from get_order's charges).")
_ORDER_ID = _text("An order's id (from list_orders).")
_NEW_TX = ("posted (YYYY-MM-DD), amount (signed: money in positive, spending negative), payee (the name), and optionally "
           "category (an existing one) and notes")
_RULE_FIELDS = _fields("The rule: match (text in the payee or description), match_mode (contains, starts or exact), account_id, "
                       "direction (in or out), amount_min, amount_max, and what it does: category, rename (a payee name), "
                       "review (true: leave for review), split ([{category, percent}]).")
_RECURRING_FIELDS = ("name, account_id, amount (signed: income positive), frequency (weekly, biweekly, semimonthly, monthly, "
                     "quarterly, semiannual, yearly, dates or once), anchor_date (YYYY-MM-DD), and optionally end_date, match "
                     "(texts in the transactions' payee), amount_mode (fixed, last or avg3), amount_min, amount_max, dates "
                     "(for dates/semimonthly: 04-15, 10-15 or 1, 15) and active (1 or 0)")
_ACCOUNT_FIELDS = _fields("Any of: display_name, kind (checking, savings, credit, loan, investment), owner, hidden, in_forecast, "
                          "networth_hidden, owed_positive (1 or 0), pay_from (an account id), for a card pay_mode, pay_amount and "
                          "apr, for a loan interest_rate and monthly_payment. Empty clears one.")
_ASSET_FIELDS = _fields("name, kind (home, vehicle or other), value, yearly_change (% a year), loan_account_id (its loan), "
                        "auto_update (1: Realie values a home), address, url, notes.")
_COMPANY_FIELDS = _fields("name, share_price, price_as_of (YYYY-MM-DD), in_networth (1 or 0).")
_GRANT_FIELDS = _fields("label, kind (iso, nso, rsu, rsa or shares), quantity, strike, granted_on, vest_start, expires_on (YYYY-MM-DD), exercised.")
_BANK_FIELDS = _fields("owner, bank, account_type (checking, savings or business), opened_on, bonus, and the requirements: dd_total, "
                       "dd_count, debit_count, min_balance, monthly_fee, deadline_days, keep_open_days, early_close_fee, manual_dd, "
                       "manual_debits, received_amount, repeat_months.")
_UNDO = {"type": "object", "description": "The `was` an earlier reply sent back."}

ANY_TOOLS: list[dict[str, Any]] = [
    # transactions
    _endpoint("add_transactions", "Enter many transactions into one account at once, as from a bank statement the person "
              "pasted or uploaded. First confirm the account (list_accounts) and show the person the rows you read, each with "
              "its date, payee and signed amount (money in positive), and get a yes; then send them all in one call (up to 500). "
              "A row already in the account (same day, amount and payee) or repeated in the batch is skipped, and a bad row is "
              "reported without stopping the rest. Rules categorize the rest; what's left goes to Review. Tell the person how "
              "many were added and which were skipped and why.", "POST", "transactions/import",
              {"account": _ACCOUNT, "transactions": {"type": "array", "maxItems": 500, "description": "The rows: " + _NEW_TX + ".",
                                                      "items": {"type": "object"}}},
              ["account", "transactions"], extra=("account", "transactions"), idempotent=True),
    _endpoint("add_transaction", "Add one transaction by hand (cash, a cheque the bank hasn't shown yet). fields: account (an id "
              "from list_accounts), " + _NEW_TX + ".", "POST", "transactions", {"fields": _fields("account, " + _NEW_TX + ".")},
              ["fields"], fields=True),
    _endpoint("update_transaction", "Change a transaction's payee, posted date, amount or notes (any of them). {\"category\": null} "
              "takes its category (and any split) away. A pending synced one's date and amount can't change. The reply's `was` "
              "puts it back with {\"restore\": was}.", "POST", "transactions/{id}",
              {"transaction_id": _TX_ID, "fields": _fields("Any of payee, posted, amount, notes; or category: null.")},
              ["transaction_id", "fields"], ids="transaction_id", fields=True, text_id=True, idempotent=True),
    _endpoint("delete_transaction", "Delete a transaction that was added by hand (a bank's can't be deleted).", "DELETE", "transactions/{id}",
              {"transaction_id": _TX_ID}, ["transaction_id"], ids="transaction_id", text_id=True),
    _endpoint("split_transaction", "Split a transaction across categories: splits is [{amount, category}] adding up to its amount "
              "(same sign); an empty list puts it back together.", "POST", "transactions/{id}/split",
              {"transaction_id": _TX_ID, "splits": {"type": "array", "items": {"type": "object"}}}, ["transaction_id", "splits"],
              ids="transaction_id", extra=("splits",), text_id=True, idempotent=True),
    _endpoint("set_transaction_name", "Show a transaction under the bank's text (use: bank) or the brand's name again (use: brand); "
              "with all, every transaction of that brand, and future syncs too.", "POST", "transactions/{id}/name",
              {"transaction_id": _TX_ID, "use": {"type": "string", "enum": ["bank", "brand"]}, "all": {"type": "boolean"}},
              ["transaction_id", "use"], ids="transaction_id", extra=("use", "all"), text_id=True, idempotent=True),
    _endpoint("link_transaction_recurring", "Link a transaction to a recurring item (recurring_id), or make a new recurring item "
              "from it (new: its frequency, like monthly).", "POST", "transactions/{id}/recurring",
              {"transaction_id": _TX_ID, "recurring_id": _REC_ID, "new": _text("A frequency: weekly, monthly, yearly...")},
              ["transaction_id"], ids="transaction_id", extra=("recurring_id", "new"), text_id=True),
    _endpoint("unlink_transaction_recurring", "Mark a transaction as not part of any recurring item.", "POST", "transactions/{id}/recurring",
              {"transaction_id": _TX_ID}, ["transaction_id"], ids="transaction_id", text_id=True, idempotent=True),
    _endpoint("bulk_update_transactions", "Change many transactions at once: ids (a list) or filter (list_transactions' query as an "
              "object: month, account, category, q...), and any of category, payee, reviewed (true). The reply's `was` puts them "
              "back with {\"restore\": was}.", "POST", "transactions/bulk",
              {"fields": _fields("ids or filter, and category, payee, reviewed; or restore.")}, ["fields"], fields=True),
    # budget
    _endpoint("set_budget", "Set a spending category's monthly budget.", "POST", "budget",
              {"category": _CATEGORY, "amount": {"type": "number", "exclusiveMinimum": 0}}, ["category", "amount"],
              extra=("category", "amount"), idempotent=True),
    _endpoint("remove_budget", "Remove a spending category's budget.", "POST", "budget", {"category": _CATEGORY}, ["category"],
              run=_budget_off, idempotent=True, destructive=True),
    _endpoint("set_budget_rollover", "Turn rolling over a category's unspent budget into the next month on (from this month) or off.",
              "POST", "budget", {"category": _CATEGORY, "rollover": {"type": "boolean"}}, ["category", "rollover"],
              extra=("category", "rollover"), idempotent=True),
    # categories
    _endpoint("add_category", "Add a category, optionally under a parent, as income or a transfer.", "POST", "categories",
              {"name": _text("The new category's name."), "parent": _CATEGORY, "is_income": {"type": "boolean"},
               "is_transfer": {"type": "boolean"}}, ["name"], extra=("name", "parent", "is_income", "is_transfer")),
    _endpoint("rename_category", "Rename a category (its transactions, budget and rules follow).", "POST", "categories/rename",
              {"name": _CATEGORY, "new_name": _text("Its new name.")}, ["name", "new_name"], extra=("name", "new_name"), idempotent=True),
    _endpoint("move_category", "Move a category under another (parent), or to the top level (no parent).", "POST", "categories/move",
              {"name": _CATEGORY, "parent": _CATEGORY}, ["name"], extra=("name", "parent"), idempotent=True),
    _endpoint("remove_category", "Remove a category (not one with subcategories): its transactions and rules move to move_to, or "
              "without one its transactions go back to Review uncategorized and its rules are deleted.", "POST", "categories/remove", {"name": _CATEGORY, "move_to": _CATEGORY}, ["name"],
              extra=("name", "move_to")),
    _endpoint("set_category_card", "Choose the card or account a category's spending is paid with in the forecast (pay_with: an "
              "account id; none: the one used most).", "POST", "categories/pay-with", {"name": _CATEGORY, "pay_with": _ACCOUNT},
              ["name"], extra=("name", "pay_with"), idempotent=True),
    # rules
    _endpoint("list_rules", "The rules that categorize and rename transactions as they arrive.", "GET", "rules", {}, [],
              run=_list_rules, reads=True),
    _endpoint("preview_rule", "What a rule would match before it's saved (nothing is changed).", "POST", "rules/preview",
              {"fields": _RULE_FIELDS}, ["fields"], fields=True, reads=True),
    _endpoint("add_rule", "Add a rule; with apply, also run it over past transactions.", "POST", "rules",
              {"fields": _RULE_FIELDS, "apply": {"type": "boolean"}}, ["fields"], fields=True, extra=("apply",)),
    _endpoint("update_rule", "Change a rule (send the whole rule, as list_rules gives it).", "POST", "rules/{id}",
              {"rule_id": _RULE_ID, "fields": _RULE_FIELDS}, ["rule_id", "fields"], ids="rule_id", fields=True, idempotent=True),
    _endpoint("delete_rule", "Delete a rule (what it already changed stays).", "DELETE", "rules/{id}", {"rule_id": _RULE_ID},
              ["rule_id"], ids="rule_id"),
    _endpoint("apply_rule", "Run a rule over every past transaction it matches (ones you categorized yourself are left alone).",
              "POST", "rules/{id}/apply", {"rule_id": _RULE_ID}, ["rule_id"], ids="rule_id"),
    # recurring
    _endpoint("add_recurring", "Add a recurring bill or income. fields: " + _RECURRING_FIELDS + ".", "POST", "recurring",
              {"fields": _fields(_RECURRING_FIELDS + ".")}, ["fields"], fields=True),
    _endpoint("update_recurring", "Change a recurring item: send all of its fields (" + _RECURRING_FIELDS + "); one left out goes "
              "back to its default.", "POST", "recurring/{id}", {"recurring_id": _REC_ID, "fields": _fields(_RECURRING_FIELDS + ".")},
              ["recurring_id", "fields"], ids="recurring_id", fields=True, idempotent=True),
    _endpoint("delete_recurring", "Delete a recurring item (its transactions stay, unlinked).", "DELETE", "recurring/{id}",
              {"recurring_id": _REC_ID}, ["recurring_id"], ids="recurring_id"),
    _endpoint("set_recurring_amount", "Make an amount (signed) a recurring item's own fixed amount from now on; with key (rec:<id>:<date>) "
              "that date's one-off amount goes. The reply's `previous` puts it back with {\"restore\": previous}.", "POST",
              "recurring/{id}/amount", {"recurring_id": _REC_ID, "amount": {"type": "number"}, "key": _text("rec:<id>:<YYYY-MM-DD>")},
              ["recurring_id", "amount"], ids="recurring_id", extra=("amount", "key"), idempotent=True),
    _endpoint("dismiss_missed_recurring", "Dismiss a missed-payment alert (key: rec:<id>:<date>, from list_recurring's missed).",
              "POST", "recurring/dismiss", {"key": _text("The alert's key.")}, ["key"], extra=("key",), idempotent=True),
    _endpoint("add_recurring_match_text", "Also link transactions with this text in their payee to a recurring item from now on.",
              "POST", "recurring/{id}/match", {"recurring_id": _REC_ID, "text": _text("The text.")}, ["recurring_id", "text"],
              ids="recurring_id", extra=("text",), idempotent=True),
    _endpoint("dismiss_recurring_suggestion", "Mark a suggested recurring item as not recurring (key from GET /api/recurring/suggestions).",
              "POST", "recurring/suggestions/dismiss", {"key": _text("The suggestion's key.")}, ["key"], extra=("key",), idempotent=True),
    _endpoint("restore_recurring_suggestion", "Bring back a dismissed recurring suggestion.", "POST", "recurring/suggestions/restore",
              {"key": _text("The suggestion's key.")}, ["key"], extra=("key",), idempotent=True),
    # forecast
    _endpoint("set_forecast_amount", "Change the amount the forecast expects for one upcoming item (key: rec:<id>:<date>, card:..., "
              "cardclose:... or stmt:..., as get_overview gives it; signed).", "POST", "overrides",
              {"key": _text("The item's key."), "amount": {"type": "number"}}, ["key", "amount"], extra=("key", "amount"), idempotent=True),
    _endpoint("clear_forecast_amount", "Drop a changed forecast amount, so the item's usual amount counts again.", "DELETE", "overrides",
              {"key": _text("The item's key.")}, ["key"], extra=("key",), idempotent=True),
    # accounts
    _endpoint("update_account", "Change an account's settings. Which bank connection it comes from is changed in Runway only.",
              "POST", "accounts/{id}", {"account_id": _ACCOUNT, "fields": _ACCOUNT_FIELDS}, ["account_id", "fields"],
              ids="account_id", fields=True, text_id=True, idempotent=True),
    _endpoint("add_statement", "Enter a card statement: statement_date (its closing date), balance, due_date and optionally "
              "minimum_payment.", "POST", "accounts/{id}/statements",
              {"account_id": _ACCOUNT, "fields": _fields("statement_date, balance, due_date, minimum_payment.")},
              ["account_id", "fields"], ids="account_id", fields=True, text_id=True, idempotent=True),
    _endpoint("remove_statement", "Remove a card statement that was entered by hand.", "POST", "accounts/{id}/statements/{id}/remove",
              {"account_id": _ACCOUNT, "statement_date": _DAY}, ["account_id", "statement_date"], ids=("account_id", "statement_date"),
              text_id=True),
    _endpoint("remove_account", "Delete an account and everything that belongs to it (its transactions, balances, statements); "
              "syncs leave it out until it's restored. GET /api/accounts/{id}/removal says what it takes.", "POST",
              "accounts/{id}/remove", {"account_id": _ACCOUNT}, ["account_id"], ids="account_id", text_id=True),
    _endpoint("restore_account", "Bring back a deleted account (from GET /api/accounts/deleted).", "POST", "accounts/{id}/restore",
              {"account_id": _ACCOUNT}, ["account_id"], ids="account_id", text_id=True),
    # net worth
    _endpoint("add_asset", "Add an asset to net worth (a home, a car...).", "POST", "assets", {"fields": _ASSET_FIELDS}, ["fields"], fields=True),
    _endpoint("update_asset", "Change an asset's fields.", "POST", "assets/{id}", {"asset_id": _ASSET_ID, "fields": _ASSET_FIELDS},
              ["asset_id", "fields"], ids="asset_id", fields=True, idempotent=True),
    _endpoint("remove_asset", "Remove an asset and its value history.", "POST", "assets/{id}/remove", {"asset_id": _ASSET_ID},
              ["asset_id"], ids="asset_id"),
    _endpoint("refresh_asset", "Look a home's value up at Realie now (uses one of the month's lookups).", "POST", "assets/{id}/refresh",
              {"asset_id": _ASSET_ID}, ["asset_id"], ids="asset_id"),
    # equity
    _endpoint("add_equity_company", "Add a company you hold equity in.", "POST", "equity/companies", {"fields": _COMPANY_FIELDS},
              ["fields"], fields=True),
    _endpoint("update_equity_company", "Change a company's fields.", "POST", "equity/companies/{id}",
              {"company_id": _COMPANY_ID, "fields": _COMPANY_FIELDS}, ["company_id", "fields"], ids="company_id", fields=True,
              text_id=True, idempotent=True),
    _endpoint("remove_equity_company", "Remove a company and all its grants.", "POST", "equity/companies/{id}/remove",
              {"company_id": _COMPANY_ID}, ["company_id"], ids="company_id", text_id=True),
    _endpoint("add_equity_grant", "Add a grant to a company.", "POST", "equity/companies/{id}/grants",
              {"company_id": _COMPANY_ID, "fields": _GRANT_FIELDS}, ["company_id", "fields"], ids="company_id", fields=True, text_id=True),
    _endpoint("update_equity_grant", "Change a grant's fields.", "POST", "equity/grants/{id}", {"grant_id": _GRANT_ID, "fields": _GRANT_FIELDS},
              ["grant_id", "fields"], ids="grant_id", fields=True, text_id=True, idempotent=True),
    _endpoint("remove_equity_grant", "Remove a grant.", "POST", "equity/grants/{id}/remove", {"grant_id": _GRANT_ID}, ["grant_id"],
              ids="grant_id", text_id=True),
    # investments
    _endpoint("save_retirement_plan", "Keep the retirement planner's inputs (plan: the planner's object; null forgets it).", "POST",
              "investments/plan", {"plan": {"type": ["object", "null"]}}, ["plan"],
              run=lambda fetch, a: fetch("investments/plan", {}, {"plan": a.get("plan")}), idempotent=True),
    _endpoint("set_tracked_holdings", "Replace what a tracked (manually entered) investment account holds: rows [{ticker or name, "
              "shares, pct (of each contribution, adding up to 100), value (for a fund without a ticker)}].", "POST", "tracked/{id}",
              {"account_id": _ACCOUNT, "rows": {"type": "array", "items": {"type": "object"}}}, ["account_id", "rows"],
              ids="account_id", extra=("rows",), text_id=True, idempotent=True),
    _endpoint("set_cost_basis", "Set what was paid for a holding in one account: per_share (or cost_basis, the total); empty clears it.",
              "POST", "investments/cost", {"fields": _fields("account_id, security_id (from get_investments), per_share or cost_basis.")},
              ["fields"], fields=True, idempotent=True),
    # churning
    _endpoint("remove_card", "Remove a churning card, with its benefits, rates and to-dos.", "POST", "churning/cards/{id}/remove",
              {"card_id": _CARD_ID}, ["card_id"], ids="card_id"),
    _endpoint("set_card_rate", "Set what a card earns in a category: multiplier (points per dollar; empty removes it), portal_only.",
              "POST", "churning/cards/{id}/rates", {"card_id": _CARD_ID, "category": _CATEGORY, "multiplier": {"type": ["number", "null"]},
                                                   "portal_only": {"type": "boolean"}},
              ["card_id", "category"], ids="card_id", extra=("category", "multiplier", "portal_only"), idempotent=True),
    _endpoint("remove_benefit", "Remove a card's benefit and its uses.", "POST", "churning/benefits/{id}/remove", {"benefit_id": _BENEFIT_ID},
              ["benefit_id"], ids="benefit_id"),
    _endpoint("remove_task", "Remove a churning to-do.", "POST", "churning/tasks/{id}/remove", {"task_id": _TASK_ID}, ["task_id"], ids="task_id"),
    _endpoint("remove_planned_item", "Remove a planned card or bank bonus.", "POST", "churning/wishlist/{id}/remove", {"wish_id": _WISH_ID},
              ["wish_id"], ids="wish_id"),
    _endpoint("mark_planned_item_applied", "Mark a planned item applied for: it becomes a card or bank bonus, opened on opened_on.",
              "POST", "churning/wishlist/{id}/applied", {"wish_id": _WISH_ID, "opened_on": _DAY}, ["wish_id"], ids="wish_id",
              extra=("opened_on",)),
    _endpoint("set_point_value", "Set what a points currency's point is worth (cents; key from churning), or add a currency of your "
              "own (no key: name, cents, kind bank, airline, hotel, cash or other).", "POST", "churning/currencies",
              {"fields": _fields("key or name, cents, kind.")}, ["fields"], fields=True, idempotent=True),
    _endpoint("remove_currency", "Remove a points currency you added.", "POST", "churning/currencies/{id}/remove",
              {"key": _text("The currency's key.")}, ["key"], ids="key", text_id=True),
    _endpoint("set_points_balance", "Set someone's points balance in a currency (as_of: the day, today unless given).", "POST",
              "churning/balances", {"owner": _OWNER, "currency": _text("The currency's key."), "points": {"type": "number"}, "as_of": _DAY},
              ["owner", "currency", "points"], extra=("owner", "currency", "points", "as_of"), idempotent=True),
    _endpoint("set_credit_score", "Record someone's credit score (source: where it's from; as_of: the day).", "POST", "churning/scores",
              {"owner": _OWNER, "score": {"type": "integer"}, "source": _text("Where it's from."), "as_of": _DAY},
              ["owner", "score"], extra=("owner", "score", "source", "as_of"), idempotent=True),
    _endpoint("add_bank_bonus", "Track a bank account bonus.", "POST", "churning/bank", {"fields": _BANK_FIELDS}, ["fields"], fields=True),
    _endpoint("update_bank_bonus", "Change fields of a bank bonus.", "POST", "churning/bank/{id}",
              {"bonus_id": {**_ID, "description": "A bank bonus's id (from churning)."}, "fields": _BANK_FIELDS},
              ["bonus_id", "fields"], ids="bonus_id", fields=True, idempotent=True),
    _endpoint("remove_bank_bonus", "Remove a bank bonus.", "POST", "churning/bank/{id}/remove",
              {"bonus_id": {**_ID, "description": "A bank bonus's id."}}, ["bonus_id"], ids="bonus_id"),
    _endpoint("dismiss_found_card", "Stop offering a credit card account as a churning card to add (GET /api/churning/found).", "POST",
              "churning/found/{id}/dismiss", {"account_id": _ACCOUNT}, ["account_id"], ids="account_id", text_id=True, idempotent=True),
    _endpoint("undismiss_found_card", "Offer a dismissed credit card account again.", "POST", "churning/found/{id}/undismiss",
              {"account_id": _ACCOUNT}, ["account_id"], ids="account_id", text_id=True, idempotent=True),
    _endpoint("suggest_card_details", "Ask the person's AI model what it knows of a card from its bank (issuer) and name (product) "
              "only; nothing is saved.", "POST", "churning/suggest", {"issuer": _text("The bank, as churning names it."), "product": _text("The card.")},
              ["issuer", "product"], extra=("issuer", "product"), idempotent=True),
    # orders
    _endpoint("match_orders", "Categorize order items still waiting, then match orders to card charges and split them again.",
              "POST", "retail/match", {}, [], idempotent=True),
    _endpoint("restore_order_item", "Undo an order item's category: was, from set_order_item_category's reply.", "POST",
              "retail/items/{id}/restore", {"item_id": {**_ID, "description": "The item's id."}, "was": _UNDO}, ["item_id", "was"],
              ids="item_id", extra=("was",)),
    _endpoint("link_order_charge", "Pair an order's charge with a card transaction (tx_id).", "POST", "retail/charges/{id}/link",
              {"charge_id": _CHARGE_ID, "tx_id": _TX_ID}, ["charge_id", "tx_id"], ids="charge_id", extra=("tx_id",), text_id=True),
    _endpoint("unlink_order_charge", "Unpair an order's charge from its transaction. The reply's `was` undoes it (restore_order_charge).",
              "POST", "retail/charges/{id}/unlink", {"charge_id": _CHARGE_ID}, ["charge_id"], ids="charge_id", text_id=True),
    _endpoint("split_charge_by_items", "Split a charge's transaction by its order's items, even if it was categorized by hand.",
              "POST", "retail/charges/{id}/apply", {"charge_id": _CHARGE_ID}, ["charge_id"], ids="charge_id", text_id=True),
    _endpoint("restore_order_charge", "Undo unlinking or splitting a charge: was, from that reply.", "POST", "retail/charges/{id}/restore",
              {"charge_id": _CHARGE_ID, "was": _UNDO}, ["charge_id", "was"], ids="charge_id", extra=("was",), text_id=True),
    _endpoint("suggest_order_categories", "Ask the person's AI model for a category for each of an order's items that has none; "
              "nothing is saved.", "POST", "retail/orders/{id}/suggest", {"order_id": _ORDER_ID}, ["order_id"], ids="order_id",
              text_id=True, idempotent=True),
    # categorizing in bulk
    _endpoint("recategorize", "Send everything uncategorized or waiting for review (not ones categorized by hand) through the rules "
              "and the AI model again; their current categories are cleared first.", "POST", "recategorize", {}, []),
    _endpoint("ai_suggest_categories", "Ask the person's AI model for categories for what's waiting in Review, one per merchant "
              "(skip: merchants to leave out). Sends those merchants to the model; nothing is saved.", "POST", "ai/suggest",
              {"skip": {"type": "array", "items": {"type": "string"}}}, [], extra=("skip",), idempotent=True),
    _endpoint("ai_apply_category", "Set a category on a group of transactions (tx_ids, from ai_suggest_categories): category, or "
              "new_category (and direction in or out) to create it first; remember makes a rule for the merchant.", "POST", "ai/apply",
              {"fields": _fields("tx_ids, category or new_category and direction, remember.")}, ["fields"], fields=True),
    # anything else
    _endpoint("list_endpoints", "Every Runway endpoint call_endpoint can reach, by area, the destructive ones marked.", "GET",
              "endpoints", {}, [], run=lambda fetch, _a: fetch("endpoints", {}), reads=True, route=False),
    _endpoint("call_endpoint", "Last resort, for what no other tool does: call one of Runway's endpoints (list_endpoints) as its "
              "web app does. body is what the web app's form sends. Anything it can't reach is refused.", "POST", "",
              {"method": {"type": "string", "enum": ["GET", "POST", "DELETE"]}, "path": _text("Like /api/rules/12."),
               "body": {"type": "object"}, "query": {"type": "object"}}, ["method", "path"], run=_call_endpoint, destructive=True, route=False),
]
ALL_TOOLS = TOOLS + WRITE_TOOLS + CATEGORIZE_TOOLS + ANY_TOOLS
BY_NAME = {t["name"]: t for t in ALL_TOOLS}


def offered(fetch: Fetch) -> list[dict[str, Any]]:
    """The tools this server offers: the reading ones, plus each scope's changing ones only while this connection may
    use them ("write" brings them all)."""
    if writes_allowed(fetch, WRITE):
        return ALL_TOOLS
    return (TOOLS + (WRITE_TOOLS if writes_allowed(fetch, CHURNING) else [])
            + (CATEGORIZE_TOOLS if writes_allowed(fetch, CATEGORIZE) else []))


def _annotations(t: dict) -> dict:
    if not t.get("write") or t.get("reads"):
        return {"readOnlyHint": True, "openWorldHint": False}
    # A change: the assistant asks you first, and for a destructive one, says what goes
    return {"readOnlyHint": False, "destructiveHint": bool(t.get("destructive")), "idempotentHint": bool(t.get("idempotent")),
            "openWorldHint": False}


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
        about = ("Access to a Runway personal finance app: accounts, transactions, budget, reports, net worth and "
                 "credit-card churning (cards, benefits, upcoming fees). Amounts are in dollars.")
        if writes_allowed(fetch, WRITE):
            told = f"{about} {ANYTHING_RULES}"
        else:
            can = [what for scope, what in ((CHURNING, "the churning tools that add or change things (never delete)"),
                                            (CATEGORIZE, "the tools that set a transaction's or order item's category"))
                   if writes_allowed(fetch, scope)]
            told = f"{about} Everything is read-only" + (f" except {' and '.join(can)}, which need the person's go-ahead." if can else ".")
        return ok({"protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                   "capabilities": {"tools": {"listChanged": False}}, "serverInfo": {"name": "runway", "version": "1"},
                   "instructions": told})
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

