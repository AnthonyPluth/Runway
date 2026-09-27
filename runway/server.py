"""Local web server: JSON API + static UI + background daily sync. Standard library only."""
from __future__ import annotations

import calendar
import gzip
import hashlib
import html
import ipaddress
from http.cookies import SimpleCookie
import json
import mimetypes
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("application/manifest+json", ".webmanifest")
import os
import secrets
import threading
import time
import traceback
import urllib.parse
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import sqlalchemy.exc
from dateutil.relativedelta import relativedelta

from . import oidc, sfinvest
from . import networth, notify, rentcast, webpush
from . import brands, carta, categories, categorize, db, equity, forecast, merchants, reports, plaid, plaidbank, portfolio, prices, recurring, retail, rules, simplefin, splits

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
# Files anyone may fetch: the sign-in pages' look, and what a phone needs to install Runway (it fetches the manifest
# without cookies). None of them hold any data.
PUBLIC_FILES = {"/app.css", "/logo.svg", "/logo-180.png", "/fonts/Geist-Variable.woff2", "/manifest.webmanifest", "/sw.js",
                "/icon-192.png", "/icon-512.png", "/icon-maskable-512.png"}
DAILY_SYNC_HOUR = 6          # the daily sync runs on the first check after this hour (local time)
VISIT_SYNC_MINUTES = 60      # opening Runway syncs if the last sync is older than this (SimpleFIN allows ~24 a day)
_sync_lock = threading.Lock()
_inv_lock = threading.Lock()
AUTO_SYNC = True             # False with --no-sync: no daily sync and no sync on opening the app

ACCOUNT_FIELDS = {
    "display_name": str, "kind": str, "pay_from": str,
    "in_forecast": int, "daily_spend": int, "hidden": int, "owed_positive": int, "owner": str,
}
KINDS = {"checking", "savings", "credit", "loan", "investment"}
FREQS = {"weekly", "biweekly", "semimonthly", "monthly", "quarterly", "semiannual", "yearly", "dates"}


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ------------------------------------------------------------------------------------------------ actions

def plaid_banks(conn) -> bool:
    return plaid.configured(conn) and any(plaidbank.is_bank_item(r) for r in conn.execute("SELECT products FROM plaid_items").fetchall())


def bank_configured(conn) -> bool:
    """Whether there's anything to sync bank accounts from: SimpleFIN, or a Plaid bank or card connection."""
    return bool(db.get_setting(conn, "simplefin_access_url")) or plaid_banks(conn)


def run_sync() -> dict:
    if not _sync_lock.acquire(blocking=False):
        raise ApiError("A sync is already running.", 409)
    try:
        try:
            with db.session() as conn:
                access_url = db.get_setting(conn, "simplefin_access_url")
                use_plaid = plaid_banks(conn)
                if not access_url and not use_plaid:
                    raise ApiError("Connect SimpleFIN or a Plaid bank in Settings first.")
                result = simplefin.sync(conn, access_url) if access_url else {"new": [], "errors": []}
                if use_plaid:   # accounts set to Plaid, and card statements
                    pb = plaidbank.sync_all(conn)
                    result["new"] += pb["new"]
                    result["errors"] += pb["errors"]
                counts = categorize.categorize(conn, result["new"])
                recurring.auto_match(conn)
                try:   # new card transactions may be Amazon or Target orders the extension already sent
                    retail.match_and_apply(conn)
                except Exception:
                    traceback.print_exc()
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
        except ApiError:
            raise
        except simplefin.SimpleFinError as e:
            _record_failed_sync(str(e))
            raise ApiError(str(e), 502)
        except Exception as e:
            traceback.print_exc()
            _record_failed_sync(f"The sync stopped with an error ({type(e).__name__}); the details are in Runway's log.")
            raise ApiError("The sync failed; the details are in Runway's log.", 500)
    finally:
        _sync_lock.release()


def _record_failed_sync(message: str) -> None:
    """Written in a session of its own: the sync's session rolled back, and the failure must still show (the
    sidebar's "Last sync failed", and the can't-sync notification)."""
    with db.session() as conn:
        conn.execute("INSERT INTO sync_log(ok, message) VALUES (0, ?)", (message,))


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
        notify_now()
    if invest:
        try:
            run_investment_sync()
        except ApiError:
            pass


def notify_now() -> None:
    """Send any new alerts to subscribed devices. Never lets a notification problem break a sync."""
    try:
        with db.session() as conn:
            notify.run(conn)
    except Exception:
        traceback.print_exc()


def sync_on_visit() -> dict:
    """Someone opened Runway: sync in the background if the data is more than VISIT_SYNC_MINUTES old."""
    if not AUTO_SYNC:   # --no-sync / RUNWAY_NO_SYNC=1: only when you ask (Settings or the sync buttons)
        return {"started": False}
    with db.session() as conn:
        configured = bank_configured(conn)
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
                configured = bank_configured(conn)
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
        "connected": bank_configured(conn),
        "brands": brands.account_brands(conn),   # each account's institution logo (or letter)
        "simplefin": bool(db.get_setting(conn, "simplefin_access_url")),
        "has_api_key": bool(db.get_setting(conn, "openrouter_api_key")),
        "llm_model": db.get_setting(conn, "llm_model") or categorize.DEFAULT_MODEL,
        "last_sync_ok": db.get_setting(conn, "last_sync_ok"),
        "last_log": dict(last_log) if last_log else None,
        "last_llm_error": db.get_setting(conn, "last_llm_error"),
        "review_count": conn.execute("SELECT COUNT(*) FROM transactions WHERE needs_review=1").fetchone()[0],
        "plaid_undecided": plaid.undecided_count(conn),   # accounts from Plaid waiting for you to say what they are
        "horizon_days": int(db.get_setting(conn, "horizon_days", "90") or 90),
        "syncing": _sync_lock.locked() or _inv_lock.locked(),
        "primary_account": db.get_setting(conn, "primary_account"),
        "auto_ai_on_sync": (db.get_setting(conn, "auto_ai_on_sync", "1") or "1") == "1",
        "rentcast_configured": rentcast.configured(conn),
        "database": "postgres" if db.using_postgres() else "sqlite",
        "version": os.environ.get("RUNWAY_VERSION") or "dev",
        "owners": owner_choices(conn),
        "user": getattr(_current, "user", None),
    }


