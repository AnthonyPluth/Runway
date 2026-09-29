"""Plaid client: Link tokens, token exchange, and investment holdings and activity.

Bank and credit card connections (transactions, balances and card statements) are in plaidbank.py, and the request
helper both use is in plaidapi.py. This module sits on top: it hands bank connections to plaidbank when syncing or
removing them."""
from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta, UTC

from . import db, plaidbank, secretbox
from . import settings_keys as sk
# Re-exported: the rest of Runway (and the tests, which patch plaid.call) reach Plaid through this module.
from .plaidapi import HOSTS as HOSTS, PlaidError as PlaidError, base_url as base_url, call as call, configured as configured

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
        row = conn.execute("SELECT access_token FROM plaid_items WHERE item_id=?", (item_id,)).fetchone()
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
    conn.execute(
        "INSERT INTO plaid_items(item_id, access_token, institution_id, institution_name, env, products) VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(item_id) DO UPDATE SET access_token=excluded.access_token, error=NULL",
        (item_id, secretbox.encrypt(token), institution.get("institution_id"), institution.get("name"), db.get_setting(conn, sk.PLAID_ENV, "production"),
         ",".join(fallback)),
    )
    conn.commit()
    try:   # which products this connection has (investments, or transactions and/or liabilities)
        info = call(conn, "/item/get", {"access_token": token}).get("item") or {}
        # Optional products (card statements) can show up only as consented until they're first used.
        prods = sorted(set(info.get("products") or []) | set(info.get("billed_products") or []) | set(info.get("consented_products") or []))
    except PlaidError:
        prods = []
    prods = [p for p in prods if p in ("investments", "transactions", "liabilities")]
    if prods:
        conn.execute("UPDATE plaid_items SET products=? WHERE item_id=?", (",".join(prods), item_id))
        conn.commit()
    return item_id


def remove_item(conn, item_id: str) -> None:
    row = conn.execute("SELECT access_token FROM plaid_items WHERE item_id=?", (item_id,)).fetchone()
    if not row:
        raise PlaidError("Connection not found")
    try:
        call(conn, "/item/remove", {"access_token": row["access_token"]})
    except PlaidError as e:
        if e.code not in ("ITEM_NOT_FOUND", "INVALID_ACCESS_TOKEN"):
            raise
    plaidbank.forget_item(conn, item_id)
    ids = [r["id"] for r in conn.execute("SELECT id FROM inv_accounts WHERE item_id=?", (item_id,))]
    for aid in ids:
        for table in ("holdings", "inv_transactions", "inv_snapshots"):
            conn.execute(f"DELETE FROM {table} WHERE account_id=?", (aid,))
        conn.execute("DELETE FROM accounts WHERE id=?", ("pl:" + aid,))   # its entry in your accounts, if it had one
    conn.execute("DELETE FROM inv_accounts WHERE item_id=?", (item_id,))
    conn.execute("DELETE FROM plaid_items WHERE item_id=?", (item_id,))


def item_accounts(conn, item_id: str) -> set[tuple[str, str]]:
    """A connection's accounts as (name, last four digits), whichever kind of connection it is."""
    rows = conn.execute("SELECT name, mask FROM inv_accounts WHERE item_id=? UNION ALL SELECT name, mask FROM plaid_accounts "
                        "WHERE item_id=?", (item_id, item_id)).fetchall()
    return {(" ".join((r["name"] or "").lower().split()), r["mask"] or "") for r in rows}


def duplicates(conn, item_id: str) -> list[dict]:
    """Other connections to the same institution, of the same kind, that include some of this one's accounts: the same
    login linked twice. Those accounts then count twice (investments and net worth)."""
    me = conn.execute("SELECT * FROM plaid_items WHERE item_id=?", (item_id,)).fetchone()
    mine = item_accounts(conn, item_id)
    if not me or not mine:
        return []
    out = []
    for other in conn.execute("SELECT * FROM plaid_items WHERE item_id<>? AND (institution_id=? OR lower(institution_name)=lower(?))",
                              (item_id, me["institution_id"] or "", me["institution_name"] or "")).fetchall():
        if plaidbank.is_bank_item(other) != plaidbank.is_bank_item(me):
            continue   # e.g. a bank's card statements and its brokerage: different accounts by design
        shared = mine & item_accounts(conn, other["item_id"])
        if shared:
            out.append({"item_id": other["item_id"], "shared": len(shared), "adds_nothing": mine <= shared})
    return out


# ------------------------------------------------------------------------------------------------ syncing

