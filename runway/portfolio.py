"""Portfolio analytics over investment data from Plaid (positions plus activity) or SimpleFIN (positions only).

History is rebuilt from activity, the way Ghostfolio does it: start from what you hold today and walk backwards
through buys, sells and cash movements, valuing each day's positions at that day's price. Returns are
time-weighted, so deposits and withdrawals don't count as gains.

SimpleFIN sends no activity, so those accounts are rebuilt from the daily position snapshots Runway saves on each
sync: each day's value is the latest snapshot's positions at that day's prices, and any change in positions between
snapshots counts as money added or withdrawn. Before the first snapshot the page can only estimate, by assuming you
held the same positions; `estimated_before` marks where that stops.
"""
from __future__ import annotations

import bisect
import contextlib
import re
import statistics
from collections import defaultdict
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta
from sqlalchemy import and_, case, func, literal_column, or_, select

from . import db, merchants, plaid, planner, prices, splits
from . import settings_keys as sk
from .models import (Account, Category, CostOverride, Holding, HoldingSnapshot, InvAccount, InvTransaction, ManualPosition,
                     ManualState, PlaidItem, Price, Security, Transaction)

HISTORY_DAYS = 730
# Cash moving in or out of the account from outside (not investment results).
FLOW_SUBTYPES = {"deposit", "contribution", "withdrawal", "distribution", "transfer", "send", "request", "rollover"}
CORPORATE_ACTIONS = {"split", "stock distribution", "spin off", "merger", "adjustment", "exercise", "assignment", "expire"}
INCOME_SUBTYPES = {"dividend", "qualified dividend", "non-qualified dividend", "interest", "long-term capital gain",
                   "short-term capital gain", "unqualified gain", "interest receivable"}
ASSET_CLASS = {"equity": "Stocks", "etf": "ETFs", "mutual fund": "Mutual funds", "fixed income": "Bonds",
               "cash": "Cash", "cryptocurrency": "Crypto", "derivative": "Options", "loan": "Other", "other": "Other"}


def asset_class(sec: dict) -> str:
    if (sec.get("security_id") or sec.get("id")) == "sf:balance":
        return "Not reported"
    if sec.get("is_cash"):
        return "Cash"
    return ASSET_CLASS.get((sec.get("type") or "other").lower(), "Other")


def _accounts(conn) -> list[dict]:
    """Investment accounts. One is hidden if you hid the account it is in Settings -> Accounts (the only way to leave
    one out): SimpleFIN's 'sf:<account id>', or the account a Plaid one was matched to.

    An account connected through both SimpleFIN and Plaid is one account: the Plaid one, which has the fuller data
    (holdings, cost basis, activity), stands for it, and the SimpleFIN one is marked duplicate_of it and never counted
    (the page leaves it out)."""
    ia = InvAccount
    institution = func.coalesce(PlaidItem.institution_name, ia.institution).label("institution_name")
    rows = db.rows(conn.execute(
        select(ia, institution,
               select(func.count()).select_from(ManualPosition).where(ManualPosition.account_id == ia.id)
               .scalar_subquery().label("tracked"),
               select(ManualState.drift).where(ManualState.account_id == ia.id).scalar_subquery().label("drift"),
               func.coalesce(Account.hidden, 0).label("hidden_in_accounts"))
        .outerjoin(PlaidItem, PlaidItem.item_id == ia.item_id)
        .outerjoin(Account, Account.id == case((ia.source == "simplefin", func.substr(ia.id, 4)), else_=ia.account_id))
        .order_by(institution, ia.name)))
    from_plaid = [a for a in rows if a["source"] == "plaid"]

    def twin(sf: dict) -> dict | None:
        """The Plaid account a SimpleFIN one also is: the one matched to it (Settings), else the only one at the same
        institution whose last digits are in its name ("Individual Brokerage (6702)" is E*TRADE's ••6702)."""
        matched = [p for p in from_plaid if p["account_id"] and p["account_id"] == sf["id"][3:]]
        if matched:
            return matched[0]
        same = [p for p in from_plaid if p["mask"] and re.search(r"(?<!\d)" + re.escape(p["mask"]) + r"(?!\d)", sf["name"] or "")
                and plaid._same_institution(sf["institution_name"], p["institution_name"])]
        return same[0] if len(same) == 1 else None

    also = set()
    for a in rows:
        t = twin(a) if a["source"] == "simplefin" else None
        a["duplicate_of"] = t["id"] if t else None
        if t:
            also.add(t["id"])
        a["hidden"] = 1 if a["hidden"] or a["hidden_in_accounts"] or t else 0
    for a in rows:
        a["also_simplefin"] = a["id"] in also
    return rows


def _visible_ids(conn) -> list[str]:
    return [a["id"] for a in _accounts(conn) if not a["hidden"]]


# ------------------------------------------------------------------------------------------------ holdings

