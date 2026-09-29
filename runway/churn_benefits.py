"""Churning: a card's benefits (lounge access, Uber and airline credits, a free night...) and their use.

For each benefit it works out the current period (a calendar month, quarter, half or year, or the cardmember year that
starts on the day the card was opened), what's left of a credit this period, when it resets (use it or lose it),
and what it's worth a year. A card's net annual fee is its fee less what the benefits you'll actually use are worth.

The presets are generic kinds of benefit, not any card's terms: amounts and periods differ by card and change, so
you fill in your card's.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from dateutil.relativedelta import relativedelta
from sqlalchemy import delete, insert, select

from . import churning, db
from .models import ChurnBenefit, ChurnBenefitUse, ChurnCard

KINDS = {"credit": "Credit", "access": "Access", "status": "Status", "other": "Other"}
# months per period, and periods per year (what a period's credit is worth a year)
PERIODS: dict[str, dict[str, Any]] = {
    "monthly": {"name": "Monthly", "months": 1, "per_year": 12},
    "quarterly": {"name": "Quarterly", "months": 3, "per_year": 4},
    "semiannual": {"name": "Twice a year", "months": 6, "per_year": 2},
    "annual": {"name": "Yearly", "months": 12, "per_year": 1},
    "every_4_years": {"name": "Every 4 years", "months": 48, "per_year": 0.25},
    "one_time": {"name": "Once", "months": None, "per_year": 0},
}
BASES = {"calendar": "Calendar (resets Jan 1, the 1st of the month...)",
         "anniversary": "Cardmember year (resets on the day the card was opened)"}
SHORT_LEAD_DAYS = 14   # remind about a monthly or quarterly credit this long before it resets
LONG_LEAD_DAYS = 30    # ... and anything longer
WARN_DAYS = 7

# Quick-add: kinds of benefit many cards have. Deliberately without amounts (they differ by card, and change).
PRESETS: list[dict[str, Any]] = [
    {"key": "lounge", "name": "Lounge access (Priority Pass or other)", "kind": "access", "period": "annual", "basis": "anniversary"},
    {"key": "lyft", "name": "Lyft credit", "kind": "credit", "period": "monthly", "basis": "calendar"},
    {"key": "instacart", "name": "Instacart credit", "kind": "credit", "period": "monthly", "basis": "calendar"},
    {"key": "blacklane", "name": "Blacklane credit", "kind": "credit", "period": "semiannual", "basis": "calendar"},
    {"key": "uber", "name": "Uber / Uber Eats credit", "kind": "credit", "period": "monthly", "basis": "calendar"},
    {"key": "hotel_credit", "name": "Annual hotel credit", "kind": "credit", "period": "annual", "basis": "anniversary"},
    {"key": "airline_credit", "name": "Airline incidental credit", "kind": "credit", "period": "annual", "basis": "calendar"},
    {"key": "dining", "name": "Dining credit", "kind": "credit", "period": "monthly", "basis": "calendar"},
    {"key": "streaming", "name": "Streaming credit", "kind": "credit", "period": "monthly", "basis": "calendar"},
    {"key": "global_entry", "name": "Global Entry / TSA PreCheck credit", "kind": "credit", "period": "every_4_years",
     "basis": "anniversary"},
    {"key": "travel_credit", "name": "Travel credit", "kind": "credit", "period": "annual", "basis": "anniversary"},
    {"key": "free_night", "name": "Free night certificate", "kind": "other", "period": "annual", "basis": "anniversary"},
    {"key": "companion", "name": "Companion pass or certificate", "kind": "other", "period": "annual", "basis": "anniversary"},
]
PRESET_KEYS = {p["key"]: p for p in PRESETS}


# ------------------------------------------------------------------------------------------------ periods

def period(benefit: dict, opened_on: str, day: date) -> tuple[date, date | None]:
    """The period `day` falls in: its first and last day (None: a one-time benefit without an expiry).

    Calendar: months, quarters and halves of the calendar year (every 4 years: counted from Jan 1 of the year the card
    was opened). Cardmember year: counted from the day the card was opened, each start worked out from that day (so a
    card opened on Jan 31 or Feb 29 resets on the month's last day where there's no such day, and back on the 31st
    or the 29th when there is)."""
    opened = date.fromisoformat(opened_on)
    kind = benefit.get("period") or "annual"
    months = PERIODS.get(kind, PERIODS["annual"])["months"]
    if months is None:
        return opened, date.fromisoformat(benefit["expires_on"]) if benefit.get("expires_on") else None
    if (benefit.get("basis") or "calendar") == "anniversary":
        anchor = opened
    else:
        anchor = date(opened.year if months > 12 else day.year, 1, 1)
    k = max(0, ((day.year - anchor.year) * 12 + day.month - anchor.month) // months)
    while k > 0 and churning.add_months(anchor, k * months) > day:
        k -= 1
    return churning.add_months(anchor, k * months), churning.add_months(anchor, (k + 1) * months) - relativedelta(days=1)


def lead_days(benefit: dict) -> int:
    if benefit.get("remind_days") is not None:
        return int(benefit["remind_days"])
    return SHORT_LEAD_DAYS if benefit.get("period") in ("monthly", "quarterly") else LONG_LEAD_DAYS


def is_credit(benefit: dict) -> bool:
    return (benefit.get("kind") or "credit") == "credit" and bool(benefit.get("amount"))


def value_per_year(benefit: dict) -> float:
    """What it's worth a year: your own figure, else a credit's amount times its periods a year (a one-time
    benefit: nothing a year). Access and the like are worth only what you say."""
    if benefit.get("annual_value") is not None:
        return float(benefit["annual_value"])
    if not is_credit(benefit):
        return 0.0
    return float(benefit["amount"]) * PERIODS.get(benefit.get("period") or "annual", PERIODS["annual"])["per_year"]


def summarize(benefit: dict, card: dict, uses: list[dict], today: date) -> dict:
    """A benefit as the page shows it: this period, what's used and left, and whether to remind you now."""
    start, end = period(benefit, card["opened_on"], today)
    mine = [u for u in uses if u["period_start"] == start.isoformat()]
    used = None
    remaining = None
    if is_credit(benefit):
        if any(u["amount_used"] is None for u in mine):
            used = float(benefit["amount"])
        else:
            used = round(sum(u["amount_used"] for u in mine), 2)
        remaining = round(max(0.0, float(benefit["amount"]) - used), 2)
    lead = lead_days(benefit)
    days_left = (end - today).days if end else None
    expiring = bool(benefit.get("active", 1) and remaining and remaining > churning.CENT and days_left is not None
                    and 0 <= days_left <= lead and (card.get("status") or "open") == "open")
    return {**{k: v for k, v in benefit.items() if k != "created_at"},
            "period_start": start.isoformat(), "period_end": end.isoformat() if end else None,
            "used": used, "used_count": len(mine), "remaining": remaining, "value_per_year": round(value_per_year(benefit), 2),
            "lead_days": lead, "days_left": days_left, "expiring": expiring,
            "remind_now": expiring and bool(benefit.get("remind", 1)),
            "uses": [{"id": u["id"], "amount_used": u["amount_used"], "used_on": u["used_on"]} for u in mine]}


