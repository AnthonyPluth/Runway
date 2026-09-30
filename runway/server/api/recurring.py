"""Recurring bills and income: the list, adding and changing them, suggestions, and missed ones."""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import and_, delete, func, insert, or_, select, update

from ... import db, forecast, recurring
from ...models import Account, Override, Recurring, Transaction
from ..common import ApiError


FREQS = {"weekly", "biweekly", "semimonthly", "monthly", "quarterly", "semiannual", "yearly", "dates"}
# The columns _recurring_values() gives, in its order.
COLUMNS = ("name", "account_id", "amount", "frequency", "anchor_date", "match", "end_date", "active", "amount_mode", "dates")


def api_recurring_missed(conn, _q, _b):
    return recurring.missed(conn)


def api_recurring_dismiss(conn, _q, body):
    key = body.get("key") or ""
    if not key.startswith("rec:"):
        raise ApiError("Unknown alert")
    recurring.dismiss(conn, key)
    return {"ok": True}


def api_recurring(conn, _q, _b):
    items = db.rows(conn.execute(recurring.with_account_name().order_by(Recurring.active.desc(), Recurring.name)))
    today = date.today()
    for it in items:
        hist = recurring.matched(conn, it["id"], 12)
        it["matched_count"] = conn.execute(
            select(func.count()).select_from(Transaction).where(Transaction.recurring_id == it["id"])).scalar()
        it["last_matched"] = hist[0] if hist else None
        it["expected_amount"] = recurring.expected_amount(it, hist)
        nxt = [d for d in forecast.occurrences(it, today, today + timedelta(days=400))
               if not recurring.already_happened(it, d, hist, today)]
        it["next_date"] = nxt[0].isoformat() if nxt else None
    missed = recurring.missed(conn, today)
    for it in items:
        it["missed"] = [m for m in missed if m["recurring_id"] == it["id"]]
    return items


def _recurring_values(conn, body):
    name = (body.get("name") or "").strip()
    acct = body.get("account_id") or ""
    freq = body.get("frequency") or "monthly"
    try:
        amount = db.number(body.get("amount"))
        anchor = date.fromisoformat(body.get("anchor_date") or "").isoformat()
    except (TypeError, ValueError):
        raise ApiError("Amount and a date (YYYY-MM-DD) are required") from None
    if not name or freq not in FREQS or not conn.execute(select(Account.id).where(Account.id == acct)).fetchone():
        raise ApiError("Name, account and frequency are required")
    end = body.get("end_date") or None
    if end:
        end = date.fromisoformat(end).isoformat()
    match = (body.get("match") or "").strip().lower() or None
    mode = body.get("amount_mode") or "fixed"
    if mode not in recurring.AMOUNT_MODES:
        raise ApiError("Unknown amount mode")
    dates = None
    if freq in ("dates", "semimonthly"):
        try:
            spec = forecast.parse_dates(body.get("dates") or "", freq)
        except ValueError:
            raise ApiError("List the dates like 04-15, 10-15 (or Apr 15, Oct 15)" if freq == "dates"
                           else "List the days of the month like 1, 15") from None
        dates = ",".join(f"{d}" if freq == "semimonthly" else f"{m:02d}-{d:02d}" for m, d in spec)
    return (name, acct, amount, freq, anchor, match, end, int(body.get("active", 1)), mode, dates)


def api_recurring_add(conn, _q, body):
    vals = _recurring_values(conn, body)
    cur = conn.execute(insert(Recurring).values(dict(zip(COLUMNS, vals, strict=True))))
    linked = recurring.auto_match(conn, [cur.lastrowid])
    return {"ok": True, "id": cur.lastrowid, "linked": linked}


def api_recurring_update(conn, _q, body, rid):
    rid = int(rid)
    old = conn.execute(select(Recurring).where(Recurring.id == rid)).fetchone()
    if not old:
        raise ApiError("Recurring item not found", 404)
    vals = _recurring_values(conn, body)
    conn.execute(update(Recurring).where(Recurring.id == rid).values(dict(zip(COLUMNS, vals, strict=True))))
    new = dict(conn.execute(select(Recurring).where(Recurring.id == rid)).fetchone())
    if recurring.match_text(dict(old)) != recurring.match_text(new) or old["account_id"] != new["account_id"]:
        # Merchant text changed: drop links that no longer fit, then match again.
        m = recurring.match_text(new)
        t = Transaction
        conn.execute(update(t).where(t.recurring_id == rid, or_(
            t.account_id != new["account_id"],
            and_(db.instr(func.lower(func.coalesce(t.payee, "")), m) == 0,
                 db.instr(func.lower(func.coalesce(t.description, "")), m) == 0))).values(recurring_id=None))
    linked = recurring.auto_match(conn, [rid])
    return {"ok": True, "linked": linked}


def api_recurring_delete(conn, _q, _b, rid):
    rid = int(rid)
    conn.execute(update(Transaction).where(Transaction.recurring_id == rid).values(recurring_id=None))
    conn.execute(delete(Override).where(Override.key.like(f"rec:{rid}:%")))
    conn.execute(delete(Recurring).where(Recurring.id == rid))
    return {"ok": True}


def api_tx_recurring(conn, _q, body, tx_id):
    """Link a transaction to a recurring item, create one from it, or mark it as not recurring."""
    try:
        if body.get("new"):
            freq = body["new"] if body["new"] in FREQS else "monthly"
            rid = recurring.create_from_transaction(conn, tx_id, freq)
            return {"ok": True, "recurring_id": rid}
        rid = body.get("recurring_id")
        recurring.link(conn, tx_id, int(rid) if rid else None)
    except ValueError as e:
        raise ApiError(str(e)) from e
    return {"ok": True}


def api_recurring_suggestions(conn, _q, _b):
    return forecast.suggest_recurring(conn, date.today())


def api_recurring_suggestion_dismiss(conn, _q, body):
    """Mark a suggestion as not recurring, so it isn't offered again."""
    key = body.get("key")
    if not isinstance(key, str) or not key.strip() or len(key) > 300:
        raise ApiError("Unknown suggestion")
    forecast.dismiss_suggestion(conn, key)
    return {"ok": True}