def owner_choices(conn) -> list[str]:
    """First names of everyone who has signed in (plus any owner already set), for the account Owner menus."""
    names = [r["first_name"] for r in conn.execute("SELECT first_name FROM users WHERE first_name IS NOT NULL ORDER BY last_seen")]
    names += [r["owner"] for r in conn.execute("SELECT DISTINCT owner FROM accounts WHERE owner IS NOT NULL AND owner<>''")]
    out = []
    for n in names:
        if n and n not in out and n != "Joint":
            out.append(n)
    return out


def api_push(conn, _q, _b):
    _priv, pub = webpush.vapid_keys(conn)
    return {"public_key": pub, "prefs": notify.prefs(conn),
            "devices": [{"endpoint": s["endpoint"], "device": s["device"], "created": s["created"], "last_ok": s["last_ok"],
                         "last_error": s["last_error"]} for s in notify.subscriptions(conn)],
            "recent": db.rows(conn.execute("SELECT title, sent FROM notify_log ORDER BY sent DESC LIMIT 8"))}


def api_push_subscribe(conn, _q, body):
    try:
        notify.subscribe(conn, body.get("subscription") or {}, str(body.get("device") or ""), (getattr(_current, "user", None) or {}).get("sub"))
    except ValueError as e:
        raise ApiError(str(e))
    return {"ok": True}


def api_push_unsubscribe(conn, _q, body):
    notify.unsubscribe(conn, str(body.get("endpoint") or ""))
    return {"ok": True}


def api_push_prefs(conn, _q, body):
    try:
        return notify.save_prefs(conn, body)
    except ValueError as e:
        raise ApiError(str(e))


def api_push_test(conn, _q, body):
    r = notify.send_all(conn, {"title": "Runway notifications are on", "body": "You'll hear about card payments, low balances and missed bills here.",
                               "url": "/#overview", "tag": "test"}, only=body.get("endpoint") or None)
    if not r["sent"]:
        raise ApiError("Nothing was delivered. " + ("; ".join(r["failed"]) if r["failed"] else "No device is subscribed."))
    return r


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
    accts = db.rows(conn.execute("SELECT * FROM accounts ORDER BY hidden, kind, COALESCE(display_name, name)"))
    items = {r["plaid_account_id"]: r for r in db.rows(conn.execute(
        "SELECT p.plaid_account_id, p.mask, p.item_id, i.products, i.institution_name, s.last_statement_date, s.next_due_date "
        "FROM plaid_accounts p JOIN plaid_items i ON i.item_id=p.item_id "
        "LEFT JOIN card_statements s ON s.plaid_account_id=p.plaid_account_id"))}
    for a in accts:   # which providers this account can use, and (cards) its latest statement dates
        it = items.get(a.get("plaid_account_id") or "")
        a["plaid_link"] = ({"institution": it["institution_name"], "mask": it["mask"],
                            "transactions": "transactions" in (it["products"] or ""),
                            "closed": it["last_statement_date"], "due": it["next_due_date"],
                            "statement_note": db.get_setting(conn, f"plaid_stmt_note:{it['item_id']}")} if it else None)
    return accts


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
        sets.append(f"{k}=?")
        vals.append(v)
    if sets:
        conn.execute(f"UPDATE accounts SET {', '.join(sets)} WHERE id=?", (*vals, acct_id))
    if body.get("provider"):
        try:
            plaidbank.set_provider(conn, acct_id, body["provider"])
        except ValueError as e:
            raise ApiError(str(e))
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
            where.append("t.category IS NULL AND COALESCE(t.is_split, 0)=0")
        else:
            family = [cat] + categories.descendants(conn, cat)   # a category includes its subcategories
            ph = ",".join("?" * len(family))
            # a split transaction counts under every category it's split into, not the one on the row
            where.append(f"((COALESCE(t.is_split, 0)=0 AND t.category IN ({ph})) OR EXISTS "
                         f"(SELECT 1 FROM tx_splits s WHERE s.tx_id=t.id AND s.category IN ({ph})))")
            args.extend(family * 2)
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
        "SELECT t.*, " + db.label_sql("a") + " AS account_name, a.kind AS account_kind, r.name AS recurring_name "
        "FROM transactions t JOIN accounts a ON a.id=t.account_id LEFT JOIN recurring r ON r.id=t.recurring_id "
        f"WHERE {' AND '.join(where)} ORDER BY t.posted DESC, t.id LIMIT ? OFFSET ?"
    )
    items = db.rows(conn.execute(sql, (*args, limit, offset)))
    parts = splits.of(conn, [t["id"] for t in items if t["is_split"]])
    orders = retail.for_transactions(conn, [t["id"] for t in items if t["amount"] < 0])
    logos = merchants.for_transactions(conn, items)
    for t in items:
        t["splits"] = parts.get(t["id"], [])
        t["retail"] = orders.get(t["id"])
        t["logo"] = f"/api/merchants/{urllib.parse.quote(logos[t['id']], safe='')}/logo" if t["id"] in logos else None
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


def api_tx_split(conn, _q, body, tx_id):
    """Split one transaction across categories, or (with no parts) put it back together."""
    parts = body.get("splits")
    if not isinstance(parts, list):
        raise ApiError("Send the parts to split this into")
    try:
        saved = splits.set_splits(conn, tx_id, parts)
    except splits.SplitError as e:
        raise ApiError(str(e))
    return {"ok": True, "splits": saved}


def api_tx_bulk(conn, _q, body, *_):
    """Change many transactions at once (the checkboxes on Transactions)."""
    ids = body.get("ids")
    if not isinstance(ids, list):
        raise ApiError("Select some transactions first")
    try:
        n = categorize.bulk_update(conn, ids, body.get("category") or None, body.get("payee") or None,
                                   bool(body.get("reviewed")))
    except ValueError as e:
        raise ApiError(str(e))
    return {"ok": True, "updated": n}


def api_tx_accept(conn, _q, _b, tx_id):
    categorize.accept_suggestion(conn, tx_id)
    return {"ok": True}


# ------------------------------------------------------------------------------------------------ Amazon and Target

def api_retail(conn, _q, _b):
    return retail.status(conn)


def api_retail_token(conn, _q, _b):
    """A new key for the browser extension; shown once."""
    return {"token": retail.new_token(conn)}


