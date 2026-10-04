"""Investment positions from SimpleFIN Bridge.

SimpleFIN Bridge attaches a `holdings` list to investment accounts (symbol, shares, market value, cost basis). It
doesn't send buys, sells or dividends, so every sync also records a dated snapshot of positions; the portfolio engine
uses those snapshots to separate market moves from money added or taken out.
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta

from sqlalchemy import delete, func, insert, select, update

from . import db, tracked
from . import settings_keys as sk
from .models import Account, Holding, HoldingSnapshot, InvAccount, InvSnapshot, InvTransaction, Price, Security, Setting

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
    r = conn.execute(select(Price.date, Price.close).where(Price.ticker == symbol).order_by(Price.date.desc()).limit(1)).fetchone()
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
    row = conn.execute(select(Account.kind, Account.hidden).where(Account.id == acct_id)).fetchone()
    kind = row["kind"] if row else None
    if is_new and kind != "investment" and _has_investments(raw):
        conn.execute(update(Account).where(Account.id == acct_id).values(kind="investment"))
        kind = "investment"
    iid = inv_id(acct_id)
    # Keep what SimpleFIN sent so positions can be re-checked once fresh prices arrive.
    db.set_setting(conn, sk.sf_raw(acct_id), json.dumps({"acct": {k: acct.get(k) for k in ("name", "currency", "holdings")},
                                                          "org": org, "balance": balance}))
    if kind != "investment":
        # No longer treated as an investment account: drop what we stored before.
        if conn.execute(select(InvAccount.id).where(InvAccount.id == iid)).fetchone():
            for model in (Holding, HoldingSnapshot, InvSnapshot, InvTransaction):
                conn.execute(delete(model).where(model.account_id == iid))
            conn.execute(delete(InvAccount).where(InvAccount.id == iid))
            seen = json.loads(db.get_setting(conn, sk.SIMPLEFIN_HOLDINGS_SEEN) or "{}")
            seen.pop(acct_id, None)
            db.set_setting(conn, sk.SIMPLEFIN_HOLDINGS_SEEN, json.dumps(seen))
        return False

    name = acct.get("name") or acct_id
    db.upsert(conn, InvAccount, {"id": iid, "item_id": "simplefin", "name": name, "type": "investment", "subtype": None,
                                 "balance": balance, "currency": acct.get("currency") or "USD", "source": "simplefin",
                                 "institution": org},
              key=["id"], update=["name", "balance", "currency", "institution"])

    positions: dict[str, dict] = {}
    for h in raw:
        symbol = str(h.get("symbol") or "").strip().upper()
        desc = str(h.get("description") or h.get("name") or "").strip()
        shares = _num(h.get("shares"))
        value = _num(h.get("market_value"))
        price = _num(h.get("price"))
        if value is None and shares is not None and price is not None:
            value = shares * price
        cash = _is_cash(symbol, desc)
        if not cash:
            value = market_value(conn, symbol, shares, value, today)
        if value is None:
            continue
        cost = _num(h.get("cost_basis"))
        purchase_price = _num(h.get("purchase_price"))
        if cost is None and purchase_price and shares:
            cost = purchase_price * shares   # purchase_price is per share
        if not cost and not cash and shares:
            cost = None   # a zero cost basis on a real position means the brokerage didn't report one
        name = clean_name(desc, symbol) if symbol else (desc or None)
        sec_id = "sf:" + (symbol or re.sub(r"\W+", "-", desc.lower())[:60] or str(h.get("id")))
        db.upsert(conn, Security, {"id": sec_id, "ticker": symbol or None, "name": name, "is_cash": 1 if cash else 0,
                                   "currency": h.get("currency") or "USD", "isin": h.get("isin")},
                  key=["id"], update=lambda ex: {"name": func.coalesce(ex.name, Security.name), "is_cash": ex.is_cash,
                                                 "isin": func.coalesce(ex.isin, Security.isin)})
        if name is None:  # drop a junk name stored by an earlier version
            conn.execute(update(Security).where(Security.id == sec_id, Security.name.like("%keyboard_arrow%")).values(name=None))
        if cash:
            conn.execute(update(Security).where(Security.id == sec_id).values(type="cash"))
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
        row = conn.execute(select(Account.balance_date).where(Account.id == acct_id)).fetchone()
        t = tracked.value(conn, iid, balance, (row["balance_date"] if row else None) or today.isoformat(), today)
        if t:
            positions = t["positions"]
            tracked_leftover = True

    # Whatever the balance doesn't explain is uninvested cash (or the whole balance, when no positions come through).
    leftover = balance - sum(p["value"] for p in positions.values())
    if abs(leftover) >= 1.0:
        if tracked_leftover:
            cid, sec_name, sec_type, sec_cash = "sf:unexplained", "Difference from synced balance", "other", 1
        elif positions:
            cid, sec_name, sec_type, sec_cash = "sf:cash", "Cash", "cash", 1
        else:  # e.g. a 401(k) where SimpleFIN only knows the total: invested in something, we just can't see what
            cid, sec_name, sec_type, sec_cash = BALANCE_ONLY, "Balance only (no positions reported)", "other", 0
        db.insert_ignore(conn, Security, {"id": cid, "ticker": None, "name": sec_name, "type": sec_type, "is_cash": sec_cash,
                                          "currency": "USD"}, key=["id"])
        p = positions.setdefault(cid, {"quantity": 0.0, "value": 0.0, "cost": 0.0, "cost_known": True})
        p["quantity"] += leftover
        p["value"] += leftover

    conn.execute(delete(Holding).where(Holding.account_id == iid))
    conn.execute(delete(HoldingSnapshot).where(HoldingSnapshot.account_id == iid, HoldingSnapshot.date == today.isoformat()))
    conn.execute(insert(Holding), [
        {"account_id": iid, "security_id": sec_id, "quantity": p["quantity"],
         "price": p["value"] / p["quantity"] if p["quantity"] else None, "price_as_of": today.isoformat(),
         "value": p["value"], "cost_basis": p["cost"] if p["cost_known"] else None, "currency": "USD"}
        for sec_id, p in positions.items()])
    conn.execute(insert(HoldingSnapshot), [
        {"date": today.isoformat(), "account_id": iid, "security_id": sec_id, "quantity": p["quantity"], "value": p["value"]}
        for sec_id, p in positions.items()])
    db.upsert(conn, InvSnapshot, {"date": today.isoformat(), "account_id": iid, "value": balance}, key=["date", "account_id"])

    # What SimpleFIN actually sent, so Setup can show it (field names and counts only).
    seen = json.loads(db.get_setting(conn, sk.SIMPLEFIN_HOLDINGS_SEEN) or "{}")
    fields = sorted({k for h in raw for k in h}) if raw else []
    seen[acct_id] = {"id": acct_id, "name": name, "org": org, "positions": len(raw), "fields": fields, "at": today.isoformat()}
    db.set_setting(conn, sk.SIMPLEFIN_HOLDINGS_SEEN, json.dumps(seen))
    return True


def recapture_all(conn, today: date | None = None) -> int:
    """Re-run capture for every SimpleFIN account from what it last sent (after prices refresh)."""
    n = 0
    for key, value in conn.execute(select(Setting.key, Setting.value).where(Setting.key.like(sk.SF_RAW_PREFIX + "%"))).fetchall():
        acct_id = key[len(sk.SF_RAW_PREFIX):]
        if not conn.execute(select(Account.id).where(Account.id == acct_id)).fetchone():
            continue
        d = json.loads(value)
        if capture(conn, d["acct"], acct_id, d.get("org"), d.get("balance") or 0.0, today):
            n += 1
    return n