def load(conn) -> tuple[dict[int, list[dict]], dict[int, list[dict]]]:
    """Every benefit by card, and every use by benefit."""
    by_card: dict[int, list[dict]] = {}
    for b in db.rows(conn.execute(select(ChurnBenefit).order_by(ChurnBenefit.card_id, ChurnBenefit.name, ChurnBenefit.id))):
        by_card.setdefault(b["card_id"], []).append(b)
    uses: dict[int, list[dict]] = {}
    for u in db.rows(conn.execute(select(ChurnBenefitUse).order_by(ChurnBenefitUse.used_on, ChurnBenefitUse.id))):
        uses.setdefault(u["benefit_id"], []).append(u)
    return by_card, uses


def for_card(card: dict, benefits: list[dict], uses: dict[int, list[dict]], today: date) -> dict:
    """A card's benefits, what the ones you'll use are worth a year, and its annual fee less that."""
    out = [summarize(b, card, uses.get(b["id"], []), today) for b in benefits]
    counted = round(sum(b["value_per_year"] for b in out if b.get("active", 1) and b.get("counts", 1)), 2)
    fee = float(card.get("annual_fee") or 0)
    return {"benefits": out, "benefits_value": counted, "net_fee": round(fee - counted, 2)}


def money(x: float) -> str:
    return f"${x:,.0f}" if abs(x - round(x)) < churning.CENT else f"${x:,.2f}"