def holdings(conn) -> list[dict]:
    ids = _visible_ids(conn)
    if not ids:
        return []
    s = Security
    rows = db.rows(conn.execute(
        select(Holding, s.ticker, s.name.label("sec_name"), s.type, s.subtype, s.is_cash, s.sector, s.industry, s.close_price,
               InvAccount.name.label("account_name"), InvAccount.id.label("acct_id"))
        .join(s, s.id == Holding.security_id).join(InvAccount, InvAccount.id == Holding.account_id)
        .where(Holding.account_id.in_(ids))))
    manual = {(r["account_id"], r["security_id"]): (r["cost_basis"], r["per_share"]) for r in conn.execute(select(CostOverride))}
    by_sec: dict[str, dict] = {}
    for r in rows:
        _add_lot(by_sec, r, manual)
    total = sum(h["value"] for h in by_sec.values()) or 1.0
    out = [_finish_holding(conn, h, total) for h in by_sec.values()]
    out.sort(key=lambda h: -h["value"])
    logos = merchants.holding_logos(conn, out)   # where Runway serves a holding's logo, once it has fetched one
    for h in out:
        h["logo"] = logos.get(h["group"])
    return out


def _lot_cost(r: dict, manual: dict) -> tuple[float | None, float | None]:
    """A position's cost basis, and the per-share cost you entered if you did. Your own number is a price per share
    (scales with shares held) or an older total; without one it's what the institution reports."""
    key = (r["account_id"], r["security_id"])
    if key not in manual:
        return r["cost_basis"], None
    total, per_share = manual[key]
    return (per_share * (r["quantity"] or 0) if per_share is not None else total), per_share


def _add_lot(by_sec: dict[str, dict], r: dict, manual: dict) -> None:
    """Add one account's position to the row for its fund."""
    value = r["value"] if r["value"] is not None else (r["quantity"] or 0) * (r["price"] or r["close_price"] or 0)
    is_manual = (r["account_id"], r["security_id"]) in manual
    cost, per_share = _lot_cost(r, manual)
    # The same fund in several accounts (or from different sources) is one row: group by ticker.
    group = f"t:{r['ticker'].upper()}" if prices.usable_ticker(r["ticker"]) and not r["is_cash"] else r["security_id"]
    h = by_sec.setdefault(group, {
        "security_id": r["security_id"], "group": group, "ticker": r["ticker"], "name": r["sec_name"], "type": r["type"],
        "asset_class": asset_class(r), "sector": r["sector"], "is_cash": bool(r["is_cash"]),
        "quantity": 0.0, "value": 0.0, "cost_basis": 0.0, "cost_known": True, "accounts": [], "price": r["price"],
        "lots": [], "cost_manual": False,
    })
    if not h["name"] and r["sec_name"]:
        h["name"] = r["sec_name"]
    h["lots"].append({"account_id": r["account_id"], "security_id": r["security_id"], "account_name": r["account_name"], "quantity": r["quantity"] or 0,
                      "value": round(value, 2), "cost_basis": cost, "reported_cost_basis": r["cost_basis"],
                      "per_share": per_share if per_share is not None else (cost / r["quantity"] if cost is not None and r["quantity"] else None),
                      "manual": is_manual})
    h["cost_manual"] = h["cost_manual"] or is_manual
    h["quantity"] += r["quantity"] or 0
    h["value"] += value
    if cost is None and not r["is_cash"]:
        h["cost_known"] = False
    else:
        h["cost_basis"] += cost if cost is not None else value
    if r["account_name"] not in h["accounts"]:
        h["accounts"].append(r["account_name"])


def _finish_holding(conn, h: dict, total: float) -> dict:
    """A fund's row for the page: its price, share of the portfolio, gain and day's change, floats rounded."""
    h["price"] = h["value"] / h["quantity"] if h["quantity"] else h["price"]
    h["allocation"] = h["value"] / total
    if h["cost_known"] and not h["is_cash"] and h["cost_basis"]:
        h["gain"] = h["value"] - h["cost_basis"]
        h["gain_pct"] = h["gain"] / h["cost_basis"]
    else:
        h["gain"] = h["gain_pct"] = None
    h["day_change"] = _day_change(conn, h)
    prev = h["value"] - h["day_change"] if h["day_change"] is not None else None
    h["day_change_pct"] = h["day_change"] / prev if prev else None
    return {k: (round(v, 6) if isinstance(v, float) else v) for k, v in h.items()}


def _day_change(conn, h: dict) -> float | None:
    if h["is_cash"] or not prices.usable_ticker(h["ticker"]):
        return None
    rows = conn.execute(select(Price.close).where(Price.ticker == h["ticker"]).order_by(Price.date.desc()).limit(2)).fetchall()
    if len(rows) < 2 or not rows[1]["close"]:
        return None
    return round(h["quantity"] * (rows[0]["close"] - rows[1]["close"]), 2)


