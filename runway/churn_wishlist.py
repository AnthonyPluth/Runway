"""Churning: the cards and bank bonuses you want next, and what's in the way of applying.

Waiting to get back under 5/24, or for a credit score to improve: each planned card or bank bonus lists its
blockers and the earliest day you could apply, by the same rules the rest of the page uses:

- 5/24, for an issuer whose rule needs you under it (churning.ISSUERS): the day the person is back under 5.
  Optionally (assume_prior_planned) the cards planned before this one count as opened, for that projection.
- The issuer's bonus rule for the card family, from the cards you've had (churning.eligibility), or the bank's rule
  for a bank bonus (bank_bonuses.eligibility, with the rule you entered on the plan).
- Your own "not before" day.
- A credit score you want first, against the latest one you entered: a blocker without a day.
- For a bank bonus: an offer that ends before you could apply.

A bonus still being earned on a card from the same issuer isn't a blocker, just a note. Planned items don't count
toward 5/24 or rank as a best card: only opened ones do. Scores are what you type in; Runway never fetches one.
"""
from __future__ import annotations

import re
import urllib.parse
from datetime import date
from typing import Any

from sqlalchemy import delete, func, insert, select

from . import bank_bonuses, churning, db, validate
from .models import ChurnScore, ChurnWish

KINDS = ("card", "bank_bonus")
STATUSES = ("wanted", "ready", "applied", "dropped")
OPEN = ("wanted", "ready")        # still to apply for
OFFER_WARN_DAYS = 14


# ------------------------------------------------------------------------------------------------ scores

def scores(conn) -> dict[str, dict]:
    """Each person's latest score, with the ones before it (newest first)."""
    out: dict[str, dict] = {}
    for r in db.rows(conn.execute(select(ChurnScore).order_by(ChurnScore.owner, ChurnScore.as_of.desc()))):
        s = out.setdefault(r["owner"], {"owner": r["owner"], "score": r["score"], "as_of": r["as_of"],
                                        "source": r["source"], "history": []})
        s["history"].append({"score": r["score"], "as_of": r["as_of"], "source": r["source"]})
    return out


def set_score(conn, body: dict, today: date) -> None:
    """A score you looked up, for a person and a day (today unless given); no score removes that day's."""
    owner = churning._owner(body.get("owner"), conn)
    as_of = churning._date(body.get("as_of"), "day of the score") or today.isoformat()
    score = churning._int(body.get("score"), "credit score", 300, 900)
    if score is None:
        conn.execute(delete(ChurnScore).where(ChurnScore.owner == owner, ChurnScore.as_of == as_of))
        return
    db.upsert(conn, ChurnScore, {"owner": owner, "as_of": as_of, "score": score,
                                 "source": churning._text(body.get("source"), "source", 60)}, key=["owner", "as_of"])


# ------------------------------------------------------------------------------------------------ blockers

def _pseudo_card(w: dict, opened_on: str) -> dict:
    """A planned card as churning's rules see a card: not held, no bonus yet."""
    return {"id": -w["id"], "owner": w["owner"], "issuer": w.get("issuer") or "other", "product": w.get("product") or "",
            "family": w.get("family"), "opened_on": opened_on, "status": "planned", "business": w.get("business"),
            "authorized_user": 0, "changed_from": None}


def _finish(blockers: list[dict], notes: list[str]) -> dict:
    dated = [b["date"] for b in blockers if b.get("date")]
    return {"blockers": blockers, "hints": notes, "earliest_apply": max(dated) if dated else None, "ready": not blockers}


def _common(w: dict, score: dict | None, today: date, blockers: list[dict], notes: list[str]) -> None:
    if w.get("wait_until") and w["wait_until"] > today.isoformat():
        blockers.append({"kind": "wait", "text": f"You're waiting until {w['wait_until']}", "date": w["wait_until"]})
    if w.get("min_score"):
        if score is None:
            notes.append(f"No credit score entered for {w['owner']} to compare with {w['min_score']}")
        elif score["score"] < w["min_score"]:
            blockers.append({"kind": "score", "date": None,
                             "text": f"Score {score['score']} of {w['min_score']} wanted (as of {score['as_of']})"})


