"""The app's state (the sidebar, the getting-started checklist), the Overview's forecast and its amounts you've changed,
and the general settings."""
from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime

from sqlalchemy import delete, func, select

from ... import brands, categorize, db, forecast, merchants, monitoring, plaid, realie, recurring
from ... import settings_keys as sk
from ...models import Account, Budget, Override, Recurring, SyncLog, Transaction, User
from ..common import ApiError, _current
from ..sync import _inv_lock, _sync_lock, bank_configured
from .recurring import recurring_logos


def api_state(conn, _q, _b):
    last_log = conn.execute(select(SyncLog.at, SyncLog.ok, SyncLog.message).order_by(SyncLog.id.desc()).limit(1)).fetchone()
    return {
        "connected": bank_configured(conn),
        "brands": brands.account_brands(conn),   # each account's institution logo (or letter)
        "connection_logos": brands.connection_logos(conn),   # each bank connection's, by its institution's name
        "simplefin": bool(db.get_setting(conn, sk.SIMPLEFIN_ACCESS_URL)),
        "has_api_key": bool(db.get_setting(conn, sk.OPENROUTER_API_KEY)),
        "llm_model": db.get_setting(conn, sk.LLM_MODEL) or categorize.DEFAULT_MODEL,
        "last_sync_ok": with_offset(db.get_setting(conn, sk.LAST_SYNC_OK)),
        "last_log": {**dict(last_log), "at": with_offset(last_log["at"], utc=True)} if last_log else None,
        "sync_warnings": json.loads(db.get_setting(conn, sk.LAST_SYNC_WARNINGS) or "[]"),   # what banks said on that sync
        "last_llm_error": db.get_setting(conn, sk.LAST_LLM_ERROR),
        "review_count": conn.execute(select(func.count()).select_from(Transaction)
                                     .where(Transaction.needs_review == 1, db.not_investment())).fetchone()[0],
        "plaid_undecided": plaid.undecided_count(conn),   # accounts from Plaid waiting for you to say what they are
        "horizon_days": int(db.get_setting(conn, sk.HORIZON_DAYS, "90") or 90),
        "syncing": _sync_lock.locked() or _inv_lock.locked(),
        "primary_account": db.get_setting(conn, sk.PRIMARY_ACCOUNT),
        "auto_ai_on_sync": (db.get_setting(conn, sk.AUTO_AI_ON_SYNC, "1") or "1") == "1",
        "realie_configured": realie.configured(conn),
        "finnhub_configured": bool(db.get_setting(conn, sk.FINNHUB_API_KEY)),
        "logodev_configured": merchants.configured(conn),
        "database": "postgres" if db.using_postgres() else "sqlite",
        "version": os.environ.get("RUNWAY_VERSION") or "dev",
        "sentry": monitoring.browser_config(getattr(_current, "user", None)),   # the web app's error reports (runway/monitoring.py), or None
        "owners": owner_choices(conn),
        "user": getattr(_current, "user", None),
        "setup": setup_steps(conn),
    }


def with_offset(stamp: str | None, utc: bool = False) -> str | None:
    """A stored timestamp with its UTC offset, so the browser shows it in its own time zone. Settings hold the server's
    local time ("2026-09-30T07:02:00"), the sync log UTC ("2026-09-30 12:02:00"); one that has an offset keeps it."""
    if not stamp:
        return stamp
    try:
        t = datetime.fromisoformat(stamp)
    except ValueError:
        return stamp
    if t.tzinfo is None:
        t = t.replace(tzinfo=UTC) if utc else t.astimezone()
    return t.isoformat(timespec="seconds")


def owner_choices(conn) -> list[str]:
    """First names of everyone who has signed in (plus any owner already set), for the account Owner menus."""
    names = [r["first_name"] for r in conn.execute(
        select(User.first_name).where(User.first_name.is_not(None)).order_by(User.last_seen))]
    names += [r["owner"] for r in conn.execute(
        select(Account.owner).distinct().where(Account.owner.is_not(None), Account.owner != ""))]
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
    # a recurring item wears its logo: the one you chose for it, else its last matched transaction's
    ids = sorted({e["recurring_id"] for e in fc["events"] if e.get("recurring_id")})
    if ids:
        logos = recurring_logos(conn, db.rows(conn.execute(select(Recurring.id, Recurring.name).where(Recurring.id.in_(ids)))))
        for e in fc["events"]:
            e["logo"] = logos.get(e.get("recurring_id"))
    name = func.coalesce(Account.display_name, Account.name).label("name")
    fc["all_accounts"] = db.rows(conn.execute(
        select(Account.id, name, Account.kind, Account.balance, Account.balance_date, Account.owed_positive, Account.hidden)
        .order_by(Account.kind, name)))
    as_of = {a["id"]: a["balance_date"] for a in fc["all_accounts"]}
    for a in fc["accounts"]:   # the day each forecast balance is from, for the Overview's "Balance as of"
        a["balance_date"] = as_of.get(a["id"])
    return fc


def api_override_set(conn, _q, body):
    key = str(body.get("key") or "")
    if not key.startswith(("rec:", "card:", "cardclose:", "stmt:")):
        raise ApiError("Unknown item")
    try:
        amount = db.number(body.get("amount"))
    except (TypeError, ValueError):
        raise ApiError("Enter an amount") from None
    db.upsert(conn, Override, {"key": key, "amount": amount}, key=["key"])
    return {"ok": True}


def api_override_delete(conn, _q, body):
    conn.execute(delete(Override).where(Override.key == str(body.get("key") or "")))
    return {"ok": True}


def setup_steps(conn) -> dict:
    """The getting-started checklist on the Overview: which steps are done, and whether it's been put away."""
    checking = conn.execute(select(func.count()).select_from(Account)
                            .where(Account.hidden == 0, Account.kind == "checking")).fetchone()[0]
    return {
        "bank": bank_configured(conn) and bool(conn.execute(select(Account.id).limit(1)).fetchone()),
        "primary": bool(db.get_setting(conn, sk.PRIMARY_ACCOUNT)) or checking == 1,
        "recurring": bool(conn.execute(select(Recurring.id).limit(1)).fetchone()),
        "budgets": bool(conn.execute(select(Budget.category).where(Budget.amount > 0).limit(1)).fetchone()),
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
        if acct and not conn.execute(
                select(Account.id).where(Account.id == acct, Account.kind.in_(["checking", "savings"]))).fetchone():
            raise ApiError("Pick a checking or savings account")
        db.set_setting(conn, sk.PRIMARY_ACCOUNT, acct)
    if "auto_ai_on_sync" in body:
        db.set_setting(conn, sk.AUTO_AI_ON_SYNC, "1" if body.get("auto_ai_on_sync") else "0")
    if "horizon_days" in body:
        db.set_setting(conn, sk.HORIZON_DAYS, str(max(14, min(int(body["horizon_days"]), 365))))
    if "setup_dismissed" in body:
        db.set_setting(conn, sk.SETUP_DISMISSED, "1" if body.get("setup_dismissed") else None)
    return {"ok": True}
