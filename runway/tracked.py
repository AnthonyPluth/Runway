"""Tracked holdings for accounts that only report a balance (e.g. a Vestwell 401(k) through SimpleFIN).

You enter what the account holds (shares per fund) and how new contributions are split (percent per fund).
On every sync:
  1. each fund is valued at its closing price on the balance date (so price timing isn't mistaken for money moving);
  2. if the account's balance is higher than the funds are worth by more than a little, that's a contribution:
     it buys shares of each fund per your election at that day's price, and the new share counts are saved, so
     the same money is never counted twice;
  3. funds without a ticker (collective trusts and the like) share whatever the priced funds don't explain,
     in proportion to their last known value;
  4. anything left over is shown as a small "difference" line, and `drift` says how far off the share counts
     look, so the page can suggest updating them from a statement.
"""
from __future__ import annotations

from datetime import date, timedelta

from . import db

MIN_CONTRIBUTION = 5.0         # dollars
MIN_CONTRIBUTION_SHARE = 0.003  # of the balance: below this, differences are treated as noise
DRIFT_WARN = 0.02


def positions_for(conn, account_id: str) -> list[dict]:
    return db.rows(conn.execute(
        "SELECT m.*, s.ticker, s.name FROM manual_positions m LEFT JOIN securities s ON s.id=m.security_id "
        "WHERE m.account_id=? ORDER BY s.ticker, s.name", (account_id,)))


def _price_on(conn, ticker: str | None, on: str) -> float | None:
    if not ticker:
        return None
    r = conn.execute("SELECT close FROM prices WHERE ticker=? AND date<=? AND date>=? ORDER BY date DESC LIMIT 1",
                     (ticker, on, (date.fromisoformat(on) - timedelta(days=7)).isoformat())).fetchone()
    return r["close"] if r and r["close"] else None


def value(conn, account_id: str, balance: float, balance_date: str, today: date) -> dict | None:
    """Positions {security_id: {quantity, value, cost, cost_known}} for a tracked account, plus bookkeeping.
    Returns None if nothing is tracked for this account."""
    rows = positions_for(conn, account_id)
    if not rows:
        return None
    on = min(balance_date or today.isoformat(), today.isoformat())
    priced, unpriced = [], []
    for r in rows:
        px = _price_on(conn, r["ticker"], on)
        (priced if px else unpriced).append({**r, "price": px})

    def total_priced():
        return sum(r["shares"] * r["price"] for r in priced)

    unpriced_last = sum(r["last_value"] or 0 for r in unpriced)
    state = conn.execute("SELECT * FROM manual_state WHERE account_id=?", (account_id,)).fetchone()
    gap = balance - total_priced() - unpriced_last
    # The first time after you enter holdings, whatever gap there is (a statement a few weeks old, say) becomes the
    # baseline; only growth beyond it counts as new money.
    offset = gap if state is None or state["baseline"] is None else state["baseline"]
    diff = gap - offset
    contributed = 0.0
    # Only with at least one priced fund can new money be told apart from market moves.
    if priced and diff > max(MIN_CONTRIBUTION, MIN_CONTRIBUTION_SHARE * balance):
        # New money: buy per the contribution election (spread evenly if no election is set).
        weights = {r["security_id"]: (r["pct"] or 0) for r in rows}
        if sum(weights.values()) <= 0:
            weights = {k: 1.0 for k in weights}
        wsum = sum(weights.values())
        for r in priced:
            buy = diff * weights[r["security_id"]] / wsum
            r["shares"] += buy / r["price"]
            conn.execute("UPDATE manual_positions SET shares=? WHERE account_id=? AND security_id=?",
                         (r["shares"], account_id, r["security_id"]))
        for r in unpriced:
            r["last_value"] = (r["last_value"] or 0) + diff * weights[r["security_id"]] / wsum
        contributed = diff
        conn.execute("INSERT INTO manual_contributions(account_id, date, amount) VALUES (?,?,?)", (account_id, on, round(diff, 2)))
    # Funds without a price absorb the rest (market moves included), in proportion to their last value.
    rest = balance - total_priced()
    if unpriced:
        base = sum(r["last_value"] or 0 for r in unpriced)
        for r in unpriced:
            share = (r["last_value"] or 0) / base if base > 0 else 1 / len(unpriced)
            r["value"] = max(0.0, rest * share)
            conn.execute("UPDATE manual_positions SET last_value=? WHERE account_id=? AND security_id=?",
                         (r["value"], account_id, r["security_id"]))
    positions = {}
    for r in priced:
        positions[r["security_id"]] = {"quantity": r["shares"], "value": r["shares"] * r["price"], "cost": 0.0, "cost_known": False}
    for r in unpriced:
        positions[r["security_id"]] = {"quantity": r["value"], "value": r["value"], "cost": 0.0, "cost_known": False}
    explained = sum(p["value"] for p in positions.values())
    leftover = balance - explained
    drift = abs(leftover) / balance if balance else 0.0
    conn.execute("INSERT INTO manual_state(account_id, drift, checked, last_balance, baseline) VALUES (?,?,?,?,?) ON CONFLICT(account_id) DO UPDATE SET "
                 "drift=excluded.drift, checked=excluded.checked, last_balance=excluded.last_balance, "
                 "baseline=COALESCE(manual_state.baseline, excluded.baseline)",
                 (account_id, round(drift, 5), today.isoformat(), balance, offset if priced else None))
    return {"positions": positions, "leftover": leftover, "drift": drift, "contributed": contributed}


