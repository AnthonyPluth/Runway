"""Plaid client: Link tokens, token exchange, and investment holdings and activity.

Bank and credit card connections (transactions, balances and card statements) are in plaidbank.py, and the request
helper both use is in plaidapi.py. This module sits on top: it hands bank connections to plaidbank when syncing or
removing them."""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, UTC
from typing import Any

from sqlalchemy import delete, func, null, or_, select, union_all, update

from ..domain import brands, deleted_accounts
from ..storage import db, secretbox
from . import plaidbank
from ..storage import settings_keys as sk
# Re-exported: the rest of Runway (and the tests, which patch plaid.call) reach Plaid through this module.
from ..storage.models import (Account, Holding, InvAccount, InvSnapshot, InvTransaction, PlaidAccount, PlaidItem,
                              Security)
from .plaidapi import HOSTS as HOSTS, PlaidError as PlaidError, base_url as base_url, call as call, configured as configured
from .plaidbank import _institution

HISTORY_DAYS = 730      # Plaid keeps up to 24 months of investment activity
REFRESH_DAYS = 45       # window re-read on routine syncs
PAGE = 500              # Plaid's maximum page size


# ------------------------------------------------------------------------------------------------ linking

def link_token(conn, item_id: str | None = None, kind: str = "investments") -> str:
    """kind: "investments", or "bank" (transactions, plus card statements where the bank offers them), or "cards"
    (card statements only, for a Plaid account without the Transactions product)."""
    body = {
        "client_name": "Runway",
        "user": {"client_user_id": "runway-local-user"},
        "country_codes": ["US"],
        "language": "en",
    }
    if item_id:  # update mode: fix an expired login without creating a new Item
        row = conn.execute(select(PlaidItem.access_token).where(PlaidItem.item_id == item_id)).fetchone()
        if not row:
            raise PlaidError("Connection not found")
        body["access_token"] = row["access_token"]
    elif kind == "bank":
        body["products"] = ["transactions"]
        body["optional_products"] = ["liabilities"]
        body["transactions"] = {"days_requested": 730}
    elif kind == "cards":
        body["products"] = ["liabilities"]
    else:
        body["products"] = ["investments"]
    redirect = redirect_uri(conn)
    if redirect:
        body["redirect_uri"] = redirect
    try:
        return call(conn, "/link/token/create", body)["link_token"]
    except PlaidError as e:
        if redirect and "redirect" in str(e).lower():   # not added to Plaid's allowed redirect URIs (yet): pop-up only
            body.pop("redirect_uri")
            return call(conn, "/link/token/create", body)["link_token"]
        raise


def redirect_uri(conn) -> str | None:
    """Where banks that sign you in on their own site (OAuth: Chase, Capital One, ...) send you back to Runway.
    Needed on phones and in the installed app, where the bank can't open in a pop-up. It must be listed under
    Allowed redirect URIs in the Plaid Dashboard; without it, those banks only work from a computer's browser."""
    explicit = db.get_setting(conn, sk.PLAID_REDIRECT_URI)
    if explicit:
        return explicit
    public = (os.environ.get("RUNWAY_PUBLIC_URL") or "").rstrip("/")
    return public + "/plaid/oauth" if public.startswith("https://") else None


KIND_PRODUCTS = {"bank": ["transactions"], "cards": ["liabilities"], "investments": ["investments"]}


