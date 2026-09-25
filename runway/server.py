"""Local web server: JSON API + static UI + background daily sync. Standard library only."""
from __future__ import annotations

import calendar
import html
import ipaddress
from http.cookies import SimpleCookie
import json
import mimetypes
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("font/woff2", ".woff2")
import sqlite3
import os
import threading
import time
import traceback
import urllib.parse
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import oidc, sfinvest
from . import networth, rentcast
from . import categories, categorize, db, forecast, plaid, portfolio, prices, recurring, simplefin

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
DAILY_SYNC_HOUR = 6          # the daily sync runs on the first check after this hour (local time)
VISIT_SYNC_MINUTES = 60      # opening Runway syncs if the last sync is older than this (SimpleFIN allows ~24 a day)
_sync_lock = threading.Lock()
_inv_lock = threading.Lock()

ACCOUNT_FIELDS = {
    "display_name": str, "kind": str, "closing_day": int, "due_day": int, "pay_from": str,
    "in_forecast": int, "daily_spend": int, "hidden": int, "owed_positive": int,
}
KINDS = {"checking", "savings", "credit", "loan", "investment"}
FREQS = {"weekly", "biweekly", "semimonthly", "monthly", "quarterly", "semiannual", "yearly", "dates"}


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ------------------------------------------------------------------------------------------------ actions

def run_sync() -> dict:
    if not _sync_lock.acquire(blocking=False):
        raise ApiError("A sync is already running.", 409)
    try:
        with db.session() as conn:
            access_url = db.get_setting(conn, "simplefin_access_url")
            if not access_url:
                raise ApiError("Connect SimpleFIN in Settings first.")
            try:
                result = simplefin.sync(conn, access_url)
                counts = categorize.categorize(conn, result["new"])
                recurring.auto_match(conn)
                if conn.execute("SELECT 1 FROM inv_accounts WHERE source='simplefin'").fetchone():
                    try:
                        refresh_prices(conn)
                    except Exception:  # prices are a nice-to-have; never fail the bank sync over them
                        pass
                msg = f"{len(result['new'])} new transactions"
                if result["errors"]:
                    msg += " · bank messages: " + "; ".join(result["errors"])[:500]
                try:
                    rentcast.refresh_due(conn)
                except Exception:
                    pass
                networth.summary(conn)   # record today's net worth
                conn.execute("INSERT INTO sync_log(ok, message) VALUES (1, ?)", (msg,))
                db.set_setting(conn, "last_sync_ok", datetime.now().isoformat(timespec="seconds"))
                return {"new": len(result["new"]), "categorized": counts, "bank_messages": result["errors"]}
            except simplefin.SimpleFinError as e:
                conn.execute("INSERT INTO sync_log(ok, message) VALUES (0, ?)", (str(e),))
                raise ApiError(str(e), 502)
    finally:
        _sync_lock.release()


def run_investment_sync() -> dict:
    """Pull holdings and activity from every Plaid connection, then refresh price history."""
    if not _inv_lock.acquire(blocking=False):
        raise ApiError("An investment sync is already running.", 409)
    try:
        with db.session() as conn:
            out = {"items": 0, "errors": [], "prices": {}}
            if plaid.configured(conn) and conn.execute("SELECT 1 FROM plaid_items").fetchone():
                out = plaid.sync_all(conn)
            if not conn.execute("SELECT 1 FROM inv_accounts").fetchone():
                return out
            out["prices"] = refresh_prices(conn)
            db.set_setting(conn, "last_inv_sync", datetime.now().isoformat(timespec="seconds"))
            return out
    finally:
        _inv_lock.release()


def refresh_prices(conn) -> dict:
    tickers = [r["ticker"] for r in conn.execute(
        "SELECT DISTINCT s.ticker FROM securities s WHERE s.is_cash=0 AND s.ticker IS NOT NULL AND "
        "(s.id IN (SELECT security_id FROM holdings) OR s.id IN (SELECT security_id FROM inv_transactions) "
        "OR s.id IN (SELECT security_id FROM manual_positions))")]
    tickers.append(prices.BENCHMARK)
    out = prices.refresh(conn, tickers, date.today() - timedelta(days=portfolio.HISTORY_DAYS + 10))
    sfinvest.recapture_all(conn)   # re-check reported position values against the fresh prices
    prices.fill_security_types(conn)
    return out


def _older_than(stamp: str | None, **delta) -> bool:
    return not stamp or datetime.now() - datetime.fromisoformat(stamp) > timedelta(**delta)


def daily_due(last: str | None, now: datetime | None = None) -> bool:
    """Once a day: after DAILY_SYNC_HOUR if the last sync was on an earlier day, or any time it's been 24 hours."""
    now = now or datetime.now()
    if not last:
        return True
    prev = datetime.fromisoformat(last)
    return (prev.date() < now.date() and now.hour >= DAILY_SYNC_HOUR) or now - prev > timedelta(hours=24)


def _sync_everything(bank: bool, invest: bool) -> None:
    if bank:
        try:
            run_sync()
        except ApiError:
            pass
    if invest:
        try:
            run_investment_sync()
        except ApiError:
            pass


def sync_on_visit() -> dict:
    """Someone opened Runway: sync in the background if the data is more than VISIT_SYNC_MINUTES old."""
    with db.session() as conn:
        configured = bool(db.get_setting(conn, "simplefin_access_url"))
        bank = configured and _older_than(db.get_setting(conn, "last_sync_ok"), minutes=VISIT_SYNC_MINUTES) \
            and _older_than(db.get_setting(conn, "last_auto_sync_attempt"), minutes=VISIT_SYNC_MINUTES)
        has_inv = bool(conn.execute("SELECT 1 FROM inv_accounts").fetchone())
        invest = has_inv and _older_than(db.get_setting(conn, "last_inv_sync"), minutes=VISIT_SYNC_MINUTES)
        if bank:
            db.set_setting(conn, "last_auto_sync_attempt", datetime.now().isoformat(timespec="seconds"))
    if (bank or invest) and not _sync_lock.locked() and not _inv_lock.locked():
        threading.Thread(target=_sync_everything, args=(bank, invest), daemon=True).start()
        return {"started": True}
    return {"started": False}


