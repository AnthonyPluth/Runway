"""Investments: the portfolio, live prices, cost basis, accounts you track by hand, and the retirement plan."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, select

from ... import db, planner, portfolio, prices, sfinvest, tracked
from ...models import CostOverride, Holding, ManualContribution, ManualState, Security
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
    if not conn.execute(select(Holding.account_id).where(Holding.account_id == acct, Holding.security_id == sec)).fetchone():
        raise ApiError("That holding isn't in this account")
    v = body.get("per_share", body.get("cost_basis"))
    if v in (None, ""):
        conn.execute(delete(CostOverride).where(CostOverride.account_id == acct, CostOverride.security_id == sec))
        return {"ok": True, "cleared": True}
    try:
        v = db.number(str(v).replace(",", "").replace("$", ""))
    except ValueError:
        raise ApiError("Enter a number") from None
    if v < 0:
        raise ApiError("Price can't be negative")
    if "per_share" in body:
        db.upsert(conn, CostOverride, {"account_id": acct, "security_id": sec, "cost_basis": 0, "per_share": v},
                  key=["account_id", "security_id"])
    else:
        db.upsert(conn, CostOverride, {"account_id": acct, "security_id": sec, "cost_basis": v, "per_share": None},
                  key=["account_id", "security_id"])
    return {"ok": True}


def live_tickers(conn) -> list[str]:
    return [r[0] for r in conn.execute(
        select(Security.ticker).distinct().select_from(Holding).join(Security, Security.id == Holding.security_id)
        .where(Security.is_cash == 0, Security.ticker.is_not(None)))]


def api_live_quotes(conn, _q, _b):
    """Near real-time prices for everything held (plus the S&P 500 fund, which tells us if the market is open)."""
    q = prices.quotes([*live_tickers(conn), prices.BENCHMARK])
    return {"quotes": q, "market": prices.market_state(q.get(prices.BENCHMARK)),
            "as_of": datetime.now().isoformat(timespec="seconds")}


def api_tracked_get(conn, _q, _b, acct_id):
    st = conn.execute(select(ManualState).where(ManualState.account_id == acct_id)).fetchone()
    c = ManualContribution
    return {"positions": tracked.positions_for(conn, acct_id), "state": dict(st) if st else None,
            "contributions": db.rows(conn.execute(
                select(c.date, c.amount).where(c.account_id == acct_id).order_by(c.date.desc()).limit(12)))}


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
