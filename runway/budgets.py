"""Budgets: what each category spent in a month, and what a budget that rolls over carries into the next one. Shared by
the Budget page and the forecast, which spends the budgets."""
from __future__ import annotations

from datetime import date

from dateutil.relativedelta import relativedelta
from sqlalchemy import func, select

from . import splits
from .models import Account


def month_totals(conn, start: date, end: date) -> dict:
    """Net amount per category for the month, across checking, savings and cards (not loans or investments)."""
    t = splits.parts()
    rows_ = conn.execute(
        select(t.c.category.label("category"), func.sum(t.c.amount).label("total"))
        .join(Account, Account.id == t.c.account_id)
        .where(t.c.posted >= start.isoformat(), t.c.posted < end.isoformat(), Account.hidden == 0,
               Account.kind.in_(["checking", "savings", "credit"]))
        .group_by(t.c.category)
    ).fetchall()
    return {r["category"]: r["total"] or 0.0 for r in rows_}


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
    rolling over (overspending isn't carried; a month that goes over just uses up what was carried)."""
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
    m = min(starts.values())
    while m < month:
        nxt = m + relativedelta(months=1)
        spent = family_spent(cats, month_totals(conn, m, nxt))
        for name, start in starts.items():
            if start <= m:
                carry[name] = max(0.0, round(budget_rows[name]["amount"] + carry[name] - spent.get(name, 0.0), 2))
        m = nxt
    return carry