def allocation(conn, hold: list[dict]) -> dict:
    total = sum(h["value"] for h in hold) or 1.0

    def group(key) -> list[dict]:
        g: dict[str, float] = defaultdict(float)
        for h in hold:
            g[key(h)] += h["value"]
        return sorted(({"name": k, "value": round(v, 2), "share": v / total} for k, v in g.items() if abs(v) > 0.005),
                      key=lambda x: -x["value"])

    by_account: dict[str, float] = defaultdict(float)
    ids = _visible_ids(conn)
    for a in _accounts(conn):
        if a["id"] not in ids:
            continue
        v = conn.execute(select(func.sum(Holding.value)).where(Holding.account_id == a["id"])).fetchone()[0]
        by_account[f"{a['institution_name'] or ''} {a['name'] or ''}".strip()] += v if v is not None else (a["balance"] or 0)
    acct_total = sum(by_account.values()) or 1.0
    top = hold[:10]
    rest = sum(h["value"] for h in hold[10:])
    return {
        "asset_class": group(lambda h: h["asset_class"]),
        "sector": group(lambda h: "Cash" if h["is_cash"] else (h["sector"] or ("Funds (diversified)" if h["asset_class"] in ("ETFs", "Mutual funds") else "Unclassified"))),
        "account": sorted(({"name": k, "value": round(v, 2), "share": v / acct_total} for k, v in by_account.items()), key=lambda x: -x["value"]),
        "holding": [{"name": h["ticker"] or h["name"], "value": round(h["value"], 2), "share": h["allocation"]} for h in top]
                   + ([{"name": f"Everything else ({len(hold) - 10})", "value": round(rest, 2), "share": rest / total}] if rest else []),
    }


# ------------------------------------------------------------------------------------------------ activity & income

_SF_KINDS = [  # SimpleFIN activity is just text; sort it into the same buckets Plaid uses
    (re.compile(r"reinvest", re.I), "buy", "dividend reinvestment"),
    (re.compile(r"dividend|div\b|capital gain", re.I), "cash", "dividend"),
    (re.compile(r"interest", re.I), "cash", "interest"),
    (re.compile(r"\bfee|advisory|management", re.I), "fee", "fee"),
    (re.compile(r"\b(bought|buy|purchase)", re.I), "buy", "buy"),
    (re.compile(r"\b(sold|sell|sale)\b", re.I), "sell", "sell"),
    (re.compile(r"contribution|payroll|employer|match", re.I), "cash", "contribution"),
    (re.compile(r"deposit|transfer|ach|withdraw|distribution|rollover", re.I), "cash", "transfer"),
]


def _sf_activity(conn, since: str | None = None, limit: int = 500) -> list[dict]:
    accts = [a for a in _accounts(conn) if not a["hidden"] and a.get("source") == "simplefin"]
    if not accts:
        return []
    names = {a["id"][3:]: a["name"] for a in accts}
    t_ = Transaction
    out = []
    for t in db.rows(conn.execute(
            select(t_.id, t_.account_id, t_.posted, t_.amount, t_.description)
            .where(t_.account_id.in_(list(names)), t_.pending == 0, t_.posted >= (since or "0000"))
            .order_by(t_.posted.desc()).limit(limit))):
        ttype, sub = "cash", "other"
        for rx, ty, su in _SF_KINDS:
            if rx.search(t["description"] or ""):
                ttype, sub = ty, su
                break
        out.append({"id": "sf-tx:" + t["id"], "account_id": "sf:" + t["account_id"], "security_id": None, "date": t["posted"],
                    "name": t["description"], "type": ttype, "subtype": sub, "quantity": 0, "price": None, "fees": 0,
                    "amount": -(t["amount"] or 0.0), "ticker": None, "sec_name": None, "account_name": names[t["account_id"]]})
    return out


def activity(conn, limit: int = 500) -> list[dict]:
    ids = _visible_ids(conn)
    if not ids:
        return []
    t = InvTransaction
    rows = db.rows(conn.execute(
        select(t, Security.ticker, Security.name.label("sec_name"), InvAccount.name.label("account_name"))
        .outerjoin(Security, Security.id == t.security_id).join(InvAccount, InvAccount.id == t.account_id)
        .where(t.account_id.in_(ids)).order_by(t.date.desc(), t.id).limit(limit)))
    rows += _sf_activity(conn, limit=limit)
    rows.sort(key=lambda t: (t["date"], t["id"]), reverse=True)
    return rows[:limit]


def _is_income(t: dict) -> bool:
    return (t.get("subtype") or "").lower() in INCOME_SUBTYPES and (t.get("amount") or 0) < 0


def income(conn, today: date | None = None, months: int = 24) -> dict:
    today = today or date.today()
    keys = _month_keys(today, months)
    inc = {k: 0.0 for k in keys}
    fees = {k: 0.0 for k in keys}
    _add_plaid_income(conn, keys[0] + "-01", inc, fees)
    for t in _sf_activity(conn, since=keys[0] + "-01", limit=100000):
        k = t["date"][:7]
        if k in inc and t["subtype"] in ("dividend", "interest") and t["amount"] < 0:
            inc[k] += -t["amount"]
        elif k in fees and t["type"] == "fee" and t["amount"] > 0:
            fees[k] += t["amount"]
    last12 = keys[-12:]
    return {"months": keys, "income": [round(inc[k], 2) for k in keys], "fees": [round(fees[k], 2) for k in keys],
            "income_12m": round(sum(inc[k] for k in last12), 2), "fees_12m": round(sum(fees[k] for k in last12), 2)}


