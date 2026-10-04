"""Recurring bills and income: the list, adding and changing them, suggestions, and missed ones."""
from __future__ import annotations

import urllib.parse
from datetime import date, timedelta

from sqlalchemy import and_, delete, func, insert, not_, or_, select, update

from ... import dates, db, forecast, merchants, recurring, validate
from ...models import Account, Override, Recurring, Transaction
from ..common import ApiError, row_id, text
from .transactions import tx_logos


FREQS = {"weekly", "biweekly", "semimonthly", "monthly", "quarterly", "semiannual", "yearly", "dates", "once"}
NOT_FOUND = "Recurring item not found"
REQUIRED = "Amount and a date (YYYY-MM-DD) are required"
# An item's amount and its range are amounts of money (validate.MAX_AMOUNT); the range is kept to the cent.
_v = validate.Validator(ApiError, drop="", missing=REQUIRED, not_number=REQUIRED, not_date=REQUIRED, too_large="The amount is too large")
_range = validate.Validator(ApiError, drop="", not_number="The amount range must be numbers", too_large="The amount is too large")
_amount = validate.Validator(ApiError, drop="", missing="Enter an amount", not_number="Enter an amount", too_large="The amount is too large")
# The columns _recurring_values() gives, in its order.
COLUMNS = ("name", "account_id", "amount", "frequency", "anchor_date", "match", "end_date", "active", "amount_mode", "dates",
           "amount_min", "amount_max")


def api_recurring_missed(conn, _q, _b):
    return recurring.missed(conn)


def api_recurring_dismiss(conn, _q, body):
    key = text(body.get("key"), "key")
    if not key.startswith("rec:"):
        raise ApiError("Unknown alert")
    recurring.dismiss(conn, key)
    return {"ok": True}