def api_retail_token_remove(conn, _q, _b):
    retail.remove_token(conn)
    return {"ok": True}


def api_retail_settings(conn, _q, body):
    if "ai" in body:
        db.set_setting(conn, "retail_ai", "1" if body.get("ai") else "0")
    return {"ok": True}


def api_retail_match(conn, _q, _b):
    """Categorize items still waiting (with the AI, if set up), then match and split again."""
    items = retail.categorize_items(conn)
    out = retail.match_and_apply(conn)
    for ch in conn.execute("SELECT id FROM retail_charges WHERE tx_id IS NOT NULL AND applied IS NOT NULL").fetchall():
        r = retail.apply(conn, ch["id"])
        if r in ("split", "category"):
            out[r] += 1
    out["items"] = items
    return out


def api_retail_order(conn, _q, _b, oid):
    try:
        return retail.order_detail(conn, oid)
    except retail.RetailError as e:
        raise ApiError(str(e), 404)


def api_retail_item(conn, _q, body, item_id):
    try:
        return retail.set_item_category(conn, int(item_id), body.get("category") or "", body.get("remember", True) is not False)
    except retail.RetailError as e:
        raise ApiError(str(e))


def api_retail_unlink(conn, _q, _b, charge_id):
    retail.unlink(conn, charge_id)
    return {"ok": True}


def api_retail_link(conn, _q, body, charge_id):
    try:
        return {"result": retail.link(conn, charge_id, body.get("tx_id") or "")}
    except retail.RetailError as e:
        raise ApiError(str(e))


def api_retail_apply(conn, _q, _b, charge_id):
    """Split this charge's transaction by its items even if you had categorized it yourself."""
    return {"result": retail.apply(conn, charge_id, force=True)}


def api_retail_candidates(conn, _q, _b, charge_id):
    try:
        return retail.candidates(conn, charge_id)
    except retail.RetailError as e:
        raise ApiError(str(e), 404)


# The browser extension's calls (/api/ext/...), signed with its key rather than a sign-in. Each takes one page the
# extension read from the store, and says what to fetch next.
TARGET_DETAIL_URLS = {
    # {base} is Target's order API as the extension found it on target.com, {key} its API key, {order} the order.
    "store": ["{base}/{order}/store_order_details?key={key}"],
    "online": ["{base}/{order}/orders?key={key}", "{base}/orders/{order}?key={key}"],
}


def ext_start(conn, body):
    r = body.get("retailer")
    if r not in retail.RETAILERS:
        raise retail.RetailError("Unknown store")
    return {"since": retail.since(conn, r), "detail_urls": TARGET_DETAIL_URLS if r == "target" else None,
            "version": os.environ.get("RUNWAY_VERSION") or "dev"}


def ext_amazon_transactions(conn, body):
    return retail.amazon_transactions(conn, str(body.get("html") or ""))


def ext_amazon_order(conn, body):
    return retail.amazon_order(conn, str(body.get("order_number") or ""), str(body.get("html") or ""))


def ext_target_history(conn, body):
    return retail.target_history(conn, body.get("data"), body.get("purchase_type"))


def ext_target_order(conn, body):
    return retail.target_order(conn, str(body.get("order_number") or ""), body.get("data"))


def ext_finish(conn, body):
    return retail.finish(conn, body.get("retailer"))


EXT_ROUTES = {
    "/api/ext/ping": lambda conn, body: {"ok": True},
    "/api/ext/start": ext_start,
    "/api/ext/amazon/transactions": ext_amazon_transactions,
    "/api/ext/amazon/order": ext_amazon_order,
    "/api/ext/target/history": ext_target_history,
    "/api/ext/target/order": ext_target_order,
    "/api/ext/finish": ext_finish,
}
MAX_EXT_BODY = 16 * 1024 * 1024      # one store page (Amazon's order pages are large)
EXTENSION_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "extension")


def extension_zip() -> bytes | None:
    """The browser extension (extension/ next to runway/), zipped into a folder to load unpacked."""
    import io
    import zipfile
    if not os.path.isfile(os.path.join(EXTENSION_DIR, "manifest.json")):
        return None
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(EXTENSION_DIR):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for f in sorted(files):
                if f.startswith("."):
                    continue
                full = os.path.join(root, f)
                z.write(full, os.path.join("runway-orders", os.path.relpath(full, EXTENSION_DIR)))
    return buf.getvalue()


# ------------------------------------------------------------------------------------------------ equity and Carta

def carta_redirect_uri(origin: str | None = None) -> str:
    """Where Carta sends you back: Runway's public address (or the one you're using) + /carta/callback. It must be
    registered as a redirect URI for your app in Carta's developer portal."""
    base = (os.environ.get("RUNWAY_PUBLIC_URL") or "").rstrip("/")
    if not base and origin and host_allowed(urllib.parse.urlsplit(origin).netloc):
        base = origin.rstrip("/")
    return (base or "http://localhost:8765") + "/carta/callback"


def api_equity(conn, _q, _b):
    out = equity.overview(conn)
    out["carta"] = carta.settings(conn)
    return out


def _equity(fn, *args):
    try:
        return fn(*args)
    except equity.EquityError as e:
        raise ApiError(str(e))


def api_equity_company_add(conn, _q, body):
    return {"id": _equity(equity.save_company, conn, body)}


def api_equity_company_update(conn, _q, body, cid):
    return {"id": _equity(equity.save_company, conn, body, cid)}


def api_equity_company_remove(conn, _q, _b, cid):
    equity.remove_company(conn, cid)
    return {"ok": True}


def api_equity_grant_add(conn, _q, body, cid):
    return {"id": _equity(equity.save_grant, conn, cid, body)}


def api_equity_grant_update(conn, _q, body, gid):
    row = conn.execute("SELECT company_id FROM equity_grants WHERE id=?", (gid,)).fetchone()
    if not row:
        raise ApiError("Grant not found", 404)
    return {"id": _equity(equity.save_grant, conn, row["company_id"], body, gid)}


def api_equity_grant_remove(conn, _q, _b, gid):
    equity.remove_grant(conn, gid)
    return {"ok": True}


def api_carta_settings(conn, _q, body):
    try:
        carta.save_settings(conn, body)
    except carta.CartaError as e:
        raise ApiError(str(e))
    return {"ok": True, "redirect_uri": carta_redirect_uri(body.get("origin"))}


