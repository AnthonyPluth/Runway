"""The Budget page: each category's budget, what it has spent this month, and what rolls over; and the income expected
in a month (an income category's budget) against what has come in."""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import delete, func, select, update

from ... import categories, db, forecast, validate
from ...dates import days_in_month
from ...budgets import budget_carry, month_totals
from ...models import Account, Budget, Category
from ...money import CENT
from ..common import ApiError, _month_range, text

EXPECTED_DAYS = 366   # how far ahead a month's expected payments are worked out (the forecast's longest horizon)
# A budget is an amount of money (validate.MAX_AMOUNT), kept as typed.
_amount = validate.Validator(ApiError, drop="", missing="Enter an amount", not_number="Enter an amount",
                             too_large="The amount is too large")


def api_budget(conn, q, _b):
    today = date.today()
    start, end = _month_range(q)
    days = days_in_month(start)
    every = categories.all_categories(conn)
    cats = [c for c in every if not c["is_transfer"] and not c["is_income"]]
    # Refunds are money in but come off spending: they're not income here, nor in the total.
    icats = [c for c in every if c["is_income"] and c["top"] != "Refunds"]
    income_cats = [c["name"] for c in icats]
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
    upcoming = upcoming_events(conn, today, start, end)
    coming = subtree_sums(cats, [(e["category"], -e["amount"]) for e in upcoming["out"]])
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
                    "spent": spent, "own_spent": own[c["name"]], "left": round(b + carried - spent, 2) if b is not None else None,
                    "expected": round(coming.get(c["name"], 0.0), 2)})   # still to come this month: see expected()
    # Income: what's come in (`spent`, above zero) against what's expected (`budget`), and the paychecks still to come in
    # the forecast. No rollover and no account: the forecast's paychecks are recurring items, not budgets.
    own_in = {c["name"]: round(totals.get(c["name"], 0.0), 2) for c in icats}
    coming_in = subtree_sums(icats, [(e["category"], e["amount"]) for e in upcoming["in"]])
    income_rows = []
    for c in icats:
        below = [k["name"] for k in icats if c["name"] in k["path"][:-1]]
        received = round(own_in[c["name"]] + sum(own_in[k] for k in below), 2)
        b = budgets.get(c["name"])
        income_rows.append({"name": c["name"], "parent": c["parent"], "path": c["path"], "depth": c["depth"], "top": c["top"],
                            "has_children": bool(below), "budget": b, "pay_with": None, "rollover_from": None, "carried": 0.0,
                            "available": b, "usual_account": None, "spent": received, "own_spent": own_in[c["name"]],
                            "left": round(b - received, 2) if b is not None else None,
                            "expected": round(coming_in.get(c["name"], 0.0), 2)})
    current = start <= today < end
    return {
        "month": f"{start:%Y-%m}",
        "days_in_month": days,
        "day": today.day if current else (days if end <= today else 0),
        "categories": out,  # tree order: each category followed by its subcategories
        "income": round(sum(totals.get(c, 0.0) for c in income_cats), 2),
        "income_rows": income_rows,   # income categories, in tree order, shaped like `categories`: `spent` is what came in
        "uncategorized": round(-totals.get(None, 0.0), 2),
        # accounts a category can be paid with: cards and cash accounts
        "pay_accounts": [{"id": r["id"], "name": r["name"], "kind": r["kind"]} for r in conn.execute(
            select(Account.id, func.coalesce(Account.display_name, Account.name).label("name"), Account.kind)
            .where(Account.hidden == 0, Account.kind.in_(["credit", "checking", "savings"]))
            .order_by((Account.kind == "credit").desc(), func.coalesce(Account.display_name, Account.name)))],
    }


def upcoming_events(conn, today: date, start: date, end: date) -> dict[str, list[dict]]:
    """The recurring payments the forecast still has coming in the month from start to end, from today on: `out`, money
    out of its accounts and charges on cards; `in`, money into its accounts (cards don't receive income). The forecast
    lists only what hasn't posted, so none of it is in what's spent or received yet. A month that's over has none."""
    if end <= today or start > today + timedelta(days=EXPECTED_DAYS):
        return {"out": [], "in": []}
    fc = forecast.build(conn, today, max(1, (end - timedelta(days=1) - today).days))
    first, last = max(start, today).isoformat(), end.isoformat()
    def due(e: dict) -> bool:
        return e["kind"] == "recurring" and first <= e["date"] < last and bool(e.get("category"))
    return {"out": [e for e in fc["events"] + fc.get("charges", []) if due(e) and e["amount"] < 0],
            "in": [e for e in fc["events"] if due(e) and e["amount"] > 0]}


def subtree_sums(cats: list[dict], amounts: list[tuple[str, float]]) -> dict[str, float]:
    """Each amount added to its category and every category above it (as spending is), for the categories in `cats`."""
    path = {c["name"]: c["path"] for c in cats}
    out: dict[str, float] = {}
    for cat, amount in amounts:
        for name in path.get(cat, []):
            out[name] = out.get(name, 0.0) + amount
    return out


def api_budget_set(conn, _q, body):
    cat = text(body.get("category"), "category")
    found = conn.execute(select(Category.is_income).where(Category.name == cat, Category.is_transfer == 0)).fetchone()
    if not found:
        raise ApiError("Pick a spending or income category")
    income = bool(found[0])   # an income budget is what's expected to come in: no rollover, no card
    if "rollover" in body and "amount" not in body:   # rolling over from this month on, or not
        if income:
            raise ApiError("Income doesn’t roll over")
        start = f"{date.today():%Y-%m}" if validate.on(body.get("rollover")) else None
        if not conn.execute(update(Budget).where(Budget.category == cat).values(rollover_from=start)).rowcount:
            raise ApiError("Set a budget for this category first")
        return {"ok": True}
    if "pay_with" in body and "amount" not in body:   # the category's card (kept here for a release: /api/categories/pay-with)
        if income:
            return {"ok": True}
        try:
            categories.set_pay_with(conn, cat, text(body.get("pay_with"), "pay_with") or None)
        except categories.CategoryError as e:
            raise ApiError(str(e)) from e
        return {"ok": True}
    amt = body.get("amount")
    if amt in (None, "", 0, "0"):
        conn.execute(delete(Budget).where(Budget.category == cat))   # the parent's budget stays as it is
        return {"ok": True, "raised": []}
    amt = abs(_amount.amount(amt, "amount", cents=False, required=True))
    db.upsert(conn, Budget, {"category": cat, "amount": amt}, key=["category"])
    return {"ok": True, "raised": raise_parents(conn, cat)}


def raise_parents(conn, cat: str) -> list[dict]:
    """A parent's budget covers its subcategories', so one that's now less than its subcategories' budgets added up
    (those that have one) goes up to that, and so on up the tree. A parent is never lowered, and one without a budget
    is left without one. Returns the budgets raised, nearest first."""
    parents = dict(conn.execute(select(Category.name, Category.parent)).fetchall())
    budgets = dict(conn.execute(select(Budget.category, Budget.amount)).fetchall())
    raised = []
    for above in reversed(categories.path(parents, cat)[:-1]):
        if above not in budgets:
            continue
        total = round(sum(budgets[k] for k, p in parents.items() if p == above and k in budgets), 2)
        if total > budgets[above] + CENT:
            conn.execute(update(Budget).where(Budget.category == above).values(amount=total))
            budgets[above] = total
            raised.append({"category": above, "amount": total})
    return raised
