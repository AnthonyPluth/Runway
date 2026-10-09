"""Suggested budgets for the Budget page (domain/budget_suggest.py): read only; a suggestion is applied through
POST /api/budget like any budget you type."""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select

from ...dates import add_months, month_key, month_start
from ...domain import budget_suggest, categories
from ...storage import db
from ...storage.models import Budget
from ..common import _month_range
from ..contract import BudgetSuggestions
from .budget import EXPECTED_DAYS, upcoming_events


def api_budget_suggestions(conn, q, _b) -> BudgetSuggestions:
    """The suggestions for the month asked for (`month`): its recurring payments when it's still to come, else next
    month's (this month's have partly been paid, and they're in what it has spent). A month past the forecast's reach
    (EXPECTED_DAYS) counts the last month it reaches, and `recurring_month` says which."""
    today = date.today()
    start, _ = _month_range(q)
    if start <= today:
        start = month_start(today, 1)
    start = min(start, month_start(today + timedelta(days=EXPECTED_DAYS)))
    end = add_months(start, 1)
    cats = [c for c in categories.all_categories(conn) if not c["is_transfer"] and not c["is_income"]]
    budgets = {r["category"]: r["amount"] for r in db.rows(conn.execute(select(Budget.category, Budget.amount)))}
    months = budget_suggest.history_months(budget_suggest.first_posted(conn), today)
    recurring = [(e["category"], -e["amount"]) for e in upcoming_events(conn, today, start, end)["out"]]
    return {"months": len(months), "first": month_key(months[0]) if months else None,
            "last": month_key(months[-1]) if months else None, "recurring_month": month_key(start),
            "suggestions": budget_suggest.suggest(cats, budget_suggest.monthly_totals(conn, months), recurring, budgets)}