def background_sync() -> None:
    while True:
        try:
            with db.session() as conn:
                configured = bool(db.get_setting(conn, "simplefin_access_url"))
                last = db.get_setting(conn, "last_sync_ok")
                last_try = db.get_setting(conn, "last_auto_sync_attempt")
                last_inv = db.get_setting(conn, "last_inv_sync")
            # Don't hammer SimpleFIN after failures: at most one automatic attempt every 3 hours.
            bank = configured and daily_due(last) and _older_than(last_try, hours=3)
            if bank:
                with db.session() as conn:
                    db.set_setting(conn, "last_auto_sync_attempt", datetime.now().isoformat(timespec="seconds"))
            _sync_everything(bank, daily_due(last_inv))
        except Exception:
            traceback.print_exc()
        time.sleep(15 * 60)


# ------------------------------------------------------------------------------------------------ handlers

def api_state(conn, _q, _b):
    last_log = conn.execute("SELECT at, ok, message FROM sync_log ORDER BY id DESC LIMIT 1").fetchone()
    return {
        "connected": bool(db.get_setting(conn, "simplefin_access_url")),
        "has_api_key": bool(db.get_setting(conn, "openrouter_api_key")),
        "llm_model": db.get_setting(conn, "llm_model") or categorize.DEFAULT_MODEL,
        "last_sync_ok": db.get_setting(conn, "last_sync_ok"),
        "last_log": dict(last_log) if last_log else None,
        "last_llm_error": db.get_setting(conn, "last_llm_error"),
        "review_count": conn.execute("SELECT COUNT(*) FROM transactions WHERE needs_review=1").fetchone()[0],
        "horizon_days": int(db.get_setting(conn, "horizon_days", "90") or 90),
        "syncing": _sync_lock.locked() or _inv_lock.locked(),
        "primary_account": db.get_setting(conn, "primary_account"),
        "auto_ai_on_sync": (db.get_setting(conn, "auto_ai_on_sync", "1") or "1") == "1",
        "rentcast_configured": rentcast.configured(conn),
        "database": "postgres" if db.using_postgres() else "sqlite",
        "version": os.environ.get("RUNWAY_VERSION") or "dev",
        "user": getattr(_current, "user", None),
    }


def api_overview(conn, q, _b):
    horizon = int(q.get("days", [db.get_setting(conn, "horizon_days", "90") or 90])[0])
    horizon = max(14, min(horizon, 365))
    fc = forecast.build(conn, date.today(), horizon)
    fc["missed"] = recurring.missed(conn)
    fc["all_accounts"] = db.rows(conn.execute(
        "SELECT id, COALESCE(display_name, name) AS name, kind, balance, balance_date, owed_positive, hidden "
        "FROM accounts ORDER BY kind, name"
    ))
    return fc


def api_accounts(conn, _q, _b):
    return db.rows(conn.execute("SELECT * FROM accounts ORDER BY hidden, kind, COALESCE(display_name, name)"))


def api_account_update(conn, _q, body, acct_id):
    if not conn.execute("SELECT 1 FROM accounts WHERE id=?", (acct_id,)).fetchone():
        raise ApiError("Account not found", 404)
    sets, vals = [], []
    for k, v in body.items():
        if k not in ACCOUNT_FIELDS:
            continue
        if v in ("", None):
            v = None
        elif ACCOUNT_FIELDS[k] is int:
            v = int(v)
        else:
            v = str(v).strip()
        if k == "kind" and v not in KINDS:
            raise ApiError("Unknown account type")
        if k in ("closing_day", "due_day") and v is not None and not 1 <= v <= 31:
            raise ApiError("Days must be between 1 and 31")
        sets.append(f"{k}=?")
        vals.append(v)
    if sets:
        conn.execute(f"UPDATE accounts SET {', '.join(sets)} WHERE id=?", (*vals, acct_id))
    return {"ok": True}


def api_transactions(conn, q, _b):
    where, args = ["1=1"], []
    if q.get("review", ["0"])[0] == "1":
        where.append("t.needs_review=1")
    if q.get("recurring", [""])[0]:
        where.append("t.recurring_id=?")
        args.append(int(q["recurring"][0]))
    if q.get("account", [""])[0]:
        where.append("t.account_id=?")
        args.append(q["account"][0])
    if q.get("category", [""])[0]:
        cat = q["category"][0]
        if cat == "__none__":
            where.append("t.category IS NULL")
        else:
            family = [cat] + categories.descendants(conn, cat)   # a category includes its subcategories
            where.append(f"t.category IN ({','.join('?' * len(family))})")
            args.extend(family)
    if q.get("month", [""])[0]:   # YYYY-MM
        start, end = _month_range({"month": q["month"]})
        where.append("t.posted>=? AND t.posted<?")
        args += [start.isoformat(), end.isoformat()]
    if q.get("scope", [""])[0] == "budget":   # the same accounts the Budget page counts
        where.append("t.account_id IN (SELECT id FROM accounts WHERE hidden=0 AND kind IN ('checking','savings','credit'))")
    if q.get("q", [""])[0]:
        like = f"%{q['q'][0].lower()}%"
        where.append("(lower(t.payee) LIKE ? OR lower(t.description) LIKE ?)")
        args += [like, like]
    limit = max(1, min(int(q.get("limit", ["200"])[0]), 1000))
    offset = max(0, int(q.get("offset", ["0"])[0]))
    sql = (
        "SELECT t.*, COALESCE(a.display_name, a.name) AS account_name, a.kind AS account_kind, r.name AS recurring_name "
        "FROM transactions t JOIN accounts a ON a.id=t.account_id LEFT JOIN recurring r ON r.id=t.recurring_id "
        f"WHERE {' AND '.join(where)} ORDER BY t.posted DESC, t.id LIMIT ? OFFSET ?"
    )
    items = db.rows(conn.execute(sql, (*args, limit, offset)))
    total = conn.execute(
        f"SELECT COUNT(*) FROM transactions t WHERE {' AND '.join(where)}", args
    ).fetchone()[0]
    return {"items": items, "total": total}


def api_tx_category(conn, _q, body, tx_id):
    try:
        n = categorize.set_category(conn, tx_id, body.get("category", ""), bool(body.get("remember")))
    except ValueError as e:
        raise ApiError(str(e))
    return {"ok": True, "also_updated": n}


