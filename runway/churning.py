"""Churning: credit cards opened for their sign-up bonuses, for you and your partner.

For each person it works out:

- 5/24: personal cards opened in the last 24 months, from any bank. Chase won't approve you at 5 or more. Cards
  where you're an authorized user, business cards (most don't report to your personal credit) and product changes
  (the same account under a new name) don't count. Closed cards still do. Each card stops counting 24 months after
  it was opened, so the count goes down on known days.
- When a card's bonus can be earned again, by the issuer's rule (ISSUERS below), or the day you entered instead.
- What's coming up: annual fees (decide to keep, downgrade or close, unless you already have: PLANS), the plan you
  made for a card (downgrade the AAdvantage card before its fee, with a Done action), bonus spending deadlines,
  credits about to reset (runway/churn_benefits.py), your own to-dos, 5/24 fall-off days and bonuses becoming
  available again, and when you can apply for a card or bonus you planned (runway/churn_wishlist.py). A card can be
  left out of all of it.
- Spending toward a bonus: for a card linked to a Runway account, its purchases since it was opened, counted the way
  Reports counts spending (split transactions by their parts; card payments and transfers left out; refunds lower
  it). Otherwise, what you entered.
- Rewards: points each linked card earned this year, estimated from its spending by category and its earning rates,
  plus balances you entered, valued at what you say a point is worth.
- The best card for a purchase: every open card ranked by what it returns in a category (points per dollar times
  what a point is worth), with a nudge toward a card that still needs spending for its bonus, and a rate earned
  only through the issuer's travel portal offered as an option (or counted, if you say you'll book there).

Everything here is an estimate or a rule of thumb: the banks don't publish most of these rules, change them, and
decide each application themselves. The page says so.
"""
from __future__ import annotations

import calendar
import re
from datetime import date
from typing import Any

from dateutil.relativedelta import relativedelta
from sqlalchemy import delete, func, insert, select, update

from . import bank_bonuses, churn_benefits, churn_wishlist, db, reports, splits
from .models import (Account, Category, ChurnBalance, ChurnBankBonus, ChurnCard, ChurnCurrency, ChurnRate, ChurnTask,
                     ChurnScore, ChurnWish, User)

# Each issuer's bonus rule, as commonly reported by the churning community (Doctor of Credit, r/churning data
# points). They are not published policy, they change, and offers carry their own terms: the page shows them as a
# rule of thumb to confirm with the issuer. `months`: a bonus on the same card family again this long after the last
# one; `lifetime`: once per lifetime per card (family); `five24`: the issuer won't approve you at 5/24 or more;
# `hold`: not while you still hold a card in the family.
ISSUERS: dict[str, dict[str, Any]] = {
    "chase": {"name": "Chase", "months": 48, "five24": True, "hold": True,
              "rule": "Approval needs you under 5/24. A bonus again 48 months after the last one on the same family "
                      "(every Sapphire is one family), and not while you hold a card in that family."},
    "amex": {"name": "Amex", "lifetime": True,
             "rule": "Once per lifetime per card, when the offer has lifetime language (most do; some targeted offers "
                     "don't). Amex can also decline a bonus at application with its pop-up."},
    "citi": {"name": "Citi", "months": 48,
             "rule": "48 months since the last bonus on the same card, on most cards; the exact wording varies by card."},
    "capital_one": {"name": "Capital One", "months": 48,
                    "rule": "No published rule; waiting about 48 months since the last bonus on the same card is a "
                            "common guideline."},
    "bank_of_america": {"name": "Bank of America", "months": 24,
                        "rule": "Not if you've had the same card in the last 24 months, per most offers' terms."},
    "barclays": {"name": "Barclays", "months": 24,
                 "rule": "Not if you've had a bonus on the same card in the last 24 months, per most offers' terms."},
    "us_bank": {"name": "US Bank", "months": 24,
                "rule": "No published rule; 24 months since the last bonus on the same card is a common guideline."},
    "wells_fargo": {"name": "Wells Fargo", "months": 15,
                    "rule": "Offers usually exclude you if you opened a Wells Fargo card in the last 15 months."},
    "discover": {"name": "Discover", "lifetime": True,
                 "rule": "New cardmembers only: once per lifetime per card."},
    "other": {"name": "Other", "months": 24,
              "rule": "No rule known; 24 months since the last bonus on the same card is assumed."},
}

# What a point is worth, in cents, until you say otherwise (Settings on the page). ESTIMATES: roughly the
# community-consensus values travelers reported around VALUES_AS_OF (published valuations vary, and none is
# official), rounded down a little so the page doesn't promise the best case. No bank, Plaid included, reports what
# a point is worth, and scraping the sites that publish valuations is against their terms, so these are data you
# edit: every value can be changed per currency, and the page says which are estimates and which are yours. Revisit
# them (and VALUES_AS_OF) now and then.
VALUES_AS_OF = "2026-09"
CURRENCY_KINDS: dict[str, str] = {"bank": "Bank points", "airline": "Airline miles", "hotel": "Hotel points",
                                  "cash": "Cash back", "other": "Other"}
CURRENCIES: dict[str, dict[str, Any]] = {
    "cash": {"name": "Cash back", "kind": "cash", "cents": 1.0},
    "ur": {"name": "Chase Ultimate Rewards", "kind": "bank", "cents": 1.5},
    "mr": {"name": "Amex Membership Rewards", "kind": "bank", "cents": 1.5},
    "ty": {"name": "Citi ThankYou points", "kind": "bank", "cents": 1.4},
    "c1": {"name": "Capital One miles", "kind": "bank", "cents": 1.4},
    "bilt": {"name": "Bilt Rewards", "kind": "bank", "cents": 1.5},
    "aa": {"name": "American AAdvantage", "kind": "airline", "cents": 1.4},
    "united": {"name": "United MileagePlus", "kind": "airline", "cents": 1.3},
    "delta": {"name": "Delta SkyMiles", "kind": "airline", "cents": 1.2},
    "southwest": {"name": "Southwest Rapid Rewards", "kind": "airline", "cents": 1.4},
    "jetblue": {"name": "JetBlue TrueBlue", "kind": "airline", "cents": 1.3},
    "alaska": {"name": "Alaska Mileage Plan (Atmos Rewards)", "kind": "airline", "cents": 1.5},
    "aeroplan": {"name": "Air Canada Aeroplan", "kind": "airline", "cents": 1.5},
    "avios": {"name": "British Airways Avios", "kind": "airline", "cents": 1.4},
    # The generic keys cards used before the programs were listed: kept, so those cards and your values still work.
    "airline": {"name": "Other airline miles", "kind": "airline", "cents": 1.2},
    "marriott": {"name": "Marriott Bonvoy", "kind": "hotel", "cents": 0.8},
    "hilton": {"name": "Hilton Honors", "kind": "hotel", "cents": 0.5},
    "hyatt": {"name": "World of Hyatt", "kind": "hotel", "cents": 1.7},
    "ihg": {"name": "IHG One Rewards", "kind": "hotel", "cents": 0.6},
    "hotel": {"name": "Other hotel points", "kind": "hotel", "cents": 0.6},
    "points": {"name": "Other points", "kind": "other", "cents": 1.0},
}
ESTIMATE_NOTE = (f"Estimate as of {VALUES_AS_OF}: roughly what travelers commonly report getting, not an official or "
                 "verified value. Change it to match how you redeem.")

