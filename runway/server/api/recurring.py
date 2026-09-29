"""Recurring bills and income: the list, adding and changing them, suggestions, and missed ones."""
from __future__ import annotations

from datetime import date, timedelta

from ... import db, forecast, recurring
from ..common import ApiError


FREQS = {"weekly", "biweekly", "semimonthly", "monthly", "quarterly", "semiannual", "yearly", "dates"}


def api_recurring_missed(conn, _q, _b):
    return recurring.missed(conn)


def api_recurring_dismiss(conn, _q, body):
    key = body.get("key") or ""
    if not key.startswith("rec:"):
        raise ApiError("Unknown alert")
    recurring.dismiss(conn, key)
    return {"ok": True}


def api_recurring(conn, _q, _b):
    items = db.rows(conn.execute(
        "SELECT r.*, " + db.label_sql("a") + " AS account_name FROM recurring r "
        "LEFT JOIN accounts a ON a.id=r.account_id ORDER BY r.active DESC, r.name"
    ))
    today = date.today()
    for it in items:
        hist = recurring.matched(conn, it["id"], 12)
        it["matched_count"] = conn.execute("SELECT COUNT(*) FROM transactions WHERE recurring_id=?", (it["id"],)).fetchone()[0]
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
    if not name or freq not in FREQS or not conn.execute("SELECT 1 FROM accounts WHERE id=?", (acct,)).fetchone():
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
    cur = conn.execute(
        "INSERT INTO recurring(name, account_id, amount, frequency, anchor_date, match, end_date, active, amount_mode, dates) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        vals,
    )
    linked = recurring.auto_match(conn, [cur.lastrowid])
    return {"ok": True, "id": cur.lastrowid, "linked": linked}


def api_recurring_update(conn, _q, body, rid):
    rid = int(rid)
    old = conn.execute("SELECT * FROM recurring WHERE id=?", (rid,)).fetchone()
    if not old:
        raise ApiError("Recurring item not found", 404)
    vals = _recurring_values(conn, body)
    conn.execute(
        "UPDATE recurring SET name=?, account_id=?, amount=?, frequency=?, anchor_date=?, match=?, end_date=?, active=?, "
        "amount_mode=?, dates=? WHERE id=?",
        (*vals, rid),
    )
    new = dict(conn.execute("SELECT * FROM recurring WHERE id=?", (rid,)).fetchone())
    if recurring.match_text(dict(old)) != recurring.match_text(new) or old["account_id"] != new["account_id"]:
        # Merchant text changed: drop links that no longer fit, then match again.
        m = recurring.match_text(new)
        conn.execute(
            "UPDATE transactions SET recurring_id=NULL WHERE recurring_id=? AND (account_id<>? OR "
            "(instr(lower(COALESCE(payee,'')), ?)=0 AND instr(lower(COALESCE(description,'')), ?)=0))",
            (rid, new["account_id"], m, m),
        )
    linked = recurring.auto_match(conn, [rid])
    return {"ok": True, "linked": linked}


def api_recurring_delete(conn, _q, _b, rid):
    rid = int(rid)
    conn.execute("UPDATE transactions SET recurring_id=NULL WHERE recurring_id=?", (rid,))
    conn.execute("DELETE FROM overrides WHERE key LIKE ?", (f"rec:{rid}:%",))
    conn.execute("DELETE FROM recurring WHERE id=?", (rid,))
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