def api_tx_accept(conn, _q, _b, tx_id):
    categorize.accept_suggestion(conn, tx_id)
    return {"ok": True}


def api_categories(conn, _q, _b):
    cats = categories.all_categories(conn)
    counts = {r["category"]: r["n"] for r in conn.execute("SELECT category, COUNT(*) AS n FROM transactions GROUP BY category")}
    for c in cats:
        c["transactions"] = counts.get(c["name"], 0)
    return cats


def api_category_add(conn, _q, body):
    try:
        categories.add(conn, body.get("name") or "", body.get("parent") or None,
                       bool(body.get("is_transfer")), bool(body.get("is_income")))
    except categories.CategoryError as e:
        raise ApiError(str(e))
    return {"ok": True}


def api_category_rename(conn, _q, body):
    try:
        categories.rename(conn, body.get("name") or "", body.get("new_name") or "")
    except categories.CategoryError as e:
        raise ApiError(str(e))
    return {"ok": True}


def api_category_move(conn, _q, body):
    try:
        categories.move(conn, body.get("name") or "", body.get("parent") or None)
    except categories.CategoryError as e:
        raise ApiError(str(e))
    return {"ok": True}


def api_category_remove(conn, _q, body):
    try:
        n = categories.remove(conn, body.get("name") or "", body.get("move_to") or None)
    except categories.CategoryError as e:
        raise ApiError(str(e))
    return {"ok": True, "moved": n}


def api_rules(conn, _q, _b):
    return db.rows(conn.execute("SELECT * FROM rules ORDER BY match"))


def api_rule_add(conn, _q, body):
    match = (body.get("match") or "").strip().lower()
    cat = body.get("category") or ""
    if len(match) < 2 or not conn.execute("SELECT 1 FROM categories WHERE name=?", (cat,)).fetchone():
        raise ApiError("Rule needs match text and a valid category")
    conn.execute(
        "INSERT INTO rules(match, category) VALUES (?,?) ON CONFLICT(match) DO UPDATE SET category=excluded.category",
        (match, cat),
    )
    if body.get("apply"):
        conn.execute(
            "UPDATE transactions SET category=?, category_source='rule', confidence=1, needs_review=0 "
            "WHERE category_source IS NOT 'manual' AND (instr(lower(payee), ?)>0 OR instr(lower(description), ?)>0)",
            (cat, match, match),
        )
    return {"ok": True}


def _apply_rule(conn, match: str, cat: str) -> int:
    return conn.execute(
        "UPDATE transactions SET category=?, category_source='rule', confidence=1, needs_review=0 "
        "WHERE category_source IS NOT 'manual' AND (instr(lower(payee), ?)>0 OR instr(lower(description), ?)>0)",
        (cat, match, match),
    ).rowcount


def api_rule_update(conn, _q, body, rule_id):
    match = (body.get("match") or "").strip().lower()
    cat = body.get("category") or ""
    if len(match) < 2 or not conn.execute("SELECT 1 FROM categories WHERE name=?", (cat,)).fetchone():
        raise ApiError("Rule needs match text and a valid category")
    clash = conn.execute("SELECT id FROM rules WHERE match=? AND id<>?", (match, int(rule_id))).fetchone()
    if clash:
        raise ApiError("Another rule already uses that text")
    conn.execute("UPDATE rules SET match=?, category=? WHERE id=?", (match, cat, int(rule_id)))
    return {"ok": True}


def api_rule_apply(conn, _q, _b, rule_id):
    r = conn.execute("SELECT * FROM rules WHERE id=?", (int(rule_id),)).fetchone()
    if not r:
        raise ApiError("Rule not found", 404)
    return {"ok": True, "updated": _apply_rule(conn, r["match"], r["category"])}


def api_rule_delete(conn, _q, _b, rule_id):
    conn.execute("DELETE FROM rules WHERE id=?", (int(rule_id),))
    return {"ok": True}


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
        "SELECT r.*, COALESCE(a.display_name, a.name) AS account_name FROM recurring r "
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
        amount = float(body.get("amount"))
        anchor = date.fromisoformat(body.get("anchor_date") or "").isoformat()
    except (TypeError, ValueError):
        raise ApiError("Amount and a date (YYYY-MM-DD) are required")
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
                           else "List the days of the month like 1, 15")
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
        raise ApiError(str(e))
    return {"ok": True}


def api_override_set(conn, _q, body):
    key = str(body.get("key") or "")
    if not key.startswith(("rec:", "card:", "stmt:")):
        raise ApiError("Unknown item")
    try:
        amount = float(body.get("amount"))
    except (TypeError, ValueError):
        raise ApiError("Enter an amount")
    conn.execute(
        "INSERT INTO overrides(key, amount) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET amount=excluded.amount", (key, amount)
    )
    return {"ok": True}


def api_override_delete(conn, _q, body):
    conn.execute("DELETE FROM overrides WHERE key=?", (str(body.get("key") or ""),))
    return {"ok": True}


def _month_range(q):
    today = date.today()
    try:
        y, m = (int(x) for x in (q.get("month", [f"{today:%Y-%m}"])[0]).split("-"))
        start = date(y, m, 1)
    except ValueError:
        raise ApiError("Month must look like 2026-09")
    return start, date(y + (m == 12), m % 12 + 1, 1)