def card_blockers(w: dict, cards: list[dict], today: date, score: dict | None, prior: list[dict] | None = None) -> dict:
    """What's in the way of applying for a planned card. `cards`: churning.state()'s raw cards (with their bonus
    state); `prior`: planned cards to count as opened (for assume_prior_planned)."""
    blockers: list[dict] = []
    notes: list[str] = []
    rule = churning.ISSUERS.get(w.get("issuer") or "other", churning.ISSUERS["other"])
    me = _pseudo_card(w, today.isoformat())
    fam = churning.family_key(me)
    known = [c["eligible_on"] for c in cards if c.get("eligible_on") and c["owner"] == w["owner"]
             and c["issuer"] == me["issuer"] and churning.family_key(c) == fam]
    if known:   # you know better, on a card of the same family: that day
        me["eligible_on"] = max(known)
    if rule.get("five24"):
        f = churning.five24(cards + (prior or []), w["owner"], today)
        if not f["under"]:
            extra = " (counting the cards planned before this one)" if prior else ""
            blockers.append({"kind": "five24", "date": f["under_on"],
                             "text": f"{w['owner']} is at {f['count']}/24{extra}; under 5/24 on {f['under_on']}"})
    # The bonus rule, from the cards you've had. A bonus still being earned doesn't block a new application (the
    # issuer's rule is about bonuses received), so it's left out here and noted below.
    settled = [{**c, "_state": None} for c in cards]
    e = churning.eligibility(me, settled, today)
    if e["status"] == "never":
        blockers.append({"kind": "bonus_rule", "date": None, "text": e["why"]})
    elif e["status"] == "held":
        blockers.append({"kind": "held", "date": None, "text": e["why"]})
    elif e["status"] == "later":
        blockers.append({"kind": "bonus_rule", "date": e["on"], "text": f"{e['issuer']}: {e['why']}"})
    for c in cards:
        if c["owner"] == w["owner"] and c["issuer"] == w.get("issuer") and c.get("_state") == "active":
            left = max(0.0, (c.get("bonus_spend") or 0) - (c.get("_spent") or 0))
            notes.append(f"Still spending toward {c['product']}'s bonus, ${left:,.0f} left")
    _common(w, score, today, blockers, notes)
    return _finish(blockers, notes)


def bank_blockers(w: dict, bonuses: list[dict], today: date, score: dict | None) -> dict:
    """What's in the way of applying for a planned bank bonus. `bonuses`: bank_bonuses.overview()'s."""
    blockers: list[dict] = []
    notes: list[str] = []
    bank = (w.get("bank") or "").strip().lower()
    same = [b for b in bonuses if b["owner"] == w["owner"] and b["bank"].strip().lower() == bank]
    for b in same:
        if (b.get("status") or "open") in ("open", "pending") and not b.get("received_on"):
            notes.append(f"A {b['bank']} bonus is still being earned")
    me = {"id": -w["id"], "owner": w["owner"], "bank": w.get("bank") or "", "status": "planned",
          "repeat_months": w.get("repeat_months"), "once_per_lifetime": w.get("once_per_lifetime")}
    if same and not (me["repeat_months"] or me["once_per_lifetime"]):
        # No rule on the plan: the one you entered on your last bonus from this bank, and its eligible-again day.
        last = max(same, key=lambda b: (b["opened_on"], b["id"]))
        me.update(repeat_months=last.get("repeat_months"), once_per_lifetime=last.get("once_per_lifetime"),
                  eligible_on=last.get("eligible_on"))
    e = bank_bonuses.eligibility(me, [b for b in same if b.get("received_on")], today)
    if e["status"] == "never":
        blockers.append({"kind": "bonus_rule", "date": None, "text": e["why"]})
    elif e["status"] == "later":
        blockers.append({"kind": "bonus_rule", "date": e["on"], "text": e["why"]})
    elif e["status"] == "unknown":
        notes.append("You've had a bonus from this bank: add its rule (months between bonuses) to know when")
    _common(w, score, today, blockers, notes)
    out = _finish(blockers, notes)
    end = w.get("offer_expires_on")
    if end and end < today.isoformat():
        blockers.append({"kind": "offer", "date": None, "text": f"The offer ended on {end}"})
    elif end and out["earliest_apply"] and out["earliest_apply"] > end:
        blockers.append({"kind": "offer", "date": None, "text": f"The offer ends on {end}, before you could apply"})
    return {**out, "ready": not blockers}


