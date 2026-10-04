"""Reports: spending over time, merchants, income against spending, and a drill-down of where money went.

Everything counts the way Budget does: checking, savings and cards that aren't hidden; split transactions by their
parts; card payments and transfers left out. Spending in a category is what went out less what came back (a refund
lowers it, and so does anything in Refunds), and money in is what the income categories other than Refunds received.
Money in that isn't categorized yet isn't counted as either: it's as likely a transfer as a paycheck.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from dateutil.relativedelta import relativedelta

from sqlalchemy import func, select

from . import categories, dates, db, splits
from .models import Account, Transaction

TOP = 7                      # series shown by name; the rest are "Everything else"
SCOPE = (Account.hidden == 0, Account.kind.in_(["checking", "savings", "credit"]))   # conditions on the joined Account
GROUPS = ("category", "merchant", "account")


def month_list(end: str, months: int) -> list[str]:
    """The `months` months ("2026-09") ending with `end` ("YYYY-MM"), oldest first."""
    return dates.month_keys(dates.key_start(end), months)


def _bounds(months: list[str]) -> tuple[str, str]:
    return f"{months[0]}-01", f"{dates.next_month_key(months[-1])}-01"


def _with_data(conn, ms: list[str]) -> list[str]:
    """The months from the first one with a transaction in an account the reports count: before it there's no history,
    not $0 of spending. History that starts after the 1st (a bank's backfill, an import) makes that month a part one,
    which would pull the averages down, so the months start the month after, unless that would leave only the last
    month. The last month always stays, so a new install still shows this month."""
    first = conn.execute(select(func.min(Transaction.posted)).select_from(Transaction)
                         .join(Account, Account.id == Transaction.account_id).where(*SCOPE)).scalar()
    start = (first or "")[:7] or ms[-1]
    if first and first[8:10] != "01":
        after = dates.next_month_key(start)
        if after < ms[-1]:
            start = after
    return [m for m in ms if m >= start] or ms[-1:]


def _rows(conn, start: str, end: str) -> list[dict]:
    t = splits.parts()
    return db.rows(conn.execute(
        select(t.c.id, func.substr(t.c.posted, 1, 7).label("month"), t.c.posted, t.c.amount, t.c.payee, t.c.description,
               t.c.category, t.c.account_id, db.account_label_expr().label("account_name"))
        .select_from(t).join(Account, Account.id == t.c.account_id)
        .where(t.c.posted >= start, t.c.posted < end, *SCOPE)))


class _Kinds:
    """What each category is: its top-level category, and whether it's income or a transfer."""

    def __init__(self, conn):
        self.cats = {c["name"]: c for c in categories.all_categories(conn)}

    def kind(self, row) -> str | None:
        c = self.cats.get(row["category"])
        if c is None:   # uncategorized: money out is spending; money in could be anything, so it isn't counted
            return "spend" if row["amount"] < 0 else None
        if c["top"] == "Refunds":   # money back lowers spending rather than counting as income
            return "spend"
        return "transfer" if c["is_transfer"] else "income" if c["is_income"] else "spend"

    def top(self, name: str | None) -> str:
        c = self.cats.get(name)
        return c["top"] if c else "Uncategorized"

    def second(self, name: str | None) -> str | None:
        c = self.cats.get(name)
        return c["path"][1] if c and len(c["path"]) > 1 else None


def merchant_name(row) -> str:
    return (row.get("payee") or row.get("description") or "Unknown").strip() or "Unknown"


def _group_key(row, group: str, kinds: _Kinds) -> str:
    if group == "merchant":
        return merchant_name(row)
    if group == "account":
        return row["account_id"]
    return kinds.top(row["category"])


def _month_back(month: str, n: int) -> str:
    return dates.month_key(dates.month_start(dates.key_start(month), -n))


def spending_over_time(conn, end: str, months: int = 12, group: str = "category", today: date | None = None) -> dict:
    """Spending per month, by top-level category, merchant or account: the biggest few by name, the rest together.
    Months before the first transaction are left out.

    When the last month is the one `today` is in, it's only part of a month, so each series also gets `same_point`:
    what it spent last month (`prev`) and the same month a year before (`year_ago`, None when that's outside the
    months shown) up to the same day of the month (`through`), to compare like with like."""
    if group not in GROUPS:
        raise ValueError("Group by category, merchant or account")
    ms = _with_data(conn, month_list(end, max(2, min(months, 36))))
    kinds = _Kinds(conn)
    partial = today is not None and f"{today:%Y-%m}" == ms[-1]
    day = today.day if today is not None and partial else 0
    back = {_month_back(ms[-1], 1): "prev", _month_back(ms[-1], 12): "year_ago"}
    per: dict[str, dict[str, float]] = {}
    early: dict[str, dict[str, float]] = {}      # the months to compare with, up to the same day
    label: dict[str, str] = {}
    for r in _rows(conn, *_bounds(ms)):
        if kinds.kind(r) != "spend":
            continue
        key = _group_key(r, group, kinds)
        label.setdefault(key, r["account_name"] if group == "account" else key)
        g = per.setdefault(key, {})
        g[r["month"]] = g.get(r["month"], 0.0) - r["amount"]
        if partial and r["month"] in back and int(r["posted"][8:10]) <= day:
            e = early.setdefault(key, {})
            e[back[r["month"]]] = e.get(back[r["month"]], 0.0) - r["amount"]
    year_ago_shown = _month_back(ms[-1], 12) in ms
    series: list[dict[str, Any]] = []
    for key, by_month in per.items():
        values = [round(max(0.0, by_month.get(m, 0.0)), 2) for m in ms]
        total = round(sum(values), 2)
        if total > 0.005:
            s: dict[str, Any] = {"name": label[key], "values": values, "total": total}
            if group == "account":
                s["account"] = key
            if partial:
                e = early.get(key, {})
                s["same_point"] = {"prev": round(max(0.0, e.get("prev", 0.0)), 2),
                                   "year_ago": round(max(0.0, e.get("year_ago", 0.0)), 2) if year_ago_shown else None}
            series.append(s)
    series.sort(key=lambda s: -s["total"])
    shown, rest = series[:TOP], series[TOP:]
    if len(rest) == 1:
        shown.append(rest[0])
    elif rest:
        values = [round(sum(s["values"][i] for s in rest), 2) for i in range(len(ms))]
        other: dict[str, Any] = {"name": f"Everything else ({len(rest)})", "values": values, "total": round(sum(values), 2),
                                 "other": True, "members": [s["name"] for s in rest]}
        if partial:
            other["same_point"] = {"prev": round(sum(s["same_point"]["prev"] for s in rest), 2),
                                   "year_ago": round(sum(s["same_point"]["year_ago"] for s in rest), 2) if year_ago_shown else None}
        shown.append(other)
    totals = [round(sum(s["values"][i] for s in series), 2) for i in range(len(ms))]
    return {"months": ms, "group": group, "series": shown, "totals": totals, "all": series,
            "through": f"{today:%Y-%m-%d}" if today is not None and partial else None}


def _in_and_out(conn, kinds: _Kinds, ms: list[str]) -> list[dict[str, Any]]:
    inc = {m: 0.0 for m in ms}
    out = {m: 0.0 for m in ms}
    for r in _rows(conn, *_bounds(ms)):
        k = kinds.kind(r)
        if k == "income":
            inc[r["month"]] += r["amount"]
        elif k == "spend":
            out[r["month"]] -= r["amount"]
    rows: list[dict[str, Any]] = []
    for m in ms:
        i, o = round(max(0.0, inc[m]), 2), round(max(0.0, out[m]), 2)
        rows.append({"month": m, "income": i, "spending": o, "net": round(i - o, 2),
                     "rate": round((i - o) / i, 4) if i > 0 else None})
    return rows


def income_vs_spending(conn, end: str, months: int = 12) -> dict:
    """Money in and money out each month, what was left, and the share of income kept (savings rate). Months before the
    first transaction are left out, so the year's count of months is the months with history. The year is January to
    `end` whatever number of months is asked for."""
    ms = _with_data(conn, month_list(end, max(2, min(months, 36))))
    kinds = _Kinds(conn)
    rows = _in_and_out(conn, kinds, ms)
    year = ms[-1][:4]
    year_ms = _with_data(conn, month_list(ms[-1], int(ms[-1][5:7])))
    have = {r["month"]: r for r in rows}
    ytd = [have[m] for m in year_ms] if all(m in have for m in year_ms) else _in_and_out(conn, kinds, year_ms)
    ytd = [r for r in ytd if r["month"].startswith(year)]
    ti, to = round(sum(r["income"] for r in ytd), 2), round(sum(r["spending"] for r in ytd), 2)
    return {"months": rows, "year": {"year": year, "months": len(ytd), "income": ti, "spending": to, "net": round(ti - to, 2),
                                     "rate": round((ti - to) / ti, 4) if ti > 0 else None}}


def merchants(conn, start: str, end: str, limit: int = 100, q: str = "") -> dict:
    """Where you spent the most: each merchant's total, visits, average, last visit and usual category. `q` keeps the
    merchants whose name has it in (any case); `count` and `total` are then of those."""
    needle = q.strip().lower()
    kinds = _Kinds(conn)
    agg: dict[str, dict] = {}
    for r in _rows(conn, start, end):
        if kinds.kind(r) != "spend":
            continue
        name = merchant_name(r)
        if needle and needle not in name.lower():
            continue
        a = agg.setdefault(name.lower(), {"name": name, "total": 0.0, "count": 0, "last": "", "cats": {}, "ids": set()})
        a["total"] -= r["amount"]
        if r["id"] not in a["ids"]:   # a split transaction's parts are one visit
            a["ids"].add(r["id"])
            a["count"] += 1
        a["last"] = max(a["last"], r["posted"])
        cat = kinds.top(r["category"])
        a["cats"][cat] = a["cats"].get(cat, 0.0) - r["amount"]
    out = []
    for a in agg.values():
        if a["total"] <= 0.005:
            continue
        out.append({"name": a["name"], "total": round(a["total"], 2), "count": a["count"],
                    "average": round(a["total"] / a["count"], 2), "last": a["last"],
                    "category": max(a["cats"].items(), key=lambda kv: kv[1])[0]})
    out.sort(key=lambda x: -x["total"])
    return {"start": start, "end": end, "merchants": out[:limit], "count": len(out),
            "total": round(sum(x["total"] for x in out), 2)}


def merchant(conn, name: str, end: str, months: int = 12) -> dict:
    """One merchant: spending there each month and its transactions."""
    ms = month_list(end, max(2, min(months, 36)))
    kinds = _Kinds(conn)
    key = (name or "").strip().lower()
    by_month = {m: 0.0 for m in ms}
    txs, seen = [], set()
    for r in sorted(_rows(conn, *_bounds(ms)), key=lambda r: (r["posted"], r["id"]), reverse=True):
        if merchant_name(r).lower() != key or kinds.kind(r) == "transfer":
            continue
        if kinds.kind(r) == "spend":
            by_month[r["month"]] -= r["amount"]
        if r["id"] not in seen:
            seen.add(r["id"])
            txs.append(r)
    ids = [t["id"] for t in txs]
    whole = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        whole.update({r["id"]: r for r in db.rows(conn.execute(
            select(Transaction.id, Transaction.posted, Transaction.amount, Transaction.category, Transaction.is_split)
            .where(Transaction.id.in_(chunk))))})
    values = [round(max(0.0, by_month[m]), 2) for m in ms]
    return {"name": name, "months": ms, "values": values, "total": round(sum(values), 2),
            "transactions": [{"id": t["id"], "posted": t["posted"], "amount": whole[t["id"]]["amount"],
                              "category": "Split" if whole[t["id"]]["is_split"] else whole[t["id"]]["category"],
                              "account_name": t["account_name"], "description": t["description"]} for t in txs if t["id"] in whole][:100]}


def breakdown(conn, start: str, end: str) -> dict:
    """Spending as a tree for the treemap: top-level category -> subcategory -> merchant."""
    kinds = _Kinds(conn)
    root: dict = {"name": "Spending", "value": 0.0, "children": {}}
    for r in _rows(conn, start, end):
        if kinds.kind(r) != "spend":
            continue
        v = -r["amount"]
        top = kinds.top(r["category"])
        sub = kinds.second(r["category"]) or f"{top} (general)"
        path = [top, sub, merchant_name(r)]
        node = root
        node["value"] += v
        for name in path:
            node = node["children"].setdefault(name, {"name": name, "value": 0.0, "children": {}})
            node["value"] += v

    def finish(node, depth):
        kids = [finish(k, depth + 1) for k in node["children"].values() if k["value"] > 0.005]
        kids.sort(key=lambda k: -k["value"])
        # a category with only its "general" part skips a level
        if depth == 1 and len(kids) == 1 and kids[0]["name"].endswith("(general)"):
            kids = kids[0]["children"]
        out = {"name": node["name"], "value": round(node["value"], 2)}
        if kids:
            out["children"] = kids
        return out
    tree = finish(root, 0)
    tree.setdefault("children", [])
    return {"start": start, "end": end, "tree": tree}


def transactions(conn, start: str, end: str, category: str | None = None, merchant_: str | None = None,
                 limit: int = 200) -> list[dict]:
    """The spending behind one block of the treemap (a top-level category, a subcategory, and/or a merchant)."""
    kinds = _Kinds(conn)
    out, seen = [], set()
    for r in sorted(_rows(conn, start, end), key=lambda r: (r["posted"], r["id"]), reverse=True):
        if kinds.kind(r) != "spend":
            continue
        if category and category not in (kinds.top(r["category"]), kinds.second(r["category"]) or f"{kinds.top(r['category'])} (general)"):
            continue
        if merchant_ and merchant_name(r).lower() != merchant_.lower():
            continue
        out.append({"id": r["id"], "posted": r["posted"], "amount": r["amount"], "payee": merchant_name(r),
                    "category": r["category"], "account_name": r["account_name"], "part": r["id"] in seen})
        seen.add(r["id"])
        if len(out) >= limit:
            break
    return out


def month_pace(conn, today: date) -> dict:
    """Spending so far this month, day by day and added up, next to last month's, for the Overview.
    `this` runs to today; `last` covers all of last month. Both count spending the way everything else here does."""
    first = today.replace(day=1)
    prev = dates.month_start(first, -1)
    kinds = _Kinds(conn)
    this_days, last_days = [0.0] * today.day, [0.0] * (first - prev).days
    for r in _rows(conn, prev.isoformat(), (today + relativedelta(days=1)).isoformat()):
        if kinds.kind(r) != "spend":
            continue
        d = dates.parse_day(r["posted"])
        days = this_days if d >= first else last_days
        days[d.day - 1] -= r["amount"]

    def running(xs: list[float]) -> list[float]:
        out, total = [], 0.0
        for x in xs:
            total += x
            out.append(round(total, 2))
        return out

    this, last = running(this_days), running(last_days)
    same_point = last[min(today.day, len(last)) - 1] if last else 0.0
    return {"month": f"{first:%Y-%m}", "prev_month": f"{prev:%Y-%m}", "this": this, "last": last,
            "spent": this[-1] if this else 0.0, "last_same_point": same_point, "last_total": last[-1] if last else 0.0}
