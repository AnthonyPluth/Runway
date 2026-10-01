"""Churning: cards and bank accounts you and your partner opened for their bonuses, what they earn, points values,
balances, to-dos, plans for each card and its benefits."""
from __future__ import annotations

from datetime import date

from sqlalchemy import select

from ... import bank_bonuses, churn_benefits, churn_wishlist, churning, db, notify
from ...models import Category
from ..common import ApiError, _current
from .state import owner_choices


def _churn(fn, *args):
    try:
        return fn(*args)
    except churning.ChurnError as e:
        raise ApiError(str(e)) from e


def _id(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ApiError("Not found", 404) from None


def api_churning(conn, _q, _b):
    out = churning.overview(conn, date.today(), owner_choices(conn))
    # Churning's push alerts, with whether each is on, so the page can offer their switches.
    prefs = notify.prefs(conn, notify.person(getattr(_current, "user", None)))
    out["alert_prefs"] = [{**a, "on": bool(prefs.get(a["key"]))} for a in churning.ALERT_PREFS]
    return out


def api_churning_best(conn, q, _b):
    """Open cards ranked for a purchase in a category (optionally one person's, optionally for an amount). `portal=1`:
    you'll book through the issuer's portal, so portal-only rates count."""
    category = (q.get("category", [""])[0] or "").strip() or None
    if category and not conn.execute(select(Category.name).where(Category.name == category)).fetchone():
        raise ApiError("Pick a category")
    owner = (q.get("owner", [""])[0] or "").strip() or None
    amount = None
    if q.get("amount", [""])[0]:
        try:
            amount = db.number(q["amount"][0])
        except (TypeError, ValueError):
            raise ApiError("The amount must be a number") from None
        if amount < 0:
            raise ApiError("The amount can't be negative")
    portal = (q.get("portal", [""])[0] or "").strip().lower() in ("1", "true", "on", "yes")
    return {"category": category, "portal": portal,
            "cards": churning.best(conn, date.today(), category, owner, amount, portal)}


def api_churn_card_add(conn, _q, body):
    return {"id": _churn(churning.save_card, conn, body)}


def api_churn_card_update(conn, _q, body, card_id):
    return {"id": _churn(churning.save_card, conn, body, _id(card_id))}


def api_churn_card_remove(conn, _q, _b, card_id):
    churning.remove_card(conn, _id(card_id))
    return {"ok": True}


def api_churn_rate(conn, _q, body, card_id):
    _churn(churning.set_rate, conn, _id(card_id), str(body.get("category") or ""), body.get("multiplier"),
           body.get("portal_only"))
    return {"ok": True}


def api_churn_plan_done(conn, _q, body, card_id):
    return _churn(churning.plan_done, conn, _id(card_id), date.today(), (body or {}).get("on"))


def api_churn_plan_undo(conn, _q, _b, card_id):
    return _churn(churning.plan_undo, conn, _id(card_id))


def api_churn_benefit_add(conn, _q, body, card_id):
    return {"id": _churn(churn_benefits.save, conn, body or {}, _id(card_id))}


def api_churn_benefit_update(conn, _q, body, benefit_id):
    return {"id": _churn(churn_benefits.save, conn, body or {}, None, _id(benefit_id))}


def api_churn_benefit_remove(conn, _q, _b, benefit_id):
    churn_benefits.remove(conn, _id(benefit_id))
    return {"ok": True}


def api_churn_benefit_use(conn, _q, body, benefit_id):
    return {"id": _churn(churn_benefits.use, conn, _id(benefit_id), body or {}, date.today())}


def api_churn_benefit_unuse(conn, _q, body, benefit_id):
    _churn(churn_benefits.unuse, conn, _id(benefit_id), body or {}, date.today())
    return {"ok": True}


def api_churn_currency(conn, _q, body):
    return {"key": _churn(churning.save_currency, conn, body)}


def api_churn_currency_remove(conn, _q, _b, key):
    _churn(churning.remove_currency, conn, key)
    return {"ok": True}


def api_churn_balance(conn, _q, body):
    _churn(churning.set_balance, conn, body.get("owner"), str(body.get("currency") or ""), body.get("points"), date.today(),
           body.get("as_of"))
    return {"ok": True}


def api_churn_task_add(conn, _q, body):
    return {"id": _churn(churning.save_task, conn, body)}


def api_churn_task_update(conn, _q, body, task_id):
    return {"id": _churn(churning.save_task, conn, body, _id(task_id))}


def api_churn_task_remove(conn, _q, _b, task_id):
    churning.remove_task(conn, _id(task_id))
    return {"ok": True}


def api_churn_task_snooze(conn, _q, body, task_id):
    return {"snooze_until": _churn(churning.snooze_task, conn, _id(task_id), body or {}, date.today())}



def api_churn_wish_add(conn, _q, body):
    return {"id": _churn(churn_wishlist.save, conn, body or {})}


def api_churn_wish_update(conn, _q, body, wish_id):
    return {"id": _churn(churn_wishlist.save, conn, body or {}, _id(wish_id))}


def api_churn_wish_remove(conn, _q, _b, wish_id):
    churn_wishlist.remove(conn, _id(wish_id))
    return {"ok": True}


def api_churn_wish_applied(conn, _q, body, wish_id):
    return _churn(churn_wishlist.applied, conn, _id(wish_id), body or {}, date.today())


def api_churn_score(conn, _q, body):
    _churn(churn_wishlist.set_score, conn, body or {}, date.today())
    return {"ok": True}


def api_bank_bonus_add(conn, _q, body):
    return {"id": _churn(bank_bonuses.save, conn, body)}


def api_bank_bonus_update(conn, _q, body, bonus_id):
    return {"id": _churn(bank_bonuses.save, conn, body, _id(bonus_id))}


def api_bank_bonus_remove(conn, _q, _b, bonus_id):
    bank_bonuses.remove(conn, _id(bonus_id))
    return {"ok": True}
