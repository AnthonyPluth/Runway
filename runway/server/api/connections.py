"""Bank and brokerage connections: syncing, SimpleFIN, and Plaid's settings, Link and each connection's sync."""
from __future__ import annotations

import json
import threading
import time
from datetime import date, timedelta

from sqlalchemy import func, select

from ... import categorize, db, monitoring, plaid, plaidbank, recurring, simplefin
from ... import settings_keys as sk
from ...models import Account, CardStatement, InvAccount, PlaidAccount, PlaidItem
from ..common import ApiError, own_session, text
from ..sync import _inv_lock, _sync_lock, refresh_prices, run_sync, sync_on_visit


def api_connect(conn, _q, body):
    token = text(body.get("token"), "token").strip()
    if not token:
        raise ApiError("Paste a SimpleFIN setup token.")
    try:
        if token.startswith("http") and not token.startswith("https://"):
            raise ApiError("A SimpleFIN access URL must start with https://.")
        access_url = token if token.startswith("https://") else simplefin.claim_setup_token(token)
        simplefin.check_address(access_url)
        simplefin.fetch_accounts(access_url, date.today() - timedelta(days=3))  # prove it works before saving
    except simplefin.SimpleFinError as e:
        raise ApiError(monitoring.public_text(str(e)), 502) from e
    db.set_setting(conn, sk.SIMPLEFIN_ACCESS_URL, access_url)
    return {"ok": True}


def api_plaid_status(conn, _q, _b):
    items = db.rows(conn.execute(
        select(PlaidItem.item_id, PlaidItem.institution_name, PlaidItem.env, PlaidItem.created_at, PlaidItem.last_sync,
               PlaidItem.error, PlaidItem.products, PlaidItem.inv_last_sync, PlaidItem.inv_error)
        .order_by(PlaidItem.institution_name)))
    for it in items:
        # Each sync that reads the connection keeps its own error and time ("sides"); the connection's own error is
        # either one's (the bank side's first), and its last sync the older one's (none until both have synced).
        mine = {"bank": (it["last_sync"], it["error"]), "investments": (it.pop("inv_last_sync"), it.pop("inv_error"))}
        sides = {s: {"last_sync": mine[s][0], "error": mine[s][1]} for s in ("bank", "investments") if s in plaidbank.syncs(it)}
        times = [v["last_sync"] for v in sides.values()]
        it["last_sync"] = None if None in times else min(times)
        it["error"] = next((v["error"] for v in sides.values() if v["error"]), None)
        it["sides"] = sides
        it["bank"] = plaidbank.is_bank_item(it)
        it["products"] = sorted(plaidbank.products(it))
        it["duplicates"] = plaid.duplicates(conn, it["item_id"])
        if it["bank"]:
            it["accounts"] = db.rows(conn.execute(
                select(PlaidAccount.plaid_account_id.label("id"), PlaidAccount.name, PlaidAccount.official_name, PlaidAccount.subtype,
                       PlaidAccount.type, PlaidAccount.mask, PlaidAccount.current.label("balance"), PlaidAccount.ignored,
                       Account.id.label("account_id"), db.account_label_expr().label("account_name"), Account.provider,
                       CardStatement.last_statement_date, CardStatement.last_statement_balance, CardStatement.next_due_date)
                .select_from(PlaidAccount)
                .outerjoin(Account, Account.plaid_account_id == PlaidAccount.plaid_account_id)
                .outerjoin(CardStatement, CardStatement.plaid_account_id == PlaidAccount.plaid_account_id)
                .where(PlaidAccount.item_id == it["item_id"]).order_by(PlaidAccount.type, PlaidAccount.name)))
        else:
            it["accounts"] = db.rows(conn.execute(
                select(InvAccount.id, InvAccount.name, InvAccount.official_name, InvAccount.subtype, InvAccount.mask,
                       InvAccount.balance, InvAccount.account_id)
                .where(InvAccount.item_id == it["item_id"]).order_by(InvAccount.name)))
            it["candidates"] = plaid.investment_candidates(conn, it["item_id"])   # what each account could be
    return {"configured": plaid.configured(conn), "env": db.get_setting(conn, sk.PLAID_ENV, "production"),
            "client_id": db.get_setting(conn, sk.PLAID_CLIENT_ID) or "", "items": items,
            "redirect_uri": plaid.redirect_uri(conn),
            "last_inv_sync": db.get_setting(conn, sk.LAST_INV_SYNC), "syncing": _inv_lock.locked(),
            "inv_accounts": conn.execute(select(func.count()).select_from(InvAccount)).scalar(),
            "simplefin_connected": bool(db.get_setting(conn, sk.SIMPLEFIN_ACCESS_URL)),
            "simplefin_last_sync": db.get_setting(conn, sk.LAST_SYNC_OK),
            "simplefin_seen": list(json.loads(db.get_setting(conn, sk.SIMPLEFIN_HOLDINGS_SEEN) or "{}").values())}


