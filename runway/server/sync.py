"""Keeping the data fresh: the bank and investment syncs, when they're due, and the background loop that runs them."""
from __future__ import annotations

import contextlib
import sys
import threading
import time
from datetime import date, datetime, timedelta

from sqlalchemy import insert, or_, select

from .. import categorize, db, merchants, monitoring, networth, notify, plaid, plaidbank, portfolio, prices, realie, recurring, retail, sfinvest, simplefin
from .. import settings_keys as sk
from ..models import Holding, InvAccount, InvTransaction, ManualPosition, PlaidItem, Security, SyncLog
from .common import ApiError

DAILY_SYNC_HOUR = 7          # banks and cards (SimpleFIN and Plaid) sync once a day, on the first check after this hour
                             # (local time; the image's TZ is America/Chicago): late enough for overnight ACH, early
                             # enough to review in the morning. Opening Runway only catches up a missed one.
PLAID_SYNC_HOUR = DAILY_SYNC_HOUR   # Plaid's quota is small, so opening Runway or pressing Sync doesn't ask it again
                             # that day; a connection's own Sync button in Settings still does.
PLAID_REFRESH_AT = (6, 30)   # before that sync, Plaid is told to fetch from the banks (Transactions Refresh), so the
                             # sync gets the banks as of now and not as of Plaid's own last visit. Only before
                             # PLAID_SYNC_HOUR: a refresh after the day's sync would be a call for nothing.
VISIT_SYNC_MINUTES = 60      # opening Runway refreshes investments (prices) if they're older than this
_sync_lock = threading.Lock()
_inv_lock = threading.Lock()
AUTO_SYNC = True             # False with --no-sync: no daily sync and no sync on opening the app


def plaid_banks(conn) -> bool:
    return plaid.configured(conn) and any(plaidbank.is_bank_item(r) for r in conn.execute(select(PlaidItem.products)).fetchall())


def bank_configured(conn) -> bool:
    """Whether there's anything to sync bank accounts from: SimpleFIN, or a Plaid bank or card connection."""
    return bool(db.get_setting(conn, sk.SIMPLEFIN_ACCESS_URL)) or plaid_banks(conn)


def run_sync() -> dict:
    if not _sync_lock.acquire(blocking=False):
        raise ApiError("A sync is already running.", 409)
    try:
        try:
            with db.session() as conn:
                access_url = db.get_setting(conn, sk.SIMPLEFIN_ACCESS_URL)
                has_plaid = plaid_banks(conn)
                if not access_url and not has_plaid:
                    raise ApiError("Connect SimpleFIN or a Plaid bank in Settings first.")
                use_plaid = has_plaid and plaid_due(db.get_setting(conn, sk.LAST_PLAID_BANK_SYNC))
                if use_plaid:   # counted when asked, so a failing Plaid isn't asked again until tomorrow
                    db.set_setting(conn, sk.LAST_PLAID_BANK_SYNC, datetime.now().isoformat(timespec="seconds"))
                    conn.commit()
                result = simplefin.sync(conn, access_url) if access_url else {"new": [], "errors": []}
                if use_plaid:   # accounts set to Plaid, and card statements
                    pb = plaidbank.sync_all(conn)
                    result["new"] += pb["new"]
                    result["errors"] += pb["errors"]
                try:   # logos: Plaid's new ones, and big brands' (a nice-to-have; never fail the sync)
                    merchants.note_sites(conn)
                    merchants.fetch_logos(conn)
                except Exception:
                    monitoring.report()
                counts = categorize.categorize(conn, result["new"])
                recurring.auto_match(conn)
                try:   # new card transactions may be Amazon, Target or Costco orders the extension already sent
                    retail.match_and_apply(conn)
                except Exception:
                    monitoring.report()
                if conn.execute(select(InvAccount.id).where(InvAccount.source == "simplefin")).fetchone():
                    try:
                        refresh_prices(conn)
                    except Exception:  # prices are a nice-to-have; never fail the bank sync over them
                        monitoring.report()   # per-ticker network failures are handled inside; this is a real bug
                msg = f"{len(result['new'])} new transactions"
                if result["errors"]:
                    msg += " · bank messages: " + "; ".join(result["errors"])[:500]
                try:   # home values: weekly and optional; Realie's own errors stop it quietly, so anything else is a bug
                    realie.refresh_due(conn)
                except Exception:
                    monitoring.report()
                networth.summary(conn)   # record today's net worth
                conn.execute(insert(SyncLog).values(ok=1, message=msg))
                db.set_setting(conn, sk.LAST_SYNC_OK, datetime.now().isoformat(timespec="seconds"))
                return {"new": len(result["new"]), "categorized": counts, "bank_messages": result["errors"]}
        except ApiError:
            raise
        except simplefin.SimpleFinError as e:
            _record_failed_sync(str(e))
            raise ApiError(str(e), 502) from e
        except Exception as e:
            monitoring.report()
            _record_failed_sync(f"The sync stopped with an error ({type(e).__name__}); the details are in Runway's log.")
            raise ApiError("The sync failed; the details are in Runway's log.", 500) from e
    finally:
        _sync_lock.release()


