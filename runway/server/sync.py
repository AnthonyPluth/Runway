"""Keeping the data fresh: the bank and investment syncs, when they're due, and the background loop that runs them."""
from __future__ import annotations

import contextlib
import json
import threading
import time
from datetime import date, datetime, timedelta

from sqlalchemy import insert, or_, select

from ..domain import categorize, merchants, networth, notify, portfolio, recurring, retail
from ..storage import db
from .. import monitoring
from ..providers import plaid, plaidbank, prices, realie, sfinvest, simplefin
from ..storage import settings_keys as sk
from ..providers.banks import bank_configured, plaid_banks
from ..storage.models import Holding, InvAccount, InvTransaction, ManualPosition, PlaidItem, Security, SyncLog
from .common import ApiError

DAILY_SYNC_HOUR = 7          # banks and cards (SimpleFIN and Plaid) sync once a day, on the first check after this hour
                             # (local time; the image's TZ is America/New_York): late enough for overnight ACH, early
                             # enough to review in the morning. Opening Runway only catches up a missed one.
PLAID_SYNC_HOUR = DAILY_SYNC_HOUR   # the syncs Runway starts itself (the daily one, catching up when you open it) ask
                             # Plaid once a day; the Sync button, and a connection's own in Settings, always do.
PLAID_REFRESH_AT = (6, 30)   # before that sync, Plaid is told to fetch from the banks (Transactions Refresh), so the
                             # sync gets the banks as of now and not as of Plaid's own last visit. Only before
                             # PLAID_SYNC_HOUR: a refresh after the day's sync would be a call for nothing.
VISIT_SYNC_MINUTES = 60      # opening Runway refreshes investments (prices) if they're older than this
CRON_SLUG = "runway-bank-sync"   # the bank sync's Sentry Cron Monitor (see run_sync)
_sync_lock = threading.Lock()
_inv_lock = threading.Lock()
AUTO_SYNC = True             # False with --no-sync: no daily sync and no sync on opening the app


def run_sync(ask_plaid: bool = False) -> dict:
    """The bank sync: SimpleFIN, then Plaid's bank and card connections. Plaid is asked once a day (plaid_due), or
    every time with ask_plaid (you pressed Sync)."""
    if not _sync_lock.acquire(blocking=False):
        raise ApiError("A sync is already running.", 409)
    check_in = None
    try:
        try:
            with db.session() as conn:
                access_url = db.get_setting(conn, sk.SIMPLEFIN_ACCESS_URL)
                has_plaid = plaid_banks(conn)
                if not access_url and not has_plaid:
                    raise ApiError("Connect SimpleFIN or a Plaid bank in Settings first.")
                # Every bank sync that starts checks in with Sentry's Cron Monitor, yours from the Sync
                # button too: the monitor is about the data being fresh each day. One that can't start (another is
                # running, nothing is connected) doesn't.
                # With automatic syncing off (--no-sync), there's no daily schedule to promise: the check-in goes to a
                # monitor you set up, and doesn't create one that would report every day you don't press Sync as missed.
                check_in = monitoring.cron_start(CRON_SLUG, f"0 {DAILY_SYNC_HOUR} * * *" if AUTO_SYNC else None)
                use_plaid = has_plaid and (ask_plaid or plaid_due(db.get_setting(conn, sk.LAST_PLAID_BANK_SYNC)))
                if use_plaid:   # counted when asked, so a failing Plaid isn't asked again until tomorrow
                    db.set_setting(conn, sk.LAST_PLAID_BANK_SYNC, datetime.now().isoformat(timespec="seconds"))
                    conn.commit()
                result: dict = {"new": [], "errors": []}
                simplefin_failed: simplefin.SimpleFinError | None = None
                if access_url:
                    try:
                        result = simplefin.sync(conn, access_url)
                    except simplefin.SimpleFinError as e:
                        if not use_plaid:
                            raise
                        # Plaid was counted as asked today, so it's asked now all the same: SimpleFIN being down
                        # mustn't leave the accounts set to Plaid without their day's transactions. The sync still
                        # counts as failed (below), so SimpleFIN is tried again later.
                        conn.rollback()
                        simplefin_failed = e
                if use_plaid:   # accounts set to Plaid, and card statements
                    pb = plaidbank.sync_all(conn)
                    result["new"] += pb["new"]
                    result["errors"] += pb["errors"]
                # What the banks said is kept and shown (the log, Settings, the sidebar), so nothing in it may be an
                # account number or the like.
                result["errors"] = [monitoring.public_text(x) for x in result["errors"]]
                try:   # logos: Plaid's new ones, and big brands' (a nice-to-have; never fail the sync)
                    merchants.note_sites(conn)
                    merchants.refresh_holding_logos(conn)   # before the rest: held funds' logos wait behind merchants' otherwise
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
                if simplefin_failed is not None:
                    conn.commit()   # what Plaid brought in is kept; the failure is recorded with SimpleFIN's error
                    raise simplefin_failed
                conn.execute(insert(SyncLog).values(ok=1, message=msg))
                db.set_setting(conn, sk.LAST_SYNC_OK, datetime.now().isoformat(timespec="seconds"))
                # The sync worked, but a bank may still need you (an expired login): kept apart, so the sidebar can say so.
                db.set_setting(conn, sk.LAST_SYNC_WARNINGS, json.dumps(result["errors"]) if result["errors"] else None)
                monitoring.metric("count", "runway.sync.new_transactions", len(result["new"]))
                out = {"new": len(result["new"]), "categorized": counts, "bank_messages": result["errors"]}
            monitoring.cron_finish(check_in, True)   # once it's saved
            return out
        except ApiError:
            monitoring.cron_finish(check_in, False)
            raise
        except simplefin.SimpleFinError as e:
            monitoring.cron_finish(check_in, False)
            said = monitoring.public_text(str(e))   # it quotes the bridge's answer
            _record_failed_sync(said)
            raise ApiError(said, 502) from e
        except Exception as e:
            monitoring.cron_finish(check_in, False)
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
            try:   # logos for what you hold (a nice-to-have; never fail the sync)
                merchants.refresh_holding_logos(conn)
            except Exception:
                monitoring.report()
            return out
    finally:
        _inv_lock.release()


def refresh_prices(conn, today: date | None = None) -> dict:
    today = today or date.today()
    s = Security
    tickers = [r["ticker"] for r in conn.execute(
        select(s.ticker).distinct().where(s.is_cash == 0, s.ticker.is_not(None),
                                          or_(s.id.in_(select(Holding.security_id)), s.id.in_(select(InvTransaction.security_id)),
                                              s.id.in_(select(ManualPosition.security_id)))))]
    tickers.append(prices.BENCHMARK)
    out = prices.refresh(conn, tickers, today - timedelta(days=portfolio.HISTORY_DAYS + 10), today=today)
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
            with monitoring.task("plaid refresh"):   # traced only when it's due, not on every check
                for e in plaidbank.refresh_all(conn):
                    monitoring.log(f"Plaid refresh: {e}", "warning", stderr=True)
    except Exception:
        monitoring.report()


def _sync_everything(bank: bool, invest: bool) -> None:
    """The syncs Runway starts itself (the daily one, and catching up when you open it), each a trace of its own."""
    if bank:
        with contextlib.suppress(ApiError), monitoring.task("bank sync"):
            run_sync()
        with monitoring.task("notifications"):
            notify_now()
    if invest:
        with contextlib.suppress(ApiError), monitoring.task("investment sync"):
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
