"""Budgets: each budget's amount in a month, what each category spent in a month, and what a budget that rolls over
carries into the next one. Shared by the Budget page and the forecast, which spends the budgets.

A budget has its usual amount (budgets.amount) and, for any month, can have an amount of its own instead
(budget_months): for that month only, nothing carried forward to the months after it. Every other month, past or still
to come, has the usual amount, and changing it changes every month without an amount of its own."""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from sqlalchemy import func, literal_column, select

from ..storage import db
from . import splits
from ..dates import month_key, month_start
from ..storage.models import Account, Budget, MonthBudget


def load(conn) -> dict[str, dict]:
    """Every budget by category: its row (`amount`, the usual amount; `rollover_from`) with `months`, the months that
    have an amount of their own ({"YYYY-MM": amount})."""
    out = {r["category"]: {**r, "months": {}} for r in db.rows(conn.execute(select(Budget)))}
    for cat, month, amount in conn.execute(select(MonthBudget.category, MonthBudget.month, MonthBudget.amount)).fetchall():
        if cat in out:
            out[cat]["months"][month] = amount
    return out


def amount_in(row: dict, month: str) -> float:
    """A budget's amount in a month ("YYYY-MM"): the month's own, else the usual amount. `row` is one of load()'s."""
    return row.get("months", {}).get(month, row["amount"])


def _totals_query(start: date, end: date, by_month: bool = False):
    """Net amount per category from start up to end, across checking, savings and cards (not loans or investments);
    `by_month`: per category in each month too ("YYYY-MM", as `month`)."""
    t = splits.parts()
    # Constants in the SQL, not parameters: Postgres matches the GROUP BY expression to the selected one.
    group = [func.substr(t.c.posted, literal_column("1"), literal_column("7")).label("month")] if by_month else []
    return (select(*group, t.c.category.label("category"), func.sum(t.c.amount).label("total"))
            .join(Account, Account.id == t.c.account_id)
            .where(t.c.posted >= start.isoformat(), t.c.posted < end.isoformat(), *db.SPENDING_ACCOUNTS)
            .group_by(*group, t.c.category))


def month_totals(conn, start: date, end: date) -> dict:
    """Net amount per category for the month, across checking, savings and cards (not loans or investments)."""
    return {r["category"]: r["total"] or 0.0 for r in conn.execute(_totals_query(start, end)).fetchall()}


def family_spent(cats: list[dict], totals: dict) -> dict[str, float]:
    """What each spending category spent in a month, counting its subcategories."""
    out = {c["name"]: 0.0 for c in cats}
    for c in cats:   # each category's own spending counts toward it and everything above it
        for name in c["path"]:
            if name in out:
                out[name] -= totals.get(c["name"], 0.0)
    return {k: round(v, 2) for k, v in out.items()}


def budget_carry(conn, cats: list[dict], budget_rows: dict, month: date) -> dict[str, float]:
    """For each budget that rolls over: what's carried into `month`, the unspent part of every month since it started
    rolling over, at each month's amount (amount_in; overspending isn't carried; a month that goes over just uses up what
    was carried). `budget_rows` are load()'s."""
    starts = {}
    names = {c["name"] for c in cats}   # spending budgets only: income doesn't roll over
    for name, r in budget_rows.items():
        if name not in names:
            continue
        try:
            if r.get("rollover_from"):
                starts[name] = date.fromisoformat(r["rollover_from"] + "-01")
        except ValueError:
            continue
    carry = {name: 0.0 for name in starts}
    if not starts:
        return carry
    months, m = [], min(starts.values())
    while m < month:
        months.append(m)
        m = month_start(m, 1)
    if not months:
        return carry
    totals: dict[str, dict] = defaultdict(dict)   # every month's (month_totals), in one query
    for r in conn.execute(_totals_query(months[0], m, by_month=True)).fetchall():
        totals[r["month"]][r["category"]] = r["total"] or 0.0
    for m in months:
        spent = family_spent(cats, totals.get(month_key(m), {}))
        for name, start in starts.items():
            if start <= m:
                carry[name] = max(0.0, round(amount_in(budget_rows[name], month_key(m)) + carry[name] - spent.get(name, 0.0), 2))
    return carry