def _month_keys(today: date, months: int) -> list[str]:
    """The last `months` months ("2026-09"), oldest first, ending with this one."""
    keys = []
    y, m = today.year, today.month
    for _ in range(months):
        keys.append(f"{y:04d}-{m:02d}")
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    keys.reverse()
    return keys


def _add_plaid_income(conn, since: str, inc: dict[str, float], fees: dict[str, float]) -> None:
    """Add dividends, interest and fees from Plaid activity to the monthly totals (months not listed are skipped)."""
    ids = _visible_ids(conn)
    if not ids:
        return
    for t in db.rows(conn.execute(select(InvTransaction).where(InvTransaction.account_id.in_(ids), InvTransaction.date >= since))):
        k = t["date"][:7]
        if k not in inc:
            continue
        if _is_income(t):
            inc[k] += -t["amount"]
        if (t["type"] or "") == "fee" and (t["amount"] or 0) > 0:
            fees[k] += t["amount"]
        elif (t["fees"] or 0) > 0:
            fees[k] += t["fees"]


# ------------------------------------------------------------------------------------------------ history & returns

class _PriceBook:
    """Price of a security on a day: real historical close (forward-filled), else a traded price, else today's."""

    def __init__(self, conn, start: date):
        self.conn, self.start = conn, start
        self.cache: dict[str, tuple[list[str], list[float]]] = {}
        self.missing: set[str] = set()

    def series(self, ticker: str | None) -> tuple[list[str], list[float]]:
        if not prices.usable_ticker(ticker):
            return [], []
        if ticker not in self.cache:
            real, _adj, _ = prices.history(self.conn, ticker, self.start)
            ds = sorted(real)
            self.cache[ticker] = (ds, [real[d] for d in ds])
        return self.cache[ticker]

    def price(self, sec: dict, d: str, fallback: float | None) -> float | None:
        ds, vals = self.series(sec.get("ticker"))
        if ds:
            i = bisect.bisect_right(ds, d) - 1
            if i >= 0:
                return vals[i]
            return vals[0]
        self.missing.add(sec.get("ticker") or sec.get("name") or sec["id"])
        return fallback


def history(conn, today: date | None = None, days: int = HISTORY_DAYS) -> dict:
    today = today or date.today()
    accts = [a for a in _accounts(conn) if not a["hidden"]]
    ids = [a["id"] for a in accts if a.get("source") != "simplefin"]
    sf_ids = [a["id"] for a in accts if a.get("source") == "simplefin"]
    if not accts:
        return {"dates": [], "value": [], "flows": [], "invested": [], "twr": [], "missing_prices": [], "estimated_before": None}
    start = _history_start(conn, ids, sf_ids, today, days)
    dates = [(start + timedelta(days=i)).isoformat() for i in range((today - start).days + 1)]
    n = len(dates)
    secs = {r["id"]: r for r in db.rows(conn.execute(select(Security)))}
    book = _PriceBook(conn, start)
    value = [0.0] * n
    flows = [0.0] * n

    for aid in ids:
        acct_values = _replay_account(conn, aid, dates, secs, book, flows)
        for i in range(n):
            value[i] += acct_values[i]

    estimated_before = _add_snapshot_accounts(conn, sf_ids, dates, secs, book, value, flows)
    twr = _time_weighted(value, flows)
    invested = _invested(value, flows)
    return {"dates": dates, "value": [round(v, 2) for v in value], "flows": [round(f, 2) for f in flows],
            "invested": [round(v, 2) for v in invested], "twr": [round(r, 6) for r in twr],
            "missing_prices": sorted(book.missing), "estimated_before": estimated_before}


def _history_start(conn, ids: list[str], sf_ids: list[str], today: date, days: int) -> date:
    """The first day of the history: `days` back, or the day before the first activity if that's later. SimpleFIN
    accounts have no activity to start from, so with any of them the full span is shown."""
    start = today - timedelta(days=days)
    if not ids or sf_ids:
        return start
    first_tx = conn.execute(select(func.min(InvTransaction.date)).where(InvTransaction.account_id.in_(ids))).fetchone()[0]
    if first_tx:
        start = max(start, date.fromisoformat(first_tx) - timedelta(days=1))
    return start


