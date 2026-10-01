"""Recurring bills and income: the list, adding and changing them, suggestions, and missed ones."""
from __future__ import annotations

import urllib.parse
from datetime import date, timedelta

from sqlalchemy import and_, delete, func, insert, not_, or_, select, update

from ... import db, forecast, merchants, recurring
from ...models import Account, Override, Recurring, Transaction
from ..common import ApiError
from .transactions import tx_logos


FREQS = {"weekly", "biweekly", "semimonthly", "monthly", "quarterly", "semiannual", "yearly", "dates"}
# The columns _recurring_values() gives, in its order.
COLUMNS = ("name", "account_id", "amount", "frequency", "anchor_date", "match", "end_date", "active", "amount_mode", "dates",
           "amount_min", "amount_max")


def api_recurring_missed(conn, _q, _b):
    return recurring.missed(conn)


def api_recurring_dismiss(conn, _q, body):
    key = body.get("key") or ""
    if not key.startswith("rec:"):
        raise ApiError("Unknown alert")
    recurring.dismiss(conn, key)
    return {"ok": True}


def recurring_logos(conn, items: list[dict]) -> dict[int, str | None]:
    """{recurring id: its logo's URL}. An item wears the logo of the last transaction matched to it, unless you chose one
    for it: the logo picker on Bills & income picks by the item's name (a bill needn't have a matched transaction, and its
    name is what you'd call the merchant). That's the same choice Transactions keeps by merchant name, so a name that is
    also a merchant's changes both. A choice of "no logo" leaves the item without one."""
    ids = [i["id"] for i in items]
    if not ids:
        return {}
    last: dict[int, dict] = {}
    for t in db.rows(conn.execute(
            select(Transaction).where(Transaction.recurring_id.in_(ids)).order_by(Transaction.posted.desc()))):
        last.setdefault(t["recurring_id"], t)
    logos = tx_logos(conn, list(last.values()))
    out = {rid: logos.get(t["id"]) for rid, t in last.items()}
    chosen = merchants.chosen_for(conn, [{"id": str(i["id"]), "payee": i["name"]} for i in items])
    for i in items:
        if str(i["id"]) in chosen:
            mid = chosen[str(i["id"])]
            out[i["id"]] = f"/api/merchants/{urllib.parse.quote(mid, safe='')}/logo" if mid else None
    return out


def api_recurring(conn, _q, _b):
    items = db.rows(conn.execute(recurring.with_account_name().order_by(Recurring.active.desc(), Recurring.name)))
    today = date.today()
    for it in items:
        hist = recurring.matched(conn, it["id"], 12)
        it["matched_count"] = conn.execute(
            select(func.count()).select_from(Transaction).where(Transaction.recurring_id == it["id"])).scalar()
        it["last_matched"] = hist[0] if hist else None
        it["expected_amount"] = recurring.expected_amount(it, hist)
        it["suggested_amount"] = recurring.stale_amount(it, hist, today)   # its last payments all came to something else
        paid = recurring.paid_by_occurrence(it, hist)
        nxt = [d for d in forecast.occurrences(it, today, today + timedelta(days=400))
               if recurring.still_due(it, d, paid, today, it["expected_amount"]) is not None]
        it["next_date"] = nxt[0].isoformat() if nxt else None
    logos = recurring_logos(conn, items)
    missed = recurring.missed(conn, today)
    for it in items:
        it["logo"] = logos.get(it["id"])
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
    match = recurring.clean_texts(body.get("match"))
    try:
        lo, hi = (None if body.get(k) in (None, "") else round(abs(db.number(body[k])), 2) for k in ("amount_min", "amount_max"))
    except (TypeError, ValueError):
        raise ApiError("The amount range must be numbers") from None
    if lo is not None and hi is not None and lo > hi:
        raise ApiError("The smallest amount is bigger than the largest")
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
    return (name, acct, amount, freq, anchor, match, end, int(body.get("active", 1)), mode, dates, lo, hi)


def api_recurring_add(conn, _q, body):
    vals = _recurring_values(conn, body)
    cur = conn.execute(insert(Recurring).values(dict(zip(COLUMNS, vals, strict=True), amount_since=date.today().isoformat())))
    linked = recurring.auto_match(conn, [cur.lastrowid])
    return {"ok": True, "id": cur.lastrowid, "linked": linked}