def exchange(conn, public_token: str, institution: dict | None = None, kind: str | None = None) -> str:
    """Swap Link's public token for the connection's access token, and save it straight away: from here on the
    connection exists (and bills) at Plaid, so it must never be lost to a failure after this point. `kind` is what
    was linked ("bank", "cards" or "investments"), used when Plaid can't say which products the connection has."""
    res = call(conn, "/item/public_token/exchange", {"public_token": public_token})
    item_id, token = res["item_id"], res["access_token"]
    institution = institution or {}
    fallback = KIND_PRODUCTS.get(kind or "", ["investments"])
    db.upsert(conn, PlaidItem, {"item_id": item_id, "access_token": secretbox.encrypt(token),
                                "institution_id": institution.get("institution_id"), "institution_name": institution.get("name"),
                                "env": db.get_setting(conn, sk.PLAID_ENV, "production"), "products": ",".join(fallback)},
              key=["item_id"], update=lambda ex: {"access_token": ex.access_token, "error": null(), "inv_error": null()})
    conn.commit()
    try:   # which products this connection has (investments, or transactions and/or liabilities)
        info = call(conn, "/item/get", {"access_token": token}).get("item") or {}
        # Optional products (card statements) can show up only as consented until they're first used.
        prods = sorted(set(info.get("products") or []) | set(info.get("billed_products") or []) | set(info.get("consented_products") or []))
    except PlaidError:
        prods = []
    prods = [p for p in prods if p in ("investments", "transactions", "liabilities")]
    if prods:
        conn.execute(update(PlaidItem).where(PlaidItem.item_id == item_id).values(products=",".join(prods)))
        conn.commit()
    return item_id


def remove_item(conn, item_id: str) -> None:
    row = conn.execute(select(PlaidItem.access_token).where(PlaidItem.item_id == item_id)).fetchone()
    if not row:
        raise PlaidError("Connection not found")
    try:
        call(conn, "/item/remove", {"access_token": row["access_token"]})
    except PlaidError as e:
        if e.code not in ("ITEM_NOT_FOUND", "INVALID_ACCESS_TOKEN"):
            raise
    plaidbank.forget_item(conn, item_id)
    ids = conn.execute(select(InvAccount.id).where(InvAccount.item_id == item_id)).scalars()
    for aid in ids:
        for model in (Holding, InvTransaction, InvSnapshot):
            conn.execute(delete(model).where(model.account_id == aid))
        conn.execute(delete(Account).where(Account.id == "pl:" + aid))   # its entry in your accounts, if it had one
    conn.execute(delete(InvAccount).where(InvAccount.item_id == item_id))
    conn.execute(delete(PlaidItem).where(PlaidItem.item_id == item_id))


def item_accounts(conn, item_id: str) -> set[tuple[str, str]]:
    """A connection's accounts as (name, last four digits), whichever kind of connection it is."""
    rows = conn.execute(union_all(select(InvAccount.name, InvAccount.mask).where(InvAccount.item_id == item_id),
                                  select(PlaidAccount.name, PlaidAccount.mask).where(PlaidAccount.item_id == item_id))).fetchall()
    return {(" ".join((r["name"] or "").lower().split()), r["mask"] or "") for r in rows}


def duplicates(conn, item_id: str) -> list[dict]:
    """Other connections to the same institution, of the same kind, that include some of this one's accounts: the same
    login linked twice. Those accounts then count twice (investments and net worth)."""
    me = conn.execute(select(PlaidItem).where(PlaidItem.item_id == item_id)).fetchone()
    mine = item_accounts(conn, item_id)
    if not me or not mine:
        return []
    out = []
    for other in conn.execute(select(PlaidItem).where(
            PlaidItem.item_id != item_id,
            or_(PlaidItem.institution_id == (me["institution_id"] or ""),
                func.lower(PlaidItem.institution_name) == func.lower(me["institution_name"] or "")))).fetchall():
        if plaidbank.is_bank_item(other) != plaidbank.is_bank_item(me):
            continue   # e.g. a bank's card statements and its brokerage: different accounts by design
        shared = mine & item_accounts(conn, other["item_id"])
        if shared:
            out.append({"item_id": other["item_id"], "shared": len(shared), "adds_nothing": mine <= shared})
    return out


# ------------------------------------------------------------------------------------------------ syncing

# ------------------------------------------------------------------------------------------------ in your accounts

def investment_candidates(conn, item_id: str) -> list[dict]:
    """Your investment accounts (from SimpleFIN) that a Plaid account at this institution could be."""
    inst = _institution(conn, item_id)
    linked_to = select(func.min(InvAccount.id)).where(InvAccount.account_id == Account.id).scalar_subquery()
    return [dict(a) for a in conn.execute(   # linked_to: the Plaid account already linked to it, if any
        select(Account.id, Account.name, Account.display_name, Account.org, Account.balance, linked_to.label("linked_to"))
        .where(Account.kind == "investment", Account.id.not_like("pl:%")).order_by(Account.name)).fetchall()
        if brands.same_institution(a["org"] or a["name"], inst)]