def api_carta_connect(conn, _q, body):
    """Where to send you to approve Runway at Carta."""
    try:
        return {"url": carta.authorize_url(conn, carta_redirect_uri(body.get("origin")))}
    except carta.CartaError as e:
        raise ApiError(str(e))


def api_carta_sync(conn, _q, _b):
    try:
        return carta.sync(conn)
    except carta.CartaError as e:
        raise ApiError(str(e), 502)


def api_carta_disconnect(conn, _q, _b):
    carta.disconnect(conn)
    return {"ok": True}


def api_categories(conn, _q, _b):
    cats = categories.all_categories(conn)
    counts = {r["category"]: r["n"] for r in conn.execute(f"SELECT category, COUNT(*) AS n FROM {splits.PARTS} t GROUP BY category")}
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
    names = {r["id"]: db.account_label(r) for r in conn.execute("SELECT id, name, display_name, owner FROM accounts")}
    out = []
    for r in sorted(rules.load(conn), key=lambda r: (r["match"] or "~", r["id"])):
        r["summary"] = rules.describe(r, names)
        out.append(r)
    return out


def api_rule_add(conn, _q, body):
    try:
        rid = rules.save(conn, body)
        return {"ok": True, "id": rid, "updated": rules.apply_rule(conn, rid) if body.get("apply") else 0}
    except rules.RuleError as e:
        raise ApiError(str(e))


def api_rule_update(conn, _q, body, rule_id):
    try:
        rules.save(conn, body, int(rule_id))
    except rules.RuleError as e:
        raise ApiError(str(e))
    return {"ok": True}


def api_rule_preview(conn, _q, body):
    """What a rule you're writing would match, before you save it."""
    return rules.preview(conn, body)


def api_rule_apply(conn, _q, _b, rule_id):
    try:
        return {"ok": True, "updated": rules.apply_rule(conn, int(rule_id))}
    except rules.RuleError as e:
        raise ApiError(str(e), 404)


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
    return start, start + relativedelta(months=1)


def _month_totals(conn, start: date, end: date) -> dict:
    """Net amount per category for the month, across checking, savings and cards (not loans or investments)."""
    rows_ = conn.execute(
        f"SELECT t.category AS category, SUM(t.amount) AS total FROM {splits.PARTS} t JOIN accounts a ON a.id=t.account_id "
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
    budget_rows = {r["category"]: r for r in db.rows(conn.execute("SELECT * FROM budgets"))}
    budgets = {k: r["amount"] for k, r in budget_rows.items()}
    usual = {p["category"]: p["usual"] for p in forecast.budget_plan(conn, today)}
    own = {c["name"]: round(-totals.get(c["name"], 0.0), 2) for c in cats}
    out = []
    for c in cats:
        below = [k["name"] for k in cats if c["name"] in k["path"][:-1]]
        spent = round(own[c["name"]] + sum(own[k] for k in below), 2)
        b = budgets.get(c["name"])
        out.append({"name": c["name"], "parent": c["parent"], "path": c["path"], "depth": c["depth"], "top": c["top"],
                    "has_children": bool(below), "budget": b,
                    "pay_with": budget_rows[c["name"]]["pay_with"] if c["name"] in budget_rows else None,
                    "usual_account": usual.get(c["name"]),
                    "spent": spent, "own_spent": own[c["name"]], "left": round(b - spent, 2) if b is not None else None})
    current = start <= today < end
    return {
        "month": f"{start:%Y-%m}",
        "days_in_month": days,
        "day": today.day if current else (days if end <= today else 0),
        "categories": out,  # tree order: each category followed by its subcategories
        "income": round(sum(totals.get(c, 0.0) for c in income_cats), 2),
        "uncategorized": round(-totals.get(None, 0.0), 2),
        # accounts a category can be paid with: cards and cash accounts
        "pay_accounts": [{"id": r["id"], "name": r["name"], "kind": r["kind"]} for r in conn.execute(
            "SELECT id, COALESCE(display_name, name) AS name, kind FROM accounts WHERE hidden=0 "
            "AND kind IN ('credit','checking','savings') ORDER BY kind='credit' DESC, COALESCE(display_name, name)")],
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


def _ym(q, key="end") -> str:
    v = (q.get(key, [""])[0] or f"{date.today():%Y-%m}")[:7]
    try:
        date(int(v[:4]), int(v[5:7]), 1)
    except ValueError:
        raise ApiError("Month must look like 2026-09")
    return v


def _day(q, key: str, default: date) -> str:
    v = q.get(key, [""])[0]
    if not v:
        return default.isoformat()
    try:
        return date.fromisoformat(v).isoformat()
    except ValueError:
        raise ApiError("Dates must look like 2026-09-01")


def _months(q) -> int:
    return max(2, min(int(q.get("months", ["12"])[0]), 36))


def _span(q):
    """start (inclusive) and end (exclusive) days; this month by default."""
    first = date.today().replace(day=1)
    return _day(q, "start", first), _day(q, "end", first + relativedelta(months=1))


def api_report_spending(conn, q, _b):
    try:
        return reports.spending_over_time(conn, _ym(q), _months(q), q.get("group", ["category"])[0])
    except ValueError as e:
        raise ApiError(str(e))


def api_report_income(conn, q, _b):
    return reports.income_vs_spending(conn, _ym(q), _months(q))


def api_report_merchants(conn, q, _b):
    return reports.merchants(conn, *_span(q))


def api_report_merchant(conn, q, _b):
    return reports.merchant(conn, q.get("name", [""])[0], _ym(q), _months(q))


def api_report_breakdown(conn, q, _b):
    return reports.breakdown(conn, *_span(q))


def api_report_transactions(conn, q, _b):
    return reports.transactions(conn, *_span(q), q.get("category", [""])[0] or None, q.get("merchant", [""])[0] or None)


def api_budget_set(conn, _q, body):
    cat = body.get("category") or ""
    if not conn.execute("SELECT 1 FROM categories WHERE name=? AND is_transfer=0 AND is_income=0", (cat,)).fetchone():
        raise ApiError("Pick a spending category")
    if "pay_with" in body and "amount" not in body:   # just choosing the card
        acct = body.get("pay_with") or None
        if acct and not conn.execute("SELECT 1 FROM accounts WHERE id=?", (acct,)).fetchone():
            raise ApiError("Account not found")
        conn.execute("UPDATE budgets SET pay_with=? WHERE category=?", (acct, cat))
        return {"ok": True}
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
        if token.startswith("http") and not token.startswith("https://"):
            raise ApiError("A SimpleFIN access URL must start with https://.")
        access_url = token if token.startswith("https://") else simplefin.claim_setup_token(token)
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
        "SELECT id FROM transactions WHERE COALESCE(is_split, 0)=0 "
        "AND (category IS NULL OR (needs_review=1 AND COALESCE(category_source, '') <> 'manual'))"
    )]
    conn.execute(
        "UPDATE transactions SET category=NULL, category_source=NULL, confidence=NULL "
        "WHERE needs_review=1 AND COALESCE(category_source, '') <> 'manual'"
    )
    return categorize.categorize(conn, ids)


