"""Churning: cards you and your partner opened for their bonuses, what they earn, points values, balances and to-dos."""
from __future__ import annotations

from datetime import date

from sqlalchemy import select

from ... import churning, db
from ...models import Category
from ..common import ApiError
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
    return churning.overview(conn, date.today(), owner_choices(conn))


def api_churning_best(conn, q, _b):
    """Open cards ranked for a purchase in a category (optionally one person's, optionally for an amount)."""
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
    return {"category": category, "cards": churning.best(conn, date.today(), category, owner, amount)}


def api_churn_card_add(conn, _q, body):
    return {"id": _churn(churning.save_card, conn, body)}


def api_churn_card_update(conn, _q, body, card_id):
    return {"id": _churn(churning.save_card, conn, body, _id(card_id))}


def api_churn_card_remove(conn, _q, _b, card_id):
    churning.remove_card(conn, _id(card_id))
    return {"ok": True}


def api_churn_rate(conn, _q, body, card_id):
    _churn(churning.set_rate, conn, _id(card_id), str(body.get("category") or ""), body.get("multiplier"))
    return {"ok": True}


def api_churn_currency(conn, _q, body):
    return {"key": _churn(churning.save_currency, conn, body)}


def api_churn_currency_remove(conn, _q, _b, key):
    _churn(churning.remove_currency, conn, key)
    return {"ok": True}


def api_churn_balance(conn, _q, body):
    _churn(churning.set_balance, conn, body.get("owner"), str(body.get("currency") or ""), body.get("points"), date.today())
    return {"ok": True}


def api_churn_task_add(conn, _q, body):
    return {"id": _churn(churning.save_task, conn, body)}


def api_churn_task_update(conn, _q, body, task_id):
    return {"id": _churn(churning.save_task, conn, body, _id(task_id))}


def api_churn_task_remove(conn, _q, _b, task_id):
    churning.remove_task(conn, _id(task_id))
    return {"ok": True}