def save(conn, account_id: str, rows: list[dict], today: date | None = None) -> None:
    """Replace what a tracked account holds. rows: [{ticker or name, shares, pct}] (shares may be empty for a
    fund without a ticker; give its current value instead as `value`)."""
    today = today or date.today()
    if not conn.execute("SELECT 1 FROM inv_accounts WHERE id=?", (account_id,)).fetchone():
        raise ValueError("Account not found")
    clean = []
    for r in rows:
        ticker = (r.get("ticker") or "").strip().upper()
        name = (r.get("name") or "").strip()
        if not ticker and not name:
            continue
        try:
            shares = db.number(str(r.get("shares") or 0).replace(",", ""))
            pct = db.number(str(r.get("pct") or 0).replace("%", ""))
            val = db.number(str(r.get("value") or 0).replace(",", "").replace("$", ""))
        except ValueError:
            raise ValueError(f"Check the numbers for {ticker or name}")
        if shares < 0 or not 0 <= pct <= 100 or val < 0:
            raise ValueError(f"Check the numbers for {ticker or name}")
        if not ticker and not val:
            raise ValueError(f"{name}: without a ticker, enter its current value instead of shares")
        sec_id = "man:" + (ticker or "".join(ch for ch in name.lower() if ch.isalnum())[:40])
        conn.execute("INSERT INTO securities(id, ticker, name, is_cash, currency) VALUES (?,?,?,0,'USD') ON CONFLICT(id) DO UPDATE SET "
                     "name=COALESCE(excluded.name, securities.name)", (sec_id, ticker or None, name or None))
        clean.append((account_id, sec_id, shares if ticker else 0.0, pct, None if ticker else val, today.isoformat()))
    if clean and abs(sum(c[3] for c in clean) - 100) > 0.5 and sum(c[3] for c in clean) > 0:
        raise ValueError(f"Contribution percentages add up to {sum(c[3] for c in clean):g}%, not 100%")
    conn.execute("DELETE FROM manual_positions WHERE account_id=?", (account_id,))
    conn.executemany("INSERT INTO manual_positions(account_id, security_id, shares, pct, last_value, updated) VALUES (?,?,?,?,?,?)", clean)
    conn.execute("DELETE FROM manual_state WHERE account_id=?", (account_id,))
