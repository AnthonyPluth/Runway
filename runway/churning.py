"""Churning: credit cards opened for their sign-up bonuses, for you and your partner.

For each person it works out:

- 5/24: personal cards opened in the last 24 months, from any bank. Chase won't approve you at 5 or more. Cards
  where you're an authorized user, business cards (most don't report to your personal credit) and product changes
  (the same account under a new name) don't count. Closed cards still do. Each card stops counting 24 months after
  it was opened, so the count goes down on known days.
- When a card's bonus can be earned again, by the issuer's rule (ISSUERS below), or the day you entered instead.
- What's coming up: annual fees (decide to keep, downgrade or close), bonus spending deadlines, your own to-dos,
  5/24 fall-off days and bonuses becoming available again.
- Spending toward a bonus: for a card linked to a Runway account, its purchases since it was opened, counted the way
  Reports counts spending (split transactions by their parts; card payments and transfers left out; refunds lower
  it). Otherwise, what you entered.
- Rewards: points each linked card earned this year, estimated from its spending by category and its earning rates,
  plus balances you entered, valued at what you say a point is worth.
- The best card for a purchase: every open card ranked by what it returns in a category (points per dollar times
  what a point is worth), with a nudge toward a card that still needs spending for its bonus.

Everything here is an estimate or a rule of thumb: the banks don't publish most of these rules, change them, and
decide each application themselves. The page says so.
"""
from __future__ import annotations

import calendar
import re
from datetime import date
from typing import Any

from dateutil.relativedelta import relativedelta
from sqlalchemy import delete, insert, select, update

from . import db, reports, splits
from .models import Account, Category, ChurnBalance, ChurnCard, ChurnCurrency, ChurnRate, ChurnTask

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

# What a point is worth, in cents, until you say otherwise (Settings on the page). Deliberately modest: roughly what
# careful redemptions get, not the best case.
CURRENCIES: dict[str, tuple[str, float]] = {
    "cash": ("Cash back", 1.0),
    "ur": ("Chase Ultimate Rewards", 1.5),
    "mr": ("Amex Membership Rewards", 1.5),
    "ty": ("Citi ThankYou points", 1.4),
    "c1": ("Capital One miles", 1.4),
    "airline": ("Airline miles", 1.2),
    "hotel": ("Hotel points", 0.6),
    "points": ("Other points", 1.0),
}

STATUSES = ("open", "closed", "product_changed")
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


def rate_for(card_rates: dict[str, float], category: str | None, parents: dict[str, str | None], base: float) -> float:
    """Points per dollar in a category: its own rate, else its parent's (a rate on Travel covers Travel > Hotels),
    else the card's base rate."""
    seen: set[str] = set()
    c = category
    while c and c not in seen:
        if c in card_rates:
            return card_rates[c]
        seen.add(c)
        c = parents.get(c)
    return base


def values(conn) -> dict[str, dict]:
    """Every currency and what a point is worth: the defaults, with what you changed, then the ones you added."""
    mine = {r["key"]: r for r in db.rows(conn.execute(select(ChurnCurrency).order_by(ChurnCurrency.name)))}
    out: dict[str, dict] = {}
    for key, (name, cents) in CURRENCIES.items():
        r = mine.get(key)
        out[key] = {"key": key, "name": name, "cents": r["cents"] if r else cents, "default": cents, "custom": False}
    for key, r in mine.items():
        if key not in CURRENCIES:
            out[key] = {"key": key, "name": r["name"], "cents": r["cents"], "default": None, "custom": True}
    return out


def best_cards(cards: list[dict], rates: dict[int, dict[str, float]], vals: dict[str, dict], category: str | None,
               parents: dict[str, str | None], today: date, owner: str | None = None,
               amount: float | None = None) -> list[dict]:
    """Open cards ranked by what they return on a purchase in this category: points per dollar times what a point
    is worth (as cents per dollar, i.e. a percentage). A card still short of its bonus says how much is left."""
    out = []
    for c in cards:
        if (c.get("status") or "open") != "open" or (owner and c["owner"] != owner):
            continue
        mult = rate_for(rates.get(c["id"], {}), category, parents, base_rate(c))
        v = vals.get(c.get("currency") or "cash") or vals["cash"]
        ret = mult * v["cents"]
        item: dict[str, Any] = {"id": c["id"], "owner": c["owner"], "product": c["product"], "issuer": c["issuer"],
                                "multiplier": mult, "currency": v["name"], "cents": v["cents"], "return_pct": round(ret, 2),
                                "value": round((amount or 0) * ret / 100, 2) if amount else None, "bonus": None}
        if c.get("_state") == "active":
            item["bonus"] = {"remaining": round(max(0.0, (c.get("bonus_spend") or 0) - (c.get("_spent") or 0)), 2),
                             "deadline": c["_deadline"], "amount": c.get("bonus"), "currency": v["name"]}
        out.append(item)
    out.sort(key=lambda x: (-x["return_pct"], x["bonus"] is None, x["product"].lower()))
    return out


