"""Equity compensation: stock options, RSUs and shares in companies (usually private) you work or worked for.

Each grant vests on a schedule: nothing before the cliff, then the months so far at once, then a slice every month
(or quarter) until it's all vested. What it's worth uses the company's share price (the latest 409A fair market
value for a private company, or whatever price you set):

- options: (price - exercise price) for each vested option not yet exercised, plus price for each exercised share;
  an option under water is worth nothing;
- RSUs, restricted stock and shares: price for each vested share.

Only what has vested counts toward net worth (and only for companies you leave switched on). Grants come from
Carta (runway/carta.py) or are entered by hand.
"""
from __future__ import annotations

import calendar
import secrets
from datetime import date

from . import db

KINDS = {"iso": "ISO options", "nso": "NSO options", "rsu": "RSUs", "rsa": "Restricted stock", "shares": "Shares"}
OPTIONS = {"iso", "nso"}


class EquityError(ValueError):
    pass


def _add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    y, m = d.year + y, m + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def _months_between(a: date, b: date) -> int:
    """Whole months from a to b (a vesting date counts on its day of the month)."""
    n = (b.year - a.year) * 12 + (b.month - a.month)
    return n - 1 if b.day < a.day and n > 0 else max(0, n)


def vested_on(g: dict, on: date) -> float:
    """Shares (or options) vested by this date, by the grant's schedule."""
    qty = g["quantity"] or 0.0
    start = date.fromisoformat(g["vest_start"]) if g.get("vest_start") else (
        date.fromisoformat(g["granted_on"]) if g.get("granted_on") else None)
    total = g.get("vest_months") or 0
    if not start or total <= 0:
        return qty if (start is None or on >= start) else 0.0
    if on < start:
        return 0.0
    months = _months_between(start, on)
    if months < (g.get("cliff_months") or 0):
        return 0.0
    every = max(1, g.get("vest_every") or 1)
    months = min(total, months - months % every)
    return round(qty * months / total, 4)


def vested_now(g: dict, today: date) -> float:
    """Today's vested amount: what Carta reported, if it has, else the schedule."""
    if g.get("vested_reported") is not None:
        return min(g["quantity"] or 0.0, g["vested_reported"])
    return vested_on(g, today)


def fully_vested_on(g: dict) -> str | None:
    start = g.get("vest_start") or g.get("granted_on")
    if not start or not g.get("vest_months"):
        return None
    return _add_months(date.fromisoformat(start), g["vest_months"]).isoformat()


def value(g: dict, price: float | None, vested: float) -> dict:
    """What a grant is worth at this share price: the vested part and the part still to vest."""
    price = price or 0.0
    qty = g["quantity"] or 0.0
    if g["kind"] in OPTIONS:
        spread = max(0.0, price - (g.get("strike") or 0.0))
        exercised = min(g.get("exercised") or 0.0, vested)
        now = spread * (vested - exercised) + price * exercised
        later = spread * max(0.0, qty - vested)
        cost = (g.get("strike") or 0.0) * (vested - exercised)   # to exercise what's vested
    else:
        now, later, cost = price * vested, price * max(0.0, qty - vested), 0.0
    return {"vested_value": round(now, 2), "unvested_value": round(later, 2), "exercise_cost": round(cost, 2)}


def schedule(g: dict, until: date | None = None) -> list[tuple[str, float]]:
    """Cumulative vested amount at each vesting date (for the chart)."""
    start = g.get("vest_start") or g.get("granted_on")
    if not start:
        return []
    d0 = date.fromisoformat(start)
    total = g.get("vest_months") or 0
    if total <= 0:
        return [(d0.isoformat(), g["quantity"])]
    every = max(1, g.get("vest_every") or 1)
    out = [(d0.isoformat(), 0.0)]
    for m in range(every, total + every, every):
        d = _add_months(d0, min(m, total))
        if until and d > until:
            break
        v = vested_on(g, d)
        if v != out[-1][1]:
            out.append((d.isoformat(), v))
    return out


def overview(conn, today: date | None = None) -> dict:
    """Every company and grant with what it's worth today, and the totals."""
    today = today or date.today()
    companies = db.rows(conn.execute("SELECT id, name, share_price, price_as_of, in_networth, source, updated "
                                     "FROM equity_companies ORDER BY name"))
    grants = db.rows(conn.execute("SELECT * FROM equity_grants ORDER BY COALESCE(granted_on, vest_start), id"))
    by_company: dict[str, list] = {}
    for g in grants:
        g.pop("raw", None)
        by_company.setdefault(g["company_id"], []).append(g)
    totals = {"vested_value": 0.0, "unvested_value": 0.0, "in_networth": 0.0}
    for c in companies:
        c["grants"] = by_company.get(c["id"], [])
        c["vested_value"] = c["unvested_value"] = 0.0
        for g in c["grants"]:
            g["vested"] = round(vested_now(g, today), 4)
            g["fully_vested_on"] = fully_vested_on(g)
            g["schedule"] = schedule(g)
            g.update(value(g, c["share_price"], g["vested"]))
            c["vested_value"] += g["vested_value"]
            c["unvested_value"] += g["unvested_value"]
        c["vested_value"], c["unvested_value"] = round(c["vested_value"], 2), round(c["unvested_value"], 2)
        totals["vested_value"] += c["vested_value"]
        totals["unvested_value"] += c["unvested_value"]
        if c["in_networth"]:
            totals["in_networth"] += c["vested_value"]
    return {"companies": companies, **{k: round(v, 2) for k, v in totals.items()}, "kinds": KINDS}


