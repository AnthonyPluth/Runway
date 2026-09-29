"""The Budget page: each category's budget, what it has spent this month, and what rolls over."""
from __future__ import annotations

import calendar
from datetime import date

from dateutil.relativedelta import relativedelta

from ... import categories, db, forecast, splits
from ..common import ApiError, _month_range


def _month_totals(conn, start: date, end: date) -> dict:
    """Net amount per category for the month, across checking, savings and cards (not loans or investments)."""
    rows_ = conn.execute(
        f"SELECT t.category AS category, SUM(t.amount) AS total FROM {splits.PARTS} t JOIN accounts a ON a.id=t.account_id "
        "WHERE t.posted>=? AND t.posted<? AND a.hidden=0 AND a.kind IN ('checking','savings','credit') GROUP BY t.category",
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    return {r["category"]: r["total"] or 0.0 for r in rows_}


def _family_spent(cats: list[dict], totals: dict) -> dict[str, float]:
    """What each spending category spent in a month, counting its subcategories."""
    out = {c["name"]: 0.0 for c in cats}
    for c in cats:   # each category's own spending counts toward it and everything above it
        for name in c["path"]:
            if name in out:
                out[name] -= totals.get(c["name"], 0.0)
    return {k: round(v, 2) for k, v in out.items()}


def budget_carry(conn, cats: list[dict], budget_rows: dict, month: date) -> dict[str, float]:
    """For each budget that rolls over: what's carried into `month`, the unspent part of every month since it started
    rolling over (overspending isn't carried; a month that goes over just uses up what was carried)."""
    starts = {}
    for name, r in budget_rows.items():
        try:
            if r.get("rollover_from"):
                starts[name] = date.fromisoformat(r["rollover_from"] + "-01")
        except ValueError:
            continue
    carry = {name: 0.0 for name in starts}
    if not starts:
        return carry
    m = min(starts.values())
    while m < month:
        nxt = m + relativedelta(months=1)
        spent = _family_spent(cats, _month_totals(conn, m, nxt))
        for name, start in starts.items():
            if start <= m:
                carry[name] = max(0.0, round(budget_rows[name]["amount"] + carry[name] - spent.get(name, 0.0), 2))
        m = nxt
    return carry


def api_budget(conn, q, _b):
    today = date.today()
    start, end = _month_range(q)
    days = calendar.monthrange(start.year, start.month)[1]
    cats = [c for c in categories.all_categories(conn) if not c["is_transfer"] and not c["is_income"]]
    income_cats = [c["name"] for c in categories.all_categories(conn) if c["is_income"] and c["top"] != "Refunds"]
    totals = _month_totals(conn, start, end)
    budget_rows = {r["category"]: r for r in db.rows(conn.execute("SELECT * FROM budgets"))}
    budgets = {k: r["amount"] for k, r in budget_rows.items()}
    usual = {p["category"]: p["usual"] for p in forecast.budget_plan(conn, today)}
    own = {c["name"]: round(-totals.get(c["name"], 0.0), 2) for c in cats}
    carry = budget_carry(conn, cats, budget_rows, start)
    out = []
    for c in cats:
        below = [k["name"] for k in cats if c["name"] in k["path"][:-1]]
        spent = round(own[c["name"]] + sum(own[k] for k in below), 2)
        b = budgets.get(c["name"])
        row = budget_rows.get(c["name"]) or {}
        carried = carry.get(c["name"], 0.0)
        out.append({"name": c["name"], "parent": c["parent"], "path": c["path"], "depth": c["depth"], "top": c["top"],
                    "has_children": bool(below), "budget": b,
                    "pay_with": row.get("pay_with"),
                    "rollover_from": row.get("rollover_from"),
                    "carried": carried,   # from earlier months, when the budget rolls over
                    "available": round(b + carried, 2) if b is not None else None,
                    "usual_account": usual.get(c["name"]),
                    "spent": spent, "own_spent": own[c["name"]], "left": round(b + carried - spent, 2) if b is not None else None})
    current = start <= today < end
    return {
        "month": f"{start:%Y-%m}",
        "days_in_month": days,
        "day": today.day if current else (days if end <= today else 0),
        "categories": out,  # tree order: each category followed by its subcategories
        "income": round(sum(totals.get(c, 0.0) for c in income_cats), 2),
        "uncategorized": round(-totals.get(None, 0.0), 2),
        # accounts a category can be paid with: cards and cash accounts
        "pay_accounts": [{"id": r["id"], "name": r["name"], "kind": r["kind"]} for r in conn.execute(
            "SELECT id, COALESCE(display_name, name) AS name, kind FROM accounts WHERE hidden=0 "
            "AND kind IN ('credit','checking','savings') ORDER BY kind='credit' DESC, COALESCE(display_name, name)")],
    }


def api_budget_set(conn, _q, body):
    cat = body.get("category") or ""
    if not conn.execute("SELECT 1 FROM categories WHERE name=? AND is_transfer=0 AND is_income=0", (cat,)).fetchone():
        raise ApiError("Pick a spending category")
    if "rollover" in body and "amount" not in body:   # rolling over from this month on, or not
        start = f"{date.today():%Y-%m}" if body.get("rollover") else None
        if not conn.execute("UPDATE budgets SET rollover_from=? WHERE category=?", (start, cat)).rowcount:
            raise ApiError("Set a budget for this category first")
        return {"ok": True}
    if "pay_with" in body and "amount" not in body:   # just choosing the card
        acct = body.get("pay_with") or None
        if acct and not conn.execute("SELECT 1 FROM accounts WHERE id=?", (acct,)).fetchone():
            raise ApiError("Account not found")
        conn.execute("UPDATE budgets SET pay_with=? WHERE category=?", (acct, cat))
        return {"ok": True}
    amt = body.get("amount")
    if amt in (None, "", 0, "0"):
        conn.execute("DELETE FROM budgets WHERE category=?", (cat,))
        return {"ok": True}
    try:
        amt = abs(db.number(amt))
    except (TypeError, ValueError):
        raise ApiError("Enter an amount") from None
    conn.execute("INSERT INTO budgets(category, amount) VALUES (?,?) ON CONFLICT(category) DO UPDATE SET amount=excluded.amount", (cat, amt))
    return {"ok": True}