def api_plaid_settings(conn, _q, body):
    if "env" in body:
        if text(body["env"], "env") not in plaid.HOSTS:
            raise ApiError("Environment must be sandbox or production")
        db.set_setting(conn, sk.PLAID_ENV, body["env"])
    if body.get("client_id") is not None:
        db.set_setting(conn, sk.PLAID_CLIENT_ID, text(body["client_id"], "client_id").strip() or None)
    if text(body.get("secret"), "secret").strip():
        db.set_setting(conn, sk.PLAID_SECRET, text(body["secret"], "secret").strip())
    if "redirect_uri" in body:
        db.set_setting(conn, sk.PLAID_REDIRECT_URI, text(body.get("redirect_uri"), "redirect_uri").strip() or None)
    return {"ok": True}


def api_plaid_link_token(conn, _q, body):
    kind = text(body.get("kind"), "kind") or "investments"
    if body.get("item_id"):
        kind = "investments"   # reconnecting: the connection keeps its products
    if kind not in ("investments", "bank", "cards"):
        raise ApiError("Unknown kind of connection")
    try:
        token = plaid.link_token(conn, text(body.get("item_id"), "item_id") or None, kind)
    except plaid.PlaidError as e:
        if not (kind == "bank" and e.code in ("INVALID_PRODUCT", "PRODUCTS_NOT_SUPPORTED", "INVALID_FIELD")):
            raise ApiError(monitoring.public_text(str(e)), 502) from e
        try:   # Transactions isn't enabled for this Plaid account: card statements only
            token, kind = plaid.link_token(conn, None, "cards"), "cards"
        except plaid.PlaidError:
            raise ApiError(monitoring.public_text(str(e)), 502) from e
    # Kept so Link can pick up where it left off when a bank sends you back to /plaid/oauth (possibly in another
    # browser, like Safari from the installed app). Link tokens expire after 4 hours.
    db.set_setting(conn, sk.PLAID_PENDING_LINK, json.dumps({"token": token, "kind": kind, "item_id": body.get("item_id") or None,
                                                          "at": time.time()}))
    return {"link_token": token, "kind": kind}


def api_plaid_oauth_resume(conn, _q, _b):
    """The Link session to continue after the bank's sign-in page sends you back."""
    try:
        p = json.loads(db.get_setting(conn, sk.PLAID_PENDING_LINK) or "{}")
    except ValueError:
        p = {}
    if not p.get("token") or time.time() - p.get("at", 0) > 4 * 3600:
        raise ApiError("That bank connection has expired. Start it again from Settings → Connections.", 404)
    return {"link_token": p["token"], "kind": p.get("kind"), "item_id": p.get("item_id")}


LINK_SYNC_WAIT = 120   # seconds a new connection waits for a running sync before its first one


class _ItemLock:
    """The sync locks a connection's own sync must hold: the bank sync's and/or the investment sync's, one for each side
    it has (plaidbank.syncs), so neither side is read twice at once. Taken in the same order as everywhere else that
    takes both (bank first), so two of them can't wait on each other."""

    def __init__(self, conn, item_id: str):
        item = conn.execute(select(PlaidItem.products).where(PlaidItem.item_id == item_id)).fetchone()
        sides = plaidbank.syncs(item) if item else {"investments"}
        self.locks = [lock for side, lock in (("bank", _sync_lock), ("investments", _inv_lock)) if side in sides]
        self.held: list[threading.Lock] = []

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        deadline = time.monotonic() + timeout if timeout >= 0 else None
        for lock in self.locks:
            left = -1 if deadline is None else max(0.0, deadline - time.monotonic())
            if not (lock.acquire(timeout=left) if blocking else lock.acquire(blocking=False)):
                self.release()
                return False
            self.held.append(lock)
        return True

    def release(self) -> None:
        while self.held:
            self.held.pop().release()


