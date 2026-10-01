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
from typing import Any

from sqlalchemy import delete, func, select

from . import db, validate
from .models import EquityCompany, EquityGrant

KINDS = {"iso": "ISO options", "nso": "NSO options", "rsu": "RSUs", "rsa": "Restricted stock", "shares": "Shares"}
OPTIONS = {"iso", "nso"}
GRANT_COLUMNS = [c for c in EquityGrant.__table__.c if c.key != "raw"]   # a grant, less what Carta sent


class EquityError(ValueError):
    pass


MAX_MONTHS = 600          # the longest vesting (50 years): a longer one runs off the calendar (_add_months)
MIN_YEAR, MAX_YEAR = 1900, 2200   # dates a grant can carry, for the same reason


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


def vested_later(g: dict, on: date, today: date) -> float:
    """What will have vested by `on` (today or later). By the schedule, from today's amount (vested_now). When Carta
    has reported what's vested, that's where it starts from: the rest vests as the schedule vests its rest, so it's all
    vested when the schedule is. Carta's figure can be ahead of the schedule worked out here (its vesting start isn't
    always the grant date Runway has), and the schedule alone would hold it flat until it caught up."""
    qty = g["quantity"] or 0.0
    reported = g.get("vested_reported")
    by_schedule = vested_on(g, on)
    if reported is None:
        return min(qty, max(vested_now(g, today), by_schedule))
    reported = min(qty, reported)
    try:
        since = date.fromisoformat(str(g.get("vested_reported_on") or "")[:10])
    except ValueError:
        since = today
    at_report = vested_on(g, min(since, on))
    if at_report >= qty:   # the schedule had it all vested by then: Carta's is behind, and the schedule's done
        return max(reported, by_schedule)
    share = max(0.0, by_schedule - at_report) / (qty - at_report)
    return round(min(qty, reported + (qty - reported) * share), 4)


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
    companies = db.rows(conn.execute(
        select(EquityCompany.id, EquityCompany.name, EquityCompany.share_price, EquityCompany.price_as_of,
               EquityCompany.in_networth, EquityCompany.source, EquityCompany.updated).order_by(EquityCompany.name)))
    grants = db.rows(conn.execute(
        select(*GRANT_COLUMNS).order_by(func.coalesce(EquityGrant.granted_on, EquityGrant.vest_start), EquityGrant.id)))
    by_company: dict[str, list] = {}
    for g in grants:
        by_company.setdefault(g["company_id"], []).append(g)
    totals = {"vested_value": 0.0, "unvested_value": 0.0, "in_networth": 0.0}
    for c in companies:
        c["grants"] = by_company.get(c["id"], [])
        c["vested_value"] = c["unvested_value"] = 0.0
        for g in c["grants"]:
            try:
                g["vested"] = round(vested_now(g, today), 4)
                g["fully_vested_on"] = fully_vested_on(g)
                g["schedule"] = schedule(g)
            except (ValueError, OverflowError):   # a schedule that runs off the calendar (saved before MAX_MONTHS):
                g["vested"], g["fully_vested_on"], g["schedule"] = 0.0, None, []   # this grant, not the whole page
                g["problem"] = "This grant’s vesting can’t be worked out: check its dates and vesting length."
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


def _expired(g: dict, on: date) -> bool:
    try:
        return bool(g.get("expires_on")) and date.fromisoformat(str(g["expires_on"])[:10]) <= on
    except ValueError:
        return False