def _replay_account(conn, aid: str, dates: list[str], secs: dict, book: _PriceBook, flows: list[float]) -> list[float]:
    """One account's end-of-day value on each date, rebuilt from what it holds today by walking its activity backwards.
    Money it gained or lost from outside is added to `flows` along the way."""
    n = len(dates)
    hold = db.rows(conn.execute(select(Holding).where(Holding.account_id == aid)))
    t = InvTransaction
    txs = db.rows(conn.execute(select(t).where(t.account_id == aid, t.date > dates[0], t.type != "cancel").order_by(t.date)))
    cash_now = sum((h["value"] if h["value"] is not None else h["quantity"] or 0) for h in hold
                   if secs.get(h["security_id"], {}).get("is_cash"))
    if not hold:
        bal = conn.execute(select(InvAccount.balance).where(InvAccount.id == aid)).fetchone()[0] or 0.0
        cash_now = bal  # balance-only account: treat as cash-like
    qty_now = {h["security_id"]: h["quantity"] or 0 for h in hold if not secs.get(h["security_id"], {}).get("is_cash")}
    cur_price = {h["security_id"]: h["price"] for h in hold}
    qty_delta, cash_delta, trades = _activity_changes(txs, dates, secs, book, cur_price, flows)

    # Walk backwards: end-of-day positions.
    all_secs = set(qty_now) | set(qty_delta)
    qty = {s: qty_now.get(s, 0.0) for s in all_secs}
    cash = cash_now
    acct_values = [0.0] * n
    for i in range(n - 1, -1, -1):
        d = dates[i]
        v = cash
        for s, qv in qty.items():
            if abs(qv) < 1e-9:
                continue
            v += qv * _held_price(book, {"id": s, **secs.get(s, {})}, d, cur_price.get(s), trades.get(s))
        acct_values[i] = v
        # step back to the end of the previous day
        for s in all_secs:
            qty[s] -= qty_delta[s][i] if s in qty_delta else 0.0
        cash -= cash_delta[i]
    # Today's value is what the institution reports right now.
    acct_values[-1] = sum((h["value"] if h["value"] is not None else 0) for h in hold) if hold else cash_now
    return acct_values


def _activity_changes(txs: list[dict], dates: list[str], secs: dict, book: _PriceBook, cur_price: dict,
                      flows: list[float]) -> tuple[dict[str, list[float]], list[float], dict[str, list[tuple[str, float]]]]:
    """What each day's activity changed: shares per security, cash, and the prices trades were made at. Money moved
    in or out from outside is added to `flows`."""
    n = len(dates)
    qty_delta: dict[str, list[float]] = defaultdict(lambda: [0.0] * n)
    cash_delta = [0.0] * n
    trades: dict[str, list[tuple[str, float]]] = defaultdict(list)
    idx = {d: i for i, d in enumerate(dates)}
    for t in txs:
        i = idx.get(t["date"])
        if i is None:
            continue
        sec = secs.get(t["security_id"] or "", {})
        is_cash_sec = bool(sec.get("is_cash"))
        ttype, sub = (t["type"] or "").lower(), (t["subtype"] or "").lower()
        moves_shares = bool(t["security_id"] and not is_cash_sec and t["quantity"])
        # Only trades and transfers change how many shares you hold. (Some institutions attach a share count to
        # plain cash deposits or dividends; applying it would invent or remove shares.)
        if moves_shares and ttype in ("buy", "sell", "transfer"):
            qty_delta[t["security_id"]][i] += t["quantity"]
            if t["price"]:
                trades[t["security_id"]].append((t["date"], t["price"]))
        # Buying or selling a money-market "cash" fund is just moving cash around; everything else changes cash.
        if not (is_cash_sec and ttype in ("buy", "sell")):
            cash_delta[i] += -(t["amount"] or 0)  # amount > 0 means cash left the account
        flow, in_kind = _outside_money(t, sec, ttype, sub, moves_shares, book, cur_price)
        flows[i] += flow
        if in_kind:
            cash_delta[i] += (t["amount"] or 0)  # in-kind transfers don't move cash
    return qty_delta, cash_delta, trades


def _outside_money(t: dict, sec: dict, ttype: str, sub: str, moves_shares: bool, book: _PriceBook,
                   cur_price: dict) -> tuple[float, bool]:
    """Money a transaction brought into the account from outside (negative: taken out), and whether it was shares
    moved in kind rather than cash."""
    if ttype == "cash" and sub in FLOW_SUBTYPES:
        return -(t["amount"] or 0), False
    if ttype != "transfer" or sub in CORPORATE_ACTIONS:
        return 0.0, False   # splits, spin-offs and the like change share counts, not how much you've put in
    if moves_shares:
        px = t["price"] or book.price({"id": t["security_id"], **sec}, t["date"], cur_price.get(t["security_id"]))
        return (t["quantity"] or 0) * (px or 0), True
    return -(t["amount"] or 0), False


def _held_price(book: _PriceBook, sec: dict, d: str, current: float | None, trades: list[tuple[str, float]] | None) -> float:
    """A holding's price on a day. Without price history, the last trade on or before that day stands in (the first
    trade for days before any), and failing that the price the institution reports today."""
    fallback = current
    if trades:
        before = [p for td, p in trades if td <= d]
        fallback = before[-1] if before else trades[0][1]
    return book.price(sec, d, fallback) or 0.0


def _time_weighted(value: list[float], flows: list[float]) -> list[float]:
    """Time-weighted return: each day's growth with that day's deposits/withdrawals taken out."""
    twr = [0.0] * len(value)
    growth = 1.0
    for i in range(1, len(value)):
        # Skip days the portfolio was (nearly) empty: a first deposit into a $0 account isn't a return.
        if value[i - 1] > 1.0 and value[i] - flows[i] > 0:
            growth *= (value[i] - flows[i]) / value[i - 1]
        twr[i] = growth - 1.0
    return twr