def networth_items(conn, today: date | None = None) -> list[dict]:
    """Vested equity per company, for net worth (companies switched on only)."""
    today = today or date.today()
    out = []
    for c in overview(conn, today)["companies"]:
        if c["in_networth"] and c["vested_value"]:
            out.append({"type": "equity", "id": c["id"], "name": c["name"], "value": c["vested_value"],
                        "as_of": c["price_as_of"], "source": c["source"]})
    return out


# ------------------------------------------------------------------------------------------------ editing

def _num(v, name, allow_none=True):
    if v in (None, ""):
        if allow_none:
            return None
        raise EquityError(f"Enter the {name}")
    try:
        n = db.number(v)
    except (TypeError, ValueError):
        raise EquityError(f"The {name} must be a number")
    if n < 0:
        raise EquityError(f"The {name} can't be negative")
    return n


def _day(v, name):
    if v in (None, ""):
        return None
    try:
        return date.fromisoformat(str(v)[:10]).isoformat()
    except ValueError:
        raise EquityError(f"The {name} must be a date like 2024-03-01")


def save_company(conn, body: dict, cid: str | None = None) -> str:
    name = " ".join(str(body.get("name") or "").split())[:80]
    if not name and cid is None:
        raise EquityError("Name the company")
    fields = {}
    if name:
        fields["name"] = name
    if "share_price" in body:
        fields["share_price"] = _num(body.get("share_price"), "share price")
        fields["price_as_of"] = _day(body.get("price_as_of"), "price date") or date.today().isoformat()
    if "in_networth" in body:
        fields["in_networth"] = 1 if body.get("in_networth") else 0
    if cid is None:
        cid = "m" + secrets.token_hex(4)
        cols = ["id", *fields]
        conn.execute(f"INSERT INTO equity_companies({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", (cid, *fields.values()))
        return cid
    if not conn.execute("SELECT 1 FROM equity_companies WHERE id=?", (cid,)).fetchone():
        raise EquityError("Company not found")
    if fields:
        conn.execute(f"UPDATE equity_companies SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?", (*fields.values(), cid))
    return cid


def remove_company(conn, cid: str) -> None:
    conn.execute("DELETE FROM equity_grants WHERE company_id=?", (cid,))
    conn.execute("DELETE FROM equity_companies WHERE id=?", (cid,))


def clean_grant(body: dict) -> dict:
    kind = (body.get("kind") or "").lower()
    if kind not in KINDS:
        raise EquityError("Choose what kind of grant it is")
    g = {"kind": kind, "label": " ".join(str(body.get("label") or "").split())[:60] or None,
         "quantity": _num(body.get("quantity"), "number of shares", allow_none=False),
         "strike": _num(body.get("strike"), "exercise price") if kind in OPTIONS else None,
         "granted_on": _day(body.get("granted_on"), "grant date"),
         "vest_start": _day(body.get("vest_start"), "vesting start"),
         "expires_on": _day(body.get("expires_on"), "expiration date"),
         "exercised": _num(body.get("exercised"), "number exercised") or 0.0}
    if g["quantity"] <= 0:
        raise EquityError("Enter the number of shares")
    if kind in OPTIONS and g["strike"] is None:
        raise EquityError("Enter the exercise (strike) price")
    if g["exercised"] > g["quantity"]:
        raise EquityError("More exercised than granted")
    for k, name in (("vest_months", "vesting length"), ("cliff_months", "cliff"), ("vest_every", "vesting interval")):
        n = _num(body.get(k), name)
        g[k] = int(n) if n is not None else None
    if kind == "shares":
        g["vest_months"] = g["cliff_months"] = None
    g["vest_every"] = g["vest_every"] or 1
    if g["vest_months"] and (g["cliff_months"] or 0) > g["vest_months"]:
        raise EquityError("The cliff is longer than the vesting")
    return g


def save_grant(conn, company_id: str, body: dict, gid: str | None = None) -> str:
    if not conn.execute("SELECT 1 FROM equity_companies WHERE id=?", (company_id,)).fetchone():
        raise EquityError("Company not found")
    g = clean_grant(body)
    if gid is None:
        gid = "g" + secrets.token_hex(5)
        cols = ["id", "company_id", *g]
        conn.execute(f"INSERT INTO equity_grants({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", (gid, company_id, *g.values()))
        return gid
    if not conn.execute("SELECT 1 FROM equity_grants WHERE id=?", (gid,)).fetchone():
        raise EquityError("Grant not found")
    conn.execute(f"UPDATE equity_grants SET {', '.join(f'{k}=?' for k in g)}, vested_reported=NULL WHERE id=?", (*g.values(), gid))
    return gid


def remove_grant(conn, gid: str) -> None:
    conn.execute("DELETE FROM equity_grants WHERE id=?", (gid,))