def value_by_year(c: dict, today: date, max_years: int = 100) -> list[float]:
    """A company's vested value 0, 1, 2… years from today at today's share price, as its grants keep vesting, until
    it's all vested (the last entry holds from then on): each grant goes on vesting on its schedule (vested_later), from
    its vesting start or, without one, its grant date. `c` is a company from overview(), with its grants. Options
    that have expired already are worth only the shares exercised from them. Ones that expire later are taken as
    exercised before they do (nobody lets vested options lapse): from then on they're the shares vested by that day,
    net of their exercise price, like shares held."""
    out: list[float] = []
    for k in range(max_years + 1):
        on = _add_months(today, 12 * k)
        total, done = 0.0, True
        for g in c["grants"]:
            vested = g["vested"]   # today's (Carta's, if it reported it)
            if g["kind"] in OPTIONS and _expired(g, today):
                total += (c["share_price"] or 0.0) * min(g.get("exercised") or 0.0, vested)
                continue   # expired already: nothing more to come from it
            # an option that expires by then was exercised before it did: what vested up to that day
            at = date.fromisoformat(str(g["expires_on"])[:10]) if g["kind"] in OPTIONS and _expired(g, on) else on
            if k and not g.get("problem"):   # one whose schedule can't be worked out stays at today's
                vested = max(vested, vested_later(g, at, today))
            done = done and (bool(g.get("problem")) or vested >= (g["quantity"] or 0.0) or at != on)   # expired: settled
            total += value(g, c["share_price"], vested)["vested_value"]
        out.append(round(total, 2))
        if done:
            break
    return out


# ------------------------------------------------------------------------------------------------ editing

_v = validate.Validator(EquityError, drop="", out_of_range="The {label} can't be negative")


def _num(v, name, allow_none=True):
    return _v.number(v, name, low=0, required=not allow_none)


def _day(v, name):
    if v in (None, ""):
        return None
    try:
        d = date.fromisoformat(str(v)[:10])
    except ValueError:
        raise EquityError(f"The {name} must be a date like 2024-03-01") from None
    if not MIN_YEAR <= d.year <= MAX_YEAR:
        raise EquityError(f"The {name} must be between {MIN_YEAR} and {MAX_YEAR}")
    return d.isoformat()


def save_company(conn, body: dict, cid: str | None = None) -> str:
    name = " ".join(str(body.get("name") or "").split())[:80]
    if not name and cid is None:
        raise EquityError("Name the company")
    fields: dict[str, Any] = {}
    if name:
        fields["name"] = name
    if "share_price" in body:
        fields["share_price"] = _num(body.get("share_price"), "share price")
        fields["price_as_of"] = _day(body.get("price_as_of"), "price date") or date.today().isoformat()
    if "in_networth" in body:
        fields["in_networth"] = 1 if body.get("in_networth") else 0
    if cid is None:
        cid = "m" + secrets.token_hex(4)
        conn.orm.add(EquityCompany(id=cid, **fields))   # written at the next query or the commit
        return cid
    company = conn.orm.get(EquityCompany, cid)
    if company is None:
        raise EquityError("Company not found")
    for k, v in fields.items():
        setattr(company, k, v)
    return cid


def remove_company(conn, cid: str) -> None:
    conn.execute(delete(EquityGrant).where(EquityGrant.company_id == cid))
    conn.execute(delete(EquityCompany).where(EquityCompany.id == cid))


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
        if n is not None and n > MAX_MONTHS:
            raise EquityError(f"The {name} can't be more than {MAX_MONTHS} months")
        g[k] = int(n) if n is not None else None
    if kind == "shares":
        g["vest_months"] = g["cliff_months"] = None
    g["vest_every"] = g["vest_every"] or 1
    if g["vest_months"] and (g["cliff_months"] or 0) > g["vest_months"]:
        raise EquityError("The cliff is longer than the vesting")
    return g


def save_grant(conn, company_id: str, body: dict, gid: str | None = None) -> str:
    if not conn.execute(select(EquityCompany.id).where(EquityCompany.id == company_id)).fetchone():
        raise EquityError("Company not found")
    g = clean_grant(body)
    if gid is None:
        gid = "g" + secrets.token_hex(5)
        conn.orm.add(EquityGrant(id=gid, company_id=company_id, **g))
        return gid
    grant = conn.orm.get(EquityGrant, gid)
    if grant is None:
        raise EquityError("Grant not found")
    for k, v in g.items():
        setattr(grant, k, v)
    grant.vested_reported = None   # you changed it: the schedule counts now, not what Carta last said
    return gid


def remove_grant(conn, gid: str) -> None:
    conn.execute(delete(EquityGrant).where(EquityGrant.id == gid))