def _item_lock(conn, item_id: str) -> _ItemLock:
    return _ItemLock(conn, item_id)


def api_plaid_exchange(conn, _q, body):
    kind: str | None = text(body.get("kind"), "kind")
    if kind not in plaid.KIND_PRODUCTS:   # what Link was opened for, if the page didn't say
        try:
            kind = json.loads(db.get_setting(conn, sk.PLAID_PENDING_LINK) or "{}").get("kind")
        except ValueError:
            kind = None
    try:
        institution = body.get("institution") if isinstance(body.get("institution"), dict) else {}
        item_id = plaid.exchange(conn, text(body.get("public_token"), "public_token"), institution, kind)
    except plaid.PlaidError as e:
        raise ApiError(monitoring.public_text(str(e)), 502) from e
    lock = _item_lock(conn, item_id)
    if not lock.acquire(timeout=LINK_SYNC_WAIT):   # a sync reading the same connection at once would clash with it
        # Connected all the same: only its first sync waits, so this is good news, not an error.
        return {"ok": True, "connected": True, "sync_deferred": True, "item_id": item_id,
                "message": "Connected. A sync is running, so this connection’s accounts come in with the next one."}
    try:
        return _sync_new_item(conn, item_id, body)
    finally:
        lock.release()


def _sync_new_item(conn, item_id: str, body: dict) -> dict:
    try:
        res = plaid.sync_item(conn, item_id)
        # The same login linked a second time: its accounts would count twice. Undo it (at Plaid too) and say so.
        if any(d["adds_nothing"] for d in plaid.duplicates(conn, item_id)):
            name = (body.get("institution") or {}).get("name") or "That institution"
            plaid.remove_item(conn, item_id)
            conn.commit()
            raise ApiError(f"{name} is already connected with these accounts, so nothing was added. To fix a connection, "
                           "use its Sync or Reconnect button instead.", 409)
        item = conn.execute(select(PlaidItem.products).where(PlaidItem.item_id == item_id)).fetchone()
        if plaidbank.is_bank_item(item):
            n = len(res.pop("new", []))
            if "investments" in res:   # investments too: their prices, as below
                res["prices"] = refresh_prices(conn)
            return {"ok": True, "item_id": item_id, "bank": True, "new_transactions": n, **res}
        res["prices"] = refresh_prices(conn)
        return {"ok": True, "item_id": item_id, **res}
    except plaid.PlaidError as e:
        raise ApiError(monitoring.public_text(str(e)), 502) from e


def api_plaid_item_sync(conn, _q, _b, item_id):
    lock = _item_lock(conn, item_id)
    if not lock.acquire(blocking=False):
        raise ApiError("A sync is already running; try again once it's done.", 409)
    try:
        res = plaid.sync_item(conn, item_id)
        if "new" in res:   # a bank connection
            res["new_transactions"] = len(res["new"])
            categorize.categorize(conn, res.pop("new"))
            recurring.auto_match(conn)
            if "investments" in res:   # investments too: their prices, as below
                res["prices"] = refresh_prices(conn)
            return {"ok": True, "bank": True, **res}
        res["prices"] = refresh_prices(conn)
        return {"ok": True, **res}
    except plaid.PlaidError as e:
        raise ApiError(monitoring.public_text(str(e)), 502) from e
    finally:
        lock.release()


def api_plaid_item_remove(conn, _q, _b, item_id):
    try:
        plaid.remove_item(conn, item_id)
    except plaid.PlaidError as e:
        raise ApiError(monitoring.public_text(str(e)), 502) from e
    return {"ok": True}


def api_plaid_match(conn, _q, body):
    pid, target = str(body.get("plaid_account_id") or ""), str(body.get("target") or "")
    try:
        if conn.execute(select(InvAccount.id).where(InvAccount.id == pid, InvAccount.source == "plaid")).fetchone():
            return plaid.match_investment(conn, pid, target)
        return plaidbank.match(conn, pid, target)
    except ValueError as e:
        raise ApiError(str(e)) from e


@own_session
def api_sync(_conn, _q, _b):
    """Sync the banks now (the Sync button)."""
    return run_sync()


@own_session
def api_sync_auto(_conn, _q, _b):
    """Runway was opened: catch up a sync that's due (sync_on_visit)."""
    return sync_on_visit()