def _invested(value: list[float], flows: list[float]) -> list[float]:
    """Money put in to date: the first day's value plus every deposit and withdrawal since."""
    invested = [0.0] * len(value)
    running = value[0]
    for i in range(len(value)):
        if i:
            running += flows[i]
        invested[i] = running
    return invested


def _add_snapshot_accounts(conn, sf_ids: list[str], dates: list[str], secs: dict, book: _PriceBook,
                           value: list[float], flows: list[float]) -> str | None:
    """Add the SimpleFIN accounts' daily values and flows to the totals. Returns the latest first snapshot after the
    first day: before it, those accounts' history is an estimate."""
    estimated_before = None
    for aid in sf_ids:
        vals, fl, first_snap = _snapshot_series(conn, aid, dates, secs, book)
        for i in range(len(dates)):
            value[i] += vals[i]
            flows[i] += fl[i]
        if first_snap and first_snap > dates[0]:
            estimated_before = max(estimated_before or first_snap, first_snap)
    return estimated_before


def _snapshot_series(conn, aid: str, dates: list[str], secs: dict, book: _PriceBook) -> tuple[list[float], list[float], str | None]:
    """Daily value and external flows for an account we only have position snapshots for."""
    rows = db.rows(conn.execute(select(HoldingSnapshot).where(HoldingSnapshot.account_id == aid).order_by(HoldingSnapshot.date)))
    if not rows:  # nothing saved yet: treat today's holdings as the only snapshot
        rows = [{"date": dates[-1], "security_id": h["security_id"], "quantity": h["quantity"], "value": h["value"]}
                for h in db.rows(conn.execute(select(Holding).where(Holding.account_id == aid)))]
    snaps: dict[str, dict[str, tuple[float, float]]] = defaultdict(dict)
    for r in rows:
        snaps[r["date"]][r["security_id"]] = (r["quantity"] or 0.0, r["value"] or 0.0)
    # Snapshots taken before you entered a balance-only account's holdings are just its balance. Once there are real
    # positions, those carry the history (priced day by day) instead of a flat balance.
    balance_only = {"sf:balance", "sf:unexplained"}
    if any(set(pos) - balance_only for pos in snaps.values()):
        for d in [d for d, pos in snaps.items() if not set(pos) - balance_only]:
            del snaps[d]
    snap_dates = sorted(snaps)
    n = len(dates)
    values, flows = [0.0] * n, [0.0] * n
    if not snap_dates:
        return values, flows, None

    def worth(pos: dict[str, tuple[float, float]], d: str) -> float:
        total = 0.0
        for sid, (qty, val) in pos.items():
            sec = {"id": sid, **secs.get(sid, {})}
            if sec.get("is_cash") or not qty or sid == "sf:balance":
                total += val
                continue
            px = book.price(sec, d, val / qty)   # no price history: hold the value SimpleFIN reported
            total += qty * (px or 0.0)
        return total

    prev_key = None
    for i, d in enumerate(dates):
        k = bisect.bisect_right(snap_dates, d) - 1
        key = snap_dates[max(k, 0)]
        pos = snaps[key]
        values[i] = worth(pos, d)
        if prev_key is not None and key != prev_key:
            # Positions changed since the last snapshot: the part not explained by prices is money in or out.
            flows[i] = values[i] - worth(snaps[prev_key], d)
        prev_key = key
    last = snap_dates[-1]
    if last == dates[-1]:   # today: use what the institution reports rather than yesterday's close
        values[-1] = sum(v for _q, v in snaps[last].values())
    return values, flows, snap_dates[0]


def benchmark(conn, dates: list[str], ticker: str = prices.BENCHMARK) -> list[float | None]:
    """Total return of the benchmark since the first date (dividends reinvested), forward-filled."""
    if not dates:
        return []
    _real, adj, _ = prices.history(conn, ticker, date.fromisoformat(dates[0]))
    ds = sorted(adj)
    if not ds:
        return [None] * len(dates)
    out, base = [], None
    for d in dates:
        i = bisect.bisect_right(ds, d) - 1
        v = adj[ds[i]] if i >= 0 else None
        if v is not None and base is None:
            base = v
        out.append(round(v / base - 1.0, 6) if (v is not None and base) else None)
    return out


def period_start(period: str, today: date) -> date:
    return {
        "1M": today - timedelta(days=30), "3M": today - timedelta(days=91), "YTD": date(today.year, 1, 1) - timedelta(days=1),
        "1Y": today - timedelta(days=365), "2Y": today - timedelta(days=730),
    }.get(period, date(1900, 1, 1))


def performance(hist: dict, bench: list, period: str, today: date) -> dict:
    dates = hist["dates"]
    if not dates:
        return {}
    s = bisect.bisect_left(dates, period_start(period, today).isoformat())
    s = min(max(0, s), len(dates) - 1)
    g0 = 1 + hist["twr"][s]
    ret = (1 + hist["twr"][-1]) / g0 - 1 if g0 else 0.0
    b0, b1 = bench[s] if bench else None, bench[-1] if bench else None
    bret = ((1 + b1) / (1 + b0) - 1) if (b0 is not None and b1 is not None) else None
    net_flow = sum(hist["flows"][s + 1:])
    return {
        "period": period, "start": dates[s],
        "start_value": hist["value"][s], "end_value": hist["value"][-1],
        "net_deposits": round(net_flow, 2),
        "gain": round(hist["value"][-1] - hist["value"][s] - net_flow, 2),
        "return": round(ret, 6), "benchmark_return": round(bret, 6) if bret is not None else None,
    }