def upcoming(cards: list[dict], today: date) -> list[dict]:
    """Upcoming items (churning.upcoming's shape): a credit with money left that resets soon."""
    items = []
    for c in cards:
        if c.get("hide_upcoming"):
            continue
        for b in c.get("benefits", []):
            if not b["remind_now"]:
                continue
            end = date.fromisoformat(b["period_end"])
            items.append({"date": b["period_end"], "kind": "benefit", "card_id": c["id"], "owner": c["owner"],
                          "title": f"Use your {money(b['amount'])} {b['name']} by {end:%b %-d} ({money(b['remaining'])} left)",
                          "detail": c["product"], "warn": b["days_left"] <= WARN_DAYS, "benefit_id": b["id"],
                          "remaining": b["remaining"]})
    return items


def alerts(cards: list[dict]) -> list[dict]:
    """Push alerts: once per benefit and period, when its reminder starts."""
    out = []
    for c in cards:
        if c.get("hide_upcoming"):
            continue
        for b in c.get("benefits", []):
            if b["remind_now"]:
                end = date.fromisoformat(b["period_end"])
                out.append({"key": f"churnbenefit:{b['id']}:{b['period_end']}",
                            "title": f"{money(b['remaining'])} of {b['name']} left on {c['product']}",
                            "body": f"Use it by {end:%b %-d} or lose it ({c['owner']}).", "url": "/#churning"})
    return out


# ------------------------------------------------------------------------------------------------ changes

def _flag(v) -> int:
    return 1 if v in (True, 1, "1", "true", "on") else 0


def save(conn, body: dict, card_id: int | None = None, benefit_id: int | None = None) -> int:
    """Add a benefit to a card (a preset's name, kind and period filled in unless given), or change the fields given
    of one."""
    new = benefit_id is None
    current = None
    if not new:
        current = conn.orm.get(ChurnBenefit, benefit_id)
        if current is None:
            raise churning.ChurnError("Benefit not found")
    preset = None
    if new:
        if card_id is None or not conn.execute(select(ChurnCard.id).where(ChurnCard.id == card_id)).fetchone():
            raise churning.ChurnError("Card not found")
        if body.get("preset"):
            preset = PRESET_KEYS.get(str(body["preset"]))
            if preset is None:
                raise churning.ChurnError("Unknown preset")
        body = {**{k: v for k, v in (preset or {}).items() if k != "key"}, **{k: v for k, v in body.items() if v is not None}}
    f: dict[str, Any] = {}
    if new:
        f["card_id"] = card_id
        f["preset"] = preset["key"] if preset else None
    if new or "name" in body:
        f["name"] = churning._text(body.get("name"), "benefit's name", 80, required=True)
    if new or "kind" in body:
        kind = str(body.get("kind") or "credit")
        if kind not in KINDS:
            raise churning.ChurnError("The kind is credit, access, status or other")
        f["kind"] = kind
    if new or "period" in body:
        per = str(body.get("period") or "annual")
        if per not in PERIODS:
            raise churning.ChurnError("Pick how often it resets")
        f["period"] = per
    if new or "basis" in body:
        basis = str(body.get("basis") or "calendar")
        if basis not in BASES:
            raise churning.ChurnError("It resets on the calendar or the cardmember year")
        f["basis"] = basis
    for key, label in (("amount", "amount"), ("annual_value", "value a year")):
        if key in body:
            f[key] = churning._num(body.get(key), label, 0, 100000)
    for key in ("counts", "remind", "active"):
        if new or key in body:
            f[key] = _flag(body.get(key, 1))
    if "remind_days" in body:
        f["remind_days"] = churning._int(body.get("remind_days"), "days ahead to remind you", 0, 365)
    if "expires_on" in body:
        f["expires_on"] = churning._date(body.get("expires_on"), "day it expires")
    if "notes" in body:
        f["notes"] = churning._text(body.get("notes"), "notes", 500)
    # A credit without an amount yet is allowed: it isn't counted or reminded about until it has one.
    if new:
        return int(conn.execute(insert(ChurnBenefit).values(**f)).lastrowid)
    assert current is not None
    for k, v in f.items():
        setattr(current, k, v)
    return int(current.id)


