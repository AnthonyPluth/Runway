"""What the store parsers share: reading a store's amounts and dates, and how much of a reply they take."""
from __future__ import annotations

import re
from datetime import date, datetime

from dateutil import parser as dateparser

from ... import validate

MAX_RAW = 200_000    # characters of a Target order's or Costco receipt's reply kept for troubleshooting
MAX_ORDERS = 500     # orders taken from one page of history (a real page has a few dozen)


def read_money(v) -> float | None:
    """12.3, "12.30", "$1,234.56", {"amount": 12.3} or {"value": "12.30"} -> float."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return validate.parse_external(v)
    if isinstance(v, dict):
        for k in ("amount", "value", "total", "price"):
            if k in v:
                return read_money(v[k])
        return None
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", str(v))
    return validate.parse_external(m.group(0), drop=",") if m else None


def read_day(v) -> str | None:
    """A date in whatever form a store sends it -> YYYY-MM-DD."""
    if v is None:
        return None
    if isinstance(v, (date, datetime)):
        return v.isoformat()[:10]
    s = str(v).strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return m.group(0)
    try:
        return dateparser.parse(s).date().isoformat()
    except (ValueError, OverflowError):
        return None