def _month_totals(conn, start: date, end: date) -> dict:
    """Net amount per category for the month, across checking, savings and cards (not loans or investments)."""
    rows_ = conn.execute(
        "SELECT t.category AS category, SUM(t.amount) AS total FROM transactions t JOIN accounts a ON a.id=t.account_id "
        "WHERE t.posted>=? AND t.posted<? AND a.hidden=0 AND a.kind IN ('checking','savings','credit') GROUP BY t.category",
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    return {r["category"]: r["total"] or 0.0 for r in rows_}


def api_budget(conn, q, _b):
    today = date.today()
    start, end = _month_range(q)
    days = calendar.monthrange(start.year, start.month)[1]
    cats = [c for c in categories.all_categories(conn) if not c["is_transfer"] and not c["is_income"]]
    income_cats = [r["name"] for r in conn.execute("SELECT name FROM categories WHERE is_income=1")]
    totals = _month_totals(conn, start, end)
    budgets = {r["category"]: r["amount"] for r in conn.execute("SELECT * FROM budgets")}
    own = {c["name"]: round(-totals.get(c["name"], 0.0), 2) for c in cats}
    out = []
    for c in cats:
        below = [k["name"] for k in cats if c["name"] in k["path"][:-1]]
        spent = round(own[c["name"]] + sum(own[k] for k in below), 2)
        b = budgets.get(c["name"])
        out.append({"name": c["name"], "parent": c["parent"], "path": c["path"], "depth": c["depth"], "top": c["top"],
                    "has_children": bool(below), "budget": b,
                    "spent": spent, "own_spent": own[c["name"]], "left": round(b - spent, 2) if b is not None else None})
    current = start <= today < end
    return {
        "month": f"{start:%Y-%m}",
        "days_in_month": days,
        "day": today.day if current else (days if end <= today else 0),
        "categories": out,  # tree order: each category followed by its subcategories
        "income": round(sum(totals.get(c, 0.0) for c in income_cats), 2),
        "uncategorized": round(-totals.get(None, 0.0), 2),
    }


def api_cashflow(conn, q, _b):
    """Where money came from and went in a month, for the Sankey report."""
    start, end = _month_range(q)
    totals = _month_totals(conn, start, end)
    cats = categories.all_categories(conn)
    kind = {c["name"]: c for c in cats}
    income: dict[str, float] = {}
    spending: dict[str, dict] = {}
    for name, total in totals.items():
        c = kind.get(name)
        if c and c["is_transfer"]:
            continue
        if c and c["is_income"]:
            top = c["top"]
            income[top] = income.get(top, 0.0) + total
            continue
        if c is None:  # uncategorized: net money out counts as spending, net money in as income
            if total < 0:
                spending.setdefault("Uncategorized", {"value": 0.0, "children": {}})["value"] += -total
            elif total > 0:
                income["Uncategorized"] = income.get("Uncategorized", 0.0) + total
            continue
        top = c["top"]
        node = spending.setdefault(top, {"value": 0.0, "children": {}})
        node["value"] += -total
        if c["depth"] >= 1:  # the Sankey shows two levels; deeper subcategories count toward their level-2 ancestor
            sub = c["path"][1]
            node["children"][sub] = node["children"].get(sub, 0.0) + (-total)
    spend_list = []
    for name, node in spending.items():
        if node["value"] <= 0.005:
            continue
        kids = [{"name": k, "value": round(v, 2)} for k, v in node["children"].items() if v > 0.005]
        kid_total = sum(k["value"] for k in kids)
        if kids and node["value"] - kid_total > 0.5:
            kids.append({"name": f"{name} (general)", "value": round(node["value"] - kid_total, 2)})
        kids.sort(key=lambda k: -k["value"])
        spend_list.append({"name": name, "value": round(node["value"], 2), "children": kids})
    spend_list.sort(key=lambda n: -n["value"])
    inc_list = sorted(({"name": k, "value": round(v, 2)} for k, v in income.items() if v > 0.005), key=lambda n: -n["value"])
    total_in = round(sum(n["value"] for n in inc_list), 2)
    total_out = round(sum(n["value"] for n in spend_list), 2)
    return {"month": f"{start:%Y-%m}", "income": inc_list, "spending": spend_list,
            "total_in": total_in, "total_out": total_out, "net": round(total_in - total_out, 2)}


def api_budget_set(conn, _q, body):
    cat = body.get("category") or ""
    if not conn.execute("SELECT 1 FROM categories WHERE name=? AND is_transfer=0 AND is_income=0", (cat,)).fetchone():
        raise ApiError("Pick a spending category")
    amt = body.get("amount")
    if amt in (None, "", 0, "0"):
        conn.execute("DELETE FROM budgets WHERE category=?", (cat,))
        return {"ok": True}
    try:
        amt = abs(float(amt))
    except (TypeError, ValueError):
        raise ApiError("Enter an amount")
    conn.execute("INSERT INTO budgets(category, amount) VALUES (?,?) ON CONFLICT(category) DO UPDATE SET amount=excluded.amount", (cat, amt))
    return {"ok": True}


def api_ai_suggest(conn, _q, _b):
    if not db.get_setting(conn, "openrouter_api_key"):
        raise ApiError("Add an OpenRouter API key in Settings first.")
    try:
        return categorize.suggest_for_review(conn)
    except RuntimeError as e:
        raise ApiError(str(e), 502)


def api_ai_log(conn, _q, _b):
    return db.rows(conn.execute("SELECT * FROM ai_log ORDER BY id DESC LIMIT 25"))


def api_ai_apply(conn, _q, body):
    ids = [str(i) for i in (body.get("tx_ids") or [])]
    category = body.get("category") or ""
    new = body.get("new_category") or None
    created = False
    if new:   # accept an AI-proposed category: create it (unless it exists by now), then use it
        name = " ".join(str(new.get("name") or "").split())
        existing = conn.execute("SELECT name FROM categories WHERE lower(name)=lower(?)", (name,)).fetchone()
        if existing:
            category = existing["name"]
        else:
            parent = new.get("parent") or None
            if parent and not conn.execute("SELECT 1 FROM categories WHERE name=?", (parent,)).fetchone():
                parent = None
            try:
                categories.add(conn, name, parent, is_income=(body.get("direction") == "in" and not parent))
            except categories.CategoryError as e:
                if parent and "levels deep" in str(e):
                    categories.add(conn, name, None)
                else:
                    raise ApiError(str(e))
            category, created = name, True
    try:
        n = categorize.apply_to_group(conn, ids, category, bool(body.get("remember")))
    except ValueError as e:
        raise ApiError(str(e))
    return {"ok": True, "updated": n, "category": category, "created": created}


def api_recurring_suggestions(conn, _q, _b):
    return forecast.suggest_recurring(conn, date.today())


def api_connect(conn, _q, body):
    token = (body.get("token") or "").strip()
    if not token:
        raise ApiError("Paste a SimpleFIN setup token.")
    try:
        access_url = token if token.startswith("http") else simplefin.claim_setup_token(token)
        simplefin.fetch_accounts(access_url, date.today() - timedelta(days=3))  # prove it works before saving
    except simplefin.SimpleFinError as e:
        raise ApiError(str(e), 502)
    db.set_setting(conn, "simplefin_access_url", access_url)
    return {"ok": True}


def api_settings(conn, _q, body):
    if "openrouter_api_key" in body:
        key = (body.get("openrouter_api_key") or "").strip()
        db.set_setting(conn, "openrouter_api_key", key or None)
        db.set_setting(conn, "last_llm_error", None)
    if "llm_model" in body:
        db.set_setting(conn, "llm_model", (body.get("llm_model") or "").strip() or None)
        db.set_setting(conn, "last_llm_error", None)
    if "primary_account" in body:
        acct = body.get("primary_account") or None
        if acct and not conn.execute("SELECT 1 FROM accounts WHERE id=? AND kind IN ('checking','savings')", (acct,)).fetchone():
            raise ApiError("Pick a checking or savings account")
        db.set_setting(conn, "primary_account", acct)
    if "auto_ai_on_sync" in body:
        db.set_setting(conn, "auto_ai_on_sync", "1" if body.get("auto_ai_on_sync") else "0")
    if "horizon_days" in body:
        db.set_setting(conn, "horizon_days", str(max(14, min(int(body["horizon_days"]), 365))))
    return {"ok": True}


def api_recategorize(conn, _q, _b):
    """Send everything still uncategorized or awaiting review through rules (and the AI model, if set up) again."""
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM transactions WHERE category IS NULL OR (needs_review=1 AND category_source IS NOT 'manual')"
    )]
    conn.execute(
        "UPDATE transactions SET category=NULL, category_source=NULL, confidence=NULL "
        "WHERE needs_review=1 AND category_source IS NOT 'manual'"
    )
    return categorize.categorize(conn, ids)