def evaluate(conn, s: dict, bonuses: list[dict], today: date) -> list[dict]:
    """Every planned item with its blockers, by priority, then the earliest day you could apply."""
    items = db.rows(conn.execute(select(ChurnWish).order_by(ChurnWish.owner, ChurnWish.priority, ChurnWish.id)))
    latest = scores(conn)
    cards = s["raw_cards"]
    out = []
    prior: dict[str, list[dict]] = {}
    for w in items:
        if (w.get("status") or "wanted") not in OPEN:
            r: dict[str, Any] = {"blockers": [], "hints": [], "earliest_apply": None, "ready": False}
        elif (w.get("kind") or "card") == "card":
            r = card_blockers(w, cards, today, latest.get(w["owner"]),
                              prior.get(w["owner"]) if w.get("assume_prior_planned") else None)
            if not w.get("business"):
                prior.setdefault(w["owner"], []).append(_pseudo_card(w, r["earliest_apply"] or today.isoformat()))
        else:
            r = bank_blockers(w, bonuses, today, latest.get(w["owner"]))
        out.append({**{k: v for k, v in w.items() if k != "created_at"}, **r})
    out.sort(key=lambda x: (x["status"] not in OPEN, x.get("priority") or 10**6, x["earliest_apply"] or "", x["id"]))
    return out


def title(w: dict) -> str:
    return (w.get("product") or "") if (w.get("kind") or "card") == "card" else \
        " ".join(filter(None, [w.get("bank"), w.get("product")]))


def upcoming(wishlist: list[dict], today: date, end: date) -> list[dict]:
    """Upcoming items: when you can apply for a planned card or bonus (today, if you can now), and an offer ending."""
    items = []
    for w in wishlist:
        if (w.get("status") or "wanted") not in OPEN:
            continue
        name = title(w)
        dated = all(b.get("date") for b in w["blockers"])
        if w["ready"]:
            items.append({"date": today.isoformat(), "kind": "apply", "card_id": None, "wish_id": w["id"],
                          "owner": w["owner"], "title": f"You can apply for {name} now", "detail": _detail(w), "warn": False})
        elif dated and w["earliest_apply"] and w["earliest_apply"] <= end.isoformat():
            items.append({"date": w["earliest_apply"], "kind": "apply", "card_id": None, "wish_id": w["id"],
                          "owner": w["owner"], "title": f"You can apply for {name} on {date.fromisoformat(w['earliest_apply']):%b %-d}",
                          "detail": "; ".join(b["text"] for b in w["blockers"]), "warn": False})
        offer = w.get("offer_expires_on")
        if offer and today.isoformat() <= offer <= end.isoformat():
            items.append({"date": offer, "kind": "offer_ends", "card_id": None, "wish_id": w["id"], "owner": w["owner"],
                          "title": f"Offer for {name} ends {date.fromisoformat(offer):%b %-d}", "detail": _detail(w),
                          "warn": (date.fromisoformat(offer) - today).days <= OFFER_WARN_DAYS})
    return items


def _detail(w: dict) -> str:
    return "; ".join(w["hints"])   # (not its priority: that's a place in everyone's plans, not this person's)


def alerts(wishlist: list[dict], today: date) -> list[dict]:
    """Push alerts: once when a planned item has nothing in the way, and when its offer ends within 14 days."""
    out = []
    for w in wishlist:
        if (w.get("status") or "wanted") not in OPEN:
            continue
        name = title(w)
        if w["ready"]:
            out.append({"key": f"churnapply:{w['id']}", "title": f"You can apply for {name}",
                        "body": f"Nothing in the way for {w['owner']} now, by the page's rules of thumb.", "url": "/#churning"})
        offer = w.get("offer_expires_on")
        if offer and 0 <= (date.fromisoformat(offer) - today).days <= OFFER_WARN_DAYS:
            out.append({"key": f"churnoffer:{w['id']}:{offer}", "title": f"Offer for {name} ends {date.fromisoformat(offer):%b %-d}",
                        "body": f"{w['owner']}'s planned {'card' if w.get('kind') == 'card' else 'bank bonus'}.", "url": "/#churning"})
    return out


# ------------------------------------------------------------------------------------------------ changes

def _link(v) -> str | None:
    """The address to apply at: a web address (https, or http), nothing that a link could run (javascript:, data:)."""
    s = churning._text(v, "link", 500)
    if s is None:
        return None
    if not re.fullmatch(r"https?://[^\s<>\"']+", s, re.IGNORECASE) or not urllib.parse.urlsplit(s).hostname:
        raise churning.ChurnError("The link must be a web address starting with https://")
    return s