def _record_failed_sync(message: str) -> None:
    """Written in a session of its own: the sync's session rolled back, and the failure must still show (the
    sidebar's "Last sync failed", and the can't-sync notification)."""
    with db.session() as conn:
        conn.execute(insert(SyncLog).values(ok=0, message=message))


def run_investment_sync() -> dict:
    """Pull holdings and activity from every Plaid connection, then refresh price history."""
    if not _inv_lock.acquire(blocking=False):
        raise ApiError("An investment sync is already running.", 409)
    try:
        with db.session() as conn:
            out = {"items": 0, "errors": [], "prices": {}}
            if plaid.configured(conn) and conn.execute(select(PlaidItem.item_id).limit(1)).fetchone() \
                    and plaid_due(db.get_setting(conn, sk.LAST_PLAID_INV_SYNC)):
                db.set_setting(conn, sk.LAST_PLAID_INV_SYNC, datetime.now().isoformat(timespec="seconds"))
                conn.commit()
                out = plaid.sync_all(conn)
            if not conn.execute(select(InvAccount.id).limit(1)).fetchone():
                return out
            out["prices"] = refresh_prices(conn)
            db.set_setting(conn, sk.LAST_INV_SYNC, datetime.now().isoformat(timespec="seconds"))
            return out
    finally:
        _inv_lock.release()


def refresh_prices(conn) -> dict:
    s = Security
    tickers = [r["ticker"] for r in conn.execute(
        select(s.ticker).distinct().where(s.is_cash == 0, s.ticker.is_not(None),
                                          or_(s.id.in_(select(Holding.security_id)), s.id.in_(select(InvTransaction.security_id)),
                                              s.id.in_(select(ManualPosition.security_id)))))]
    tickers.append(prices.BENCHMARK)
    out = prices.refresh(conn, tickers, date.today() - timedelta(days=portfolio.HISTORY_DAYS + 10))
    sfinvest.recapture_all(conn)   # re-check reported position values against the fresh prices
    prices.fill_security_types(conn)
    return out


def _older_than(stamp: str | None, **delta) -> bool:
    return not stamp or datetime.now() - datetime.fromisoformat(stamp) > timedelta(**delta)


def _not_since(hour: int, last: str | None, now: datetime | None) -> bool:
    """Whether `last` is before the most recent `hour` o'clock (today's, or yesterday's if it isn't that late yet)."""
    now = now or datetime.now()
    if not last:
        return True
    since = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if now < since:
        since -= timedelta(days=1)
    return datetime.fromisoformat(last) < since


def daily_due(last: str | None, now: datetime | None = None) -> bool:
    """Whether the banks haven't synced since the most recent DAILY_SYNC_HOUR."""
    return _not_since(DAILY_SYNC_HOUR, last, now)


def plaid_due(last: str | None, now: datetime | None = None) -> bool:
    """Whether Plaid hasn't been asked since the most recent PLAID_SYNC_HOUR (today's, or yesterday's before it)."""
    return _not_since(PLAID_SYNC_HOUR, last, now)


def plaid_refresh_due(last: str | None, now: datetime | None = None) -> bool:
    """Between PLAID_REFRESH_AT and PLAID_SYNC_HOUR, if Plaid hasn't been told to refresh since PLAID_REFRESH_AT."""
    now = now or datetime.now()
    start = now.replace(hour=PLAID_REFRESH_AT[0], minute=PLAID_REFRESH_AT[1], second=0, microsecond=0)
    if not start <= now < now.replace(hour=PLAID_SYNC_HOUR, minute=0, second=0, microsecond=0):
        return False
    return not last or datetime.fromisoformat(last) < start