# ------------------------------------------------------------------------------------------------ from the database

def load(conn) -> dict:
    """The cards, rates, tasks, points values and balances, as stored."""
    cards = db.rows(conn.execute(select(ChurnCard).order_by(ChurnCard.owner, ChurnCard.opened_on, ChurnCard.id)))
    rates: dict[int, dict[str, float]] = {}
    for r in conn.execute(select(ChurnRate.card_id, ChurnRate.category, ChurnRate.multiplier)
                          .order_by(ChurnRate.card_id, ChurnRate.category)):
        rates.setdefault(r["card_id"], {})[r["category"]] = r["multiplier"]
    tasks = db.rows(conn.execute(select(ChurnTask).order_by(ChurnTask.due_on, ChurnTask.id)))
    balances = db.rows(conn.execute(select(ChurnBalance).order_by(ChurnBalance.owner, ChurnBalance.currency)))
    return {"cards": cards, "rates": rates, "tasks": tasks, "values": values(conn), "balances": balances}


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
    cards, rates, vals = d["cards"], d["rates"], d["values"]
    parents = {r["name"]: r["parent"] for r in conn.execute(select(Category.name, Category.parent))}
    year_start = date(today.year, 1, 1)
    since = min([c["opened_on"] for c in cards if c.get("account_id")] + [year_start.isoformat()])
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
        # This year's points, estimated from the linked account's spending (only while the card was this one).
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
        out_cards.append({
            **{k: val for k, val in c.items() if not k.startswith("_")},
            "rates": [{"category": k, "multiplier": m} for k, m in sorted(rates.get(c["id"], {}).items())],
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
            "rates": rates, "parents": parents, "raw_cards": cards,
            "tasks": [{**t, "product": by_id[t["card_id"]]["product"] if t["card_id"] in by_id else None,
                       "owner": by_id[t["card_id"]]["owner"] if t["card_id"] in by_id else None} for t in d["tasks"]],
            "balances": d["balances"]}


def upcoming(s: dict, today: date, horizon: int = HORIZON_DAYS) -> list[dict]:
    """Dates worth knowing, soonest first: annual fees, bonus deadlines, to-dos (overdue ones too), 5/24 fall-offs
    and bonuses available again. `warn`: needs a decision soon."""
    end = today + relativedelta(days=horizon)
    items: list[dict] = []
    said: set[tuple] = set()

    def add(day: str, kind: str, c: dict | None, title: str, detail: str, warn: bool, **extra) -> None:
        items.append({"date": day, "kind": kind, "card_id": c["id"] if c else None, "owner": c["owner"] if c else extra.get("owner"),
                      "title": title, "detail": detail, "warn": warn, **{k: v for k, v in extra.items() if k != "owner"}})

    for c in s["cards"]:
        if c["fee_due"] and date.fromisoformat(c["fee_due"]) <= end:
            days = (date.fromisoformat(c["fee_due"]) - today).days
            add(c["fee_due"], "fee", c, f"{c['product']} annual fee ${c['annual_fee']:,.0f}",
                "Keep it, downgrade or close before it posts?", days <= FEE_WARN_DAYS)
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
    for t in s["tasks"]:
        if t["done"] or date.fromisoformat(t["due_on"]) > end:
            continue
        add(t["due_on"], "task", None, t["action"], t["product"] or "", date.fromisoformat(t["due_on"]) <= today + relativedelta(days=7),
            owner=t["owner"], card_id=t["card_id"], task_id=t["id"])
    for owner, f in s["five24"].items():
        for step in f["timeline"]:
            if date.fromisoformat(step["date"]) <= end:
                card = next(c for c in f["cards"] if c["falls_off"] == step["date"])
                add(step["date"], "five24", None, f"{owner} at {step['count']}/24",
                    f"{card['product']} (opened {card['opened_on']}) stops counting", False, owner=owner)
    order = {"task": 0, "fee": 1, "bonus": 2, "five24": 3, "eligible": 4}
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
        rows = []
        for cur, row in by_cur.items():
            cents = (vals.get(cur) or vals["cash"])["cents"]
            row["earned"], row["bonuses"] = round(row["earned"]), round(row["bonuses"])
            row["cents"] = cents
            row["value"] = round((row["earned"] + row["bonuses"]) * cents / 100, 2)
            row["balance_value"] = round(row["balance"] * cents / 100, 2) if row["balance"] is not None else None
            rows.append(row)
        rows.sort(key=lambda r: -(r["value"] + (r["balance_value"] or 0)))
        out[owner] = {"currencies": rows, "value": round(sum(r["value"] for r in rows), 2),
                      "balance_value": round(sum(r["balance_value"] or 0 for r in rows), 2)}
    return out


def overview(conn, today: date, people: list[str] | None = None) -> dict:
    s = state(conn, today, people)
    return {"today": s["today"], "people": s["people"], "cards": s["cards"], "five24": s["five24"],
            "upcoming": upcoming(s, today), "rewards": rewards(s), "tasks": s["tasks"],
            "currencies": list(s["values"].values()),
            "issuers": [{"key": k, "name": v["name"], "rule": v["rule"]} for k, v in ISSUERS.items()],
            "accounts": db.rows(conn.execute(
                select(Account.id, db.account_label_expr().label("name"), Account.owner)
                .where(Account.kind == "credit").order_by(Account.hidden, db.account_label_expr())))}


def best(conn, today: date, category: str | None, owner: str | None = None, amount: float | None = None) -> list[dict]:
    s = state(conn, today)
    return best_cards(s["raw_cards"], s["rates"], s["values"], category, s["parents"], today, owner, amount)


# ------------------------------------------------------------------------------------------------ notifications

def alerts(conn, today: date, fees: bool, bonuses: bool) -> list[dict]:
    """Push alerts (runway/notify.py): an annual fee within 30 days, a bonus deadline within 14 days with spending
    left. Keyed by card and date, so each is sent once."""
    if not (fees or bonuses):
        return []
    if not conn.execute(select(ChurnCard.id).limit(1)).fetchone():
        return []
    out = []
    for c in state(conn, today)["cards"]:
        if fees and c["fee_due"] and (date.fromisoformat(c["fee_due"]) - today).days <= FEE_WARN_DAYS:
            out.append({"key": f"churnfee:{c['id']}:{c['fee_due']}",
                        "title": f"{c['product']} annual fee on {date.fromisoformat(c['fee_due']):%b %-d}",
                        "body": f"${c['annual_fee']:,.0f} ({c['owner']}). Keep it, downgrade or close?", "url": "/#churning"})
        if bonuses and c["bonus_state"] == "active" and (date.fromisoformat(c["deadline"]) - today).days <= BONUS_WARN_DAYS:
            left = max(0.0, (c.get("bonus_spend") or 0) - (c["spent"] or 0))
            out.append({"key": f"churnbonus:{c['id']}:{c['deadline']}",
                        "title": f"${left:,.0f} to spend on {c['product']} by {date.fromisoformat(c['deadline']):%b %-d}",
                        "body": f"For {c['owner']}'s sign-up bonus.", "url": "/#churning"})
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


def _owner(v) -> str:
    owner = _text(v, "person", 40, required=True)
    assert owner is not None
    if owner.lower() == "joint":
        raise ChurnError("A card belongs to one person (the one whose credit it's on)")
    return owner


def save_card(conn, body: dict, card_id: int | None = None) -> int:
    """Add a card, or change the fields given of one."""
    new = card_id is None
    fields: dict[str, Any] = {}
    if new or "owner" in body:
        fields["owner"] = _owner(body.get("owner"))
    if new or "issuer" in body:
        issuer = str(body.get("issuer") or "").strip()
        if issuer not in ISSUERS:
            raise ChurnError("Pick the bank")
        fields["issuer"] = issuer
    if new or "product" in body:
        fields["product"] = _text(body.get("product"), "card's name", 80, required=True)
    if "family" in body:
        fields["family"] = _text(body.get("family"), "family", 60)
    for key, label, limit in (("earn_note", "earning note", 200), ("notes", "notes", 2000)):
        if key in body:
            fields[key] = _text(body.get(key), label, limit)
    if new or "opened_on" in body:
        fields["opened_on"] = _date(body.get("opened_on"), "day it was opened", required=True)
    for key, label in (("closed_on", "day it was closed"), ("bonus_deadline", "bonus deadline"),
                       ("bonus_earned_on", "day the bonus posted"), ("eligible_on", "eligible-again day")):
        if key in body:
            fields[key] = _date(body.get(key), label)
    if "status" in body or new:
        status = str(body.get("status") or "open")
        if status not in STATUSES:
            raise ChurnError("Status must be open, closed or product changed")
        fields["status"] = status
    for key in ("authorized_user", "business"):
        if key in body or new:
            fields[key] = 1 if body.get(key) in (True, 1, "1", "true", "on") else 0
    if "annual_fee" in body or new:
        fields["annual_fee"] = _num(body.get("annual_fee"), "annual fee", 0, 10000) or 0.0
    if "fee_month" in body:
        fields["fee_month"] = _int(body.get("fee_month"), "fee month", 1, 12)
    if "currency" in body or new:
        cur = str(body.get("currency") or "cash")
        if cur not in values(conn):
            raise ChurnError("Pick what the card earns")
        fields["currency"] = cur
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
        return int(conn.execute(insert(ChurnCard).values(**fields)).lastrowid)
    for k, v in fields.items():
        setattr(card, k, v)
    return int(card.id)


def remove_card(conn, card_id: int) -> None:
    conn.execute(delete(ChurnRate).where(ChurnRate.card_id == card_id))
    conn.execute(delete(ChurnTask).where(ChurnTask.card_id == card_id))
    conn.execute(update(ChurnCard).where(ChurnCard.changed_from == card_id).values(changed_from=None))
    conn.execute(delete(ChurnCard).where(ChurnCard.id == card_id))


def set_rate(conn, card_id: int, category: str, multiplier) -> None:
    """What a card earns in a category; no multiplier removes the rate (the base rate applies)."""
    if not conn.execute(select(ChurnCard.id).where(ChurnCard.id == card_id)).fetchone():
        raise ChurnError("Card not found")
    category = (category or "").strip()
    if not conn.execute(select(Category.name).where(Category.name == category)).fetchone():
        raise ChurnError("Pick a category")
    mult = _num(multiplier, "points per dollar", 0, 100)
    if mult is None:
        conn.execute(delete(ChurnRate).where(ChurnRate.card_id == card_id, ChurnRate.category == category))
    else:
        db.upsert(conn, ChurnRate, {"card_id": card_id, "category": category, "multiplier": mult}, key=["card_id", "category"])


def save_currency(conn, body: dict) -> str:
    """Set what a point is worth, or add a currency of your own (a name and a value)."""
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
    db.upsert(conn, ChurnCurrency, {"key": key, "name": name, "cents": cents}, key=["key"])
    return key


def remove_currency(conn, key: str) -> None:
    """Forget a value you set (the default applies again), or a currency you added and no card earns."""
    if key not in CURRENCIES:
        used = conn.execute(select(ChurnCard.product).where(ChurnCard.currency == key).limit(1)).fetchone()
        if used:
            raise ChurnError(f"{used['product']} earns it; change that card first")
        conn.execute(delete(ChurnBalance).where(ChurnBalance.currency == key))
    conn.execute(delete(ChurnCurrency).where(ChurnCurrency.key == key))


def set_balance(conn, owner: str, currency: str, points, today: date) -> None:
    """A points balance you entered; nothing removes it."""
    owner = _owner(owner)
    if currency not in values(conn):
        raise ChurnError("Unknown currency")
    n = _num(points, "balance", 0, 1e9)
    if n is None:
        conn.execute(delete(ChurnBalance).where(ChurnBalance.owner == owner, ChurnBalance.currency == currency))
    else:
        db.upsert(conn, ChurnBalance, {"owner": owner, "currency": currency, "points": n, "as_of": today.isoformat()},
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
        fields["done"] = 1 if body.get("done") in (True, 1, "1", "true", "on") else 0
    if new:
        return int(conn.execute(insert(ChurnTask).values(**fields)).lastrowid)
    if not conn.execute(select(ChurnTask.id).where(ChurnTask.id == task_id)).fetchone():
        raise ChurnError("To-do not found")
    if fields:
        conn.execute(update(ChurnTask).where(ChurnTask.id == task_id).values(**fields))
    return int(task_id)  # type: ignore[arg-type]


def remove_task(conn, task_id: int) -> None:
    conn.execute(delete(ChurnTask).where(ChurnTask.id == task_id))