def match_investment(conn, inv_id: str, target: str, today: date | None = None) -> dict:
    """Your choice for an investment account from Plaid: "new" (its own account), the id of the account it already is
    (from SimpleFIN, so it isn't counted twice), "ignore", or "" (decide later)."""
    inv = conn.execute(select(InvAccount, PlaidItem.institution_name)
                       .outerjoin(PlaidItem, PlaidItem.item_id == InvAccount.item_id)
                       .where(InvAccount.id == inv_id, InvAccount.source == "plaid")).fetchone()
    if not inv:
        raise ValueError("That account isn't here any more.")
    own = "pl:" + inv_id
    if target == "new":
        name = (inv["name"] or inv["official_name"] or "Investment account") + (f" ••{inv['mask']}" if inv["mask"] else "")
        db.upsert(conn, Account, {"id": own, "name": name, "org": inv["institution_name"], "kind": "investment",
                                  "balance": inv["balance"] or 0.0, "balance_date": (today or date.today()).isoformat(),
                                  "provider": "plaid", "provider_since": (today or date.today()).isoformat()},
                  key=["id"], update=lambda ex: {"balance": ex.balance})
        conn.execute(update(InvAccount).where(InvAccount.id == inv_id).values(account_id=own))
        return {"ok": True, "account_id": own}
    if target and target != "ignore":
        acct = conn.execute(select(Account.name, Account.display_name)
                            .where(Account.id == target, Account.kind == "investment", Account.id.not_like("pl:%"))).fetchone()
        if not acct:
            raise ValueError("Pick one of your investment accounts.")
        if conn.execute(select(InvAccount.id).where(InvAccount.account_id == target, InvAccount.id != inv_id)).fetchone():
            raise ValueError(f"{acct['display_name'] or acct['name']} is already linked to another Plaid account. "
                             "Unlink it there first.")
    conn.execute(delete(Account).where(Account.id == own))   # it had its own account before: not any more
    conn.execute(update(InvAccount).where(InvAccount.id == inv_id).values(account_id=target or None))
    return {"ok": True, "account_id": target or None}


def update_investment_accounts(conn, item_id: str, today: date | None = None) -> None:
    """After a sync: keep the balances of Plaid investment accounts that are accounts of their own up to date, and decide
    what a new one is when it's clear: the same as one of your SimpleFIN accounts at that institution when exactly one
    has the same balance, or its own account when you have none there. Anything else waits for you (Settings)."""
    today = today or date.today()
    for inv in conn.execute(select(InvAccount).where(InvAccount.item_id == item_id, InvAccount.source == "plaid")).fetchall():
        if inv["account_id"] and inv["account_id"].startswith("pl:"):
            conn.execute(update(Account).where(Account.id == inv["account_id"])
                         .values(balance=inv["balance"] or 0.0, balance_date=today.isoformat()))
            continue
        if inv["account_id"]:
            continue
        taken = set(conn.execute(select(InvAccount.account_id).where(InvAccount.account_id.is_not(None))).scalars())
        cands = [c for c in investment_candidates(conn, item_id) if c["id"] not in taken]
        if not cands:
            match_investment(conn, inv["id"], "new", today)
            continue
        bal = inv["balance"] or 0.0
        close = [c for c in cands if bal and abs((c["balance"] or 0.0) - bal) <= max(5.0, abs(bal) * 0.01)]
        if len(close) == 1:
            match_investment(conn, inv["id"], close[0]["id"], today)


def undecided_count(conn) -> int:
    """Accounts from Plaid waiting for you to say what they are (bank and card accounts, and investment accounts)."""
    banks = conn.execute(select(func.count()).select_from(PlaidAccount).where(
        PlaidAccount.ignored == 0, PlaidAccount.type != "investment",
        PlaidAccount.plaid_account_id.not_in(select(Account.plaid_account_id).where(Account.plaid_account_id.is_not(None))))).scalar()
    invest = conn.execute(select(func.count()).select_from(InvAccount)
                          .where(InvAccount.source == "plaid", InvAccount.account_id.is_(None))).scalar()
    return banks + invest