def _range_for_new_amount(old, vals: dict) -> dict:
    """When the amount changes (say "use $2,100" on a $5,000 paycheck) and its range, left as it was, no longer holds the
    new amount, the range moves with it, keeping its proportions; otherwise the new payments would never match."""
    lo, hi, was = vals["amount_min"], vals["amount_max"], abs(old["amount"] or 0)
    if (lo, hi) != (old["amount_min"], old["amount_max"]) or (lo is None and hi is None) or was < 0.005:
        return {}
    now = abs(vals["amount"])
    if (lo is None or lo <= now) and (hi is None or now <= hi):
        return {}
    scale = now / was
    return {"amount_min": None if lo is None else round(lo * scale, 2), "amount_max": None if hi is None else round(hi * scale, 2)}


def api_recurring_update(conn, _q, body, rid):
    rid = int(rid)
    old = conn.execute(select(Recurring).where(Recurring.id == rid)).fetchone()
    if not old:
        raise ApiError("Recurring item not found", 404)
    vals = dict(zip(COLUMNS, _recurring_values(conn, body), strict=True))
    typed = (vals["amount_min"], vals["amount_max"])   # the range as you left it, before any move with the amount
    if abs(vals["amount"] - old["amount"]) >= 0.005:
        vals["amount_since"] = date.today().isoformat()   # the "use $X" hint looks at payments from here on
        vals.update(_range_for_new_amount(old, vals))
    conn.execute(update(Recurring).where(Recurring.id == rid).values(vals))
    new = dict(conn.execute(select(Recurring).where(Recurring.id == rid)).fetchone())
    # Links that no longer fit go before matching again, for what changed: ones on another account when the item moved
    # account, unless you made them (a part of a payday that came from savings, say); ones
    # without any of the texts now, unless you made them; ones outside a range you changed that were made automatically
    # (not ones from before Runway kept how: they may be yours). A range that only moved with a new amount leaves the
    # links made at the old one: they were those paydays' payments. Changing anything else leaves them all.
    t, old = Transaction, dict(old)
    gone = []
    if old["account_id"] != new["account_id"]:   # moved to another account: what matched on the old one goes, not yours
        gone.append(and_(func.coalesce(t.recurring_linked_by, "") != "you", t.account_id != new["account_id"]))
    if recurring.match_texts(old) != recurring.match_texts(new):
        gone.append(and_(func.coalesce(t.recurring_linked_by, "") != "you", not_(recurring.has_text(recurring.match_texts(new)))))
    if (old["amount_min"], old["amount_max"]) != typed:
        gone.append(and_(t.recurring_linked_by == "auto", not_(recurring.amount_fits(new))))
    if gone:
        conn.execute(update(t).where(t.recurring_id == rid, or_(*gone)).values(recurring_id=None, recurring_linked_by=None))
    linked = recurring.auto_match(conn, [rid])
    return {"ok": True, "linked": linked, "amount_min": new["amount_min"], "amount_max": new["amount_max"]}   # it may have moved


def api_recurring_add_text(conn, _q, body, rid):
    """"Also match this from now on": add another merchant text to an item."""
    try:
        linked = recurring.add_text(conn, int(rid), body.get("text") or "")
    except ValueError as e:
        raise ApiError(str(e)) from e
    return {"ok": True, "linked": linked}


def api_recurring_delete(conn, _q, _b, rid):
    rid = int(rid)
    conn.execute(update(Transaction).where(Transaction.recurring_id == rid).values(recurring_id=None, recurring_linked_by=None))
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
        text = recurring.link(conn, tx_id, int(rid) if rid else None)
    except ValueError as e:
        raise ApiError(str(e)) from e
    # None of the item's texts is on it: offer its own, so the next one links by itself.
    return {"ok": True, **({"suggest_text": text} if text else {})}


def api_recurring_suggestions(conn, _q, _b):
    return forecast.suggest_recurring(conn, date.today())


def api_recurring_suggestion_dismiss(conn, _q, body):
    """Mark a suggestion as not recurring, so it isn't offered again."""
    key = body.get("key")
    if not isinstance(key, str) or not key.strip() or len(key) > 300:
        raise ApiError("Unknown suggestion")
    forecast.dismiss_suggestion(conn, key)
    return {"ok": True}


def api_recurring_suggestions_dismissed(conn, _q, _b):
    return forecast.list_dismissed_suggestions(conn)


def api_recurring_suggestion_restore(conn, _q, body):
    """Bring a dismissed suggestion back, so it's offered again if it still looks recurring."""
    key = body.get("key")
    if not isinstance(key, str) or not forecast.restore_suggestion(conn, key):
        raise ApiError("That suggestion isn’t dismissed")
    return {"ok": True}
