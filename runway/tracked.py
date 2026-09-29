"""Tracked holdings for accounts that only report a balance (e.g. a Vestwell 401(k) through SimpleFIN).

You enter what the account holds (shares per fund) and how new contributions are split (percent per fund).
On every sync:
  1. each fund is valued at its closing price on the balance date (so price timing isn't mistaken for money moving);
  2. if every priced fund has that day's close (the last market day on or before the balance date) and the
     account's balance is higher than the funds are worth by more than a little, that's a contribution:
     it buys shares of each fund per your election at that day's price, and the new share counts are saved, so
     the same money is never counted twice;
  3. funds without a ticker (collective trusts and the like) share whatever the priced funds don't explain,
     in proportion to their last known value;
  4. anything left over is shown as a small "difference" line, and `drift` says how far off the share counts
     look, so the page can suggest updating them from a statement.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import delete, func, insert, select, update

from . import bankdays, db
from .models import InvAccount, ManualContribution, ManualPosition, ManualState, Price, Security

MIN_CONTRIBUTION = 5.0         # dollars
MIN_CONTRIBUTION_SHARE = 0.003  # of the balance: below this, differences are treated as noise
DRIFT_WARN = 0.02


def positions_for(conn, account_id: str) -> list[dict]:
    return db.rows(conn.execute(
        select(ManualPosition, Security.ticker, Security.name)
        .outerjoin(Security, Security.id == ManualPosition.security_id)
        .where(ManualPosition.account_id == account_id).order_by(Security.ticker, Security.name)))


def _price_on(conn, ticker: str | None, on: str) -> tuple[float | None, str | None]:
    """The close on or shortly before `on`, and its date."""
    if not ticker:
        return None, None
    week_before = (date.fromisoformat(on) - timedelta(days=7)).isoformat()
    r = conn.execute(select(Price.close, Price.date).where(Price.ticker == ticker, Price.date <= on, Price.date >= week_before)
                     .order_by(Price.date.desc()).limit(1)).fetchone()
    return (r["close"], r["date"]) if r and r["close"] else (None, None)


def value(conn, account_id: str, balance: float, balance_date: str, today: date) -> dict | None:
    """Positions {security_id: {quantity, value, cost, cost_known}} for a tracked account, plus bookkeeping.
    Returns None if nothing is tracked for this account."""
    rows = positions_for(conn, account_id)
    if not rows:
        return None
    on = min(balance_date or today.isoformat(), today.isoformat())
    priced: list[dict[str, Any]] = []
    unpriced: list[dict[str, Any]] = []
    for r in rows:
        px, px_date = _price_on(conn, r["ticker"], on)
        (priced if px else unpriced).append({**r, "price": px, "price_date": px_date})
    # A rise measured with an older close (prices not refreshed yet) is partly the market, not new money: only the
    # balance day's close tells them apart. The sync values again once prices are in.
    market_day = bankdays.previous_business_day(date.fromisoformat(on)).isoformat()
    fresh = all(r["price_date"] >= market_day for r in priced)

    def total_priced():
        return sum(r["shares"] * r["price"] for r in priced)

    unpriced_last = sum(r["last_value"] or 0 for r in unpriced)
    state = conn.execute(select(ManualState).where(ManualState.account_id == account_id)).fetchone()
    gap = balance - total_priced() - unpriced_last
    # The first time after you enter holdings, whatever gap there is (a statement a few weeks old, say) becomes the
    # baseline; only growth beyond it counts as new money.
    offset = gap if state is None or state["baseline"] is None else state["baseline"]
    diff = gap - offset
    contributed = 0.0
    # Only with at least one priced fund can new money be told apart from market moves.
    if priced and fresh and diff > max(MIN_CONTRIBUTION, MIN_CONTRIBUTION_SHARE * balance):
        _invest_contribution(conn, account_id, rows, priced, unpriced, diff, on)
        contributed = diff
    # Funds without a price absorb the rest (market moves included), in proportion to their last value.
    if unpriced:
        _spread_over_unpriced(conn, account_id, unpriced, balance - total_priced())
    positions = {}
    for r in priced:
        positions[r["security_id"]] = {"quantity": r["shares"], "value": r["shares"] * r["price"], "cost": 0.0, "cost_known": False}
    for r in unpriced:
        positions[r["security_id"]] = {"quantity": r["value"], "value": r["value"], "cost": 0.0, "cost_known": False}
    explained = sum(p["value"] for p in positions.values())
    leftover = balance - explained
    drift = abs(leftover) / balance if balance else 0.0
    db.upsert(conn, ManualState, {"account_id": account_id, "drift": round(drift, 5), "checked": today.isoformat(),
                                  "last_balance": balance, "baseline": offset if priced else None},
              key=["account_id"], update=lambda ex: {"drift": ex.drift, "checked": ex.checked, "last_balance": ex.last_balance,
                                                     "baseline": func.coalesce(ManualState.baseline, ex.baseline)})
    return {"positions": positions, "leftover": leftover, "drift": drift, "contributed": contributed}


def _invest_contribution(conn, account_id: str, rows: list[dict], priced: list[dict], unpriced: list[dict],
                         amount: float, on: str) -> None:
    """New money: buy per the contribution election (spread evenly if no election is set), save the new share
    counts so the same money is never counted twice, and record the contribution."""
    weights = {r["security_id"]: (r["pct"] or 0) for r in rows}
    if sum(weights.values()) <= 0:
        weights = {k: 1.0 for k in weights}
    wsum = sum(weights.values())
    for r in priced:
        buy = amount * weights[r["security_id"]] / wsum
        r["shares"] += buy / r["price"]
        conn.execute(update(ManualPosition).where(ManualPosition.account_id == account_id,
                                                  ManualPosition.security_id == r["security_id"]).values(shares=r["shares"]))
    for r in unpriced:
        r["last_value"] = (r["last_value"] or 0) + amount * weights[r["security_id"]] / wsum
    conn.execute(insert(ManualContribution).values(account_id=account_id, date=on, amount=round(amount, 2)))


def _spread_over_unpriced(conn, account_id: str, unpriced: list[dict], rest: float) -> None:
    """Value the funds without a price at what the priced ones don't explain, split by their last known value
    (evenly when none is known), and remember it."""
    base = sum(r["last_value"] or 0 for r in unpriced)
    for r in unpriced:
        share = (r["last_value"] or 0) / base if base > 0 else 1 / len(unpriced)
        r["value"] = max(0.0, rest * share)
        conn.execute(update(ManualPosition).where(ManualPosition.account_id == account_id,
                                                  ManualPosition.security_id == r["security_id"]).values(last_value=r["value"]))


def save(conn, account_id: str, rows: list[dict], today: date | None = None) -> None:
    """Replace what a tracked account holds. rows: [{ticker or name, shares, pct}] (shares may be empty for a
    fund without a ticker; give its current value instead as `value`)."""
    today = today or date.today()
    if not conn.execute(select(InvAccount.id).where(InvAccount.id == account_id)).fetchone():
        raise ValueError("Account not found")
    clean: list[dict[str, Any]] = []
    for r in rows:
        entry = _clean_entry(r)
        if entry is None:
            continue
        ticker, name, shares, pct, val = entry
        sec_id = "man:" + (ticker or "".join(ch for ch in name.lower() if ch.isalnum())[:40])
        db.upsert(conn, Security, {"id": sec_id, "ticker": ticker or None, "name": name or None, "is_cash": 0, "currency": "USD"},
                  key=["id"], update=lambda ex: {"name": func.coalesce(ex.name, Security.name)})
        clean.append({"account_id": account_id, "security_id": sec_id, "shares": shares if ticker else 0.0, "pct": pct,
                      "last_value": None if ticker else val, "updated": today.isoformat()})
    total_pct = sum(c["pct"] for c in clean)
    if clean and abs(total_pct - 100) > 0.5 and total_pct > 0:
        raise ValueError(f"Contribution percentages add up to {total_pct:g}%, not 100%")
    conn.execute(delete(ManualPosition).where(ManualPosition.account_id == account_id))
    conn.execute(insert(ManualPosition), clean)
    conn.execute(delete(ManualState).where(ManualState.account_id == account_id))


def _clean_entry(r: dict) -> tuple[str, str, float, float, float] | None:
    """One fund as entered: (ticker, name, shares, contribution percent, value), checked. None for a blank row."""
    ticker = (r.get("ticker") or "").strip().upper()
    name = (r.get("name") or "").strip()
    if not ticker and not name:
        return None
    try:
        shares = db.number(str(r.get("shares") or 0).replace(",", ""))
        pct = db.number(str(r.get("pct") or 0).replace("%", ""))
        val = db.number(str(r.get("value") or 0).replace(",", "").replace("$", ""))
    except ValueError:
        raise ValueError(f"Check the numbers for {ticker or name}") from None
    if shares < 0 or not 0 <= pct <= 100 or val < 0:
        raise ValueError(f"Check the numbers for {ticker or name}")
    if not ticker and not val:
        raise ValueError(f"{name}: without a ticker, enter its current value instead of shares")
    return ticker, name, shares, pct, val