def api_plaid_status(conn, _q, _b):
    items = db.rows(conn.execute("SELECT item_id, institution_name, env, created_at, last_sync, error FROM plaid_items ORDER BY institution_name"))
    for it in items:
        it["accounts"] = db.rows(conn.execute(
            "SELECT id, name, official_name, subtype, mask, balance, hidden FROM inv_accounts WHERE item_id=? ORDER BY name", (it["item_id"],)))
    return {"configured": plaid.configured(conn), "env": db.get_setting(conn, "plaid_env", "production"),
            "client_id": db.get_setting(conn, "plaid_client_id") or "", "items": items,
            "last_inv_sync": db.get_setting(conn, "last_inv_sync"), "syncing": _inv_lock.locked(),
            "inv_accounts": conn.execute("SELECT COUNT(*) FROM inv_accounts").fetchone()[0],
            "simplefin_connected": bool(db.get_setting(conn, "simplefin_access_url")),
            "simplefin_last_sync": db.get_setting(conn, "last_sync_ok"),
            "simplefin_seen": list(json.loads(db.get_setting(conn, "simplefin_holdings_seen") or "{}").values())}


def api_plaid_settings(conn, _q, body):
    if "env" in body:
        if body["env"] not in plaid.HOSTS:
            raise ApiError("Environment must be sandbox or production")
        db.set_setting(conn, "plaid_env", body["env"])
    if body.get("client_id") is not None:
        db.set_setting(conn, "plaid_client_id", body["client_id"].strip() or None)
    if body.get("secret"):
        db.set_setting(conn, "plaid_secret", body["secret"].strip())
    if "redirect_uri" in body:
        db.set_setting(conn, "plaid_redirect_uri", (body.get("redirect_uri") or "").strip() or None)
    return {"ok": True}


def api_plaid_link_token(conn, _q, body):
    try:
        return {"link_token": plaid.link_token(conn, body.get("item_id") or None)}
    except plaid.PlaidError as e:
        raise ApiError(str(e), 502)


def api_plaid_exchange(conn, _q, body):
    try:
        item_id = plaid.exchange(conn, body.get("public_token") or "", body.get("institution") or {})
        res = plaid.sync_item(conn, item_id)
        res["hidden_simplefin"] = plaid.hide_simplefin_duplicates(conn, item_id)
        res["prices"] = refresh_prices(conn)
        return {"ok": True, "item_id": item_id, **res}
    except plaid.PlaidError as e:
        raise ApiError(str(e), 502)


def api_plaid_item_sync(conn, _q, _b, item_id):
    try:
        res = plaid.sync_item(conn, item_id)
        res["prices"] = refresh_prices(conn)
        return {"ok": True, **res}
    except plaid.PlaidError as e:
        raise ApiError(str(e), 502)


def api_plaid_item_remove(conn, _q, _b, item_id):
    try:
        plaid.remove_item(conn, item_id)
    except plaid.PlaidError as e:
        raise ApiError(str(e), 502)
    return {"ok": True}


def api_inv_account(conn, _q, body, acct_id):
    conn.execute("UPDATE inv_accounts SET hidden=? WHERE id=?", (1 if body.get("hidden") else 0, acct_id))
    return {"ok": True}


def api_cost_basis(conn, _q, body):
    """Set the price paid per share for a holding in one account (cost basis = that x shares held). Empty clears it."""
    acct, sec = body.get("account_id") or "", body.get("security_id") or ""
    if not conn.execute("SELECT 1 FROM holdings WHERE account_id=? AND security_id=?", (acct, sec)).fetchone():
        raise ApiError("That holding isn't in this account")
    v = body.get("per_share", body.get("cost_basis"))
    if v in (None, ""):
        conn.execute("DELETE FROM cost_overrides WHERE account_id=? AND security_id=?", (acct, sec))
        return {"ok": True, "cleared": True}
    try:
        v = float(str(v).replace(",", "").replace("$", ""))
    except ValueError:
        raise ApiError("Enter a number")
    if v < 0:
        raise ApiError("Price can't be negative")
    if "per_share" in body:
        conn.execute("INSERT INTO cost_overrides(account_id, security_id, cost_basis, per_share) VALUES (?,?,0,?) "
                     "ON CONFLICT(account_id, security_id) DO UPDATE SET per_share=excluded.per_share, cost_basis=0", (acct, sec, v))
    else:
        conn.execute("INSERT INTO cost_overrides(account_id, security_id, cost_basis, per_share) VALUES (?,?,?,NULL) "
                     "ON CONFLICT(account_id, security_id) DO UPDATE SET cost_basis=excluded.cost_basis, per_share=NULL", (acct, sec, v))
    return {"ok": True}