def recurring_logos(conn, items: list[dict]) -> dict[int, str | None]:
    """{recurring id: its logo's URL}. An item wears the logo of the last transaction matched to it, unless you chose one
    for it: the logo picker on Recurring picks by the item's name (a bill needn't have a matched transaction, and its
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


def _due(conn, it: dict, hist: list[dict], today: date, skipped: set[str]) -> dict:
    """When the item is next due, as the forecast has it: next_date, the first date from today on that's still expected;
    late_date, an earlier one still expected while its matching window is open (it's late; the forecast has it today);
    skipped, the dates from today on you've skipped. Late ones count from when the account's history allows, as in the
    forecast (forecast.build)."""
    window = recurring.MATCH_WINDOW_DAYS.get(it["frequency"], 6)
    first_tx = conn.execute(select(func.min(Transaction.posted)).where(Transaction.account_id == it["account_id"])).scalar()
    since = max(today - timedelta(days=window + 1), dates.parse_day(first_tx) + timedelta(days=window) if first_tx else today)
    paid = recurring.paid_by_occurrence(it, hist)
    due, skips = [], []
    for d in forecast.occurrences(it, min(since, today - timedelta(days=1)), today + timedelta(days=400)):
        if f"rec:{it['id']}:{d.isoformat()}" in skipped:
            skips.append(d)
        elif recurring.still_due(it, d, paid, today, it["expected_amount"]) is not None:
            due.append(d)
    late = [d for d in due if d < today]
    nxt = [d for d in due if d >= today]
    return {"next_date": nxt[0].isoformat() if nxt else None, "late_date": late[0].isoformat() if late else None,
            "skipped": [d.isoformat() for d in skips if d >= today]}


def api_recurring(conn, _q, _b):
    items = db.rows(conn.execute(recurring.with_account_name().order_by(Recurring.active.desc(), Recurring.name)))
    today = date.today()
    skipped = recurring.skipped_keys(conn)
    for it in items:
        hist = recurring.matched(conn, it["id"], 12)
        it["matched_count"] = conn.execute(
            select(func.count()).select_from(Transaction).where(Transaction.recurring_id == it["id"])).scalar()
        it["last_matched"] = hist[0] if hist else None
        it["expected_amount"] = recurring.expected_amount(it, hist)
        it["suggested_amount"] = recurring.stale_amount(it, hist, today)   # its last payments all came to something else
        it.update(_due(conn, it, hist, today, skipped))
    logos = recurring_logos(conn, items)
    missed = recurring.missed(conn, today)
    for it in items:
        it["logo"] = logos.get(it["id"])
        it["missed"] = [m for m in missed if m["recurring_id"] == it["id"]]
    return items


def _recurring_values(conn, body):
    name = text(body.get("name"), "name").strip()
    acct = text(body.get("account_id"), "account_id")
    freq = text(body.get("frequency"), "frequency") or "monthly"
    amount = _v.amount(body.get("amount"), "amount", cents=False, required=True)
    anchor = _v.day(text(body.get("anchor_date"), "anchor_date"), "date", True)
    if not name or freq not in FREQS or not conn.execute(select(Account.id).where(Account.id == acct)).fetchone():
        raise ApiError("Name, account and frequency are required")
    try:
        end = date.fromisoformat(body["end_date"]).isoformat() if body.get("end_date") else None
    except (TypeError, ValueError):
        raise ApiError("Pick an end date (YYYY-MM-DD)") from None
    if end and end < anchor:
        raise ApiError("It ends before it starts")
    match = recurring.clean_texts(body.get("match"))
    lo, hi = (None if (n := _range.amount(body.get(k), k)) is None else abs(n) for k in ("amount_min", "amount_max"))
    if lo is not None and hi is not None and lo > hi:
        raise ApiError("The smallest amount is bigger than the largest")
    mode = text(body.get("amount_mode"), "amount_mode") or "fixed"
    if mode not in recurring.AMOUNT_MODES:
        raise ApiError("Unknown amount mode")
    dates = None
    if freq in ("dates", "semimonthly"):
        try:
            spec = forecast.parse_dates(text(body.get("dates"), "dates"), freq)
        except ValueError:
            raise ApiError("List the dates like 04-15, 10-15 (or Apr 15, Oct 15)" if freq == "dates"
                           else "List the days of the month like 1, 15") from None
        dates = ",".join(f"{d}" if freq == "semimonthly" else f"{m:02d}-{d:02d}" for m, d in spec)
    return (name, acct, amount, freq, anchor, match, end, validate.flag(body.get("active", 1)), mode, dates, lo, hi)


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
    rid = row_id(rid, NOT_FOUND)
    old = conn.execute(select(Recurring).where(Recurring.id == rid)).fetchone()
    if not old:
        raise ApiError(NOT_FOUND, 404)
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
    if new["frequency"] == "once" and (old["frequency"], old["anchor_date"]) != (new["frequency"], new["anchor_date"]):
        gone.append(and_(t.recurring_linked_by == "auto", not_(recurring.posted_near(new))))   # matched far from its date
    if gone:
        conn.execute(update(t).where(t.recurring_id == rid, or_(*gone)).values(recurring_id=None, recurring_linked_by=None))
    linked = recurring.auto_match(conn, [rid])
    return {"ok": True, "linked": linked, "amount_min": new["amount_min"], "amount_max": new["amount_max"]}   # it may have moved


def api_recurring_candidates(conn, q, _b, rid):
    """Transactions that could be the payment an item missed on ?date=, to link by hand (recurring.candidates)."""
    item = conn.execute(select(Recurring).where(Recurring.id == row_id(rid, NOT_FOUND))).fetchone()
    if not item:
        raise ApiError(NOT_FOUND, 404)
    try:
        day = date.fromisoformat((q.get("date") or [""])[0])
    except ValueError:
        raise ApiError("Which date?") from None
    item = dict(item)
    amount = recurring.expected_amount(item, recurring.matched(conn, item["id"], 12))
    return [{**t, "name": t["payee"] or t["description"]} for t in recurring.candidates(conn, item, day, amount)]


# What set_amount changes on an item, so putting it back (restore_amount) restores exactly what was there.
AMOUNT_COLUMNS = ("amount", "amount_mode", "amount_since", "amount_min", "amount_max")


def set_amount(conn, rid: int, amount: float) -> dict:
    """Make `amount` (signed) the item's own amount from now on, as a fixed amount: what an edit of a one-time item's
    amount does, and "From now on" after an edit of one date. Its range moves with it as an edit in Recurring moves it
    (_range_for_new_amount), the "use $X" hint looks at payments from today, and what's matched stays. Returns what it
    had before (AMOUNT_COLUMNS), for restore_amount."""
    old = conn.execute(select(Recurring).where(Recurring.id == rid)).fetchone()
    if not old:
        raise ApiError(NOT_FOUND, 404)
    vals = {"amount": round(amount, 2), "amount_mode": "fixed", "amount_min": old["amount_min"], "amount_max": old["amount_max"]}
    if abs(vals["amount"] - (old["amount"] or 0)) >= 0.005:
        vals["amount_since"] = date.today().isoformat()
        vals.update(_range_for_new_amount(old, vals))
    conn.execute(update(Recurring).where(Recurring.id == rid).values(vals))
    recurring.auto_match(conn, [rid])
    return {k: old[k] for k in AMOUNT_COLUMNS}


def restore_amount(conn, rid: int, previous) -> None:
    """Put back what set_amount changed, as it was (Undo)."""
    if not isinstance(previous, dict) or set(previous) != set(AMOUNT_COLUMNS):
        raise ApiError("Nothing to put back")
    try:
        vals = {"amount": db.number(previous["amount"]),
                **{k: None if previous[k] is None else db.number(previous[k]) for k in ("amount_min", "amount_max")}}
    except (TypeError, ValueError):
        raise ApiError("Nothing to put back") from None
    mode, since = previous["amount_mode"], previous["amount_since"]
    if mode not in (None, *recurring.AMOUNT_MODES) or not (since is None or isinstance(since, str)):
        raise ApiError("Nothing to put back")
    if not conn.execute(update(Recurring).where(Recurring.id == rid).values({**vals, "amount_mode": mode, "amount_since": since})).rowcount:
        raise ApiError(NOT_FOUND, 404)


def one_time_item(conn, key: str):
    """The one-time item a forecast key (rec:<id>:<date>) belongs to; None for any other key or item."""
    parts = key.split(":")
    if len(parts) != 3 or parts[0] != "rec" or not parts[1].isdigit():
        return None
    item = conn.execute(select(Recurring).where(Recurring.id == int(parts[1]))).fetchone()
    return item if item and item["frequency"] == "once" else None


def api_recurring_amount(conn, _q, body, rid):
    """An item's own amount, from Overview's Coming up: "From now on" after changing one date's amount ({amount, key}:
    the new amount becomes the item's, as a fixed amount, and that date's edit goes, since it's the usual now), or
    putting back what that changed ({restore}: Undo)."""
    rid = row_id(rid, NOT_FOUND)
    if "restore" in body:
        restore_amount(conn, rid, body["restore"])
        return {"ok": True}
    amount = _amount.amount(body.get("amount"), "amount", cents=False, required=True)   # (set_amount rounds it)
    previous = set_amount(conn, rid, amount)
    key = str(body.get("key") or "")
    if key.startswith(f"rec:{rid}:"):
        conn.execute(delete(Override).where(Override.key == key))
    return {"ok": True, "previous": previous}


def api_recurring_add_text(conn, _q, body, rid):
    """"Also match this from now on": add another merchant text to an item."""
    try:
        linked = recurring.add_text(conn, row_id(rid, NOT_FOUND), text(body.get("text"), "text"))
    except ValueError as e:
        raise ApiError(str(e)) from e
    return {"ok": True, "linked": linked}


def api_recurring_delete(conn, _q, _b, rid):
    rid = row_id(rid, NOT_FOUND)
    conn.execute(update(Transaction).where(Transaction.recurring_id == rid).values(recurring_id=None, recurring_linked_by=None))
    conn.execute(delete(Override).where(Override.key.like(f"rec:{rid}:%")))
    conn.execute(delete(Recurring).where(Recurring.id == rid))
    return {"ok": True}


def api_tx_recurring(conn, _q, body, tx_id):
    """Link a transaction to a recurring item, create one from it, or mark it as not recurring."""
    try:
        if body.get("new"):
            freq = body["new"] if isinstance(body["new"], str) and body["new"] in FREQS else "monthly"   # (true: monthly)
            rid = recurring.create_from_transaction(conn, tx_id, freq)
            return {"ok": True, "recurring_id": rid}
        rid = body.get("recurring_id")
        suggest = recurring.link(conn, tx_id, row_id(rid, "Unknown recurring item", 400) if rid else None)
    except ValueError as e:
        raise ApiError(str(e)) from e
    # None of the item's texts is on it: offer its own, so the next one links by itself.
    return {"ok": True, **({"suggest_text": suggest} if suggest else {})}


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