def save(conn, body: dict, wish_id: int | None = None) -> int:
    """Add a planned card or bank bonus, or change the fields given of one."""
    new = wish_id is None
    row = None
    if not new:
        row = conn.orm.get(ChurnWish, wish_id)
        if row is None:
            raise churning.ChurnError("Planned item not found")
    f: dict[str, Any] = {}
    if new or "owner" in body:
        f["owner"] = churning._owner(body.get("owner"), conn)
    if new:
        kind = str(body.get("kind") or "card")
        if kind not in KINDS:
            raise churning.ChurnError("It's a card or a bank bonus")
        f["kind"] = kind
    kind = f.get("kind") or (row.kind if row else "card")
    if "issuer" in body or (new and kind == "card"):
        issuer = str(body.get("issuer") or "").strip() or None
        if kind == "card" and issuer not in churning.ISSUERS:
            raise churning.ChurnError("Pick the bank")
        f["issuer"] = issuer
    if "product" in body or (new and kind == "card"):
        f["product"] = churning._text(body.get("product"), "card's name", 80, required=kind == "card")
    if "bank" in body or (new and kind == "bank_bonus"):
        f["bank"] = churning._text(body.get("bank"), "bank", 60, required=kind == "bank_bonus")
    for key, label, limit in (("family", "family", 60), ("requirements", "requirements", 300), ("notes", "notes", 2000)):
        if key in body:
            f[key] = churning._text(body.get(key), label, limit)
    if "apply_url" in body:
        f["apply_url"] = _link(body.get("apply_url"))
    for key in ("business", "once_per_lifetime", "assume_prior_planned"):
        if key in body or new:
            f[key] = validate.flag(body.get(key))
    for key, label, high in (("annual_fee", "annual fee", 10000), ("bonus", "bonus", 1e7), ("bonus_spend", "spending needed", 1e6)):
        if key in body:
            f[key] = churning._num(body.get(key), label, 0, high)
    for key, label, low, high in (("bonus_months", "months to spend", 1, 24), ("repeat_months", "months between bonuses", 0, 240),
                                  ("min_score", "credit score wanted", 300, 900), ("priority", "priority", 1, 1000)):
        if key in body:
            f[key] = churning._int(body.get(key), label, low, high)
    for key, label in (("wait_until", "day to wait until"), ("offer_expires_on", "day the offer ends")):
        if key in body:
            f[key] = churning._date(body.get(key), label)
    if "currency" in body:
        cur = str(body.get("currency") or "") or None
        if cur and cur not in churning.values(conn):
            raise churning.ChurnError("Pick what the bonus is paid in")
        f["currency"] = cur
    if "account_type" in body:
        t = str(body.get("account_type") or "") or None
        if t and t not in bank_bonuses.TYPES:
            raise churning.ChurnError("The account is checking, savings or business")
        f["account_type"] = t
    if "status" in body or new:
        st = str(body.get("status") or "wanted")
        if st not in STATUSES:
            raise churning.ChurnError("The status is wanted, ready, applied or dropped")
        f["status"] = st
    if new and f.get("priority") is None:   # last: one order for everyone's plans, so people's turns can alternate
        top = conn.execute(select(func.max(ChurnWish.priority))).scalar()
        f["priority"] = (top or 0) + 1
    if row is None:
        return int(conn.execute(insert(ChurnWish).values(**f)).lastrowid)
    for k, v in f.items():
        setattr(row, k, v)
    return int(row.id)


def remove(conn, wish_id: int) -> None:
    conn.execute(delete(ChurnWish).where(ChurnWish.id == wish_id))


def applied(conn, wish_id: int, body: dict, today: date) -> dict:
    """You applied: the planned card becomes a card (or the bank bonus a bank bonus), opened today unless given,
    with what the plan said; the plan is kept, marked applied, and points to it."""
    w = conn.orm.get(ChurnWish, wish_id)
    if w is None:
        raise churning.ChurnError("Planned item not found")
    if w.status == "applied":
        raise churning.ChurnError("It's already marked applied")
    opened = churning._date(body.get("opened_on"), "day it was opened") or today.isoformat()
    if (w.kind or "card") == "card":
        new_id = churning.save_card(conn, {
            "owner": w.owner, "issuer": w.issuer, "product": w.product, "family": w.family, "business": w.business,
            "annual_fee": w.annual_fee, "bonus": w.bonus, "currency": w.currency or "cash", "bonus_spend": w.bonus_spend,
            "bonus_months": w.bonus_months, "opened_on": opened, "notes": w.notes})
        kind = "card"
    else:
        if w.bonus is None:
            raise churning.ChurnError("Enter the bonus first")
        new_id = bank_bonuses.save(conn, {
            "owner": w.owner, "bank": w.bank, "account_type": w.account_type or "checking", "opened_on": opened,
            "bonus": w.bonus, "other_reqs": w.requirements, "repeat_months": w.repeat_months,
            "once_per_lifetime": w.once_per_lifetime, "notes": w.notes})
        kind = "bank_bonus"
    w.status, w.applied_on, w.applied_id = "applied", opened, new_id
    return {"kind": kind, "id": new_id, "wish_id": w.id}