# ------------------------------------------------------------------------------------------------ X-ray & FIRE

def monthly_spending(conn, today: date) -> float:
    """Average monthly spending from Runway's own transactions over the last 6 full months. Counted as Reports counts
    it: every category that isn't a transfer or income, and money out with no category. A month with more money back
    than out (a big refund) is a month of no spending, not one left out."""
    t, counted = _spending(today)
    # Constants in the SQL, not parameters: Postgres matches the GROUP BY expression to the selected one.
    month = func.substr(t.c.posted, literal_column("1"), literal_column("7")).label("m")
    rows = conn.execute(counted.with_only_columns(month, func.sum(t.c.amount).label("s")).group_by(month)).fetchall()
    months = [max(0.0, -(r["s"] or 0)) for r in rows]
    return round(statistics.mean(months), 2) if months else 0.0


def _spending(today: date):
    """The transactions monthly_spending counts (the last 6 full months), as (the parts subquery, a select of them)."""
    start = date(today.year, today.month, 1) - relativedelta(months=6)
    end = date(today.year, today.month, 1)
    t = splits.parts()
    spending = or_(and_(Category.is_transfer == 0, Category.is_income == 0), and_(Category.name.is_(None), t.c.amount < 0))
    return t, (select(t.c.amount).select_from(t)
               .join(Account, Account.id == t.c.account_id).outerjoin(Category, Category.name == t.c.category)
               .where(spending, Account.hidden == 0, Account.kind.in_(["checking", "savings", "credit"]),
                      t.c.posted >= start.isoformat(), t.c.posted < end.isoformat()))


def transfer_outflows(conn, today: date) -> list[dict]:
    """Like spent_outflows, but the money out monthly_spending leaves out as a transfer (over the same months and
    accounts): a loan's payment found here was paid but not counted as spending."""
    start = date(today.year, today.month, 1) - relativedelta(months=6)
    end = date(today.year, today.month, 1)
    t = splits.parts()
    rows = conn.execute(
        select(t.c.posted, t.c.amount, t.c.payee, t.c.description).select_from(t)
        .join(Account, Account.id == t.c.account_id).join(Category, Category.name == t.c.category)
        .where(Category.is_transfer == 1, t.c.amount < 0, Account.hidden == 0, Account.kind.in_(["checking", "savings", "credit"]),
               t.c.posted >= start.isoformat(), t.c.posted < end.isoformat()))
    return [{"month": r["posted"][:7], "amount": -r["amount"], "text": f"{r['payee'] or ''} {r['description'] or ''}".lower()}
            for r in rows]


def spent_outflows(conn, today: date) -> list[dict]:
    """Each money out that monthly_spending counts: {month (YYYY-MM), amount (positive), text (its payee and
    description, lowercased)}. To tell whether a loan's payment is in the spending figure, or was left out as a
    transfer."""
    t, counted = _spending(today)
    rows = conn.execute(counted.with_only_columns(t.c.posted, t.c.amount, t.c.payee, t.c.description).where(t.c.amount < 0))
    return [{"month": r["posted"][:7], "amount": -r["amount"], "text": f"{r['payee'] or ''} {r['description'] or ''}".lower()}
            for r in rows]


def xray(conn, hold: list[dict], alloc: dict, inc: dict, today: date) -> list[dict]:
    total = sum(h["value"] for h in hold)
    rules: list[dict] = []
    if total <= 0:
        return rules
    noncash = [h for h in hold if not h["is_cash"] and h["asset_class"] != "Not reported"]
    if noncash:
        rules.append(_largest_holding_rule(noncash[0], total))
    cash_share = sum(h["value"] for h in hold if h["is_cash"]) / total
    rules.append({"name": "Uninvested cash", "ok": cash_share <= 0.15,
                  "detail": f"{cash_share:.0%} of the portfolio is cash" + ("" if cash_share <= 0.15 else "; that part isn't growing with the market.")})
    if alloc["account"]:
        biggest = alloc["account"][0]
        rules.append({"name": "Account concentration", "ok": biggest["share"] <= 0.8 or len(alloc["account"]) == 1,
                      "detail": f"{biggest['share']:.0%} is in {biggest['name']}"})
    fee_ratio = inc["fees_12m"] / total
    rules.append({"name": "Fees paid", "ok": fee_ratio <= 0.005,
                  "detail": f"${inc['fees_12m']:,.0f} in the last 12 months ({fee_ratio:.2%} of the portfolio)"
                            + ("" if fee_ratio <= 0.005 else "; worth checking what they're for.")})
    emergency = _emergency_fund_rule(conn, today)
    if emergency:
        rules.append(emergency)
    missing_basis = [h for h in noncash if h["gain"] is None]
    if missing_basis:
        rules.append({"name": "Cost basis", "ok": False, "info": True,
                      "detail": f"{len(missing_basis)} holding{'s' if len(missing_basis) != 1 else ''} have no cost basis from the institution, so their gain isn't counted."})
    return rules


