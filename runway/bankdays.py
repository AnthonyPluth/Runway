"""When money actually moves: banks don't settle ACH payments on weekends or Federal Reserve holidays.

A payment due on a day banks are closed lands on the next business day, except money coming in (paychecks), which
employers send early so it arrives on the business day before.

Federal Reserve holidays are the US federal holidays, observed a little differently from the government's: one that
falls on a Sunday is observed on Monday, but one that falls on a Saturday isn't moved (banks are open that Friday).
"""
from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache

import holidays


@lru_cache(maxsize=32)
def bank_holidays(year: int) -> frozenset[date]:
    days = set()
    for d in holidays.US(years=[year - 1, year, year + 1], observed=False):
        if d.weekday() == 6:        # Sunday: observed Monday
            d = d + timedelta(days=1)
        if d.weekday() < 5 and d.year == year:
            days.add(d)
    return frozenset(days)


def is_business_day(d: date) -> bool:
    return d.weekday() < 5 and d not in bank_holidays(d.year)


def next_business_day(d: date) -> date:
    """d itself if banks are open; otherwise the next day they are."""
    while not is_business_day(d):
        d += timedelta(days=1)
    return d


def previous_business_day(d: date) -> date:
    """d itself if banks are open; otherwise the last day before it that they are."""
    while not is_business_day(d):
        d -= timedelta(days=1)
    return d


def settles(d: date, money_in: bool) -> date:
    """The day a payment scheduled for d actually lands: money in moves earlier, money out moves later."""
    return previous_business_day(d) if money_in else next_business_day(d)
