"""Investment positions from SimpleFIN Bridge.

SimpleFIN Bridge attaches a `holdings` list to investment accounts (symbol, shares, market value, cost basis). It
doesn't send buys, sells or dividends, so every sync also records a dated snapshot of positions; the portfolio engine
uses those snapshots to separate market moves from money added or taken out.
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta

from . import db, tracked

CASH_WORDS = re.compile(r"money market|cash|sweep|core position|deposit|fdic|treasury fund", re.I)
MONEY_MARKET = re.compile(r"^[A-Z]{3}XX$")          # SPAXX, FDRXX, VMFXX, SWVXX...
BALANCE_ONLY = "sf:balance"
CASH_SYMBOLS = {"CASH", "USD", "CUR:USD", "$CASH", "MMDA"}
# Some brokerages' SimpleFIN feeds include scraped page text (icon names, button labels) in the description.
UI_JUNK = re.compile(r"\b(keyboard_arrow_(right|down|up|left)|info_outline|flag|Trade|more_vert|expand_(more|less))\b")
MISMATCH = 0.25   # reported value this far from shares x market price is treated as wrong


def clean_name(desc: str, symbol: str) -> str | None:
    name = re.sub(r"\s+", " ", UI_JUNK.sub(" ", desc or "")).strip()
    if not name or name.upper() == symbol.upper():
        return None   # filled in later from the price service
    return name


def market_value(conn, symbol: str, shares: float | None, reported: float | None, today: date) -> float | None:
    """Check a reported position value against shares x the latest close. Some feeds put the day's change (often
    a small negative number) where the market value belongs; when the two disagree, trust the market price."""
    if not symbol or not shares or shares <= 0:
        return reported
    r = conn.execute("SELECT date, close FROM prices WHERE ticker=? ORDER BY date DESC LIMIT 1", (symbol,)).fetchone()
    if not r or not r["close"] or date.fromisoformat(r["date"]) < today - timedelta(days=7):
        return reported
    mv = shares * r["close"]
    if reported is None or reported <= 0 or abs(reported - mv) > MISMATCH * mv:
        return mv
    return reported


def _num(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", "").replace("$", ""))
    except ValueError:
        return None


def _is_cash(symbol: str, name: str) -> bool:
    return symbol.upper() in CASH_SYMBOLS or bool(MONEY_MARKET.match(symbol.upper())) or (not symbol and bool(CASH_WORDS.search(name)))


def inv_id(acct_id: str) -> str:
    return f"sf:{acct_id}"


def _has_investments(raw: list) -> bool:
    return any(not _is_cash(str(h.get("symbol") or "").strip(), str(h.get("description") or "")) for h in raw)


def capture(conn, acct: dict, acct_id: str, org: str | None, balance: float, today: date | None = None,
            is_new: bool = False) -> bool:
    """Store one SimpleFIN account's positions if it's an investment account. Returns True when captured.

    Only accounts whose type is "investment" are captured. A newly seen account that holds securities (not just a
    money-market sweep, like a cash-management checking account) starts out as an investment account."""
    today = today or date.today()
    raw = acct.get("holdings")
    raw = raw if isinstance(raw, list) else []
    row = conn.execute("SELECT kind, hidden FROM accounts WHERE id=?", (acct_id,)).fetchone()
    kind = row["kind"] if row else None
    if is_new and kind != "investment" and _has_investments(raw):
        conn.execute("UPDATE accounts SET kind='investment' WHERE id=?", (acct_id,))
        kind = "investment"
    iid = inv_id(acct_id)
    # Keep what SimpleFIN sent so positions can be re-checked once fresh prices arrive.
    db.set_setting(conn, f"sf_raw:{acct_id}", json.dumps({"acct": {k: acct.get(k) for k in ("name", "currency", "holdings")},
                                                          "org": org, "balance": balance}))
    if kind != "investment":
        # No longer treated as an investment account: drop what we stored before.
        if conn.execute("SELECT 1 FROM inv_accounts WHERE id=?", (iid,)).fetchone():
            for table in ("holdings", "holding_snapshots", "inv_snapshots", "inv_transactions"):
                conn.execute(f"DELETE FROM {table} WHERE account_id=?", (iid,))
            conn.execute("DELETE FROM inv_accounts WHERE id=?", (iid,))
            seen = json.loads(db.get_setting(conn, "simplefin_holdings_seen") or "{}")
            seen.pop(acct_id, None)
            db.set_setting(conn, "simplefin_holdings_seen", json.dumps(seen))
        return False

    name = acct.get("name") or acct_id
    conn.execute(
        "INSERT INTO inv_accounts(id, item_id, name, type, subtype, balance, currency, source, institution) "
        "VALUES (?, 'simplefin', ?, 'investment', NULL, ?, ?, 'simplefin', ?) ON CONFLICT(id) DO UPDATE SET "
        "name=excluded.name, balance=excluded.balance, currency=excluded.currency, institution=excluded.institution",
        (iid, name, balance, acct.get("currency") or "USD", org),
    )

    positions: dict[str, dict] = {}
    for h in raw:
        symbol = str(h.get("symbol") or "").strip().upper()
        desc = str(h.get("description") or h.get("name") or "").strip()
        shares = _num(h.get("shares"))
        value = _num(h.get("market_value"))
        if value is None and shares is not None and _num(h.get("price")) is not None:
            value = shares * _num(h.get("price"))
        cash = _is_cash(symbol, desc)
        if not cash:
            value = market_value(conn, symbol, shares, value, today)
        if value is None:
            continue
        cost = _num(h.get("cost_basis"))
        if cost is None and _num(h.get("purchase_price")) and shares:
            cost = _num(h.get("purchase_price")) * shares   # purchase_price is per share
        if not cost and not cash and shares:
            cost = None   # a zero cost basis on a real position means the brokerage didn't report one
        name = clean_name(desc, symbol) if symbol else (desc or None)
        sec_id = "sf:" + (symbol or re.sub(r"\W+", "-", desc.lower())[:60] or str(h.get("id")))
        conn.execute(
            "INSERT INTO securities(id, ticker, name, is_cash, currency, isin) VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "name=COALESCE(excluded.name, securities.name), is_cash=excluded.is_cash, isin=COALESCE(excluded.isin, securities.isin)",
            (sec_id, symbol or None, name, 1 if cash else 0, h.get("currency") or "USD", h.get("isin")),
        )
        if name is None:  # drop a junk name stored by an earlier version
            conn.execute("UPDATE securities SET name=NULL WHERE id=? AND name LIKE '%keyboard_arrow%'", (sec_id,))
        if cash:
            conn.execute("UPDATE securities SET type='cash' WHERE id=?", (sec_id,))
            shares = value
        p = positions.setdefault(sec_id, {"quantity": 0.0, "value": 0.0, "cost": 0.0, "cost_known": True})
        p["quantity"] += shares if shares is not None else 0.0
        p["value"] += value
        if cost is None:
            p["cost_known"] = False
        else:
            p["cost"] += cost

    tracked_leftover = False
    if not positions:   # balance-only account: use the holdings you entered, if any
        row = conn.execute("SELECT balance_date FROM accounts WHERE id=?", (acct_id,)).fetchone()
        t = tracked.value(conn, iid, balance, (row["balance_date"] if row else None) or today.isoformat(), today)
        if t:
            positions = t["positions"]
            tracked_leftover = True

    # Whatever the balance doesn't explain is uninvested cash (or the whole balance, when no positions come through).
    leftover = balance - sum(p["value"] for p in positions.values())
    if abs(leftover) >= 1.0:
        if tracked_leftover:
            cid = "sf:unexplained"
            conn.execute("INSERT INTO securities(id, ticker, name, type, is_cash, currency) "
                         "VALUES (?,NULL,'Difference from synced balance','other',1,'USD') ON CONFLICT(id) DO NOTHING", (cid,))
        elif positions:
            cid = "sf:cash"
            conn.execute("INSERT INTO securities(id, ticker, name, type, is_cash, currency) VALUES (?,NULL,'Cash','cash',1,'USD') "
                         "ON CONFLICT(id) DO NOTHING", (cid,))
        else:  # e.g. a 401(k) where SimpleFIN only knows the total: invested in something, we just can't see what
            cid = BALANCE_ONLY
            conn.execute("INSERT INTO securities(id, ticker, name, type, is_cash, currency) "
                         "VALUES (?,NULL,'Balance only (no positions reported)','other',0,'USD') ON CONFLICT(id) DO NOTHING", (cid,))
        p = positions.setdefault(cid, {"quantity": 0.0, "value": 0.0, "cost": 0.0, "cost_known": True})
        p["quantity"] += leftover
        p["value"] += leftover

    conn.execute("DELETE FROM holdings WHERE account_id=?", (iid,))
    conn.execute("DELETE FROM holding_snapshots WHERE account_id=? AND date=?", (iid, today.isoformat()))
    for sec_id, p in positions.items():
        price = p["value"] / p["quantity"] if p["quantity"] else None
        conn.execute(
            "INSERT INTO holdings(account_id, security_id, quantity, price, price_as_of, value, cost_basis, currency) VALUES (?,?,?,?,?,?,?,'USD')",
            (iid, sec_id, p["quantity"], price, today.isoformat(), p["value"], p["cost"] if p["cost_known"] else None),
        )
        conn.execute("INSERT INTO holding_snapshots(date, account_id, security_id, quantity, value) VALUES (?,?,?,?,?)",
                     (today.isoformat(), iid, sec_id, p["quantity"], p["value"]))
    conn.execute("INSERT INTO inv_snapshots(date, account_id, value) VALUES (?,?,?) ON CONFLICT(date, account_id) DO UPDATE SET value=excluded.value", (today.isoformat(), iid, balance))

    # What SimpleFIN actually sent, so Setup can show it (field names and counts only).
    seen = json.loads(db.get_setting(conn, "simplefin_holdings_seen") or "{}")
    fields = sorted({k for h in raw for k in h}) if raw else []
    seen[acct_id] = {"id": acct_id, "name": name, "org": org, "positions": len(raw), "fields": fields, "at": today.isoformat()}
    db.set_setting(conn, "simplefin_holdings_seen", json.dumps(seen))
    return True


def recapture_all(conn, today: date | None = None) -> int:
    """Re-run capture for every SimpleFIN account from what it last sent (after prices refresh)."""
    n = 0
    for key, value in conn.execute("SELECT key, value FROM settings WHERE key LIKE 'sf_raw:%'").fetchall():
        acct_id = key[len("sf_raw:"):]
        if not conn.execute("SELECT 1 FROM accounts WHERE id=?", (acct_id,)).fetchone():
            continue
        d = json.loads(value)
        if capture(conn, d["acct"], acct_id, d.get("org"), d.get("balance") or 0.0, today):
            n += 1
    return n


def repair_stored(conn, today: date | None = None) -> int:
    """Start-up repair for positions saved by earlier versions (before the raw feed was kept): rebuild each account's
    feed from its stored holdings and run it through today's checks (market values, cost basis, names)."""
    conn.execute("UPDATE securities SET name=NULL WHERE id LIKE 'sf:%' AND name LIKE '%keyboard_arrow%'")
    n = 0
    for a in conn.execute("SELECT * FROM inv_accounts WHERE source='simplefin'").fetchall():
        acct_id = a["id"][3:]
        if conn.execute("SELECT 1 FROM settings WHERE key=?", (f"sf_raw:{acct_id}",)).fetchone():
            continue
        if not conn.execute("SELECT 1 FROM accounts WHERE id=?", (acct_id,)).fetchone():
            continue
        raw = [{"symbol": h["ticker"] or "", "description": h["name"] or h["ticker"] or "", "shares": h["quantity"],
                "market_value": h["value"], "cost_basis": h["cost_basis"]}
               for h in conn.execute("SELECT h.*, s.ticker, s.name FROM holdings h JOIN securities s ON s.id=h.security_id "
                                     "WHERE h.account_id=? AND h.security_id NOT IN ('sf:cash', ?)", (a["id"], BALANCE_ONLY))]
        capture(conn, {"name": a["name"], "currency": a["currency"] or "USD", "holdings": raw}, acct_id, a["institution"],
                a["balance"] or 0.0, today)
        n += 1
    return n
