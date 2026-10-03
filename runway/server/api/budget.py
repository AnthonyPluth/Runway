"""The Budget page: each category's budget, what it has spent this month, and what rolls over."""
from __future__ import annotations

import calendar
from datetime import date

from sqlalchemy import delete, func, select, update

from ... import categories, db, forecast
from ...budgets import budget_carry, month_totals
from ...models import Account, Budget, Category
from ..common import ApiError, _month_range


def api_budget(conn, q, _b):
    today = date.today()
    start, end = _month_range(q)
    days = calendar.monthrange(start.year, start.month)[1]
    cats = [c for c in categories.all_categories(conn) if not c["is_transfer"] and not c["is_income"]]
    income_cats = [c["name"] for c in categories.all_categories(conn) if c["is_income"] and c["top"] != "Refunds"]
    totals = month_totals(conn, start, end)
    budget_rows = {r["category"]: r for r in db.rows(conn.execute(select(Budget)))}
    budgets = {k: r["amount"] for k, r in budget_rows.items()}
    # The account each category's spending goes on: the one chosen for it, and the one used most lately (for a budget
    # that counts, as the forecast works it out: outside its subcategories that have their own).
    pay_with = dict(conn.execute(select(Category.name, Category.pay_with)).fetchall())
    used = forecast.account_use(conn, today)
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
                    "pay_with": pay_with.get(c["name"]),
                    "rollover_from": row.get("rollover_from"),
                    "carried": carried,   # from earlier months, when the budget rolls over
                    "available": round(b + carried, 2) if b is not None else None,
                    "usual_account": usual[c["name"]] if c["name"] in usual else forecast.usual_account(used, [c["name"], *below]),
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
            select(Account.id, func.coalesce(Account.display_name, Account.name).label("name"), Account.kind)
            .where(Account.hidden == 0, Account.kind.in_(["credit", "checking", "savings"]))
            .order_by((Account.kind == "credit").desc(), func.coalesce(Account.display_name, Account.name)))],
    }


def api_budget_set(conn, _q, body):
    cat = body.get("category") or ""
    if not conn.execute(select(Category.name)
                        .where(Category.name == cat, Category.is_transfer == 0, Category.is_income == 0)).fetchone():
        raise ApiError("Pick a spending category")
    if "rollover" in body and "amount" not in body:   # rolling over from this month on, or not
        start = f"{date.today():%Y-%m}" if body.get("rollover") else None
        if not conn.execute(update(Budget).where(Budget.category == cat).values(rollover_from=start)).rowcount:
            raise ApiError("Set a budget for this category first")
        return {"ok": True}
    if "pay_with" in body and "amount" not in body:   # the category's card (kept here for a release: /api/categories/pay-with)
        try:
            categories.set_pay_with(conn, cat, body.get("pay_with") or None)
        except categories.CategoryError as e:
            raise ApiError(str(e)) from e
        return {"ok": True}
    amt = body.get("amount")
    if amt in (None, "", 0, "0"):
        conn.execute(delete(Budget).where(Budget.category == cat))
        return {"ok": True}
    try:
        amt = abs(db.number(amt))
    except (TypeError, ValueError):
        raise ApiError("Enter an amount") from None
    db.upsert(conn, Budget, {"category": cat, "amount": amt}, key=["category"])
    return {"ok": True}