def api_live_quotes(conn, _q, _b):
    """Near real-time prices for everything held (plus the S&P 500 fund, which tells us if the market is open)."""
    tickers = [r[0] for r in conn.execute(
        "SELECT DISTINCT s.ticker FROM holdings h JOIN securities s ON s.id=h.security_id WHERE s.is_cash=0 AND s.ticker IS NOT NULL")]
    q = prices.quotes(tickers + [prices.BENCHMARK])
    return {"quotes": q, "market": prices.market_state(q.get(prices.BENCHMARK)),
            "as_of": datetime.now().isoformat(timespec="seconds")}


def api_networth(conn, _q, _b):
    out = networth.summary(conn)
    out["assets_list"] = networth.assets(conn)
    out["loan_accounts"] = db.rows(conn.execute(
        "SELECT id, COALESCE(display_name, name) AS name, kind FROM accounts WHERE kind='loan' AND hidden=0 ORDER BY name"))
    out["rentcast"] = {"configured": rentcast.configured(conn), "used": rentcast.used_this_month(conn), "limit": rentcast.MONTHLY_LIMIT}
    return out


def api_asset_add(conn, _q, body):
    try:
        return {"id": networth.save_asset(conn, body)}
    except ValueError as e:
        raise ApiError(str(e))


def api_asset_update(conn, _q, body, asset_id):
    try:
        networth.save_asset(conn, body, int(asset_id))
    except ValueError as e:
        raise ApiError(str(e))
    return {"ok": True}


def api_asset_remove(conn, _q, _b, asset_id):
    networth.remove_asset(conn, int(asset_id))
    return {"ok": True}


def api_asset_refresh(conn, _q, _b, asset_id):
    try:
        est = rentcast.refresh_asset(conn, int(asset_id))
    except rentcast.RentCastError as e:
        raise ApiError(str(e), 502)
    return {"ok": True, **est, "used": rentcast.used_this_month(conn)}


def api_rentcast_settings(conn, _q, body):
    key = (body.get("api_key") or "").strip()
    if body.get("clear"):
        db.set_setting(conn, "rentcast_api_key", None)
    elif key:
        db.set_setting(conn, "rentcast_api_key", key)
    return {"ok": True, "configured": rentcast.configured(conn)}


def api_tracked_get(conn, _q, _b, acct_id):
    from . import tracked
    st = conn.execute("SELECT * FROM manual_state WHERE account_id=?", (acct_id,)).fetchone()
    return {"positions": tracked.positions_for(conn, acct_id), "state": dict(st) if st else None,
            "contributions": db.rows(conn.execute("SELECT date, amount FROM manual_contributions WHERE account_id=? ORDER BY date DESC LIMIT 12", (acct_id,)))}


def api_tracked_save(conn, _q, body, acct_id):
    from . import tracked
    try:
        tracked.save(conn, acct_id, body.get("rows") or [])
    except ValueError as e:
        raise ApiError(str(e))
    conn.commit()
    try:
        refresh_prices(conn)   # prices for the funds just entered, then re-value the account
    except Exception:
        sfinvest.recapture_all(conn)
    return {"ok": True}


def api_investments(conn, q, _b):
    period = q.get("period", ["1Y"])[0]
    return portfolio.overview(conn, period if period in ("1M", "3M", "YTD", "1Y", "2Y", "MAX") else "1Y")


ROUTES = [
    ("GET", "/api/state", api_state),
    ("GET", "/api/overview", api_overview),
    ("GET", "/api/accounts", api_accounts),
    ("POST", "/api/accounts/{id}", api_account_update),
    ("GET", "/api/transactions", api_transactions),
    ("POST", "/api/transactions/{id}/category", api_tx_category),
    ("POST", "/api/transactions/{id}/accept", api_tx_accept),
    ("POST", "/api/transactions/{id}/recurring", api_tx_recurring),
    ("POST", "/api/overrides", api_override_set),
    ("DELETE", "/api/overrides", api_override_delete),
    ("GET", "/api/budget", api_budget),
    ("POST", "/api/budget", api_budget_set),
    ("POST", "/api/ai/suggest", api_ai_suggest),
    ("POST", "/api/ai/apply", api_ai_apply),
    ("GET", "/api/ai/log", api_ai_log),
    ("GET", "/api/categories", api_categories),
    ("POST", "/api/categories", api_category_add),
    ("POST", "/api/categories/rename", api_category_rename),
    ("POST", "/api/categories/remove", api_category_remove),
    ("POST", "/api/categories/move", api_category_move),
    ("GET", "/api/cashflow", api_cashflow),
    ("GET", "/api/rules", api_rules),
    ("POST", "/api/rules", api_rule_add),
    ("DELETE", "/api/rules/{id}", api_rule_delete),
    ("POST", "/api/rules/{id}", api_rule_update),
    ("POST", "/api/rules/{id}/apply", api_rule_apply),
    ("GET", "/api/recurring", api_recurring),
    ("POST", "/api/recurring", api_recurring_add),
    ("GET", "/api/recurring/suggestions", api_recurring_suggestions),
    ("GET", "/api/recurring/missed", api_recurring_missed),
    ("POST", "/api/recurring/dismiss", api_recurring_dismiss),
    ("POST", "/api/recurring/{id}", api_recurring_update),
    ("DELETE", "/api/recurring/{id}", api_recurring_delete),
    ("POST", "/api/connect", api_connect),
    ("GET", "/api/plaid/status", api_plaid_status),
    ("POST", "/api/plaid/settings", api_plaid_settings),
    ("POST", "/api/plaid/link_token", api_plaid_link_token),
    ("POST", "/api/plaid/exchange", api_plaid_exchange),
    ("POST", "/api/plaid/items/{id}/sync", api_plaid_item_sync),
    ("POST", "/api/plaid/items/{id}/remove", api_plaid_item_remove),
    ("POST", "/api/plaid/accounts/{id}", api_inv_account),
    ("GET", "/api/investments", api_investments),
    ("GET", "/api/networth", api_networth),
    ("POST", "/api/assets", api_asset_add),
    ("POST", "/api/assets/{id}", api_asset_update),
    ("POST", "/api/assets/{id}/remove", api_asset_remove),
    ("POST", "/api/assets/{id}/refresh", api_asset_refresh),
    ("POST", "/api/rentcast/settings", api_rentcast_settings),
    ("GET", "/api/investments/live", api_live_quotes),
    ("GET", "/api/tracked/{id}", api_tracked_get),
    ("POST", "/api/tracked/{id}", api_tracked_save),
    ("POST", "/api/investments/cost", api_cost_basis),
    ("POST", "/api/settings", api_settings),
    ("POST", "/api/recategorize", api_recategorize),
]