def _store_securities(conn, securities: list[dict]) -> None:
    # One row at a time: a reply that lists a security twice would make one multi-row upsert fail on Postgres.
    for s in securities or []:
        db.upsert(conn, Security, {
            "id": s["security_id"], "ticker": s.get("ticker_symbol"), "name": s.get("name"), "type": s.get("type"),
            "subtype": s.get("subtype"), "close_price": s.get("close_price"), "close_as_of": s.get("close_price_as_of"),
            "is_cash": 1 if (s.get("is_cash_equivalent") or s.get("type") == "cash") else 0, "sector": s.get("sector"),
            "industry": s.get("industry"), "currency": s.get("iso_currency_code") or "USD", "cusip": s.get("cusip"),
            "isin": s.get("isin")}, key=["id"], update=_security_update)


def _security_update(ex) -> dict:
    """What a security Plaid sends again changes: everything, except that a missing price, date, sector or industry
    keeps the one already saved."""
    keep = ("close_price", "close_as_of", "sector", "industry")
    return {c: func.coalesce(ex[c], getattr(Security, c)) if c in keep else ex[c]
            for c in ("ticker", "name", "type", "subtype", "close_price", "close_as_of", "is_cash", "sector", "industry",
                      "currency", "cusip", "isin")}


def sync_item(conn, item_id: str, today: date | None = None) -> dict:
    """One connection, now (its Sync button, or just linked): each side it has (plaidbank.syncs), the bank side with
    plaidbank and its investments here. A connection with both gets the bank sync's reply, with the investments' under
    "investments"; when one side fails and the other doesn't, what went wrong is in its "error" (and on the connection,
    for that side), and both failing raises the bank side's error."""
    today = today or date.today()
    item = conn.execute(select(PlaidItem).where(PlaidItem.item_id == item_id)).fetchone()
    if not item:
        raise PlaidError("Connection not found")
    sides = plaidbank.syncs(item)
    if "investments" not in sides:
        return plaidbank.sync_item(conn, item_id, today)
    if "bank" not in sides:
        return sync_investments(conn, item, today)
    failed: PlaidError | None = None
    try:
        out = plaidbank.sync_item(conn, item_id, today)
        conn.commit()
    except PlaidError as e:   # kept on the connection (error); its investments are still read
        failed, out = e, {"accounts": 0, "matched": [], "new": [], "statements": 0}
    try:
        out["investments"] = sync_investments(conn, conn.execute(select(PlaidItem).where(PlaidItem.item_id == item_id)).fetchone(), today)
    except PlaidError as e:   # kept on the connection (inv_error)
        if failed:
            raise failed from None
        out["error"] = "; ".join([*([out["error"]] if out.get("error") else []), f"investments: {e}"])
        return out
    if failed:
        out["error"] = f"transactions and balances: {failed}"
    return out


def _set_inv_error(conn, item_id: str, e: PlaidError) -> None:
    """An investment sync that stopped part way: the connection says so (and keeps the time of its last full one)."""
    conn.execute(update(PlaidItem).where(PlaidItem.item_id == item_id).values(inv_error=e.code or str(e)))
    conn.commit()