def refresh_plaid() -> None:
    """Tell Plaid to fetch from the banks ahead of the daily sync. A problem is logged and never stops the sync."""
    try:
        with db.session() as conn:
            if not plaid_banks(conn) or not plaid_refresh_due(db.get_setting(conn, sk.LAST_PLAID_REFRESH)):
                return
            db.set_setting(conn, sk.LAST_PLAID_REFRESH, datetime.now().isoformat(timespec="seconds"))
            conn.commit()
            for e in plaidbank.refresh_all(conn):
                print(f"Plaid refresh: {e}", file=sys.stderr)
    except Exception:
        monitoring.report()


def _sync_everything(bank: bool, invest: bool) -> None:
    if bank:
        with contextlib.suppress(ApiError):
            run_sync()
        notify_now()
    if invest:
        with contextlib.suppress(ApiError):
            run_investment_sync()


def notify_now() -> None:
    """Send any new alerts to subscribed devices. Never lets a notification problem break a sync."""
    try:
        with db.session() as conn:
            notify.run(conn)
    except Exception:
        monitoring.report()


def sync_on_visit() -> dict:
    """Someone opened Runway: catch up a daily bank sync that was missed (Runway was off at DAILY_SYNC_HOUR, say), and
    refresh investments if they're more than VISIT_SYNC_MINUTES old."""
    if not AUTO_SYNC:   # --no-sync / RUNWAY_NO_SYNC=1: only when you ask (Settings or the sync buttons)
        return {"started": False}
    with db.session() as conn:
        # Banks sync once a day: a visit only starts the day's sync if it hasn't happened (at most one try every 3
        # hours, as in background_sync, so a failing bank isn't asked on every visit).
        bank = bank_configured(conn) and daily_due(db.get_setting(conn, sk.LAST_SYNC_OK)) \
            and _older_than(db.get_setting(conn, sk.LAST_AUTO_SYNC_ATTEMPT), hours=3)
        has_inv = bool(conn.execute(select(InvAccount.id).limit(1)).fetchone())
        invest = has_inv and _older_than(db.get_setting(conn, sk.LAST_INV_SYNC), minutes=VISIT_SYNC_MINUTES)
        # Only a sync that starts counts as an attempt: one skipped for a running sync would otherwise put the next
        # visit's catch-up off for 3 hours.
        start = (bank or invest) and not _sync_lock.locked() and not _inv_lock.locked()
        if start and bank:
            db.set_setting(conn, sk.LAST_AUTO_SYNC_ATTEMPT, datetime.now().isoformat(timespec="seconds"))
    if start:
        threading.Thread(target=_sync_everything, args=(bank, invest), daemon=True).start()
        return {"started": True}
    return {"started": False}


def background_sync() -> None:
    while True:
        refresh_plaid()
        try:
            with db.session() as conn:
                configured = bank_configured(conn)
                last = db.get_setting(conn, sk.LAST_SYNC_OK)
                last_try = db.get_setting(conn, sk.LAST_AUTO_SYNC_ATTEMPT)
                last_inv = db.get_setting(conn, sk.LAST_INV_SYNC)
                plaid_bank = plaid_banks(conn) and plaid_due(db.get_setting(conn, sk.LAST_PLAID_BANK_SYNC))
                plaid_inv = plaid.configured(conn) and plaid_due(db.get_setting(conn, sk.LAST_PLAID_INV_SYNC)) and any(
                    "investments" in (r["products"] or "investments") for r in conn.execute(select(PlaidItem.products)))
            # Don't hammer the banks after failures: at most one automatic attempt every 3 hours. Plaid's daily sync
            # goes ahead regardless: it's asked at most once a day anyway.
            bank = configured and ((daily_due(last) and _older_than(last_try, hours=3)) or plaid_bank)
            if bank:
                with db.session() as conn:
                    db.set_setting(conn, sk.LAST_AUTO_SYNC_ATTEMPT, datetime.now().isoformat(timespec="seconds"))
            _sync_everything(bank, daily_due(last_inv) or plaid_inv)
        except Exception:
            monitoring.report()
        time.sleep(15 * 60)