def api_plaid_status(conn, _q, _b):
    items = db.rows(conn.execute("SELECT item_id, institution_name, env, created_at, last_sync, error, products FROM plaid_items ORDER BY institution_name"))
    for it in items:
        it["bank"] = plaidbank.is_bank_item(it)
        it["products"] = sorted(plaidbank.products(it))
        it["duplicates"] = plaid.duplicates(conn, it["item_id"])
        if it["bank"]:
            it["accounts"] = db.rows(conn.execute(
                "SELECT p.plaid_account_id AS id, p.name, p.official_name, p.subtype, p.type, p.mask, p.current AS balance, "
                "p.ignored, a.id AS account_id, " + db.label_sql("a") + " AS account_name, a.provider, "
                "s.last_statement_date, s.last_statement_balance, s.next_due_date "
                "FROM plaid_accounts p LEFT JOIN accounts a ON a.plaid_account_id=p.plaid_account_id "
                "LEFT JOIN card_statements s ON s.plaid_account_id=p.plaid_account_id WHERE p.item_id=? ORDER BY p.type, p.name",
                (it["item_id"],)))
        else:
            it["accounts"] = db.rows(conn.execute(
                "SELECT id, name, official_name, subtype, mask, balance, hidden, account_id FROM inv_accounts WHERE item_id=? ORDER BY name",
                (it["item_id"],)))
            it["candidates"] = plaid.investment_candidates(conn, it["item_id"])   # what each account could be
    return {"configured": plaid.configured(conn), "env": db.get_setting(conn, "plaid_env", "production"),
            "client_id": db.get_setting(conn, "plaid_client_id") or "", "items": items,
            "redirect_uri": plaid.redirect_uri(conn),
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
    kind = body.get("kind") or "investments"
    if body.get("item_id"):
        kind = "investments"   # reconnecting: the connection keeps its products
    if kind not in ("investments", "bank", "cards"):
        raise ApiError("Unknown kind of connection")
    try:
        token = plaid.link_token(conn, body.get("item_id") or None, kind)
    except plaid.PlaidError as e:
        if not (kind == "bank" and e.code in ("INVALID_PRODUCT", "PRODUCTS_NOT_SUPPORTED", "INVALID_FIELD")):
            raise ApiError(str(e), 502)
        try:   # Transactions isn't enabled for this Plaid account: card statements only
            token, kind = plaid.link_token(conn, None, "cards"), "cards"
        except plaid.PlaidError:
            raise ApiError(str(e), 502)
    # Kept so Link can pick up where it left off when a bank sends you back to /plaid/oauth (possibly in another
    # browser, like Safari from the installed app). Link tokens expire after 4 hours.
    db.set_setting(conn, "plaid_pending_link", json.dumps({"token": token, "kind": kind, "item_id": body.get("item_id") or None,
                                                          "at": time.time()}))
    return {"link_token": token, "kind": kind}


def api_plaid_oauth_resume(conn, _q, _b):
    """The Link session to continue after the bank's sign-in page sends you back."""
    try:
        p = json.loads(db.get_setting(conn, "plaid_pending_link") or "{}")
    except ValueError:
        p = {}
    if not p.get("token") or time.time() - p.get("at", 0) > 4 * 3600:
        raise ApiError("That bank connection has expired. Start it again from Settings → Connections.", 404)
    return {"link_token": p["token"], "kind": p.get("kind"), "item_id": p.get("item_id")}


def api_plaid_exchange(conn, _q, body):
    try:
        item_id = plaid.exchange(conn, body.get("public_token") or "", body.get("institution") or {})
        res = plaid.sync_item(conn, item_id)
        # The same login linked a second time: its accounts would count twice. Undo it (at Plaid too) and say so.
        if any(d["adds_nothing"] for d in plaid.duplicates(conn, item_id)):
            name = (body.get("institution") or {}).get("name") or "That institution"
            plaid.remove_item(conn, item_id)
            conn.commit()
            raise ApiError(f"{name} is already connected with these accounts, so nothing was added. To fix a connection, "
                           "use its Sync or Reconnect button instead.", 409)
        item = conn.execute("SELECT products FROM plaid_items WHERE item_id=?", (item_id,)).fetchone()
        if plaidbank.is_bank_item(item):
            n = len(res.pop("new", []))
            return {"ok": True, "item_id": item_id, "bank": True, "new_transactions": n, **res}
        res["hidden_simplefin"] = plaid.hide_simplefin_duplicates(conn, item_id)
        res["prices"] = refresh_prices(conn)
        return {"ok": True, "item_id": item_id, **res}
    except plaid.PlaidError as e:
        raise ApiError(str(e), 502)


def api_plaid_item_sync(conn, _q, _b, item_id):
    try:
        res = plaid.sync_item(conn, item_id)
        if "new" in res:   # a bank connection
            res["new_transactions"] = len(res["new"])
            categorize.categorize(conn, res.pop("new"))
            recurring.auto_match(conn)
            return {"ok": True, "bank": True, **res}
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


def api_plaid_match(conn, _q, body):
    pid, target = str(body.get("plaid_account_id") or ""), str(body.get("target") or "")
    try:
        if conn.execute("SELECT 1 FROM inv_accounts WHERE id=? AND source='plaid'", (pid,)).fetchone():
            return plaid.match_investment(conn, pid, target)
        return plaidbank.match(conn, pid, target)
    except ValueError as e:
        raise ApiError(str(e))


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
    ("POST", "/api/transactions/bulk", api_tx_bulk),
    ("POST", "/api/transactions/{id}/category", api_tx_category),
    ("POST", "/api/transactions/{id}/accept", api_tx_accept),
    ("POST", "/api/transactions/{id}/split", api_tx_split),
    ("POST", "/api/transactions/{id}/recurring", api_tx_recurring),
    ("POST", "/api/overrides", api_override_set),
    ("DELETE", "/api/overrides", api_override_delete),
    ("GET", "/api/push", api_push),
    ("POST", "/api/push/subscribe", api_push_subscribe),
    ("POST", "/api/push/unsubscribe", api_push_unsubscribe),
    ("POST", "/api/push/prefs", api_push_prefs),
    ("POST", "/api/push/test", api_push_test),
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
    ("GET", "/api/reports/spending", api_report_spending),
    ("GET", "/api/reports/income", api_report_income),
    ("GET", "/api/reports/merchants", api_report_merchants),
    ("GET", "/api/reports/merchant", api_report_merchant),
    ("GET", "/api/reports/breakdown", api_report_breakdown),
    ("GET", "/api/reports/transactions", api_report_transactions),
    ("GET", "/api/rules", api_rules),
    ("POST", "/api/rules", api_rule_add),
    ("POST", "/api/rules/preview", api_rule_preview),
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
    ("POST", "/api/plaid/match", api_plaid_match),
    ("GET", "/api/plaid/oauth_resume", api_plaid_oauth_resume),
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
    ("GET", "/api/equity", api_equity),
    ("POST", "/api/equity/companies", api_equity_company_add),
    ("POST", "/api/equity/companies/{id}", api_equity_company_update),
    ("POST", "/api/equity/companies/{id}/remove", api_equity_company_remove),
    ("POST", "/api/equity/companies/{id}/grants", api_equity_grant_add),
    ("POST", "/api/equity/grants/{id}", api_equity_grant_update),
    ("POST", "/api/equity/grants/{id}/remove", api_equity_grant_remove),
    ("POST", "/api/carta/settings", api_carta_settings),
    ("POST", "/api/carta/connect", api_carta_connect),
    ("POST", "/api/carta/sync", api_carta_sync),
    ("POST", "/api/carta/disconnect", api_carta_disconnect),
    ("GET", "/api/retail", api_retail),
    ("POST", "/api/retail/token", api_retail_token),
    ("POST", "/api/retail/token/remove", api_retail_token_remove),
    ("POST", "/api/retail/settings", api_retail_settings),
    ("POST", "/api/retail/match", api_retail_match),
    ("GET", "/api/retail/orders/{id}", api_retail_order),
    ("POST", "/api/retail/items/{id}", api_retail_item),
    ("POST", "/api/retail/charges/{id}/unlink", api_retail_unlink),
    ("POST", "/api/retail/charges/{id}/link", api_retail_link),
    ("POST", "/api/retail/charges/{id}/apply", api_retail_apply),
    ("GET", "/api/retail/charges/{id}/candidates", api_retail_candidates),
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


MAX_JSON_BODY = 1024 * 1024          # API requests are small; anything bigger is refused before it's read
MAX_RESTORE_BODY = 200 * 1024 * 1024
REQUEST_TIMEOUT = 60                 # seconds a client may stall while sending or receiving (slow-client protection)
MAX_CONCURRENT_REQUESTS = 64

# Plaid Link (Settings → Connections) loads its script and iframe from Plaid; nothing else comes from elsewhere.
PLAID_ORIGINS = "https://cdn.plaid.com"
PLAID_API = "https://production.plaid.com https://sandbox.plaid.com"


def content_security_policy(nonce: str | None = None) -> str:
    """Only Runway's own scripts run (the page's <script> tags carry a per-response nonce; scripts they add, like Plaid
    Link, are trusted through 'strict-dynamic'). No framing, no plugins, no <base> tricks."""
    scripts = f"'nonce-{nonce}' 'strict-dynamic' 'self' {PLAID_ORIGINS}" if nonce else "'self'"
    return ("default-src 'self'; "
            f"script-src {scripts}; "
            "style-src 'self' 'unsafe-inline'; "     # inline style attributes (and Plaid Link) need this
            "img-src 'self' data:; font-src 'self'; "
            f"connect-src 'self' {PLAID_API}; "
            f"frame-src {PLAID_ORIGINS}; worker-src 'self'; manifest-src 'self'; "
            "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


def request_ref() -> str:
    return secrets.token_hex(4)


class Handler(BaseHTTPRequestHandler):
    server_version = "Runway"
    sys_version = ""                  # don't advertise the Python version
    timeout = REQUEST_TIMEOUT

    def log_message(self, fmt, *args):
        pass   # the standard per-request line includes query strings (sign-in codes); log_request writes our own

    def log_request(self, code="-", size="-"):
        # One line per request, path only (no query string: /auth/callback carries sign-in codes).
        path = urllib.parse.urlsplit(getattr(self, "path", "") or "").path
        if path == "/healthz":
            return   # the container health check, every minute
        started = getattr(self, "_started", None)
        ms = f" {int((time.monotonic() - started) * 1000)}ms" if started else ""
        print(f"{self.client_address[0]} {getattr(self, 'command', '-')} {path} {code}{ms}", flush=True)

    def _security_headers(self, nonce: str | None = None) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", content_security_policy(nonce))
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin-allow-popups")
        # The browser extension reads the answers to its own calls (it has its key, and permission for this site).
        self.send_header("Cross-Origin-Resource-Policy",
                         "cross-origin" if getattr(self, "_ext_call", False) else "same-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()")
        if oidc.config()["secure_cookie"]:   # served over https: tell browsers never to use plain http
            self.send_header("Strict-Transport-Security", "max-age=31536000")

    def _send(self, status: int, body: bytes, ctype: str = "application/json", cache: str = "no-store",
              nonce: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self._security_headers(nonce)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _error(self, e: BaseException) -> None:
        """An unexpected failure: log the details, show only a reference to them."""
        ref = request_ref()
        print(f"[error {ref}] {self.command} {urllib.parse.urlsplit(self.path).path}", flush=True)
        traceback.print_exception(type(e), e, e.__traceback__)
        self._json(500, {"error": f"Something went wrong on Runway's side (reference {ref}; the details are in its log)."})

    def _body_length(self, limit: int) -> int | None:
        """The request's Content-Length, or None (after answering) if it's missing a number or too big."""
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n < 0 or n > limit:
            self.close_connection = True
            self._json(413 if n > limit else 400, {"error": "That request is too large." if n > limit else "Bad request."})
            return None
        return n

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
        self._security_headers()
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
            # Signing out is a POST from the app (see _logout), so another site can't sign you out with a link or image.
            self._page(405, "Sign out from Runway", "Use the sign-out button at the bottom of Runway's sidebar.", ("/", "Open Runway")); return True
        if url.path == "/auth/signed-out":
            self._page(200, "Signed out", "You've signed out of Runway.", ("/auth/login", "Sign in again")); return True
        return False

    def send_response(self, code, message=None):
        self._responded = True
        super().send_response(code, message)

    def _dispatch(self, method: str) -> None:
        self._started, self._responded = time.monotonic(), False
        self._ext_call = False
        try:
            self._route(method)
        except Exception as e:   # never show internals; never leave the browser hanging
            if not self._responded:
                self._error(e)
            else:
                traceback.print_exc()

    def _same_site(self) -> bool:
        """A state-changing request must come from Runway's own pages (defense in depth beside the X-Runway header)."""
        if (self.headers.get("Sec-Fetch-Site") or "").lower() == "cross-site":
            return False
        origin = self.headers.get("Origin")
        if origin:   # "null" (sandboxed frames, file: pages) is never Runway
            return origin != "null" and host_allowed(urllib.parse.urlsplit(origin).netloc)
        return True

    def _logout(self) -> None:
        """POST /auth/logout from the app: end the session and say where to go next (the provider's sign-out page)."""
        with db.session() as conn:
            target = oidc.logout(conn, self._cookie("runway_session")) if oidc.enabled() else "/"
        body = json.dumps({"redirect": target}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Set-Cookie", self._cookie_header("runway_session", "", 0))
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _route(self, method: str) -> None:
        url = urllib.parse.urlsplit(self.path)
        if url.path == "/healthz" and method == "GET":   # container health check: says nothing about your data
            return self._send(200, b"ok", "text/plain")
        if not self._host_ok():
            return self._send(403, b"Runway doesn't recognise this address. Add it to RUNWAY_ALLOWED_HOSTS.", "text/plain")
        if url.path.startswith("/api/ext/"):   # the browser extension: its own key instead of a sign-in
            return self._extension(method, url.path)
        if method != "GET" and not self._same_site():
            return self._json(403, {"error": "forbidden"})
        if url.path.startswith("/auth/") and method == "GET" and self._auth_routes(url):
            return
        if url.path == "/auth/logout" and method == "POST":
            if self.headers.get("X-Runway") != "1":
                return self._json(403, {"error": "forbidden"})
            return self._logout()
        # The look of the sign-in pages is public; everything else needs you signed in.
        if url.path not in PUBLIC_FILES:
            self.user = self._user()
            if not self.user:
                if url.path.startswith("/api/"):
                    return self._json(401, {"error": "You've been signed out.", "login": "/auth/login"})
                back = (url.path or "/") + ("?" + url.query if url.query else "")   # e.g. /plaid/oauth?oauth_state_id=…
                return self._redirect("/auth/login?next=" + urllib.parse.quote(back, safe=""))
        if url.path == "/carta/callback" and method == "GET":
            return self._carta_callback(url)
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
            self._security_headers()
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)
            return
        if method == "GET" and url.path.startswith("/api/merchants/") and url.path.endswith("/logo"):
            mid = urllib.parse.unquote(url.path[len("/api/merchants/"):-len("/logo")])
            with db.session() as conn:
                found = merchants.logo(conn, mid)
            if not found:
                return self._send(404, b"", "text/plain")
            data, ctype = found
            etag = '"' + hashlib.sha256(data).hexdigest()[:20] + '"'
            if self.headers.get("If-None-Match") == etag:
                self.send_response(304)
                self.send_header("ETag", etag)
                self._security_headers()
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "private, max-age=604800")
            self.send_header("ETag", etag)
            self._security_headers()
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)
            return
        if method == "GET" and url.path == "/api/retail/extension.zip":
            data = extension_zip()
            if data is None:
                return self._json(404, {"error": "The extension isn't included with this copy of Runway."})
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", 'attachment; filename="runway-orders-extension.zip"')
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self._security_headers()
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)
            return
        if method == "POST" and url.path == "/api/restore":
            from . import backup
            n = self._body_length(MAX_RESTORE_BODY)
            if n is None:
                return
            if not n:
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
            n = self._body_length(MAX_JSON_BODY)
            if n is None:
                return
            if n:
                try:
                    body = json.loads(self.rfile.read(n).decode() or "{}")
                except (json.JSONDecodeError, UnicodeDecodeError):
                    return self._json(400, {"error": "Bad JSON"})
                if not isinstance(body, dict):
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
            except sqlalchemy.exc.OperationalError as e:
                if "locked" in str(e):
                    return self._json(503, {"error": "Runway is busy saving a sync. Try again in a few seconds."})
                return self._error(e)
            except (ValueError, TypeError, KeyError) as e:   # almost always a value in the request Runway can't read
                ref = request_ref()
                print(f"[bad request {ref}] {method} {url.path}: {type(e).__name__}: {e}", flush=True)
                return self._json(400, {"error": f"Runway couldn't read one of the values sent (reference {ref})."})
        return self._json(404, {"error": "Not found"})

    def _carta_callback(self, url) -> None:
        """Back from approving Runway at Carta: trade the code for a token, read your equity, and go to Net worth."""
        q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        if q.get("error"):
            return self._page(400, "Carta wasn't connected", q.get("error_description") or q["error"], ("/#setup/connections", "Back to Settings"))
        try:
            with db.session() as conn:
                if not q.get("mock"):
                    carta.finish_authorize(conn, q.get("code", ""), q.get("state", ""))
            with db.session() as conn:
                carta.sync(conn)
        except carta.CartaError as e:
            return self._page(502, "Carta wasn't connected", str(e), ("/#setup/connections", "Back to Settings"))
        self._redirect("/#networth")

    def _extension(self, method: str, path: str) -> None:
        """A call from Runway's browser extension. It carries the key made under Settings → Connections (a bearer
        token, which a web page can't send on your behalf), so it needs no sign-in or same-site checks."""
        self._ext_call = True
        fn = EXT_ROUTES.get(path)
        if method != "POST" or not fn:
            return self._json(404, {"error": "Not found"})
        with db.session() as conn:
            ok = retail.check_token(conn, self.headers.get("Authorization"))
        if not ok:
            self.close_connection = True
            return self._json(401, {"error": "Runway doesn't know this key. Make a new one under Settings → Connections."})
        n = self._body_length(MAX_EXT_BODY)
        if n is None:
            return
        try:
            body = json.loads(self.rfile.read(n).decode() or "{}") if n else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self._json(400, {"error": "Bad JSON"})
        if not isinstance(body, dict):
            return self._json(400, {"error": "Bad JSON"})
        try:
            with db.session() as conn:
                return self._json(200, fn(conn, body))
        except retail.RetailError as e:
            return self._json(400, {"error": str(e)})
        except sqlalchemy.exc.OperationalError as e:
            if "locked" in str(e):
                return self._json(503, {"error": "Runway is busy saving a sync. Try again in a few seconds."})
            return self._error(e)

    def _static(self, path: str) -> None:
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        full = os.path.realpath(os.path.join(STATIC, rel))
        if os.path.commonpath([full, STATIC]) != STATIC or not os.path.isfile(full):
            full = INDEX   # the app handles its own routes (#budget, /plaid/oauth, ...)
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        gz_ok = "gzip" in (self.headers.get("Accept-Encoding") or "")
        if full == INDEX:
            # A fresh nonce per page, so only this page's own <script> tags may run (see content_security_policy).
            nonce = secrets.token_urlsafe(16)
            with open(full, "rb") as f:
                data = f.read().replace(b"<script ", f'<script nonce="{nonce}" '.encode())
            return self._send_file(data, ctype, "no-store", None, gz_ok, nonce)
        entry = _static_entry(full)
        if self.headers.get("If-None-Match") == entry["etag"]:
            self.send_response(304)
            self.send_header("ETag", entry["etag"])
            self.send_header("Cache-Control", "no-cache")
            self._security_headers()
            self.end_headers()
            return
        # "no-cache" = keep a copy but check it's current each time (a cheap 304), so updates show up at once.
        self._send_file(entry["data"], ctype, "no-cache", entry["etag"], gz_ok, None, entry.get("gz"))

    def _send_file(self, data: bytes, ctype: str, cache: str, etag: str | None, gz_ok: bool, nonce: str | None,
                   gz: bytes | None = None) -> None:
        if gz_ok and _compressible(ctype) and len(data) > 1024:
            data, encoded = gz or gzip.compress(data, 6), True
        else:
            encoded = False
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        self.send_header("Vary", "Accept-Encoding")
        if etag:
            self.send_header("ETag", etag)
        if encoded:
            self.send_header("Content-Encoding", "gzip")
        self._security_headers(nonce)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def do_GET(self):
        self._dispatch("GET")

    def do_HEAD(self):
        self._dispatch("GET")   # same answer without the body (_send and _send_file skip it for HEAD)

    def do_POST(self):
        self._dispatch("POST")

    def do_DELETE(self):
        self._dispatch("DELETE")


