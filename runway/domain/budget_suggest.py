"""Suggested budgets: a monthly amount for each spending category, from what it has spent in recent months and the
recurring payments the forecast has coming for it. The Budget page shows them; nothing is saved until you apply one.

The method, for each spending category (its subcategories counted in, as its spending is):

- Its typical month: the median of what it spent in each of the last `MONTHS` full months (this month isn't over, so it
  doesn't count). A month it spent nothing in counts as nothing, so a one-off doesn't set the budget; a month before
  your transactions start isn't counted at all. A month where refunds outweighed spending counts as nothing.
- What's known to be coming: the recurring payments the forecast has for it in the month the budget is for.
- The suggestion is the larger of the two, so a budget at least covers the bills you know about (they're in the history
  too, so the two aren't added), rounded up to the next `STEP` dollars.

Which categories get one: every category that has a budget, plus each top-level category without one anywhere in its
family (none of its subcategories has one either). A category whose suggestion is nothing gets none: an existing budget
is never suggested away."""
from __future__ import annotations

from datetime import date
from statistics import median
from typing import TypedDict

from sqlalchemy import func, select

from ..dates import month_start
from ..money import CENT, cents
from ..storage import db
from ..storage.models import Account, Transaction
from .budgets import family_spent, month_totals

MONTHS = 6    # full months of history looked at
STEP = 5      # dollars a suggestion is rounded up to


class Suggestion(TypedDict):
    """A category's suggested budget (the API's BudgetSuggestion)."""
    category: str
    suggested: float
    typical: float          # its median month
    recurring: float        # the recurring payments coming in the budget's month
    budget: float | None    # its budget now


def round_up(amount: float, step: int = STEP) -> float:
    """An amount rounded up to the next whole `step` dollars (one already on a step stays); nothing stays nothing."""
    c = cents(amount)
    if c <= 0:
        return 0.0
    size = step * 100
    return float(-(-c // size) * size // 100)


def history_months(first_posted: date | None, today: date, months: int = MONTHS) -> list[date]:
    """The 1st of each full month to look at, oldest first: up to `months` before today's, none before the month of
    the first transaction (`first_posted`; with none there's no history)."""
    if first_posted is None:
        return []
    start = month_start(first_posted)
    return [m for m in (month_start(today, i - months) for i in range(months)) if m >= start]


def suggest(cats: list[dict], monthly: list[dict], recurring: list[tuple[str, float]],
            budgets: dict[str, float]) -> list[Suggestion]:
    """The suggestions (see the top of this file), in `cats`' order: `cats` the spending categories (as
    categories.all_categories gives them, in tree order); `monthly` each full month's net amount per category
    (budgets.month_totals: spending below zero); `recurring` the recurring payments coming in the budget's month, as
    (category, amount out, above zero); `budgets` each category's budget now."""
    spent = [family_spent(cats, m) for m in monthly]
    path = {c["name"]: c["path"] for c in cats}
    coming = {c["name"]: 0.0 for c in cats}
    for cat, amount in recurring:
        for name in path.get(cat, []):
            coming[name] += amount
    out: list[Suggestion] = []
    for c in cats:
        name = c["name"]
        family = [k["name"] for k in cats if k["top"] == c["top"]]
        if name not in budgets and (c["depth"] or any(k in budgets for k in family)):
            continue
        typical = round(median(max(0.0, s.get(name, 0.0)) for s in spent), 2) if spent else 0.0
        due = round(max(0.0, coming[name]), 2)
        suggested = round_up(max(typical, due))
        if suggested < CENT:
            continue
        out.append({"category": name, "suggested": suggested, "typical": typical, "recurring": due,
                    "budget": budgets.get(name)})
    return out


def first_posted(conn) -> date | None:
    """The day of the first transaction on the accounts spending is counted on (checking, savings and cards)."""
    first = conn.execute(select(func.min(Transaction.posted)).join(Account, Account.id == Transaction.account_id)
                         .where(*db.SPENDING_ACCOUNTS)).scalar()
    return date.fromisoformat(first[:10]) if first else None


def monthly_totals(conn, months: list[date]) -> list[dict]:
    """Each month's net amount per category (budgets.month_totals), for the months given (each its 1st)."""
    return [month_totals(conn, m, month_start(m, 1)) for m in months]