def _largest_holding_rule(top: dict, total: float) -> dict:
    """More than a quarter in one stock is a lot of single-company risk; a fund is diversified inside."""
    share = top["value"] / total
    fund = top["asset_class"] in ("ETFs", "Mutual funds")
    return {"name": "Largest single holding", "ok": share <= 0.25 or fund,
            "detail": f"{top['ticker'] or top['name']} is {share:.0%} of the portfolio"
                      + (" (a fund, so it's diversified inside)" if fund and share > 0.25 else "")
                      + ("" if share <= 0.25 or fund else ". Above 25% in one stock is a lot of single-company risk.")}


def _emergency_fund_rule(conn, today: date) -> dict | None:
    """How many months of spending the primary account covers; None without a primary account or any spending."""
    spend = monthly_spending(conn, today)
    primary = db.get_setting(conn, sk.PRIMARY_ACCOUNT)
    row = conn.execute(select(Account.balance).where(Account.id == primary)).fetchone() if primary else None
    if not (spend > 0 and row):
        return None
    months = (row["balance"] or 0) / spend
    return {"name": "Emergency fund", "ok": months >= 3,
            "detail": f"Your primary account covers {months:.1f} months of your average spending (${spend:,.0f}/month); 3–6 months is the usual guide."}


# The old financial-independence card kept the figures you changed as settings; a new retirement plan starts from them.
FIRE_FIELDS = ("annual_spending", "yearly_savings", "expected_return")


def fire_saved(conn) -> dict:
    """The figures you'd changed on the old financial-independence card."""
    out = {}
    for field in FIRE_FIELDS:
        value = db.get_setting(conn, sk.fire(field))
        if value not in (None, ""):
            with contextlib.suppress(ValueError):
                out[field] = float(value)
    return out


def plan_figures(conn, hist: dict, today: date) -> dict:
    """What the retirement plan starts from: a year's spending and saving from your own accounts, and a return.
    Yearly savings is what went into your investments in the last 12 months, rollovers and lump sums included
    (`savings_measured`, false when it's a figure you'd typed on the old card); `savings_since` is the day the
    investment history starts when that's less than a year ago, else None."""
    spend = monthly_spending(conn, today)
    flows = hist.get("flows") or []
    dates = hist.get("dates") or []
    cutoff = (today - timedelta(days=365)).isoformat()
    yearly_savings = sum(f for d, f in zip(dates, flows, strict=True) if d > cutoff)
    computed = {"annual_spending": round(spend * 12, 2), "yearly_savings": round(max(0.0, yearly_savings), 2),
                "expected_return": 0.05}
    saved = fire_saved(conn)
    measured = "yearly_savings" not in saved
    since = dates[0] if measured and dates and dates[0] > cutoff else None
    return {**computed, **saved, "savings_measured": measured, "savings_since": since}


# ------------------------------------------------------------------------------------------------ everything for the page

def overview(conn, period: str = "1Y", today: date | None = None) -> dict:
    today = today or date.today()
    hold = holdings(conn)
    alloc = allocation(conn, hold)
    inc = income(conn, today)
    hist = history(conn, today)
    bench = benchmark(conn, hist["dates"])
    perf = performance(hist, bench, period, today)
    periods = {p: performance(hist, bench, p, today) for p in ("1M", "3M", "YTD", "1Y", "2Y")}
    total = sum(h["value"] for h in hold)
    known = [h for h in hold if h["gain"] is not None]
    day_changes = [h["day_change"] for h in hold if h["day_change"] is not None]
    prev_close_value = sum(h["value"] - h["day_change"] for h in hold if h["day_change"] is not None)
    cost_missing = [h for h in hold if h["gain"] is None and not h["is_cash"] and h["asset_class"] != "Not reported"]
    return {
        "today": today.isoformat(),
        "total": round(total, 2),
        "unrealized_gain": round(sum(h["gain"] for h in known), 2) if known else None,
        "cost_basis": round(sum(h["cost_basis"] for h in known), 2) if known else None,
        "day_change": round(sum(day_changes), 2) if day_changes else None,
        "day_change_pct": round(sum(day_changes) / prev_close_value, 6) if day_changes and prev_close_value > 0 else None,
        "cost_missing": len(cost_missing), "cost_missing_value": round(sum(h["value"] for h in cost_missing), 2),
        "holdings": hold, "allocation": alloc, "income": inc,
        "history": {**hist, "benchmark": bench}, "performance": perf, "periods": periods,
        "xray": xray(conn, hold, alloc, inc, today),
        "plan": planner.overview(conn, hist["value"][-1] if hist.get("value") else 0.0, plan_figures(conn, hist, today), today),
        "accounts": [a for a in _accounts(conn) if not a["duplicate_of"]], "activity": activity(conn, 300),
    }
