"""Reports: cash flow, spending and income over time, merchants, and the month's pace."""
from __future__ import annotations

from datetime import date
from typing import Any

from dateutil.relativedelta import relativedelta

from ... import categories, reports
from ...budgets import month_totals
from ..common import ApiError, _month_range


def api_month_pace(conn, _q, _b):
    return reports.month_pace(conn, date.today())


def api_cashflow(conn, q, _b):
    """Where money came from and went in a month, for the Sankey report."""
    start, end = _month_range(q)
    totals = month_totals(conn, start, end)
    cats = categories.all_categories(conn)
    kind = {c["name"]: c for c in cats}
    income: dict[str, float] = {}
    spending: dict[str, dict] = {}
    for name, total in totals.items():
        c = kind.get(name)
        if c and c["is_transfer"]:
            continue
        if c and c["is_income"] and c["top"] != "Refunds":   # refunds lower spending, they aren't income
            top = c["top"]
            income[top] = income.get(top, 0.0) + total
            continue
        if c is None:  # uncategorized: net money out counts as spending; net money in isn't counted as income
            if total < 0:
                spending.setdefault("Uncategorized", {"value": 0.0, "children": {}})["value"] += -total
            continue
        top = c["top"]
        node = spending.setdefault(top, {"value": 0.0, "children": {}})
        node["value"] += -total
        if c["depth"] >= 1:  # the Sankey shows two levels; deeper subcategories count toward their level-2 ancestor
            sub = c["path"][1]
            node["children"][sub] = node["children"].get(sub, 0.0) + (-total)
    spend_list = []
    for name, node in spending.items():
        if node["value"] <= 0.005:
            continue
        kids = [{"name": k, "value": round(v, 2)} for k, v in node["children"].items() if v > 0.005]
        kid_total = sum(k["value"] for k in kids)
        if kids and node["value"] - kid_total > 0.5:
            kids.append({"name": f"{name} (general)", "value": round(node["value"] - kid_total, 2)})
        kids.sort(key=lambda k: -k["value"])
        spend_list.append({"name": name, "value": round(node["value"], 2), "children": kids})
    spend_list.sort(key=lambda n: -n["value"])
    inc_list: list[dict[str, Any]] = sorted(({"name": k, "value": round(v, 2)} for k, v in income.items() if v > 0.005), key=lambda n: -n["value"])
    total_in = round(sum(n["value"] for n in inc_list), 2)
    total_out = round(sum(n["value"] for n in spend_list), 2)
    return {"month": f"{start:%Y-%m}", "income": inc_list, "spending": spend_list,
            "total_in": total_in, "total_out": total_out, "net": round(total_in - total_out, 2)}


def _ym(q, key="end") -> str:
    v = (q.get(key, [""])[0] or f"{date.today():%Y-%m}")[:7]
    try:
        date(int(v[:4]), int(v[5:7]), 1)
    except ValueError:
        raise ApiError("Month must look like 2026-09") from None
    return v


def _day(q, key: str, default: date) -> str:
    v = q.get(key, [""])[0]
    if not v:
        return default.isoformat()
    try:
        return date.fromisoformat(v).isoformat()
    except ValueError:
        raise ApiError("Dates must look like 2026-09-01") from None


def _months(q) -> int:
    return max(2, min(int(q.get("months", ["12"])[0]), 36))


def _span(q) -> tuple[str, str]:
    """start (inclusive) and end (exclusive) days; this month by default."""
    first = date.today().replace(day=1)
    return _day(q, "start", first), _day(q, "end", first + relativedelta(months=1))


def api_report_spending(conn, q, _b):
    try:
        return reports.spending_over_time(conn, _ym(q), _months(q), q.get("group", ["category"])[0])
    except ValueError as e:
        raise ApiError(str(e)) from e


def api_report_income(conn, q, _b):
    return reports.income_vs_spending(conn, _ym(q), _months(q))


def api_report_merchants(conn, q, _b):
    return reports.merchants(conn, *_span(q))


def api_report_merchant(conn, q, _b):
    return reports.merchant(conn, q.get("name", [""])[0], _ym(q), _months(q))


def api_report_breakdown(conn, q, _b):
    return reports.breakdown(conn, *_span(q))


def api_report_transactions(conn, q, _b):
    return reports.transactions(conn, *_span(q), q.get("category", [""])[0] or None, q.get("merchant", [""])[0] or None)
