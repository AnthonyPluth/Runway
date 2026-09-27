"""Reports: spending over time, merchants, income against spending, and a drill-down of where money went.

Everything counts the way Budget does: checking, savings and cards that aren't hidden; split transactions by their
parts; card payments and transfers left out. Spending in a category is what went out less what came back (a refund
lowers it), and money in is what the income categories received.
"""
from __future__ import annotations

from datetime import date

from dateutil.relativedelta import relativedelta

from . import categories, db, splits

TOP = 7                      # series shown by name; the rest are "Everything else"
SCOPE = "a.hidden=0 AND a.kind IN ('checking','savings','credit')"
GROUPS = ("category", "merchant", "account")


def month_list(end: str, months: int) -> list[str]:
    y, m = (int(x) for x in end.split("-"))
    last = date(y, m, 1)
    return [f"{last - relativedelta(months=months - 1 - i):%Y-%m}" for i in range(months)]


def _bounds(months: list[str]) -> tuple[str, str]:
    y, m = (int(x) for x in months[-1].split("-"))
    return f"{months[0]}-01", (date(y, m, 1) + relativedelta(months=1)).isoformat()


def _rows(conn, start: str, end: str) -> list[dict]:
    return db.rows(conn.execute(
        f"SELECT t.id, substr(t.posted, 1, 7) AS month, t.posted, t.amount, t.payee, t.description, t.category, "
        f"t.account_id, {db.label_sql('a')} AS account_name FROM {splits.PARTS} t JOIN accounts a ON a.id=t.account_id "
        f"WHERE t.posted>=? AND t.posted<? AND {SCOPE}", (start, end)))


class _Kinds:
    """What each category is: its top-level category, and whether it's income or a transfer."""

    def __init__(self, conn):
        self.cats = {c["name"]: c for c in categories.all_categories(conn)}

    def kind(self, row) -> str | None:
        c = self.cats.get(row["category"])
        if c is None:   # uncategorized: money out is spending, money in is income
            return "spend" if row["amount"] < 0 else "income"
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
        return row["account_name"]
    return kinds.top(row["category"])


def spending_over_time(conn, end: str, months: int = 12, group: str = "category") -> dict:
    """Spending per month, by top-level category, merchant or account: the biggest few by name, the rest together."""
    if group not in GROUPS:
        raise ValueError("Group by category, merchant or account")
    ms = month_list(end, max(2, min(months, 36)))
    kinds = _Kinds(conn)
    per: dict[str, dict[str, float]] = {}
    for r in _rows(conn, *_bounds(ms)):
        if kinds.kind(r) != "spend":
            continue
        g = per.setdefault(_group_key(r, group, kinds), {})
        g[r["month"]] = g.get(r["month"], 0.0) - r["amount"]
    series = []
    for name, by_month in per.items():
        values = [round(max(0.0, by_month.get(m, 0.0)), 2) for m in ms]
        total = round(sum(values), 2)
        if total > 0.005:
            series.append({"name": name, "values": values, "total": total})
    series.sort(key=lambda s: -s["total"])
    shown, rest = series[:TOP], series[TOP:]
    if len(rest) == 1:
        shown.append(rest[0])
    elif rest:
        values = [round(sum(s["values"][i] for s in rest), 2) for i in range(len(ms))]
        shown.append({"name": f"Everything else ({len(rest)})", "values": values, "total": round(sum(values), 2), "other": True,
                      "members": [s["name"] for s in rest]})
    totals = [round(sum(s["values"][i] for s in series), 2) for i in range(len(ms))]
    return {"months": ms, "group": group, "series": shown, "totals": totals, "all": series}


def income_vs_spending(conn, end: str, months: int = 12) -> dict:
    """Money in and money out each month, what was left, and the share of income kept (savings rate)."""
    ms = month_list(end, max(2, min(months, 36)))
    kinds = _Kinds(conn)
    inc = {m: 0.0 for m in ms}
    out = {m: 0.0 for m in ms}
    for r in _rows(conn, *_bounds(ms)):
        k = kinds.kind(r)
        if k == "income":
            inc[r["month"]] += r["amount"]
        elif k == "spend":
            out[r["month"]] -= r["amount"]
    rows = []
    for m in ms:
        i, o = round(max(0.0, inc[m]), 2), round(max(0.0, out[m]), 2)
        rows.append({"month": m, "income": i, "spending": o, "net": round(i - o, 2),
                     "rate": round((i - o) / i, 4) if i > 0 else None})
    year = ms[-1][:4]
    ytd = [r for r in rows if r["month"].startswith(year)]
    ti, to = round(sum(r["income"] for r in ytd), 2), round(sum(r["spending"] for r in ytd), 2)
    return {"months": rows, "year": {"year": year, "months": len(ytd), "income": ti, "spending": to, "net": round(ti - to, 2),
                                     "rate": round((ti - to) / ti, 4) if ti > 0 else None}}


def merchants(conn, start: str, end: str, limit: int = 100) -> dict:
    """Where you spent the most: each merchant's total, visits, average, last visit and usual category."""
    kinds = _Kinds(conn)
    agg: dict[str, dict] = {}
    for r in _rows(conn, start, end):
        if kinds.kind(r) != "spend":
            continue
        name = merchant_name(r)
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
        q = ",".join("?" * len(chunk))
        whole.update({r["id"]: r for r in db.rows(conn.execute(
            f"SELECT id, posted, amount, category, is_split FROM transactions WHERE id IN ({q})", chunk))})
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
