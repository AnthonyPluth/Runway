"""Bank and brokerage connections: SimpleFIN, and Plaid's settings, Link and each connection's sync."""
from __future__ import annotations

import json
import threading
import time
from datetime import date, timedelta

from sqlalchemy import func, select

from ... import categorize, db, monitoring, plaid, plaidbank, recurring, simplefin
from ... import settings_keys as sk
from ...models import Account, CardStatement, InvAccount, PlaidAccount, PlaidItem
from ..common import ApiError
from ..sync import _inv_lock, _sync_lock, refresh_prices


def api_connect(conn, _q, body):
    token = (body.get("token") or "").strip()
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
               PlaidItem.error, PlaidItem.products).order_by(PlaidItem.institution_name)))
    for it in items:
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
        if body["env"] not in plaid.HOSTS:
            raise ApiError("Environment must be sandbox or production")
        db.set_setting(conn, sk.PLAID_ENV, body["env"])
    if body.get("client_id") is not None:
        db.set_setting(conn, sk.PLAID_CLIENT_ID, body["client_id"].strip() or None)
    if body.get("secret"):
        db.set_setting(conn, sk.PLAID_SECRET, body["secret"].strip())
    if "redirect_uri" in body:
        db.set_setting(conn, sk.PLAID_REDIRECT_URI, (body.get("redirect_uri") or "").strip() or None)
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


def _item_lock(conn, item_id: str) -> threading.Lock:
    """The sync lock a connection's own sync must hold: the bank sync's, or the investment sync's."""
    item = conn.execute(select(PlaidItem.products).where(PlaidItem.item_id == item_id)).fetchone()
    return _sync_lock if item and plaidbank.is_bank_item(item) else _inv_lock


def api_plaid_exchange(conn, _q, body):
    kind = body.get("kind")
    if kind not in plaid.KIND_PRODUCTS:   # what Link was opened for, if the page didn't say
        try:
            kind = json.loads(db.get_setting(conn, sk.PLAID_PENDING_LINK) or "{}").get("kind")
        except ValueError:
            kind = None
    try:
        item_id = plaid.exchange(conn, body.get("public_token") or "", body.get("institution") or {}, kind)
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