def sync_investments(conn, item, today: date) -> dict:
    """A connection's investment accounts, holdings and activity. Its own error and last sync time are inv_error and
    inv_last_sync (a connection's bank side has error and last_sync)."""
    item_id = item["item_id"]
    token = item["access_token"]
    conn.commit()
    try:
        h = call(conn, "/investments/holdings/get", {"access_token": token})
    except PlaidError as e:
        _set_inv_error(conn, item_id, e)
        raise
    if not item["institution_name"] and (h.get("item") or {}).get("institution_name"):
        conn.execute(update(PlaidItem).where(PlaidItem.item_id == item_id).values(institution_name=h["item"]["institution_name"]))
    deleted = deleted_accounts.inv_ids(conn)   # accounts you deleted stay deleted until you restore them
    accounts = [a for a in h.get("accounts", []) if a.get("account_id") not in deleted]
    held = [x for x in h.get("holdings", []) if x.get("account_id") not in deleted]
    for a in accounts:
        bal = a.get("balances") or {}
        db.upsert(conn, InvAccount, {"id": a["account_id"], "item_id": item_id, "name": a.get("name"),
                                     "official_name": a.get("official_name"), "type": a.get("type"), "subtype": a.get("subtype"),
                                     "mask": a.get("mask"), "balance": bal.get("current"),
                                     "currency": bal.get("iso_currency_code") or "USD"},
                  key=["id"], update=["name", "official_name", "type", "subtype", "mask", "balance", "currency"])
    _store_securities(conn, h.get("securities", []))
    update_investment_accounts(conn, item_id, today)
    account_ids = [a["account_id"] for a in accounts]
    for aid in account_ids:  # holdings are a full snapshot: replace
        conn.execute(delete(Holding).where(Holding.account_id == aid))
    for x in held:
        db.upsert(conn, Holding, {"account_id": x["account_id"], "security_id": x["security_id"], "quantity": x.get("quantity"),
                                  "price": x.get("institution_price"), "price_as_of": x.get("institution_price_as_of"),
                                  "value": x.get("institution_value"), "cost_basis": x.get("cost_basis"),
                                  "currency": x.get("iso_currency_code") or "USD"}, key=["account_id", "security_id"])
    for aid in account_ids:
        total = conn.execute(select(func.sum(Holding.value)).where(Holding.account_id == aid)).fetchone()[0]
        if total is None:
            total = conn.execute(select(InvAccount.balance).where(InvAccount.id == aid)).fetchone()[0]
        db.upsert(conn, InvSnapshot, {"date": today.isoformat(), "account_id": aid, "value": total}, key=["date", "account_id"])
    conn.commit()

    # Activity: everything Plaid has on the first sync, then a rolling window.
    first = item["inv_last_sync"] is None
    start = today - timedelta(days=HISTORY_DAYS if first else REFRESH_DAYS)
    offset, total, fetched = 0, None, 0
    while total is None or offset < total:
        try:
            res = call(conn, "/investments/transactions/get", {
                "access_token": token, "start_date": start.isoformat(), "end_date": today.isoformat(),
                "options": {"count": PAGE, "offset": offset},
            })
        except PlaidError as e:   # the holdings are saved, but not all the activity: not a sync that went fine
            _set_inv_error(conn, item_id, e)
            raise
        _store_securities(conn, res.get("securities", []))
        txs = res.get("investment_transactions", [])
        for t in txs:
            if t.get("account_id") in deleted:
                continue
            db.upsert(conn, InvTransaction, {
                "id": t["investment_transaction_id"], "account_id": t["account_id"], "security_id": t.get("security_id"),
                "date": t["date"], "name": t.get("name"), "type": t.get("type"), "subtype": t.get("subtype"),
                "quantity": t.get("quantity") or 0, "amount": t.get("amount") or 0, "price": t.get("price"),
                "fees": t.get("fees") or 0, "currency": t.get("iso_currency_code") or "USD"}, key=["id"])
        fetched += len(txs)
        total = res.get("total_investment_transactions", 0)
        offset += len(txs)
        conn.commit()
        if not txs:
            break
    conn.execute(update(PlaidItem).where(PlaidItem.item_id == item_id)
                 .values(inv_last_sync=datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"), inv_error=None))
    conn.commit()
    return {"accounts": len(account_ids), "holdings": len(held), "transactions": fetched}


def sync_all(conn, today: date | None = None) -> dict:
    """The investment sync: every connection's investments (plaidbank.syncs). Bank and card connections, and the bank
    side of one with both, sync with the bank sync (plaidbank.sync_all)."""
    today = today or date.today()
    out: dict[str, Any] = {"items": 0, "errors": []}
    for item in conn.execute(select(PlaidItem)).fetchall():
        if "investments" not in plaidbank.syncs(item):
            continue
        try:
            sync_investments(conn, item, today)
            out["items"] += 1
        except PlaidError as e:
            out["errors"].append(f"{item['institution_name'] or 'Connection'}: {e}")
    return out