def _match(pattern: str, path: str):
    p_parts, parts = pattern.strip("/").split("/"), path.strip("/").split("/")
    if len(p_parts) != len(parts):
        return None
    params = []
    for a, b in zip(p_parts, parts):
        if a == "{id}":
            params.append(urllib.parse.unquote(b))
        elif a != b:
            return None
    return params


class Handler(BaseHTTPRequestHandler):
    server_version = "Runway"

    def log_message(self, fmt, *args):  # quiet
        pass

    def _send(self, status: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, obj) -> None:
        self._send(status, json.dumps(obj).encode())

    def _host_ok(self) -> bool:
        # Refuse requests addressed to hostnames we don't know (DNS-rebinding protection).
        return host_allowed(self.headers.get("Host") or "")

    def _cookie(self, name: str) -> str | None:
        c = SimpleCookie()
        try:
            c.load(self.headers.get("Cookie") or "")
        except Exception:
            return None
        return c[name].value if name in c else None

    def _user(self) -> dict | None:
        """The signed-in person, or None. Without OIDC configured everyone is 'local'."""
        if not oidc.enabled():
            return {"name": None, "email": None, "local": True}
        with db.session() as conn:
            return oidc.session_user(conn, self._cookie("runway_session"))

    def _redirect(self, location: str, cookies: list[str] | None = None) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        for ck in cookies or []:
            self.send_header("Set-Cookie", ck)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _page(self, status: int, title: str, message: str, link: tuple[str, str] | None = None) -> None:
        body = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} · Runway</title><link rel="icon" href="/logo.svg"><link rel="stylesheet" href="/app.css"></head>
