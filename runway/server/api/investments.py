"""Investments: the portfolio, live prices, cost basis, accounts you track by hand, and the retirement plan."""
from __future__ import annotations

from datetime import datetime

from ... import db, planner, portfolio, prices, sfinvest, tracked
from ..common import ApiError
from ..sync import refresh_prices


def api_plan_save(conn, _q, body):
    """Keep the retirement plan, so it's still there next time the page loads. {"plan": null} forgets it."""
    if not isinstance(body, dict) or "plan" not in body:
        raise ApiError("Nothing to save")
    try:
        return {"ok": True, "plan": planner.save(conn, body["plan"])}
    except planner.PlanError as e:
        raise ApiError(str(e)) from e


def api_cost_basis(conn, _q, body):
    """Set the price paid per share for a holding in one account (cost basis = that x shares held). Empty clears it."""
    acct, sec = body.get("account_id") or "", body.get("security_id") or ""
    if not conn.execute("SELECT 1 FROM holdings WHERE account_id=? AND security_id=?", (acct, sec)).fetchone():
        raise ApiError("That holding isn't in this account")
    v = body.get("per_share", body.get("cost_basis"))
    if v in (None, ""):
        conn.execute("DELETE FROM cost_overrides WHERE account_id=? AND security_id=?", (acct, sec))
        return {"ok": True, "cleared": True}
    try:
        v = db.number(str(v).replace(",", "").replace("$", ""))
    except ValueError:
        raise ApiError("Enter a number") from None
    if v < 0:
        raise ApiError("Price can't be negative")
    if "per_share" in body:
        conn.execute("INSERT INTO cost_overrides(account_id, security_id, cost_basis, per_share) VALUES (?,?,0,?) "
                     "ON CONFLICT(account_id, security_id) DO UPDATE SET per_share=excluded.per_share, cost_basis=0", (acct, sec, v))
    else:
        conn.execute("INSERT INTO cost_overrides(account_id, security_id, cost_basis, per_share) VALUES (?,?,?,NULL) "
                     "ON CONFLICT(account_id, security_id) DO UPDATE SET cost_basis=excluded.cost_basis, per_share=NULL", (acct, sec, v))
    return {"ok": True}


def live_tickers(conn) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT s.ticker FROM holdings h JOIN securities s ON s.id=h.security_id WHERE s.is_cash=0 AND s.ticker IS NOT NULL")]


def api_live_quotes(conn, _q, _b):
    """Near real-time prices for everything held (plus the S&P 500 fund, which tells us if the market is open)."""
    q = prices.quotes([*live_tickers(conn), prices.BENCHMARK])
    return {"quotes": q, "market": prices.market_state(q.get(prices.BENCHMARK)),
            "as_of": datetime.now().isoformat(timespec="seconds")}


def api_tracked_get(conn, _q, _b, acct_id):
    st = conn.execute("SELECT * FROM manual_state WHERE account_id=?", (acct_id,)).fetchone()
    return {"positions": tracked.positions_for(conn, acct_id), "state": dict(st) if st else None,
            "contributions": db.rows(conn.execute("SELECT date, amount FROM manual_contributions WHERE account_id=? ORDER BY date DESC LIMIT 12", (acct_id,)))}


def api_tracked_save(conn, _q, body, acct_id):
    try:
        tracked.save(conn, acct_id, body.get("rows") or [])
    except ValueError as e:
        raise ApiError(str(e)) from e
    conn.commit()
    try:
        refresh_prices(conn)   # prices for the funds just entered, then re-value the account
    except Exception:
        sfinvest.recapture_all(conn)
    return {"ok": True}


def api_investments(conn, q, _b):
    period = q.get("period", ["1Y"])[0]
    return portfolio.overview(conn, period if period in ("1M", "3M", "YTD", "1Y", "2Y", "MAX") else "1Y")
