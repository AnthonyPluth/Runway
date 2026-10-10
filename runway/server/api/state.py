"""The app's state (the sidebar, the getting-started checklist), the Overview's forecast and its amounts you've changed,
and the general settings."""
from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime

from sqlalchemy import delete, func, select

from ...domain import brands, categorize, forecast, recurring
from ...storage import db
from ... import monitoring, validate
from ...providers import plaid
from ...storage import settings_keys as sk
from ...storage.models import Account, Budget, Override, Recurring, SyncLog, Transaction, User
from ..common import ApiError, _current, clamped_int, query_int, text

# An amount you've changed in the forecast: an amount of money (validate.MAX_AMOUNT), as typed.
_amount = validate.Validator(ApiError, drop="", missing="Enter an amount", not_number="Enter an amount",
                             too_large="The amount is too large")
from ..sync import _inv_lock, _sync_lock, bank_configured
from .recurring import one_time_item, recurring_logos, set_amount


def api_state(conn, _q, _b):
    last_log = conn.execute(select(SyncLog.at, SyncLog.ok, SyncLog.message).order_by(SyncLog.id.desc()).limit(1)).fetchone()
    # One query, the secrets among them decrypted: one saved under another RUNWAY_SECRET_KEY (a backup made elsewhere)
    # reads as not set, so Settings asks for it again.
    s = db.get_settings(conn, STATE_SETTINGS)
    present = {k for k in SECRETS_SET if s[k]}
    connected = bank_configured(conn)
    return {
        "connected": connected,
        "brands": brands.account_brands(conn),   # each account's institution logo (or letter)
        "connection_logos": brands.connection_logos(conn),   # each bank connection's, by its institution's name
        "simplefin": sk.SIMPLEFIN_ACCESS_URL in present,
        "has_api_key": sk.OPENROUTER_API_KEY in present,
        "llm_model": s[sk.LLM_MODEL] or categorize.DEFAULT_MODEL,             # (categorize.llm_model)
        "card_ai_model": s[sk.CARD_AI_MODEL] or categorize.DEFAULT_CARD_MODEL,   # (categorize.card_ai_model)
        # what an empty model field means, for Settings to show as its placeholder
        "llm_model_default": categorize.DEFAULT_MODEL, "card_ai_model_default": categorize.DEFAULT_CARD_MODEL,
        "last_sync_ok": with_offset(s[sk.LAST_SYNC_OK]),
        "last_log": {**dict(last_log), "at": with_offset(last_log["at"], utc=True)} if last_log else None,
        "sync_warnings": json.loads(s[sk.LAST_SYNC_WARNINGS] or "[]"),   # what banks said on that sync
        "last_llm_error": s[sk.LAST_LLM_ERROR],
        "last_backup": with_offset(s[sk.LAST_BACKUP]),   # the last backup downloaded from Settings
        "review_count": conn.execute(select(func.count()).select_from(Transaction)
                                     .where(Transaction.needs_review == 1, db.not_investment())).fetchone()[0],
        "plaid_undecided": plaid.undecided_count(conn),   # accounts from Plaid waiting for you to say what they are
        "horizon_days": int(s[sk.HORIZON_DAYS] or 90),
        "syncing": _sync_lock.locked() or _inv_lock.locked(),
        "primary_account": s[sk.PRIMARY_ACCOUNT],
        "auto_ai_on_sync": (s[sk.AUTO_AI_ON_SYNC] or "1") == "1",
        "churn_ai_web": (s[sk.CHURN_AI_WEB] or "1") == "1",   # card suggestions search the web
        "realie_configured": sk.REALIE_API_KEY in present,        # (realie.configured)
        "finnhub_configured": sk.FINNHUB_API_KEY in present,
        "logodev_configured": sk.LOGODEV_TOKEN in present,        # (merchants.configured)
        "database": "postgres" if db.using_postgres() else "sqlite",
        "version": os.environ.get("RUNWAY_VERSION") or "dev",
        "sentry": monitoring.browser_config(getattr(_current, "user", None)),   # the web app's error reports (runway/monitoring.py), or None
        "owners": owner_choices(conn),
        "user": getattr(_current, "user", None),
        "setup": setup_steps(conn, s, connected),
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
    horizon = query_int(q, "days", int(db.get_setting(conn, sk.HORIZON_DAYS, "90") or 90), 14, 365, "number of days")
    fc, moving = forecast.project(conn, date.today(), horizon)
    forecast.move_old_keys(conn, moving)   # card payment edits saved under their due date's key, applied already
    fc["missed"] = recurring.missed(conn)
    # The alerts put away on Overview, by their message: one stays hidden while its message reads the same, so a new
    # problem worded differently (or at another link) shows again.
    fc["dismissed_warnings"] = json.loads(db.get_setting(conn, sk.OVERVIEW_WARNINGS_DISMISSED) or "[]")
    # a recurring item wears its logo: the one you chose for it, else its last matched transaction's
    ids = sorted({e["recurring_id"] for e in fc["events"] + fc["charges"] if e.get("recurring_id")})
    if ids:
        logos = recurring_logos(conn, db.rows(conn.execute(select(Recurring.id, Recurring.name).where(Recurring.id.in_(ids)))))
        for e in fc["events"] + fc["charges"]:
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
    amount = _amount.amount(body.get("amount"), "amount", cents=False, required=True)
    # A one-time item has no usual amount to differ from: changing its one date changes the item (Recurring shows it),
    # and any old edit of that date goes with it. What it had before comes back for Undo.
    item = one_time_item(conn, key)
    if item:
        previous = set_amount(conn, item["id"], amount)
        conn.execute(delete(Override).where(Override.key == key))
        return {"ok": True, "item": {"id": item["id"], "name": item["name"]}, "previous": previous}
    db.upsert(conn, Override, {"key": key, "amount": amount}, key=["key"])
    return {"ok": True}


def api_override_delete(conn, _q, body):
    conn.execute(delete(Override).where(Override.key == str(body.get("key") or "")))
    return {"ok": True}


# The keys api_state only says are set (when they can be read): the bank connection and the services' API keys.
SECRETS_SET = (sk.SIMPLEFIN_ACCESS_URL, sk.OPENROUTER_API_KEY, sk.REALIE_API_KEY, sk.FINNHUB_API_KEY, sk.LOGODEV_TOKEN)
# The settings api_state reads, in one go.
STATE_SETTINGS = (sk.LLM_MODEL, sk.CARD_AI_MODEL, sk.LAST_SYNC_OK, sk.LAST_SYNC_WARNINGS, sk.LAST_LLM_ERROR, sk.LAST_BACKUP,
                  sk.HORIZON_DAYS, sk.PRIMARY_ACCOUNT, sk.AUTO_AI_ON_SYNC, sk.CHURN_AI_WEB, sk.SETUP_DISMISSED, *SECRETS_SET)


def setup_steps(conn, settings: dict | None = None, connected: bool | None = None) -> dict:
    """The getting-started checklist on the Overview: which steps are done, and whether it's been put away. `settings`
    (with PRIMARY_ACCOUNT and SETUP_DISMISSED) and `connected` (bank_configured), when they're already at hand."""
    s = settings if settings is not None else db.get_settings(conn, (sk.PRIMARY_ACCOUNT, sk.SETUP_DISMISSED))
    connected = bank_configured(conn) if connected is None else connected
    checking = conn.execute(select(func.count()).select_from(Account)
                            .where(Account.hidden == 0, Account.kind == "checking")).fetchone()[0]
    return {
        "bank": connected and bool(conn.execute(select(Account.id).limit(1)).fetchone()),
        "primary": bool(s[sk.PRIMARY_ACCOUNT]) or checking == 1,
        "recurring": bool(conn.execute(select(Recurring.id).limit(1)).fetchone()),
        "budgets": bool(conn.execute(select(Budget.category).where(Budget.amount > 0).limit(1)).fetchone()),
        "dismissed": s[sk.SETUP_DISMISSED] == "1",
    }


def _dismissed_warnings(v) -> list[str]:
    """The Overview's alerts put away, as a list of their messages: a warning is a sentence or two, and there are never
    many at once. Each message stays hidden while it reads the same, so a warning worded differently shows again."""
    if not isinstance(v, list) or len(v) > 50 or any(not isinstance(m, str) or len(m) > 400 for m in v):
        raise ApiError('Send "overview_warnings_dismissed" as a list of messages')
    return v


def api_settings(conn, _q, body):
    # Checked before anything is saved.
    days = None
    if "horizon_days" in body:
        if body["horizon_days"] in (None, ""):
            raise ApiError("Enter the number of days")
        days = clamped_int(body["horizon_days"], "number of days", 90, 14, 365)
    dismissed = None
    if "overview_warnings_dismissed" in body:
        dismissed = _dismissed_warnings(body.get("overview_warnings_dismissed"))
    if "openrouter_api_key" in body:
        key = text(body.get("openrouter_api_key"), "openrouter_api_key").strip()
        db.set_setting(conn, sk.OPENROUTER_API_KEY, key or None)
        db.set_setting(conn, sk.LAST_LLM_ERROR, None)
    if "llm_model" in body:
        db.set_setting(conn, sk.LLM_MODEL, text(body.get("llm_model"), "llm_model").strip() or None)
        db.set_setting(conn, sk.LAST_LLM_ERROR, None)
    if "card_ai_model" in body:
        db.set_setting(conn, sk.CARD_AI_MODEL, text(body.get("card_ai_model"), "card_ai_model").strip() or None)
        db.set_setting(conn, sk.LAST_LLM_ERROR, None)
    if "primary_account" in body:
        acct = text(body.get("primary_account"), "primary_account") or None
        if acct and not conn.execute(
                select(Account.id).where(Account.id == acct, Account.kind.in_(["checking", "savings"]))).fetchone():
            raise ApiError("Pick a checking or savings account")
        db.set_setting(conn, sk.PRIMARY_ACCOUNT, acct)
    if "auto_ai_on_sync" in body:
        db.set_setting(conn, sk.AUTO_AI_ON_SYNC, str(validate.flag(body.get("auto_ai_on_sync"))))
    if "churn_ai_web" in body:
        db.set_setting(conn, sk.CHURN_AI_WEB, str(validate.flag(body.get("churn_ai_web"))))
    if days is not None:
        db.set_setting(conn, sk.HORIZON_DAYS, str(days))
    if dismissed is not None:
        db.set_setting(conn, sk.OVERVIEW_WARNINGS_DISMISSED, json.dumps(dismissed) if dismissed else None)
    if "setup_dismissed" in body:
        db.set_setting(conn, sk.SETUP_DISMISSED, "1" if validate.on(body.get("setup_dismissed")) else None)
    return {"ok": True}