<body><main style="max-width:520px;margin:12vh auto"><div class="card" style="text-align:center">
<img src="/logo.svg" width="48" height="48" alt=""><h1 style="margin-top:12px">{html.escape(title)}</h1>
<p class="help" style="margin:0 auto 16px">{html.escape(message)}</p>
{f'<a class="btn primary" href="{html.escape(link[0])}">{html.escape(link[1])}</a>' if link else ''}</div></main></body></html>"""
        self._send(status, body.encode(), "text/html; charset=utf-8")

    def _cookie_header(self, name: str, value: str, max_age: int, path: str = "/") -> str:
        secure = "; Secure" if oidc.config()["secure_cookie"] else ""
        return f"{name}={value}; Path={path}; Max-Age={max_age}; HttpOnly; SameSite=Lax{secure}"

    def _auth_routes(self, url) -> bool:
        """/auth/* pages. Returns True if handled."""
        q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        if url.path == "/auth/login":
            if not oidc.enabled():
                self._redirect("/"); return True
            try:
                with db.session() as conn:
                    target, state = oidc.start_login(conn, q.get("next") or "/")
            except oidc.OIDCError as e:
                self._page(502, "Can't reach sign-in", str(e), ("/auth/login", "Try again")); return True
            self._redirect(target, [self._cookie_header("runway_login", state, oidc.LOGIN_TTL, "/auth")]); return True
        if url.path == "/auth/callback":
            try:
                with db.session() as conn:
                    token, nxt = oidc.finish_login(conn, q, self._cookie("runway_login"))
            except oidc.OIDCError as e:
                self._page(403, "Couldn't sign you in", str(e), ("/auth/login", "Try again")); return True
            days = oidc.config()["session_days"]
            self._redirect(nxt, [self._cookie_header("runway_session", token, days * 86400),
                                 self._cookie_header("runway_login", "", 0, "/auth")]); return True
        if url.path == "/auth/logout":
            with db.session() as conn:
                target = oidc.logout(conn, self._cookie("runway_session")) if oidc.enabled() else "/"
            self._redirect(target, [self._cookie_header("runway_session", "", 0)]); return True
        if url.path == "/auth/signed-out":
            self._page(200, "Signed out", "You've signed out of Runway.", ("/auth/login", "Sign in again")); return True
        return False

    def _dispatch(self, method: str) -> None:
        url = urllib.parse.urlsplit(self.path)
        if url.path == "/healthz" and method == "GET":   # container health check: says nothing about your data
            return self._send(200, b"ok", "text/plain")
        if not self._host_ok():
            return self._send(403, b"Runway doesn't recognise this address. Add it to RUNWAY_ALLOWED_HOSTS.", "text/plain")
        if url.path.startswith("/auth/") and method == "GET" and self._auth_routes(url):
            return
        # The look of the sign-in pages is public; everything else needs you signed in.
        if url.path not in ("/app.css", "/logo.svg", "/logo-180.png", "/fonts/Geist-Variable.woff2"):
            self.user = self._user()
            if not self.user:
                if url.path.startswith("/api/"):
                    return self._json(401, {"error": "You've been signed out.", "login": "/auth/login"})
                return self._redirect("/auth/login?next=" + urllib.parse.quote(url.path or "/"))
        if not url.path.startswith("/api/"):
            if method != "GET":
                return self._send(405, b"", "text/plain")
            return self._static(url.path)
        # State-changing calls must carry a custom header, which a foreign web page can't add without CORS approval.
        if method != "GET" and self.headers.get("X-Runway") != "1":
            return self._json(403, {"error": "forbidden"})
        if method == "GET" and url.path == "/api/backup":
            from . import backup
            with db.session() as conn:
                data = backup.dump(conn)
            self.send_response(200)
            self.send_header("Content-Type", "application/gzip")
            self.send_header("Content-Disposition", f'attachment; filename="runway-backup-{date.today().isoformat()}.json.gz"')
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
        if method == "POST" and url.path == "/api/restore":
            from . import backup
            n = int(self.headers.get("Content-Length") or 0)
            if not n or n > 200 * 1024 * 1024:
                return self._json(400, {"error": "Choose a backup file (up to 200 MB)."})
            try:
                data = backup.load(self.rfile.read(n))
                with db.session() as conn:
                    counts = backup.restore(conn, data)
                with db.session() as conn:
                    sfinvest.repair_stored(conn)
            except ValueError as e:
                return self._json(400, {"error": str(e)})
            return self._json(200, {"ok": True, "created": data.get("created"), "source": data.get("source"),
                                    "transactions": counts.get("transactions", 0), "accounts": counts.get("accounts", 0)})
        body = {}
        if method in ("POST", "DELETE"):
            n = int(self.headers.get("Content-Length") or 0)
            if n:
                try:
                    body = json.loads(self.rfile.read(n).decode() or "{}")
                except json.JSONDecodeError:
                    return self._json(400, {"error": "Bad JSON"})
        q = urllib.parse.parse_qs(url.query)
        _current.user = getattr(self, "user", None)
        if method == "POST" and url.path == "/api/investments/sync":
            try:
                bank = None
                with db.session() as conn:
                    has_sf = bool(db.get_setting(conn, "simplefin_access_url"))
                if has_sf:  # positions from SimpleFIN arrive with the regular bank sync
                    bank = run_sync()
                out = run_investment_sync()
                out["bank"] = bank
                return self._json(200, out)
            except ApiError as e:
                return self._json(e.status, {"error": str(e)})
        if method == "POST" and url.path == "/api/sync/auto":
            return self._json(200, sync_on_visit())
        if method == "POST" and url.path == "/api/sync":
            try:
                return self._json(200, run_sync())
            except ApiError as e:
                return self._json(e.status, {"error": str(e)})
        for m, pattern, fn in ROUTES:
            if m != method:
                continue
            params = _match(pattern, url.path)
            if params is None:
                continue
            try:
                with db.session() as conn:
                    result = fn(conn, q, body, *params)
                return self._json(200, result)
            except ApiError as e:
                return self._json(e.status, {"error": str(e)})
            except sqlite3.OperationalError as e:
                if "locked" in str(e):
                    return self._json(503, {"error": "Runway is busy saving a sync. Try again in a few seconds."})
                traceback.print_exc()
                return self._json(500, {"error": f"{type(e).__name__}: {e}"})
            except Exception as e:
                traceback.print_exc()
                return self._json(500, {"error": f"{type(e).__name__}: {e}"})
        return self._json(404, {"error": "Not found"})

    def _static(self, path: str) -> None:
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        full = os.path.normpath(os.path.join(STATIC, rel))
        if not full.startswith(STATIC) or not os.path.isfile(full):
            full = os.path.join(STATIC, "index.html")
        with open(full, "rb") as f:
            data = f.read()
        self._send(200, data, mimetypes.guess_type(full)[0] or "application/octet-stream")

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_DELETE(self):
        self._dispatch("DELETE")


_current = threading.local()
EXTRA_HOSTS = {h.strip().lower() for h in (os.environ.get("RUNWAY_ALLOWED_HOSTS") or "").split(",") if h.strip()}
if os.environ.get("RUNWAY_PUBLIC_URL"):   # the address you open Runway at is always allowed
    EXTRA_HOSTS.add((urllib.parse.urlsplit(os.environ["RUNWAY_PUBLIC_URL"]).hostname or "").lower())
# Names that can't be pointed at an outside website: this machine, mDNS (.local), home-router names, Tailscale.
SAFE_SUFFIXES = (".local", ".lan", ".home.arpa", ".internal", ".ts.net")


def host_allowed(host_header: str) -> bool:
    host = host_header.strip().lower()
    if host.startswith("["):                      # [::1]:8765
        host = host[1:host.find("]")] if "]" in host else host
    elif host.count(":") == 1:
        host = host.split(":")[0]
    if not host:
        return False
    if "*" in EXTRA_HOSTS or host in EXTRA_HOSTS or host == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(host)
        # An address typed directly (not a name) can't be used for DNS rebinding.
        return ip.is_loopback or ip.is_private or ip in ipaddress.ip_network("100.64.0.0/10")
    except ValueError:
        pass
    return host.endswith(SAFE_SUFFIXES) or "." not in host   # bare names like "nas" or "homeserver"


def serve(host: str = "127.0.0.1", port: int = 8765, auto_sync: bool = True) -> None:
    if oidc.enabled():
        problems = oidc.check_config()
        if problems:
            raise SystemExit("Sign-in (OIDC) isn't set up correctly:\n  - " + "\n  - ".join(problems))
    elif host not in ("127.0.0.1", "localhost", "::1") and os.environ.get("RUNWAY_ALLOW_NO_AUTH") != "1":
        raise SystemExit("Runway is set to accept connections from other devices, so it needs sign-in.\n"
                         "Set OIDC_ISSUER, OIDC_CLIENT_ID, OIDC_CLIENT_SECRET, RUNWAY_PUBLIC_URL and OIDC_ALLOWED_EMAILS\n"
                         "(or RUNWAY_ALLOW_NO_AUTH=1 if a proxy in front of Runway already handles sign-in).")
    db.init()
    with db.session() as conn:
        recurring.auto_match(conn)  # pick up matches for items created before this version
        sfinvest.repair_stored(conn)  # fix investment positions saved by earlier versions
        categories.flatten(conn)      # subcategories are one level deep
    if auto_sync:
        threading.Thread(target=background_sync, daemon=True).start()
    httpd = ThreadingHTTPServer((host, port), Handler)
    where = f"http://localhost:{port}" if host in ("127.0.0.1", "localhost") else f"port {port} on all network addresses"
    print(f"Runway is running at {where}  (data: {db.describe()})"
          f"{'  · sign-in via ' + oidc.config()['issuer'] if oidc.enabled() else ''}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