# ------------------------------------------------------------------------------------------------ in your accounts

def _compact_institution(name: str | None) -> str:
    """"E*TRADE from Morgan Stanley" and "E*Trade" → comparable keys ("etradefrommorganstanley", "etrade")."""
    return re.sub(r"[^a-z0-9]", "", re.sub(r"\b(financial|investments?|securities|bank|inc|llc)\b", "", (name or "").lower()))


def _same_institution(a: str | None, b: str | None) -> bool:
    x, y = _compact_institution(a), _compact_institution(b)
    return len(x) >= 4 and len(y) >= 4 and (x in y or y in x)


def investment_candidates(conn, item_id: str) -> list[dict]:
    """Your investment accounts (from SimpleFIN) that a Plaid account at this institution could be."""
    item = conn.execute("SELECT institution_name FROM plaid_items WHERE item_id=?", (item_id,)).fetchone()
    inst = item["institution_name"] if item else None
    return [dict(a) for a in conn.execute(   # linked_to: the Plaid account already linked to it, if any
        "SELECT a.id, a.name, a.display_name, a.org, a.balance, (SELECT MIN(i.id) FROM inv_accounts i WHERE i.account_id=a.id) AS linked_to "
        "FROM accounts a WHERE a.kind='investment' AND a.id NOT LIKE 'pl:%' ORDER BY a.name").fetchall()
        if _same_institution(a["org"] or a["name"], inst)]


def match_investment(conn, inv_id: str, target: str, today: date | None = None) -> dict:
    """Your choice for an investment account from Plaid: "new" (its own account), the id of the account it already is
    (from SimpleFIN, so it isn't counted twice), "ignore", or "" (decide later)."""
    inv = conn.execute("SELECT i.*, p.institution_name FROM inv_accounts i LEFT JOIN plaid_items p ON p.item_id=i.item_id "
                       "WHERE i.id=? AND i.source='plaid'", (inv_id,)).fetchone()
    if not inv:
        raise ValueError("That account isn't here any more.")
    own = "pl:" + inv_id
    if target == "new":
        name = (inv["name"] or inv["official_name"] or "Investment account") + (f" ••{inv['mask']}" if inv["mask"] else "")
        conn.execute("INSERT INTO accounts(id, name, org, kind, balance, balance_date, provider, provider_since) "
                     "VALUES (?,?,?, 'investment', ?,?, 'plaid', ?) ON CONFLICT(id) DO UPDATE SET balance=excluded.balance, hidden=0",
                     (own, name, inv["institution_name"], inv["balance"] or 0.0, (today or date.today()).isoformat(),
                      (today or date.today()).isoformat()))
        conn.execute("UPDATE inv_accounts SET account_id=? WHERE id=?", (own, inv_id))
        return {"ok": True, "account_id": own}
    if target and target != "ignore":
        acct = conn.execute("SELECT name, display_name FROM accounts WHERE id=? AND kind='investment' AND id NOT LIKE 'pl:%'",
                            (target,)).fetchone()
        if not acct:
            raise ValueError("Pick one of your investment accounts.")
        if conn.execute("SELECT 1 FROM inv_accounts WHERE account_id=? AND id<>?", (target, inv_id)).fetchone():
            raise ValueError(f"{acct['display_name'] or acct['name']} is already linked to another Plaid account. "
                             "Unlink it there first.")
    conn.execute("DELETE FROM accounts WHERE id=?", (own,))   # it had its own account before: not any more
    conn.execute("UPDATE inv_accounts SET account_id=? WHERE id=?", (target or None, inv_id))
    return {"ok": True, "account_id": target or None}


def update_investment_accounts(conn, item_id: str, today: date | None = None) -> None:
    """After a sync: keep the balances of Plaid investment accounts that are accounts of their own up to date, and decide
    what a new one is when it's clear: the same as one of your SimpleFIN accounts at that institution when exactly one
    has the same balance, or its own account when you have none there. Anything else waits for you (Settings)."""
    today = today or date.today()
    for inv in conn.execute("SELECT * FROM inv_accounts WHERE item_id=? AND source='plaid'", (item_id,)).fetchall():
        if inv["account_id"] and inv["account_id"].startswith("pl:"):
            conn.execute("UPDATE accounts SET balance=?, balance_date=? WHERE id=?", (inv["balance"] or 0.0, today.isoformat(), inv["account_id"]))
            continue
        if inv["account_id"]:
            continue
        taken = {r[0] for r in conn.execute("SELECT account_id FROM inv_accounts WHERE account_id IS NOT NULL")}
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
    banks = conn.execute("SELECT COUNT(*) FROM plaid_accounts p WHERE p.ignored=0 AND p.type<>'investment' AND p.plaid_account_id "
                         "NOT IN (SELECT plaid_account_id FROM accounts WHERE plaid_account_id IS NOT NULL)").fetchone()[0]
    invest = conn.execute("SELECT COUNT(*) FROM inv_accounts WHERE source='plaid' AND account_id IS NULL").fetchone()[0]
    return banks + invest


