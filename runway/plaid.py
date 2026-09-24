"""Plaid client for investment accounts: Link tokens, token exchange, holdings and investment activity."""
from __future__ import annotations

import json
import re
import os
import ssl
import urllib.error
import urllib.request
from datetime import date, timedelta

from . import db

HOSTS = {"sandbox": "https://sandbox.plaid.com", "production": "https://production.plaid.com"}
HISTORY_DAYS = 730      # Plaid keeps up to 24 months of investment activity
REFRESH_DAYS = 45       # window re-read on routine syncs
PAGE = 500              # Plaid's maximum page size


class PlaidError(Exception):
    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code


def _ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    try:
        import certifi  # type: ignore

        ctx.load_verify_locations(certifi.where())
    except Exception:
        pass
    return ctx


def configured(conn) -> bool:
    return bool(db.get_setting(conn, "plaid_client_id") and db.get_setting(conn, "plaid_secret"))


def base_url(conn) -> str:
    override = os.environ.get("RUNWAY_PLAID_URL")
    if override:
        return override.rstrip("/")
    return HOSTS.get(db.get_setting(conn, "plaid_env", "production") or "production", HOSTS["production"])


def call(conn, path: str, body: dict) -> dict:
    client_id, secret = db.get_setting(conn, "plaid_client_id"), db.get_setting(conn, "plaid_secret")
    if not client_id or not secret:
        raise PlaidError("Add your Plaid client ID and secret in Setup first.")
    payload = json.dumps({"client_id": client_id, "secret": secret, **body}).encode()
    req = urllib.request.Request(
        base_url(conn) + path, data=payload, method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "Runway/0.1", "Plaid-Version": "2020-09-14"},
    )
    try:
        with urllib.request.urlopen(req, timeout=90, context=_ctx()) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode())
        except Exception:
            err = {}
        code = err.get("error_code")
        msg = err.get("display_message") or err.get("error_message") or f"HTTP {e.code}"
        raise PlaidError(f"Plaid: {msg}" + (f" ({code})" if code else ""), code) from e
    except urllib.error.URLError as e:
        raise PlaidError(f"Couldn't reach Plaid: {e.reason}") from e


# ------------------------------------------------------------------------------------------------ linking

def link_token(conn, item_id: str | None = None) -> str:
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
    else:
        body["products"] = ["investments"]
    redirect = db.get_setting(conn, "plaid_redirect_uri")
    if redirect:
        body["redirect_uri"] = redirect
    return call(conn, "/link/token/create", body)["link_token"]


def exchange(conn, public_token: str, institution: dict | None = None) -> str:
    res = call(conn, "/item/public_token/exchange", {"public_token": public_token})
    item_id, token = res["item_id"], res["access_token"]
    institution = institution or {}
    conn.execute(
        "INSERT INTO plaid_items(item_id, access_token, institution_id, institution_name, env) VALUES (?,?,?,?,?) "
        "ON CONFLICT(item_id) DO UPDATE SET access_token=excluded.access_token, error=NULL",
        (item_id, token, institution.get("institution_id"), institution.get("name"), db.get_setting(conn, "plaid_env", "production")),
    )
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
    ids = [r["id"] for r in conn.execute("SELECT id FROM inv_accounts WHERE item_id=?", (item_id,))]
    for aid in ids:
        for table in ("holdings", "inv_transactions", "inv_snapshots"):
            conn.execute(f"DELETE FROM {table} WHERE account_id=?", (aid,))
    conn.execute("DELETE FROM inv_accounts WHERE item_id=?", (item_id,))
    conn.execute("DELETE FROM plaid_items WHERE item_id=?", (item_id,))


# ------------------------------------------------------------------------------------------------ syncing

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
    account_ids = [a["account_id"] for a in h.get("accounts", [])]
    for aid in account_ids:  # holdings are a full snapshot: replace
        conn.execute("DELETE FROM holdings WHERE account_id=?", (aid,))
    for x in h.get("holdings", []):
        conn.execute(
            "INSERT OR REPLACE INTO holdings(account_id, security_id, quantity, price, price_as_of, value, cost_basis, currency) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (x["account_id"], x["security_id"], x.get("quantity"), x.get("institution_price"), x.get("institution_price_as_of"),
             x.get("institution_value"), x.get("cost_basis"), x.get("iso_currency_code") or "USD"),
        )
    for aid in account_ids:
        total = conn.execute("SELECT SUM(value) FROM holdings WHERE account_id=?", (aid,)).fetchone()[0]
        if total is None:
            total = conn.execute("SELECT balance FROM inv_accounts WHERE id=?", (aid,)).fetchone()[0]
        conn.execute("INSERT OR REPLACE INTO inv_snapshots(date, account_id, value) VALUES (?,?,?)", (today.isoformat(), aid, total))
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
                "INSERT OR REPLACE INTO inv_transactions(id, account_id, security_id, date, name, type, subtype, quantity, amount, price, fees, currency) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
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
    conn.execute("UPDATE plaid_items SET last_sync=datetime('now'), error=NULL WHERE item_id=?", (item_id,))
    conn.commit()
    return {"accounts": len(account_ids), "holdings": len(h.get("holdings", [])), "transactions": fetched}


def sync_all(conn) -> dict:
    out = {"items": 0, "errors": []}
    for row in conn.execute("SELECT item_id, institution_name FROM plaid_items").fetchall():
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
        if key in compact(a["institution"]):
            conn.execute("UPDATE inv_accounts SET hidden=1 WHERE id=?", (a["id"],))
            hidden.append(a["name"])
    return hidden
