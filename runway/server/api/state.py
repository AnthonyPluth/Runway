"""The app's state (the sidebar, the getting-started checklist), the Overview's forecast and its amounts you've changed,
and the general settings."""
from __future__ import annotations

import os
from datetime import date

from ... import brands, categorize, db, forecast, merchants, monitoring, plaid, realie, recurring
from ... import settings_keys as sk
from ..common import ApiError, _current
from ..sync import _inv_lock, _sync_lock, bank_configured
from .transactions import tx_logos


def api_state(conn, _q, _b):
    last_log = conn.execute("SELECT at, ok, message FROM sync_log ORDER BY id DESC LIMIT 1").fetchone()
    return {
        "connected": bank_configured(conn),
        "brands": brands.account_brands(conn),   # each account's institution logo (or letter)
        "simplefin": bool(db.get_setting(conn, sk.SIMPLEFIN_ACCESS_URL)),
        "has_api_key": bool(db.get_setting(conn, sk.OPENROUTER_API_KEY)),
        "llm_model": db.get_setting(conn, sk.LLM_MODEL) or categorize.DEFAULT_MODEL,
        "last_sync_ok": db.get_setting(conn, sk.LAST_SYNC_OK),
        "last_log": dict(last_log) if last_log else None,
        "last_llm_error": db.get_setting(conn, sk.LAST_LLM_ERROR),
        "review_count": conn.execute(f"SELECT COUNT(*) FROM transactions WHERE needs_review=1 AND {db.NOT_INVESTMENT}").fetchone()[0],
        "plaid_undecided": plaid.undecided_count(conn),   # accounts from Plaid waiting for you to say what they are
        "horizon_days": int(db.get_setting(conn, sk.HORIZON_DAYS, "90") or 90),
        "syncing": _sync_lock.locked() or _inv_lock.locked(),
        "primary_account": db.get_setting(conn, sk.PRIMARY_ACCOUNT),
        "auto_ai_on_sync": (db.get_setting(conn, sk.AUTO_AI_ON_SYNC, "1") or "1") == "1",
        "realie_configured": realie.configured(conn),
        "logodev_configured": merchants.configured(conn),
        "database": "postgres" if db.using_postgres() else "sqlite",
        "version": os.environ.get("RUNWAY_VERSION") or "dev",
        "sentry": monitoring.browser_config(),   # the web app's error reports (runway/monitoring.py), or None
        "owners": owner_choices(conn),
        "user": getattr(_current, "user", None),
        "setup": setup_steps(conn),
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


def api_overview(conn, q, _b):
    horizon = int(q.get("days", [db.get_setting(conn, sk.HORIZON_DAYS, "90") or 90])[0])
    horizon = max(14, min(horizon, 365))
    fc = forecast.build(conn, date.today(), horizon)
    fc["missed"] = recurring.missed(conn)
    # a recurring item wears the logo of the last transaction matched to it
    ids = sorted({e["recurring_id"] for e in fc["events"] if e.get("recurring_id")})
    if ids:
        ph = ",".join("?" * len(ids))
        last: dict[int, dict] = {}
        for t in db.rows(conn.execute(f"SELECT * FROM transactions WHERE recurring_id IN ({ph}) ORDER BY posted DESC", ids)):
            last.setdefault(t["recurring_id"], t)
        logos = tx_logos(conn, list(last.values()))
        for e in fc["events"]:
            t = last.get(e.get("recurring_id"))
            e["logo"] = logos.get(t["id"]) if t else None
    fc["all_accounts"] = db.rows(conn.execute(
        "SELECT id, COALESCE(display_name, name) AS name, kind, balance, balance_date, owed_positive, hidden "
        "FROM accounts ORDER BY kind, name"
    ))
    return fc


def api_override_set(conn, _q, body):
    key = str(body.get("key") or "")
    if not key.startswith(("rec:", "card:", "stmt:")):
        raise ApiError("Unknown item")
    try:
        amount = db.number(body.get("amount"))
    except (TypeError, ValueError):
        raise ApiError("Enter an amount") from None
    conn.execute(
        "INSERT INTO overrides(key, amount) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET amount=excluded.amount", (key, amount)
    )
    return {"ok": True}


def api_override_delete(conn, _q, body):
    conn.execute("DELETE FROM overrides WHERE key=?", (str(body.get("key") or ""),))
    return {"ok": True}


def setup_steps(conn) -> dict:
    """The getting-started checklist on the Overview: which steps are done, and whether it's been put away."""
    checking = conn.execute("SELECT COUNT(*) FROM accounts WHERE hidden=0 AND kind='checking'").fetchone()[0]
    return {
        "bank": bank_configured(conn) and bool(conn.execute("SELECT 1 FROM accounts LIMIT 1").fetchone()),
        "primary": bool(db.get_setting(conn, sk.PRIMARY_ACCOUNT)) or checking == 1,
        "recurring": bool(conn.execute("SELECT 1 FROM recurring LIMIT 1").fetchone()),
        "budgets": bool(conn.execute("SELECT 1 FROM budgets WHERE amount>0 LIMIT 1").fetchone()),
        "dismissed": db.get_setting(conn, sk.SETUP_DISMISSED) == "1",
    }


def api_settings(conn, _q, body):
    if "openrouter_api_key" in body:
        key = (body.get("openrouter_api_key") or "").strip()
        db.set_setting(conn, sk.OPENROUTER_API_KEY, key or None)
        db.set_setting(conn, sk.LAST_LLM_ERROR, None)
    if "llm_model" in body:
        db.set_setting(conn, sk.LLM_MODEL, (body.get("llm_model") or "").strip() or None)
        db.set_setting(conn, sk.LAST_LLM_ERROR, None)
    if "primary_account" in body:
        acct = body.get("primary_account") or None
        if acct and not conn.execute("SELECT 1 FROM accounts WHERE id=? AND kind IN ('checking','savings')", (acct,)).fetchone():
            raise ApiError("Pick a checking or savings account")
        db.set_setting(conn, sk.PRIMARY_ACCOUNT, acct)
    if "auto_ai_on_sync" in body:
        db.set_setting(conn, sk.AUTO_AI_ON_SYNC, "1" if body.get("auto_ai_on_sync") else "0")
    if "horizon_days" in body:
        db.set_setting(conn, sk.HORIZON_DAYS, str(max(14, min(int(body["horizon_days"]), 365))))
    if "setup_dismissed" in body:
        db.set_setting(conn, sk.SETUP_DISMISSED, "1" if body.get("setup_dismissed") else None)
    return {"ok": True}