def _store_securities(conn, securities: list[dict]) -> None:
    for s in securities or []:
        conn.execute(
            "INSERT INTO securities(id, ticker, name, type, subtype, close_price, close_as_of, is_cash, sector, industry, currency, cusip, isin) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET ticker=excluded.ticker, name=excluded.name, "
            "type=excluded.type, subtype=excluded.subtype, close_price=COALESCE(excluded.close_price, securities.close_price), "
            "close_as_of=COALESCE(excluded.close_as_of, securities.close_as_of), is_cash=excluded.is_cash, "
            "sector=COALESCE(excluded.sector, securities.sector), industry=COALESCE(excluded.industry, securities.industry), "
            "currency=excluded.currency, cusip=excluded.cusip, isin=excluded.isin",
            (s["security_id"], s.get("ticker_symbol"), s.get("name"), s.get("type"), s.get("subtype"), s.get("close_price"),
             s.get("close_price_as_of"), 1 if (s.get("is_cash_equivalent") or s.get("type") == "cash") else 0,
             s.get("sector"), s.get("industry"), s.get("iso_currency_code") or "USD", s.get("cusip"), s.get("isin")),
        )


def sync_item(conn, item_id: str, today: date | None = None) -> dict:
    today = today or date.today()
    item = conn.execute("SELECT * FROM plaid_items WHERE item_id=?", (item_id,)).fetchone()
    if not item:
        raise PlaidError("Connection not found")
    if plaidbank.is_bank_item(item):
        return plaidbank.sync_item(conn, item_id, today)
    token = item["access_token"]
    conn.commit()
    try:
        h = call(conn, "/investments/holdings/get", {"access_token": token})
    except PlaidError as e:
        conn.execute("UPDATE plaid_items SET error=? WHERE item_id=?", (e.code or str(e), item_id))
        conn.commit()
        raise
    if not item["institution_name"] and (h.get("item") or {}).get("institution_name"):
        conn.execute("UPDATE plaid_items SET institution_name=? WHERE item_id=?", (h["item"]["institution_name"], item_id))
    for a in h.get("accounts", []):
        bal = a.get("balances") or {}
        conn.execute(
            "INSERT INTO inv_accounts(id, item_id, name, official_name, type, subtype, mask, balance, currency) VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name, official_name=excluded.official_name, type=excluded.type, "
            "subtype=excluded.subtype, mask=excluded.mask, balance=excluded.balance, currency=excluded.currency",
            (a["account_id"], item_id, a.get("name"), a.get("official_name"), a.get("type"), a.get("subtype"), a.get("mask"),
             bal.get("current"), bal.get("iso_currency_code") or "USD"),
        )
    _store_securities(conn, h.get("securities", []))
    update_investment_accounts(conn, item_id, today)
    account_ids = [a["account_id"] for a in h.get("accounts", [])]
    for aid in account_ids:  # holdings are a full snapshot: replace
        conn.execute("DELETE FROM holdings WHERE account_id=?", (aid,))
    for x in h.get("holdings", []):
        conn.execute(
            "INSERT INTO holdings(account_id, security_id, quantity, price, price_as_of, value, cost_basis, currency) "
            "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(account_id, security_id) DO UPDATE SET quantity=excluded.quantity, "
            "price=excluded.price, price_as_of=excluded.price_as_of, value=excluded.value, cost_basis=excluded.cost_basis, "
            "currency=excluded.currency",
            (x["account_id"], x["security_id"], x.get("quantity"), x.get("institution_price"), x.get("institution_price_as_of"),
             x.get("institution_value"), x.get("cost_basis"), x.get("iso_currency_code") or "USD"),
        )
    for aid in account_ids:
        total = conn.execute("SELECT SUM(value) FROM holdings WHERE account_id=?", (aid,)).fetchone()[0]
        if total is None:
            total = conn.execute("SELECT balance FROM inv_accounts WHERE id=?", (aid,)).fetchone()[0]
        conn.execute("INSERT INTO inv_snapshots(date, account_id, value) VALUES (?,?,?) ON CONFLICT(date, account_id) DO UPDATE SET value=excluded.value", (today.isoformat(), aid, total))
    conn.commit()

    # Activity: everything Plaid has on the first sync, then a rolling window.
    first = item["last_sync"] is None
    start = today - timedelta(days=HISTORY_DAYS if first else REFRESH_DAYS)
    offset, total, fetched = 0, None, 0
    while total is None or offset < total:
        res = call(conn, "/investments/transactions/get", {
            "access_token": token, "start_date": start.isoformat(), "end_date": today.isoformat(),
            "options": {"count": PAGE, "offset": offset},
        })
        _store_securities(conn, res.get("securities", []))
        txs = res.get("investment_transactions", [])
        for t in txs:
            conn.execute(
                "INSERT INTO inv_transactions(id, account_id, security_id, date, name, type, subtype, quantity, amount, price, fees, currency) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET account_id=excluded.account_id, "
                "security_id=excluded.security_id, date=excluded.date, name=excluded.name, type=excluded.type, "
                "subtype=excluded.subtype, quantity=excluded.quantity, amount=excluded.amount, price=excluded.price, "
                "fees=excluded.fees, currency=excluded.currency",
                (t["investment_transaction_id"], t["account_id"], t.get("security_id"), t["date"], t.get("name"), t.get("type"),
                 t.get("subtype"), t.get("quantity") or 0, t.get("amount") or 0, t.get("price"), t.get("fees") or 0,
                 t.get("iso_currency_code") or "USD"),
            )
        fetched += len(txs)
        total = res.get("total_investment_transactions", 0)
        offset += len(txs)
        conn.commit()
        if not txs:
            break
    conn.execute("UPDATE plaid_items SET last_sync=?, error=NULL WHERE item_id=?",
                 (datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"), item_id))
    conn.commit()
    return {"accounts": len(account_ids), "holdings": len(h.get("holdings", [])), "transactions": fetched}


def sync_all(conn) -> dict:
    out = {"items": 0, "errors": []}
    for row in conn.execute("SELECT item_id, institution_name, products FROM plaid_items").fetchall():
        if "investments" not in (row["products"] or "investments"):
            continue   # bank and card connections sync with the bank sync
        try:
            sync_item(conn, row["item_id"])
            out["items"] += 1
        except PlaidError as e:
            out["errors"].append(f"{row['institution_name'] or 'Connection'}: {e}")
    return out


def hide_simplefin_duplicates(conn, item_id: str) -> list[str]:
    """When an institution is linked through Plaid, hide the same institution's SimpleFIN account on the Investments
    page (Plaid has the fuller data). Net worth keeps using the SimpleFIN balance, so nothing is counted twice."""
    item = conn.execute("SELECT institution_name FROM plaid_items WHERE item_id=?", (item_id,)).fetchone()
    compact = lambda v: re.sub(r"[^a-z0-9]", "", (v or "").lower())   # "E*TRADE" -> "etrade"
    key = compact(re.sub(r"\b(financial|investments?|securities|bank|inc|llc)\b", "", ((item["institution_name"] if item else "") or "").lower()))
    if len(key) < 4:
        return []
    hidden = []
    for a in conn.execute("SELECT id, name, institution FROM inv_accounts WHERE source='simplefin' AND hidden=0").fetchall():
        inst = compact(re.sub(r"\b(financial|investments?|securities|bank|inc|llc)\b", "", (a["institution"] or "").lower()))
        # "E*TRADE from Morgan Stanley" (Plaid) and "E*Trade" (SimpleFIN) are the same place
        if len(inst) >= 4 and (key in inst or inst in key):
            conn.execute("UPDATE inv_accounts SET hidden=1 WHERE id=?", (a["id"],))
            hidden.append(a["name"])
    return hidden


def hide_all_duplicates(conn) -> list[str]:
    """Run the duplicate check for every Plaid connection, once (later changes are yours to make on the page)."""
    if db.get_setting(conn, sk.DEDUPE_SIMPLEFIN_V2):
        return []
    hidden = []
    for r in conn.execute("SELECT item_id FROM plaid_items WHERE COALESCE(products, 'investments') LIKE '%investments%'").fetchall():
        hidden += hide_simplefin_duplicates(conn, r["item_id"])
    db.set_setting(conn, sk.DEDUPE_SIMPLEFIN_V2, "1")
    return hidden