def remove(conn, benefit_id: int) -> None:
    conn.execute(delete(ChurnBenefitUse).where(ChurnBenefitUse.benefit_id == benefit_id))
    conn.execute(delete(ChurnBenefit).where(ChurnBenefit.id == benefit_id))


def remove_for_card(conn, card_id: int) -> None:
    ids = select(ChurnBenefit.id).where(ChurnBenefit.card_id == card_id)
    conn.execute(delete(ChurnBenefitUse).where(ChurnBenefitUse.benefit_id.in_(ids)))
    conn.execute(delete(ChurnBenefit).where(ChurnBenefit.card_id == card_id))


def _benefit_and_card(conn, benefit_id: int) -> tuple[dict, dict]:
    row = conn.execute(select(ChurnBenefit).where(ChurnBenefit.id == benefit_id)).fetchone()
    if not row:
        raise churning.ChurnError("Benefit not found")
    b = dict(row)
    card = conn.execute(select(ChurnCard.id, ChurnCard.opened_on, ChurnCard.status, ChurnCard.product, ChurnCard.owner,
                               ChurnCard.annual_fee).where(ChurnCard.id == b["card_id"])).fetchone()
    if not card:
        raise churning.ChurnError("Card not found")
    return b, dict(card)


def use(conn, benefit_id: int, body: dict, today: date) -> int:
    """Mark a benefit used in the period of `used_on` (today unless given): `amount` dollars of a credit (the rest
    of the period's credit unless given), or simply used, for one without an amount. Several uses in a period add up."""
    b, card = _benefit_and_card(conn, benefit_id)
    used_on = churning._date(body.get("used_on"), "day it was used") or today.isoformat()
    if used_on < card["opened_on"]:
        raise churning.ChurnError("It can't be used before the card was opened")
    start, _end = period(b, card["opened_on"], date.fromisoformat(used_on))
    amount = churning._num(body.get("amount"), "amount used", 0, 100000)
    if is_credit(b):
        uses = db.rows(conn.execute(select(ChurnBenefitUse.amount_used).where(
            ChurnBenefitUse.benefit_id == benefit_id, ChurnBenefitUse.period_start == start.isoformat())))
        left = 0.0 if any(u["amount_used"] is None for u in uses) else \
            round(float(b["amount"]) - sum(u["amount_used"] for u in uses), 2)
        if left <= churning.CENT:
            raise churning.ChurnError("It's all used this period already")
        if amount is None:
            amount = left
        elif amount > left + churning.CENT:
            raise churning.ChurnError(f"Only {money(left)} is left this period")
    return int(conn.execute(insert(ChurnBenefitUse).values(benefit_id=benefit_id, period_start=start.isoformat(),
                                                           amount_used=amount, used_on=used_on)).lastrowid)


def unuse(conn, benefit_id: int, body: dict, today: date) -> None:
    """Undo a use: the one given (`use_id`), else the latest in the current period."""
    b, card = _benefit_and_card(conn, benefit_id)
    use_id = churning._int(body.get("use_id"), "use", 1, 2**31 - 1)
    q = select(ChurnBenefitUse.id).where(ChurnBenefitUse.benefit_id == benefit_id)
    if use_id is None:
        start, _end = period(b, card["opened_on"], today)
        q = q.where(ChurnBenefitUse.period_start == start.isoformat())
    else:
        q = q.where(ChurnBenefitUse.id == use_id)
    row = conn.execute(q.order_by(ChurnBenefitUse.used_on.desc(), ChurnBenefitUse.id.desc()).limit(1)).fetchone()
    if not row:
        raise churning.ChurnError("Nothing to undo")
    conn.execute(delete(ChurnBenefitUse).where(ChurnBenefitUse.id == row["id"]))