STATUSES = ("open", "closed", "product_changed")
# What you mean to do about a card before its annual fee. undecided: Upcoming asks (keep, downgrade or close?);
# keep: the fee date is shown, without the question; the others: a reminder to do it, with a Done action.
PLANS = ("undecided", "keep", "downgrade", "close", "product_change")
PLAN_REMIND_DAYS = 14
PLAN_WARN_DAYS = 7        # a plan's reminder turns urgent this close to its day
BASE_MARKER = "*"         # in a card's rates list: its base rate (everything without a rate of its own)
FEE_WARN_DAYS = 30        # an annual fee is worth deciding about this long ahead
BONUS_WARN_DAYS = 14      # a bonus deadline with spending left is urgent from here
HORIZON_DAYS = 180        # how far ahead Upcoming looks
CENT = 0.005


class ChurnError(ValueError):
    pass


# ------------------------------------------------------------------------------------------------ dates

def add_months(d: date, months: int) -> date:
    """The same day `months` later; the 31st (or Feb 29) becomes the month's last day where there's no such day."""
    return d + relativedelta(months=months)


def _day(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


def _on(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


# ------------------------------------------------------------------------------------------------ one card

def family_key(card: dict) -> str:
    return (card.get("family") or card.get("product") or "").strip().lower()


def counts_toward_524(card: dict) -> bool:
    return not card.get("authorized_user") and not card.get("business") and not card.get("changed_from")


def falls_off(card: dict) -> date:
    """The day a card stops counting toward 5/24."""
    return add_months(date.fromisoformat(card["opened_on"]), 24)


def next_fee(card: dict, today: date) -> date | None:
    """The next annual fee: on the anniversary of opening (in the fee month, if you set one), from today on. None for
    a card without a fee, or one that's closed."""
    if not card.get("annual_fee") or (card.get("status") or "open") != "open":
        return None
    opened = date.fromisoformat(card["opened_on"])
    month = card.get("fee_month") or opened.month
    for year in range(max(opened.year, today.year - 1), today.year + 2):
        d = _on(year, month, opened.day)
        if d > opened and d >= today:
            return d
    return None


def has_bonus(card: dict) -> bool:
    return bool(card.get("bonus") or card.get("bonus_spend"))


def deadline(card: dict) -> date | None:
    """The last day to spend for the bonus."""
    if not has_bonus(card):
        return None
    if card.get("bonus_deadline"):
        return date.fromisoformat(card["bonus_deadline"])
    return add_months(date.fromisoformat(card["opened_on"]), card.get("bonus_months") or 3)


def bonus_state(card: dict, spent: float, today: date) -> str | None:
    """None (no bonus), earned, met (spent enough, waiting for it to post), active (still spending) or missed."""
    if not has_bonus(card):
        return None
    if card.get("bonus_earned_on"):
        return "earned"
    if spent >= (card.get("bonus_spend") or 0) - CENT:
        return "met"
    end = deadline(card)
    if (card.get("status") or "open") != "open" or (end is not None and end < today):
        return "missed"
    return "active"


# ------------------------------------------------------------------------------------------------ 5/24

def five24(cards: list[dict], owner: str, today: date) -> dict:
    """A person's 5/24: the cards that count, when each falls off, and when they'll be under 5 again."""
    counted = sorted(({"id": c["id"], "product": c["product"], "issuer": c["issuer"], "opened_on": c["opened_on"],
                       "falls_off": falls_off(c).isoformat()}
                      for c in cards if c["owner"] == owner and counts_toward_524(c) and falls_off(c) > today),
                     key=lambda c: (c["falls_off"], c["id"]))
    n = len(counted)
    # Under 5 again once all but 4 have fallen off: the (n-4)th to go.
    under_on = counted[n - 5]["falls_off"] if n >= 5 else None
    return {"owner": owner, "count": n, "under": n < 5, "under_on": under_on, "cards": counted,
            "next_fall_off": counted[0]["falls_off"] if counted else None,
            # the count after each fall-off: "at 4/24 on 2027-03-01"
            "timeline": [{"date": c["falls_off"], "count": n - 1 - i} for i, c in enumerate(counted)]}


# ------------------------------------------------------------------------------------------------ bonus again

def eligibility(card: dict, cards: list[dict], today: date, f524: dict | None = None) -> dict:
    """When this card's bonus can be earned again by the same person, by the issuer's rule of thumb.

    status: now | later (on a date) | never (once per lifetime, already earned) | in_progress (being earned now) |
    held (not while you hold a card in the family; `on` is the soonest it could be once you don't) | au (an
    authorized user doesn't earn the bonus)."""
    rule = ISSUERS.get(card["issuer"], ISSUERS["other"])
    base = {"rule": rule["rule"], "issuer": rule["name"], "override": False}
    if card.get("eligible_on"):
        on = date.fromisoformat(card["eligible_on"])
        return {**base, "status": "now" if on <= today else "later", "on": on.isoformat(), "override": True,
                "why": "The date you entered"}
    if card.get("authorized_user"):
        return {**base, "status": "au", "on": None, "why": "As an authorized user, the bonus is the cardholder's"}
    fam = family_key(card)
    same = [c for c in cards if c["owner"] == card["owner"] and c["issuer"] == card["issuer"] and family_key(c) == fam]
    if not any(c["id"] == card["id"] for c in same):
        same.append(card)
    if any(c.get("_state") == "active" or c.get("_state") == "met" for c in same):
        return {**base, "status": "in_progress", "on": None, "why": "The current bonus is still being earned"}
    earned = sorted(c["bonus_earned_on"] for c in same if c.get("bonus_earned_on"))
    name = card.get("family") or card["product"]
    on, why = today, []
    if earned:
        last = date.fromisoformat(earned[-1])
        if rule.get("lifetime"):
            return {**base, "status": "never", "on": None,
                    "why": f"Once per lifetime, and the {name} bonus was earned on {last.isoformat()}"}
        on = max(on, add_months(last, rule["months"]))
        why.append(f"{rule['months']} months after the last {name} bonus ({last.isoformat()})")
    if rule.get("five24") and f524 and not f524["under"] and f524["under_on"]:
        on = max(on, date.fromisoformat(f524["under_on"]))
        why.append(f"under 5/24 on {f524['under_on']}")
    held = [c["product"] for c in same if (c.get("status") or "open") == "open"]
    if rule.get("hold") and held:
        return {**base, "status": "held", "on": on.isoformat(),
                "why": "Not while you hold " + ", ".join(held) + ("; then " + " and ".join(why) if why else "")}
    return {**base, "status": "now" if on <= today else "later", "on": on.isoformat(),
            "why": "; ".join(why) if why else "No bonus on this card family yet"}


# ------------------------------------------------------------------------------------------------ earning

def base_rate(card: dict) -> float:
    return 1.0 if card.get("base_rate") is None else float(card["base_rate"])


def lookup_rate(card_rates: dict[str, float], category: str | None, parents: dict[str, str | None]) -> float | None:
    """A category's own rate, else its parent's (a rate on Travel covers Travel > Hotels); None without either."""
    seen: set[str] = set()
    c = category
    while c and c not in seen:
        if c in card_rates:
            return card_rates[c]
        seen.add(c)
        c = parents.get(c)
    return None


def rate_for(card_rates: dict[str, float], category: str | None, parents: dict[str, str | None], base: float) -> float:
    """Points per dollar in a category: its own rate, else its parent's, else the card's base rate."""
    r = lookup_rate(card_rates, category, parents)
    return base if r is None else r


def values(conn) -> dict[str, dict]:
    """Every currency and what a point is worth: the defaults (estimates), with what you changed, then the ones you
    added. `overridden`: a default you set a value of your own for; `estimate`: the value is still our estimate."""
    mine = {r["key"]: r for r in db.rows(conn.execute(select(ChurnCurrency).order_by(ChurnCurrency.name)))}
    out: dict[str, dict] = {}
    for key, cur in CURRENCIES.items():
        r = mine.get(key)
        cash = cur["kind"] == "cash"
        out[key] = {"key": key, "name": cur["name"], "kind": cur["kind"], "cents": r["cents"] if r else cur["cents"],
                    "default": cur["cents"], "custom": False, "overridden": r is not None,
                    "estimate": r is None and not cash, "as_of": None if cash else VALUES_AS_OF,
                    "source_note": ("Cash back: a point is a cent." if cash and r is None else
                                    f"Your value (the estimate is {cur['cents']:g}¢ as of {VALUES_AS_OF})." if r else
                                    ESTIMATE_NOTE)}
    for key, r in mine.items():
        if key not in CURRENCIES:
            out[key] = {"key": key, "name": r["name"], "kind": r.get("kind") or "other", "cents": r["cents"],
                        "default": None, "custom": True, "overridden": False, "estimate": False, "as_of": None,
                        "source_note": "A currency you added, at your value."}
    return out


def currency_groups(vals: dict[str, dict]) -> list[dict]:
    """The currencies by kind (bank points, airline miles, hotel points, cash back, other), for a grouped picker."""
    return [{"kind": k, "label": label, "keys": [v["key"] for v in vals.values() if v["kind"] == k]}
            for k, label in CURRENCY_KINDS.items() if any(v["kind"] == k for v in vals.values())]


def best_cards(cards: list[dict], rates: dict[int, dict[str, float]], vals: dict[str, dict], category: str | None,
               parents: dict[str, str | None], today: date, owner: str | None = None,
               amount: float | None = None, portal_rates: dict[int, dict[str, float]] | None = None,
               portal: bool = False) -> list[dict]:
    """Open cards ranked by what they return on a purchase in this category: points per dollar times what a point
    is worth (as cents per dollar, i.e. a percentage). A card still short of its bonus says how much is left.

    A rate earned only through the issuer's portal (Venture X's 10x hotels through Capital One Travel) isn't the
    card's rate for the category unless `portal` (you'll book through the portal): by default the card ranks at its
    normal rate and `portal_option` offers the better one; with `portal`, the better of the two counts and
    `needs_portal` says so."""
    out = []
    for c in cards:
        if (c.get("status") or "open") != "open" or (owner and c["owner"] != owner):
            continue
        v = vals.get(c.get("currency") or "cash") or vals["cash"]
        normal = rate_for(rates.get(c["id"], {}), category, parents, base_rate(c))
        via = lookup_rate((portal_rates or {}).get(c["id"], {}), category, parents)
        where = c.get("portal_name") or "the issuer's portal"
        option = None
        if via is not None and via > normal:
            option = {"multiplier": via, "return_pct": round(via * v["cents"], 2), "portal_name": c.get("portal_name"),
                      "value": round((amount or 0) * via * v["cents"] / 100, 2) if amount else None,
                      "note": f"{via:g}x if booked through {where}"}
        use_portal = bool(portal and option)
        mult = via if use_portal and via is not None else normal
        ret = mult * v["cents"]
        item: dict[str, Any] = {"id": c["id"], "owner": c["owner"], "product": c["product"], "issuer": c["issuer"],
                                "multiplier": mult, "currency": v["name"], "cents": v["cents"], "return_pct": round(ret, 2),
                                "value": round((amount or 0) * ret / 100, 2) if amount else None, "bonus": None,
                                "needs_portal": use_portal, "portal_name": c.get("portal_name"),
                                "portal_option": None if use_portal else option,
                                "note": (f"Only when booked through {where}" if use_portal else
                                         option["note"] if option else None)}
        if c.get("_state") == "active":
            item["bonus"] = {"remaining": round(max(0.0, (c.get("bonus_spend") or 0) - (c.get("_spent") or 0)), 2),
                             "deadline": c["_deadline"], "amount": c.get("bonus"), "currency": v["name"]}
        out.append(item)
    # Ties: a card short of its bonus first, then one that doesn't need the portal (less hassle), then by name.
    out.sort(key=lambda x: (-x["return_pct"], x["bonus"] is None, x["needs_portal"], x["product"].lower()))
    return out


# ------------------------------------------------------------------------------------------------ from the database

def load(conn) -> dict:
    """The cards, rates (normal, and portal-only), tasks, points values and balances, as stored."""
    cards = db.rows(conn.execute(select(ChurnCard).order_by(ChurnCard.owner, ChurnCard.opened_on, ChurnCard.id)))
    rates: dict[int, dict[str, float]] = {}
    portal: dict[int, dict[str, float]] = {}
    for r in conn.execute(select(ChurnRate.card_id, ChurnRate.category, ChurnRate.multiplier, ChurnRate.portal_only)
                          .order_by(ChurnRate.card_id, ChurnRate.category, ChurnRate.portal_only)):
        (portal if r["portal_only"] else rates).setdefault(r["card_id"], {})[r["category"]] = r["multiplier"]
    tasks = db.rows(conn.execute(select(ChurnTask).order_by(ChurnTask.due_on, ChurnTask.id)))
    balances = db.rows(conn.execute(select(ChurnBalance).order_by(ChurnBalance.owner, ChurnBalance.currency)))
    return {"cards": cards, "rates": rates, "portal_rates": portal, "tasks": tasks, "values": values(conn),
            "balances": balances}


# ------------------------------------------------------------------------------------------------ plans

def plan_active(card: dict) -> bool:
    """A plan to act on (downgrade, close, product change), not yet done, for a card still open."""
    return (card.get("plan") in ("downgrade", "close", "product_change") and not card.get("plan_done_on")
            and (card.get("status") or "open") == "open")


def plan_day(card: dict, today: date) -> date | None:
    """The day to act on the plan by: the one you set, else the day before the next annual fee (so it's done before
    you're charged). None without either."""
    if card.get("plan_date"):
        return date.fromisoformat(card["plan_date"])
    fee = next_fee(card, today)
    return fee - relativedelta(days=1) if fee else None


def plan_title(card: dict) -> str:
    target = f" to {card['plan_target']}" if card.get("plan_target") else ""
    return {"downgrade": f"Downgrade {card['product']}{target}", "close": f"Close {card['product']}",
            "product_change": f"Product change {card['product']}{target}"}.get(card.get("plan") or "", card["product"])


def remind_days(card: dict) -> int:
    return PLAN_REMIND_DAYS if card.get("plan_remind_days") is None else int(card["plan_remind_days"])


def _spending(conn, cards: list[dict], since: str, today: date) -> list[dict]:
    """The spending (Reports' way) on the accounts these cards are linked to, since a day: each part of a split
    transaction on its own, as {account_id, posted, amount (positive = spent), category}."""
    ids = sorted({c["account_id"] for c in cards if c.get("account_id")})
    if not ids:
        return []
    p = splits.parts()
    kinds = reports._Kinds(conn)
    rows = db.rows(conn.execute(select(p.c.account_id, p.c.posted, p.c.amount, p.c.category)
                                .where(p.c.account_id.in_(ids), p.c.posted >= since, p.c.posted <= today.isoformat())))
    return [{**r, "amount": -r["amount"]} for r in rows if kinds.kind(r) == "spend"]


def state(conn, today: date, people: list[str] | None = None) -> dict:
    """Everything the page and the notifications need, worked out for today."""
    d = load(conn)
    cards, rates, portal, vals = d["cards"], d["rates"], d["portal_rates"], d["values"]
    benefits, uses = churn_benefits.load(conn)
    parents = {r["name"]: r["parent"] for r in conn.execute(select(Category.name, Category.parent))}
    year_start = date(today.year, 1, 1)
    # Back to the earliest balance's day too: a balance is topped up with the points earned since it was entered.
    since = min([c["opened_on"] for c in cards if c.get("account_id")] + [b["as_of"] for b in d["balances"] if b.get("as_of")]
                + [year_start.isoformat()])
    spending = _spending(conn, cards, since, today)
    for c in cards:
        c["_deadline"] = (deadline(c) or today).isoformat() if has_bonus(c) else None
        end = min(filter(None, [c["_deadline"], c.get("closed_on"), today.isoformat()]))
        linked = [s for s in spending if s["account_id"] == c.get("account_id")]
        if c.get("account_id"):
            c["_spent"] = round(sum(s["amount"] for s in linked if c["opened_on"] <= s["posted"] <= end), 2)
        else:
            c["_spent"] = round(c.get("manual_spend") or 0, 2)
        c["_state"] = bonus_state(c, c["_spent"], today)
        # This year's points, estimated from the linked account's spending (only while the card was this one), at
        # the card's normal rates: a transaction doesn't say whether it was booked through the issuer's portal, so
        # portal-only rates aren't assumed.
        start = max(year_start.isoformat(), c["opened_on"])
        stop = min(c.get("closed_on") or today.isoformat(), today.isoformat())
        base = base_rate(c)
        pts = sum(s["amount"] * rate_for(rates.get(c["id"], {}), s["category"], parents, base)
                  for s in linked if start <= s["posted"] <= stop) if c.get("account_id") else None
        c["_points"] = round(pts) if pts is not None else None

    owners = list(dict.fromkeys([*(people or []), *(c["owner"] for c in cards)]))
    f524 = {o: five24(cards, o, today) for o in owners}
    out_cards = []
    for c in cards:
        v = vals.get(c.get("currency") or "cash") or vals["cash"]
        fee = next_fee(c, today)
        pday = plan_day(c, today)
        out_cards.append({
            **{k: val for k, val in c.items() if not k.startswith("_")},
            "rates": sorted([{"category": k, "multiplier": m, "portal_only": False} for k, m in rates.get(c["id"], {}).items()]
                            + [{"category": k, "multiplier": m, "portal_only": True} for k, m in portal.get(c["id"], {}).items()],
                            key=lambda r: (r["category"], r["portal_only"])),
            "plan": c.get("plan") or "undecided",
            "plan_remind_days": remind_days(c),
            "plan_due": pday.isoformat() if pday else None,
            "plan_active": plan_active(c),
            "hide_upcoming": 1 if c.get("hide_upcoming") else 0,
            **churn_benefits.for_card(c, benefits.get(c["id"], []), uses, today),
            "points_note": ("Estimated at the card's normal rates; bookings through the portal earn more"
                            if portal.get(c["id"]) and c.get("account_id") else None),
            "currency_name": v["name"],
            "cents": v["cents"],
            "bonus_value": round((c.get("bonus") or 0) * v["cents"] / 100, 2) if c.get("bonus") else None,
            "fee_due": fee.isoformat() if fee else None,
            "deadline": c["_deadline"],
            "spent": c["_spent"] if has_bonus(c) or c.get("account_id") else None,
            "spend_source": "account" if c.get("account_id") else "manual",
            "bonus_state": c["_state"],
            "counts_524": counts_toward_524(c) and falls_off(c) > today,
            "falls_off": falls_off(c).isoformat(),
            "eligibility": eligibility(c, cards, today, f524.get(c["owner"])),
            "points_ytd": c["_points"],
            "value_ytd": round(c["_points"] * v["cents"] / 100, 2) if c["_points"] is not None else None,
        })
    by_id = {c["id"]: c for c in out_cards}
    return {"today": today.isoformat(), "people": owners, "cards": out_cards, "five24": f524, "values": vals,
            "rates": rates, "portal_rates": portal, "parents": parents, "raw_cards": cards,
            "tasks": [{**t, "product": by_id[t["card_id"]]["product"] if t["card_id"] in by_id else None,
                       "owner": by_id[t["card_id"]]["owner"] if t["card_id"] in by_id else None} for t in d["tasks"]],
            "balances": d["balances"], "spending": spending}


def earned_since(s: dict, owner: str, currency: str, since: str) -> float:
    """Points a person's cards in a currency earned after a day: their linked accounts' spending at each card's normal
    rates while it was open (portal bookings aren't visible in a transaction, so they aren't assumed), plus bonuses
    that posted after it."""
    total = 0.0
    today = s["today"]
    for c in s["raw_cards"]:
        if c["owner"] != owner or (c.get("currency") or "cash") != currency:
            continue
        base = base_rate(c)
        stop = min(c.get("closed_on") or today, today)
        if c.get("account_id"):
            total += sum(x["amount"] * rate_for(s["rates"].get(c["id"], {}), x["category"], s["parents"], base)
                         for x in s["spending"] if x["account_id"] == c["account_id"] and since < x["posted"] <= stop
                         and x["posted"] >= c["opened_on"])
        if (c.get("bonus_earned_on") or "") > since:
            total += c.get("bonus") or 0
    return round(total)


def upcoming(s: dict, today: date, horizon: int = HORIZON_DAYS) -> list[dict]:
    """Dates worth knowing, soonest first: annual fees, what you planned to do about a card, bonus deadlines,
    credits about to reset, to-dos (overdue ones too, snoozed ones not until the snooze ends), 5/24 fall-offs and
    bonuses available again. `warn`: needs a decision (or doing) soon.

    A card you hid from Upcoming says nothing here (its to-dos, which you wrote yourself, still do). An annual fee
    asks "keep, downgrade or close?" only while you haven't decided: a card you're keeping just shows the date, and
    one with a plan shows the plan, whose own reminder starts plan_remind_days before its day."""
    end = today + relativedelta(days=horizon)
    items: list[dict] = []
    said: set[tuple] = set()

    def add(day: str, kind: str, c: dict | None, title: str, detail: str, warn: bool, **extra) -> None:
        items.append({"date": day, "kind": kind, "card_id": c["id"] if c else None, "owner": c["owner"] if c else extra.get("owner"),
                      "title": title, "detail": detail, "warn": warn, **{k: v for k, v in extra.items() if k != "owner"}})

    for c in s["cards"]:
        if c.get("hide_upcoming"):
            continue
        if c["fee_due"] and date.fromisoformat(c["fee_due"]) <= end:
            days = (date.fromisoformat(c["fee_due"]) - today).days
            plan = c.get("plan") or "undecided"
            if plan == "keep":
                detail, warn = "You're keeping it", False
            elif c["plan_active"]:
                detail, warn = f"Your plan: {plan_title(c)} before it posts", False
            else:
                detail, warn = "Keep it, downgrade or close before it posts?", days <= FEE_WARN_DAYS
            add(c["fee_due"], "fee", c, f"{c['product']} annual fee ${c['annual_fee']:,.0f}", detail, warn, plan=plan)
        if c["plan_active"] and c["plan_due"]:
            due = date.fromisoformat(c["plan_due"])
            days = (due - today).days
            if days <= c["plan_remind_days"]:   # from the reminder's start, and overdue until you check it off
                add(c["plan_due"], "plan", c, f"{plan_title(c)} by {due:%b %-d}",
                    f"Before the ${c['annual_fee']:,.0f} annual fee posts" if c["fee_due"] and c["annual_fee"]
                    else "Check it off when it's done", days <= PLAN_WARN_DAYS, plan=c["plan"])
        if c["bonus_state"] == "active" and c["deadline"] and date.fromisoformat(c["deadline"]) <= end:
            left = max(0.0, (c.get("bonus_spend") or 0) - (c["spent"] or 0))
            days = (date.fromisoformat(c["deadline"]) - today).days
            add(c["deadline"], "bonus", c, f"Spend ${left:,.0f} more on {c['product']}",
                f"${c['spent'] or 0:,.0f} of ${c.get('bonus_spend') or 0:,.0f} for the bonus", days <= BONUS_WARN_DAYS,
                spent=c["spent"], need=c.get("bonus_spend"))
        e = c["eligibility"]
        once = (c["owner"], c["issuer"], family_key(c), e["on"])   # cards in one family share the date: say it once
        if e["status"] == "later" and date.fromisoformat(e["on"]) <= end and once not in said:
            said.add(once)
            add(e["on"], "eligible", c, f"{c.get('family') or c['product']} bonus available again",
                f"{e['issuer']} rule of thumb: confirm before applying", False)
    items += churn_benefits.upcoming(s["cards"], today)
    for t in s["tasks"]:
        if t["done"] or date.fromisoformat(t["due_on"]) > end or (t.get("snooze_until") or "") > today.isoformat():
            continue
        add(t["due_on"], "task", None, t["action"], t["product"] or "", date.fromisoformat(t["due_on"]) <= today + relativedelta(days=7),
            owner=t["owner"], card_id=t["card_id"], task_id=t["id"])
    for owner, f in s["five24"].items():
        for step in f["timeline"]:
            if date.fromisoformat(step["date"]) <= end:
                card = next(c for c in f["cards"] if c["falls_off"] == step["date"])
                add(step["date"], "five24", None, f"{owner} at {step['count']}/24",
                    f"{card['product']} (opened {card['opened_on']}) stops counting", False, owner=owner)
    order = {"task": 0, "plan": 1, "fee": 2, "bonus": 3, "benefit": 4, "five24": 5, "eligible": 6}
    items.sort(key=lambda i: (i["date"], order[i["kind"]], i["title"]))
    return items


def rewards(s: dict) -> dict:
    """Per person: points this year (estimated) by currency, bonuses earned this year, and balances you entered,
    each with what it's worth."""
    vals = s["values"]
    year = s["today"][:4]
    out: dict[str, dict] = {}
    for owner in s["people"]:
        by_cur: dict[str, dict] = {}
        for c in s["cards"]:
            if c["owner"] != owner:
                continue
            cur = c.get("currency") or "cash"
            row = by_cur.setdefault(cur, {"currency": cur, "name": c["currency_name"], "earned": 0.0, "bonuses": 0.0, "balance": None})
            row["earned"] += c["points_ytd"] or 0
            if c.get("bonus_earned_on", "") and c["bonus_earned_on"][:4] == year:
                row["bonuses"] += c.get("bonus") or 0
        for b in s["balances"]:
            if b["owner"] == owner:
                v = vals.get(b["currency"])
                row = by_cur.setdefault(b["currency"], {"currency": b["currency"], "name": v["name"] if v else b["currency"],
                                                        "earned": 0.0, "bonuses": 0.0, "balance": None})
                row["balance"], row["as_of"] = b["points"], b["as_of"]
                # What it probably is now: the balance you entered plus what the cards earned since (an estimate).
                row["earned_since"] = earned_since(s, owner, b["currency"], b["as_of"]) if b.get("as_of") else 0
        rows = []
        for cur, row in by_cur.items():
            cents = (vals.get(cur) or vals["cash"])["cents"]
            row["earned"], row["bonuses"] = round(row["earned"]), round(row["bonuses"])
            row["cents"] = cents
            row["value"] = round((row["earned"] + row["bonuses"]) * cents / 100, 2)
            row["balance_value"] = round(row["balance"] * cents / 100, 2) if row["balance"] is not None else None
            # Only when the cards earned something since the balance's day; otherwise the balance is the best figure.
            since = row.get("earned_since") or 0
            row["est_balance"] = round(row["balance"] + since) if row["balance"] is not None and since else None
            row["est_value"] = round(row["est_balance"] * cents / 100, 2) if row["est_balance"] is not None else None
            rows.append(row)
        rows.sort(key=lambda r: -(r["value"] + (r["balance_value"] or 0)))
        out[owner] = {"currencies": rows, "value": round(sum(r["value"] for r in rows), 2),
                      "balance_value": round(sum(r["balance_value"] or 0 for r in rows), 2)}
    return out


def overview(conn, today: date, people: list[str] | None = None) -> dict:
    """Everything the page shows. `people` / `owners`: whose cards and bonuses there can be (the people you picked as
    account owners and who signed in, then anyone else who has a card or a bank bonus here): the choices for a
    card's owner."""
    s = state(conn, today, people)
    bank, bank_income = bank_bonuses.overview(conn, today)
    people = list(dict.fromkeys([*s["people"], *(b["owner"] for b in bank)]))
    five = {o: s["five24"].get(o) or five24([], o, today) for o in people}
    horizon = today + relativedelta(days=HORIZON_DAYS)
    wishlist = churn_wishlist.evaluate(conn, s, bank, today)
    soon = upcoming(s, today) + bank_bonuses.upcoming(bank, today, horizon) + churn_wishlist.upcoming(wishlist, today, horizon)
    soon.sort(key=lambda i: (i["date"], i["kind"] != "task", i["title"]))
    return {"today": s["today"], "people": people, "owners": people, "cards": s["cards"], "five24": five,
            "wishlist": wishlist, "scores": churn_wishlist.scores(conn),
            "upcoming": soon, "rewards": rewards(s), "tasks": s["tasks"], "bank": bank, "bank_income": bank_income,
            "currencies": list(s["values"].values()), "currency_groups": currency_groups(s["values"]),
            "values_as_of": VALUES_AS_OF, "values_note": ESTIMATE_NOTE,
            "issuers": [{"key": k, "name": v["name"], "rule": v["rule"]} for k, v in ISSUERS.items()],
            "plans": list(PLANS),
            "categories": spending_categories(conn), "base_marker": BASE_MARKER,
            "benefit_presets": churn_benefits.PRESETS,
            "benefit_kinds": [{"key": k, "name": v} for k, v in churn_benefits.KINDS.items()],
            "benefit_periods": [{"key": k, "name": v["name"]} for k, v in churn_benefits.PERIODS.items()],
            "benefit_bases": [{"key": k, "name": v} for k, v in churn_benefits.BASES.items()],
            "accounts": _accounts(conn, ["credit"]), "bank_accounts": _accounts(conn, ["checking", "savings"])}


def spending_categories(conn) -> list[dict]:
    """Runway's spending categories (not transfers or income), for a card's earning rates: parents first, each
    followed by its subcategories."""
    rows = db.rows(conn.execute(select(Category.name, Category.parent)
                                .where(func.coalesce(Category.is_transfer, 0) == 0, func.coalesce(Category.is_income, 0) == 0)))
    return sorted(rows, key=lambda r: ((r["parent"] or r["name"]).lower(), r["parent"] is not None, r["name"].lower()))


def _accounts(conn, kinds: list[str]) -> list[dict]:
    """Runway accounts of these kinds, to link a card or a bank bonus to (hidden ones last)."""
    return db.rows(conn.execute(select(Account.id, db.account_label_expr().label("name"), Account.owner)
                                .where(Account.kind.in_(kinds)).order_by(Account.hidden, db.account_label_expr())))


def best(conn, today: date, category: str | None, owner: str | None = None, amount: float | None = None,
         portal: bool = False) -> list[dict]:
    s = state(conn, today)
    return best_cards(s["raw_cards"], s["rates"], s["values"], category, s["parents"], today, owner, amount,
                      s["portal_rates"], portal)


# ------------------------------------------------------------------------------------------------ notifications

# The push alerts Churning sends, each with its switch in Settings > Notifications (notify.DEFAULTS has the keys).
ALERT_PREFS = [
    {"key": "churn_fee", "label": "A churning card's annual fee is due within 30 days (unless you're keeping it or have a plan for it)"},
    {"key": "churn_bonus", "label": "A sign-up bonus deadline (card or bank) is within 14 days, with requirements left"},
    {"key": "churn_plan", "label": "It's time to downgrade, close or change a card, as you planned"},
    {"key": "churn_benefit", "label": "A card credit with money left is about to reset"},
    {"key": "churn_apply", "label": "A card or bank bonus you planned has nothing in the way now, or its offer ends within 14 days"},
]


def alerts(conn, today: date, fees: bool, bonuses: bool, plans: bool = False, benefits: bool = False,
           apply: bool = False) -> list[dict]:
    """Push alerts (runway/notify.py): an annual fee within 30 days you haven't decided about, a bonus deadline
    within 14 days with spending left, a plan's reminder, a credit about to reset with money left, a planned card or
    bonus you can apply for now (or whose offer ends soon). Nothing for a card hidden from Upcoming. Keyed by card
    (or benefit, or plan) and date, so each is sent once."""
    if not (fees or bonuses or plans or benefits or apply):
        return []
    bank = bank_bonuses.overview(conn, today)[0] if bonuses or apply else []
    out = bank_bonuses.alerts(bank, today) if bonuses else []
    s = state(conn, today)
    if apply and conn.execute(select(ChurnWish.id).limit(1)).fetchone():
        out += churn_wishlist.alerts(churn_wishlist.evaluate(conn, s, bank, today), today)
    if not conn.execute(select(ChurnCard.id).limit(1)).fetchone():
        return out
    cards = s["cards"]
    for c in cards:
        if c.get("hide_upcoming"):
            continue
        undecided = (c.get("plan") or "undecided") == "undecided"
        if fees and undecided and c["fee_due"] and (date.fromisoformat(c["fee_due"]) - today).days <= FEE_WARN_DAYS:
            out.append({"key": f"churnfee:{c['id']}:{c['fee_due']}",
                        "title": f"{c['product']} annual fee on {date.fromisoformat(c['fee_due']):%b %-d}",
                        "body": f"${c['annual_fee']:,.0f} ({c['owner']}). Keep it, downgrade or close?", "url": "/#churning"})
        if bonuses and c["bonus_state"] == "active" and (date.fromisoformat(c["deadline"]) - today).days <= BONUS_WARN_DAYS:
            left = max(0.0, (c.get("bonus_spend") or 0) - (c["spent"] or 0))
            out.append({"key": f"churnbonus:{c['id']}:{c['deadline']}",
                        "title": f"${left:,.0f} to spend on {c['product']} by {date.fromisoformat(c['deadline']):%b %-d}",
                        "body": f"For {c['owner']}'s sign-up bonus.", "url": "/#churning"})
        if plans and c["plan_active"] and c["plan_due"] and \
                (date.fromisoformat(c["plan_due"]) - today).days <= c["plan_remind_days"]:
            due = date.fromisoformat(c["plan_due"])
            fee = f", before the ${c['annual_fee']:,.0f} annual fee posts" if c["fee_due"] and c["annual_fee"] else ""
            out.append({"key": f"churnplan:{c['id']}:{c['plan_due']}", "title": f"{plan_title(c)} by {due:%b %-d}",
                        "body": f"As {c['owner']} planned{fee}. Check it off on the Churning page when it's done.",
                        "url": "/#churning"})
    if benefits:
        out += churn_benefits.alerts(cards)
    return out


# ------------------------------------------------------------------------------------------------ changes

def _text(v, label: str, limit: int, required: bool = False) -> str | None:
    s = str(v or "").strip()
    if required and not s:
        raise ChurnError(f"Enter the {label}")
    if len(s) > limit:
        raise ChurnError(f"The {label} is too long (at most {limit} characters)")
    return s or None


def _date(v, label: str, required: bool = False) -> str | None:
    s = str(v or "").strip()
    if not s:
        if required:
            raise ChurnError(f"Enter the {label}")
        return None
    try:
        return date.fromisoformat(s).isoformat()
    except ValueError:
        raise ChurnError(f"The {label} must be a date (YYYY-MM-DD)") from None


def _num(v, label: str, low: float = 0, high: float = 1e9) -> float | None:
    if v in (None, ""):
        return None
    try:
        n = db.number(str(v).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        raise ChurnError(f"The {label} must be a number") from None
    if not low <= n <= high:
        raise ChurnError(f"The {label} must be between {low:g} and {high:g}")
    return n


def _int(v, label: str, low: int, high: int) -> int | None:
    n = _num(v, label, low, high)
    if n is not None and n != int(n):
        raise ChurnError(f"The {label} must be a whole number")
    return None if n is None else int(n)


def known_owners(conn) -> list[str]:
    """Everyone a card or bonus could belong to: who signed in, the accounts' owners (a partner who never signs in
    included), and whoever already has a card, a bank bonus, a plan or a score here. Never "Joint"."""
    names = [r[0] for r in conn.execute(select(User.first_name).where(User.first_name.is_not(None)).order_by(User.last_seen))]
    for col in (Account.owner, ChurnCard.owner, ChurnBankBonus.owner, ChurnWish.owner, ChurnScore.owner):
        names += [r[0] for r in conn.execute(select(col).distinct().where(col.is_not(None), col != "").order_by(col))]
    return [n for n in dict.fromkeys(names) if n and n.lower() != "joint"]


def _owner(v, conn=None) -> str:
    """A person's name, spelled the way it's already known (as an account's owner or a signed-in first name), so
    "alex" and "Alex" are one person."""
    owner = _text(v, "person", 40, required=True)
    assert owner is not None
    if owner.lower() == "joint":
        raise ChurnError("A card belongs to one person (the one whose credit it's on)")
    if conn is not None:
        owner = next((n for n in known_owners(conn) if n.lower() == owner.lower()), owner)
    return owner


def _flag(v) -> int:
    return 1 if v in (True, 1, "1", "true", "on") else 0


def _rates(conn, value) -> tuple[list[dict], float | None]:
    """A card's earning rates as a form sends them ([{category, multiplier, portal_only}]), checked: each category
    one of Runway's (or BASE_MARKER, the card's base rate), each multiplier 0-100, no category twice. Returns the
    rates, and the base rate if the list gave one."""
    if not isinstance(value, list):
        raise ChurnError("The earning rates must be a list")
    out: list[dict] = []
    base = None
    seen: set[tuple[str, int]] = set()
    names = {r[0] for r in conn.execute(select(Category.name))}
    for r in value:
        if not isinstance(r, dict):
            raise ChurnError("Each earning rate needs a category and points per dollar")
        category = str(r.get("category") or "").strip()
        portal = _flag(r.get("portal_only"))
        if not category:
            raise ChurnError("Pick a category for each earning rate")
        if category != BASE_MARKER and category not in names:
            raise ChurnError(f"There's no category called {category}")
        label = "everything else" if category == BASE_MARKER else category
        mult = _num(r.get("multiplier"), f"points per dollar on {label}", 0, 100)
        if mult is None:
            raise ChurnError(f"Enter the points per dollar on {label}")
        if category == BASE_MARKER:
            if portal:
                raise ChurnError("The base rate can't be portal-only; add a portal rate for a category instead")
            base = mult
        if (category, portal) in seen:
            raise ChurnError(f"{label} is listed twice" + (" as a portal rate" if portal else ""))
        seen.add((category, portal))
        if category != BASE_MARKER:
            out.append({"category": category, "multiplier": mult, "portal_only": portal})
    return out, base


def save_card(conn, body: dict, card_id: int | None = None) -> int:
    """Add a card, or change the fields given of one."""
    new = card_id is None
    fields: dict[str, Any] = {}
    if new or "owner" in body:
        fields["owner"] = _owner(body.get("owner"), conn)
    if new or "issuer" in body:
        issuer = str(body.get("issuer") or "").strip()
        if issuer not in ISSUERS:
            raise ChurnError("Pick the bank")
        fields["issuer"] = issuer
    if new or "product" in body:
        fields["product"] = _text(body.get("product"), "card's name", 80, required=True)
    if "family" in body:
        fields["family"] = _text(body.get("family"), "family", 60)
    for key, label, limit in (("earn_note", "earning note", 200), ("notes", "notes", 2000),
                              ("portal_name", "portal's name", 60), ("plan_target", "card to change it to", 80)):
        if key in body:
            fields[key] = _text(body.get(key), label, limit)
    if new or "opened_on" in body:
        fields["opened_on"] = _date(body.get("opened_on"), "day it was opened", required=True)
    for key, label in (("closed_on", "day it was closed"), ("bonus_deadline", "bonus deadline"),
                       ("bonus_earned_on", "day the bonus posted"), ("eligible_on", "eligible-again day"),
                       ("plan_date", "day to do it by"), ("plan_done_on", "day it was done")):
        if key in body:
            fields[key] = _date(body.get(key), label)
    if "status" in body or new:
        status = str(body.get("status") or "open")
        if status not in STATUSES:
            raise ChurnError("Status must be open, closed or product changed")
        fields["status"] = status
    for key in ("authorized_user", "business", "hide_upcoming"):
        if key in body or new:
            fields[key] = _flag(body.get(key))
    if "plan" in body or new:
        plan = str(body.get("plan") or "undecided")
        if plan not in PLANS:
            raise ChurnError("The plan is undecided, keep, downgrade, close or product change")
        fields["plan"] = plan
    if "plan_remind_days" in body:
        n = _int(body.get("plan_remind_days"), "days ahead to remind you", 0, 365)
        fields["plan_remind_days"] = PLAN_REMIND_DAYS if n is None else n
    if "annual_fee" in body or new:
        fields["annual_fee"] = _num(body.get("annual_fee"), "annual fee", 0, 10000) or 0.0
    if "fee_month" in body:
        fields["fee_month"] = _int(body.get("fee_month"), "fee month", 1, 12)
    if "currency" in body or new:
        cur = str(body.get("currency") or "cash")
        if cur not in values(conn):
            raise ChurnError("Pick what the card earns")
        fields["currency"] = cur
    rates = None
    if body.get("rates") is not None:
        rates, from_list = _rates(conn, body["rates"])
        if from_list is not None:
            if body.get("base_rate") not in (None, "") and _num(body.get("base_rate"), "base rate", 0, 100) != from_list:
                raise ChurnError("The base rate is given twice, differently")
            body = {**body, "base_rate": from_list}
    if "base_rate" in body or new:
        base = _num(body.get("base_rate"), "base rate", 0, 100)
        fields["base_rate"] = 1.0 if base is None else base
    for key, label, high in (("bonus", "bonus", 1e7), ("bonus_spend", "spending needed", 1e6),
                             ("manual_spend", "spending so far", 1e7)):
        if key in body:
            fields[key] = _num(body.get(key), label, 0, high)
    if "bonus_months" in body or new:
        fields["bonus_months"] = _int(body.get("bonus_months"), "months to spend", 1, 24) or 3
    if "account_id" in body:
        acct = str(body.get("account_id") or "") or None
        if acct and not conn.execute(select(Account.id).where(Account.id == acct, Account.kind == "credit")).fetchone():
            raise ChurnError("Pick one of your credit card accounts")
        fields["account_id"] = acct
    if "changed_from" in body:
        fields["changed_from"] = _int(body.get("changed_from"), "card it was changed from", 1, 2**31 - 1)

    card = None
    if not new:
        card = conn.orm.get(ChurnCard, card_id)
        if card is None:
            raise ChurnError("Card not found")
    merged = {**(db.as_dict(card) if card else {}), **fields}
    if merged.get("closed_on") and merged["closed_on"] < merged["opened_on"]:
        raise ChurnError("It can't be closed before it was opened")
    if merged.get("changed_from"):
        src = conn.execute(select(ChurnCard.owner).where(ChurnCard.id == merged["changed_from"])).fetchone()
        if not src or merged["changed_from"] == card_id or src["owner"] != merged["owner"]:
            raise ChurnError("Pick one of the same person's cards it was changed from")
    if card is None:
        card_id = int(conn.execute(insert(ChurnCard).values(**fields)).lastrowid)
    else:
        for k, v in fields.items():
            setattr(card, k, v)
    if rates is not None:
        # The whole list replaces the card's rates (all of it checked above, before anything was written).
        conn.execute(delete(ChurnRate).where(ChurnRate.card_id == card_id))
        if rates:
            conn.execute(insert(ChurnRate), [{"card_id": card_id, **r} for r in rates])
    return int(card_id)  # type: ignore[arg-type]


def remove_card(conn, card_id: int) -> None:
    conn.execute(delete(ChurnRate).where(ChurnRate.card_id == card_id))
    conn.execute(delete(ChurnTask).where(ChurnTask.card_id == card_id))
    churn_benefits.remove_for_card(conn, card_id)
    conn.execute(update(ChurnCard).where(ChurnCard.plan_new_id == card_id).values(plan_new_id=None))
    conn.execute(update(ChurnCard).where(ChurnCard.changed_from == card_id).values(changed_from=None))
    conn.execute(delete(ChurnCard).where(ChurnCard.id == card_id))


def set_rate(conn, card_id: int, category: str, multiplier, portal_only=False) -> None:
    """What a card earns in a category (only through the issuer's portal, with `portal_only`); no multiplier removes
    the rate (the base rate applies)."""
    if not conn.execute(select(ChurnCard.id).where(ChurnCard.id == card_id)).fetchone():
        raise ChurnError("Card not found")
    category = (category or "").strip()
    if not conn.execute(select(Category.name).where(Category.name == category)).fetchone():
        raise ChurnError("Pick a category")
    mult = _num(multiplier, "points per dollar", 0, 100)
    portal = _flag(portal_only)
    if mult is None:
        conn.execute(delete(ChurnRate).where(ChurnRate.card_id == card_id, ChurnRate.category == category,
                                             ChurnRate.portal_only == portal))
    else:
        db.upsert(conn, ChurnRate, {"card_id": card_id, "category": category, "multiplier": mult, "portal_only": portal},
                  key=["card_id", "category", "portal_only"])


def save_currency(conn, body: dict) -> str:
    """Set what a point is worth, or add a currency of your own (a name, a value, and optionally its kind: bank,
    airline, hotel, cash or other)."""
    key = str(body.get("key") or "").strip()
    known = values(conn)
    cents = _num(body.get("cents"), "value of a point", 0, 100)
    if cents is None:
        raise ChurnError("Enter what a point is worth, in cents")
    if key:
        if key not in known:
            raise ChurnError("Unknown currency")
        name = known[key]["name"] if not known[key]["custom"] else (_text(body.get("name"), "name", 60) or known[key]["name"])
    else:
        name = _text(body.get("name"), "name", 60, required=True) or ""
        key = "x-" + (re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "points")
        if key in known or any(v["name"].lower() == name.lower() for v in known.values()):
            raise ChurnError(f"There's already a currency called {name}")
    kind = None
    if key not in CURRENCIES:
        kind = str(body.get("kind") or (known[key]["kind"] if key in known else "") or "other")
        if kind not in CURRENCY_KINDS:
            raise ChurnError("The kind is bank, airline, hotel, cash or other")
    db.upsert(conn, ChurnCurrency, {"key": key, "name": name, "cents": cents, "kind": kind}, key=["key"])
    return key


def remove_currency(conn, key: str) -> None:
    """Forget a value you set (the default applies again), or a currency you added and no card earns."""
    if key not in CURRENCIES:
        used = conn.execute(select(ChurnCard.product).where(ChurnCard.currency == key).limit(1)).fetchone()
        if used:
            raise ChurnError(f"{used['product']} earns it; change that card first")
        conn.execute(delete(ChurnBalance).where(ChurnBalance.currency == key))
    conn.execute(delete(ChurnCurrency).where(ChurnCurrency.key == key))


def set_balance(conn, owner: str, currency: str, points, today: date, as_of=None) -> None:
    """A points balance you entered, as of a day (today unless given); no number removes it."""
    owner = _owner(owner, conn)
    if currency not in values(conn):
        raise ChurnError("Unknown currency")
    n = _num(points, "balance", 0, 1e9)
    day = _date(as_of, "day of the balance") or today.isoformat()
    if day > today.isoformat():
        raise ChurnError("The balance can't be as of a day in the future")
    if n is None:
        conn.execute(delete(ChurnBalance).where(ChurnBalance.owner == owner, ChurnBalance.currency == currency))
    else:
        db.upsert(conn, ChurnBalance, {"owner": owner, "currency": currency, "points": n, "as_of": day},
                  key=["owner", "currency"])


def save_task(conn, body: dict, task_id: int | None = None) -> int:
    new = task_id is None
    fields: dict[str, Any] = {}
    if new or "card_id" in body:
        cid = _int(body.get("card_id"), "card", 1, 2**31 - 1)
        if cid is None or not conn.execute(select(ChurnCard.id).where(ChurnCard.id == cid)).fetchone():
            raise ChurnError("Pick a card")
        fields["card_id"] = cid
    if new or "due_on" in body:
        fields["due_on"] = _date(body.get("due_on"), "day it's due", required=True)
    if new or "action" in body:
        fields["action"] = _text(body.get("action"), "what to do", 120, required=True)
    if "done" in body or new:
        fields["done"] = _flag(body.get("done"))
    if "snooze_until" in body:
        fields["snooze_until"] = _date(body.get("snooze_until"), "day to snooze it until")
    if new:
        return int(conn.execute(insert(ChurnTask).values(**fields)).lastrowid)
    if not conn.execute(select(ChurnTask.id).where(ChurnTask.id == task_id)).fetchone():
        raise ChurnError("To-do not found")
    if fields:
        conn.execute(update(ChurnTask).where(ChurnTask.id == task_id).values(**fields))
    return int(task_id)  # type: ignore[arg-type]


def remove_task(conn, task_id: int) -> None:
    conn.execute(delete(ChurnTask).where(ChurnTask.id == task_id))


def snooze_task(conn, task_id: int, body: dict, today: date) -> str | None:
    """Leave a to-do out of Upcoming until a day (`until`), or for a number of days (`days`); neither wakes it."""
    days = _int(body.get("days"), "days to snooze it", 1, 365)
    until = _date(body.get("until"), "day to snooze it until")
    if days is not None:
        until = (today + relativedelta(days=days)).isoformat()
    save_task(conn, {"snooze_until": until}, task_id)
    return until


# ------------------------------------------------------------------------------------------------ plan: done, undo

def _plan_result(card: ChurnCard, changes: list[str]) -> dict:
    return {"id": card.id, "plan": card.plan, "plan_done_on": card.plan_done_on, "status": card.status,
            "closed_on": card.closed_on, "new_card_id": card.plan_new_id, "changes": changes}


def plan_done(conn, card_id: int, today: date, on=None) -> dict:
    """Check a card's plan off. Close: the card is closed that day. Downgrade or product change: the card is marked
    product-changed that day and, when you named what it becomes, that card is added (changed from this one, so
    the same account: it doesn't count toward 5/24). Keep: just noted. Returns what changed."""
    card = conn.orm.get(ChurnCard, card_id)
    if card is None:
        raise ChurnError("Card not found")
    plan = card.plan or "undecided"
    if plan == "undecided":
        raise ChurnError("Pick a plan first")
    if card.plan_done_on:
        raise ChurnError("It's already checked off")
    day = _date(on, "day it was done") or today.isoformat()
    if day < card.opened_on:
        raise ChurnError("It can't be done before the card was opened")
    card.plan_done_on = day
    changes = []
    if plan in ("close", "downgrade", "product_change") and (card.status or "open") == "open":
        card.status = "closed" if plan == "close" else "product_changed"
        card.closed_on = day
        changes.append(f"{card.product} is marked closed on {day}" if plan == "close" else
                       f"{card.product} is marked product-changed on {day}")
        if plan != "close" and card.plan_target:
            new_id = int(conn.execute(insert(ChurnCard).values(
                owner=card.owner, issuer=card.issuer, product=card.plan_target, opened_on=day, changed_from=card.id,
                account_id=card.account_id, currency=card.currency, annual_fee=0.0, base_rate=1.0,
                fee_month=card.fee_month or date.fromisoformat(card.opened_on).month, authorized_user=card.authorized_user,
                business=card.business)).lastrowid)
            card.plan_new_id = new_id
            changes.append(f"{card.plan_target} is added, changed from {card.product} (the same account: not a new "
                           "card for 5/24); set its annual fee and earning rates")
    else:
        changes.append("Checked off")
    return _plan_result(card, changes)


def plan_undo(conn, card_id: int) -> dict:
    """Undo checking a plan off: the card is open again (if checking it off closed or changed it) and the card it
    added is removed."""
    card = conn.orm.get(ChurnCard, card_id)
    if card is None:
        raise ChurnError("Card not found")
    if not card.plan_done_on:
        raise ChurnError("It isn't checked off")
    changes = []
    if card.plan in ("close", "downgrade", "product_change") and card.status in ("closed", "product_changed") \
            and card.closed_on == card.plan_done_on:
        card.status, card.closed_on = "open", None
        changes.append(f"{card.product} is open again")
    if card.plan_new_id:
        added = conn.execute(select(ChurnCard.product).where(ChurnCard.id == card.plan_new_id)).fetchone()
        if added:
            remove_card(conn, card.plan_new_id)
            changes.append(f"{added['product']} is removed")
        card.plan_new_id = None
    card.plan_done_on = None
    return _plan_result(card, changes or ["No longer checked off"])