STATIC = os.path.realpath(STATIC)
INDEX = os.path.join(STATIC, "index.html")
_static_files: dict[str, dict] = {}
_static_lock = threading.Lock()


def _compressible(ctype: str) -> bool:
    return ctype.startswith("text/") or ctype in ("application/javascript", "application/json", "image/svg+xml",
                                                  "application/manifest+json")


def _static_entry(full: str) -> dict:
    """A static file's bytes, ETag and gzip'd copy, kept in memory until the file changes."""
    st = os.stat(full)
    key = (st.st_mtime_ns, st.st_size)
    with _static_lock:
        entry = _static_files.get(full)
        if entry and entry["key"] == key:
            return entry
    with open(full, "rb") as f:
        data = f.read()
    ctype = mimetypes.guess_type(full)[0] or ""
    entry = {"key": key, "data": data, "etag": '"' + hashlib.sha256(data).hexdigest()[:20] + '"',
             "gz": gzip.compress(data, 6) if _compressible(ctype) and len(data) > 1024 else None}
    with _static_lock:
        _static_files[full] = entry
    return entry


class Server(ThreadingHTTPServer):
    """The standard threaded server, with a cap on requests handled at once so a flood can't exhaust the machine."""
    daemon_threads = True
    request_queue_size = 128

    def __init__(self, *a, **k):
        self._slots = threading.BoundedSemaphore(MAX_CONCURRENT_REQUESTS)
        super().__init__(*a, **k)

    def process_request(self, request, client_address):
        if not self._slots.acquire(timeout=REQUEST_TIMEOUT):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


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
    from . import secretbox
    problems = secretbox.check_config()
    if problems:
        raise SystemExit("\n".join(problems))
    if db.using_postgres() and not os.environ.get("RUNWAY_SECRET_KEY"):
        print(f"Note: set RUNWAY_SECRET_KEY. Without it, the key that encrypts your saved bank access and API keys is "
              f"{secretbox.key_file_path()}, and losing that file means reconnecting them.", flush=True)
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
        plaid.hide_all_duplicates(conn)  # an institution linked through both Plaid and SimpleFIN is counted once
        for r in conn.execute("SELECT item_id FROM plaid_items WHERE COALESCE(products, 'investments') LIKE '%investments%'").fetchall():
            plaid.update_investment_accounts(conn, r["item_id"])   # investment accounts from Plaid in your accounts
        oidc.backfill_users(conn)        # people who signed in before owners existed
    global AUTO_SYNC
    AUTO_SYNC = auto_sync
    if auto_sync:
        threading.Thread(target=background_sync, daemon=True).start()
    httpd = Server((host, port), Handler)
    where = f"http://localhost:{port}" if host in ("127.0.0.1", "localhost") else f"port {port} on all network addresses"
    print(f"Runway is running at {where}  (data: {db.describe()})"
          f"{'  · sign-in via ' + oidc.config()['issuer'] if oidc.enabled() else ''}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
