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
import re
import statistics
from collections import defaultdict
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta

from . import db, prices, splits

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
    """Investment accounts. One is hidden if you hid it here (inv_accounts.hidden) or hid the account it is in
    Settings -> Accounts: SimpleFIN's 'sf:<account id>', or the account a Plaid one was matched to.

    An account connected through both SimpleFIN and Plaid is one account: the Plaid one, which has the fuller data
    (holdings, cost basis, activity), stands for it, and the SimpleFIN one is marked duplicate_of it and never counted
    (the page leaves it out)."""
    from . import plaid
    rows = db.rows(conn.execute(
        "SELECT a.*, COALESCE(i.institution_name, a.institution) AS institution_name, "
        "(SELECT COUNT(*) FROM manual_positions m WHERE m.account_id=a.id) AS tracked, "
        "(SELECT drift FROM manual_state ms WHERE ms.account_id=a.id) AS drift, "
        "COALESCE(ra.hidden, 0) AS hidden_in_accounts FROM inv_accounts a "
        "LEFT JOIN plaid_items i ON i.item_id=a.item_id "
        "LEFT JOIN accounts ra ON ra.id = (CASE WHEN a.source='simplefin' THEN substr(a.id, 4) ELSE a.account_id END) "
        "ORDER BY institution_name, a.name"))
    from_plaid = [a for a in rows if a["source"] == "plaid"]

    def twin(sf: dict) -> dict | None:
        """The Plaid account a SimpleFIN one also is: the one matched to it (Settings), else the only one at the same
        institution whose last digits are in its name ("Individual Brokerage (8933)" is E*TRADE's ••8933)."""
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
    q = ",".join("?" * len(ids))
    rows = db.rows(conn.execute(
        f"SELECT h.*, s.ticker, s.name AS sec_name, s.type, s.subtype, s.is_cash, s.sector, s.industry, s.close_price, "
        f"a.name AS account_name, a.id AS acct_id FROM holdings h JOIN securities s ON s.id=h.security_id "
        f"JOIN inv_accounts a ON a.id=h.account_id WHERE h.account_id IN ({q})", ids))
    manual = {(r["account_id"], r["security_id"]): (r["cost_basis"], r["per_share"]) for r in conn.execute("SELECT * FROM cost_overrides")}
    by_sec: dict[str, dict] = {}
    for r in rows:
        value = r["value"] if r["value"] is not None else (r["quantity"] or 0) * (r["price"] or r["close_price"] or 0)
        key = (r["account_id"], r["security_id"])
        per_share = manual[key][1] if key in manual else None
        if key in manual:   # your own number: a price per share (scales with shares held) or an older total
            cost = per_share * (r["quantity"] or 0) if per_share is not None else manual[key][0]
        else:
            cost = r["cost_basis"]
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
                          "manual": key in manual})
        h["cost_manual"] = h["cost_manual"] or key in manual
        h["quantity"] += r["quantity"] or 0
        h["value"] += value
        if cost is None and not r["is_cash"]:
            h["cost_known"] = False
        else:
            h["cost_basis"] += cost if cost is not None else value
        if r["account_name"] not in h["accounts"]:
            h["accounts"].append(r["account_name"])
    total = sum(h["value"] for h in by_sec.values()) or 1.0
    out = []
    for h in by_sec.values():
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
        out.append({k: (round(v, 6) if isinstance(v, float) else v) for k, v in h.items()})
    out.sort(key=lambda h: -h["value"])
    return out


def _day_change(conn, h: dict) -> float | None:
    if h["is_cash"] or not prices.usable_ticker(h["ticker"]):
        return None
    rows = conn.execute("SELECT close FROM prices WHERE ticker=? ORDER BY date DESC LIMIT 2", (h["ticker"],)).fetchall()
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
        v = conn.execute("SELECT SUM(value) FROM holdings WHERE account_id=?", (a["id"],)).fetchone()[0]
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
    q = ",".join("?" * len(names))
    out = []
    for t in db.rows(conn.execute(
            f"SELECT id, account_id, posted, amount, description FROM transactions WHERE account_id IN ({q}) AND pending=0 "
            f"AND posted>=? ORDER BY posted DESC LIMIT ?", (*names, since or "0000", limit))):
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
    q = ",".join("?" * len(ids))
    rows = db.rows(conn.execute(
        f"SELECT t.*, s.ticker, s.name AS sec_name, a.name AS account_name FROM inv_transactions t "
        f"LEFT JOIN securities s ON s.id=t.security_id JOIN inv_accounts a ON a.id=t.account_id "
        f"WHERE t.account_id IN ({q}) ORDER BY t.date DESC, t.id LIMIT ?", (*ids, limit)))
    rows += _sf_activity(conn, limit=limit)
    rows.sort(key=lambda t: (t["date"], t["id"]), reverse=True)
    return rows[:limit]


def _is_income(t: dict) -> bool:
    return (t.get("subtype") or "").lower() in INCOME_SUBTYPES and (t.get("amount") or 0) < 0


def income(conn, today: date | None = None, months: int = 24) -> dict:
    today = today or date.today()
    ids = _visible_ids(conn)
    keys = []
    y, m = today.year, today.month
    for _ in range(months):
        keys.append(f"{y:04d}-{m:02d}")
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    keys.reverse()
    inc = {k: 0.0 for k in keys}
    fees = {k: 0.0 for k in keys}
    if ids:
        q = ",".join("?" * len(ids))
        for t in db.rows(conn.execute(f"SELECT * FROM inv_transactions WHERE account_id IN ({q}) AND date>=?",
                                      (*ids, keys[0] + "-01"))):
            k = t["date"][:7]
            if k not in inc:
                continue
            if _is_income(t):
                inc[k] += -t["amount"]
            if (t["type"] or "") == "fee" and (t["amount"] or 0) > 0:
                fees[k] += t["amount"]
            elif (t["fees"] or 0) > 0:
                fees[k] += t["fees"]
    for t in _sf_activity(conn, since=keys[0] + "-01", limit=100000):
        k = t["date"][:7]
        if k in inc and t["subtype"] in ("dividend", "interest") and t["amount"] < 0:
            inc[k] += -t["amount"]
        elif k in fees and t["type"] == "fee" and t["amount"] > 0:
            fees[k] += t["amount"]
    last12 = keys[-12:]
    return {"months": keys, "income": [round(inc[k], 2) for k in keys], "fees": [round(fees[k], 2) for k in keys],
            "income_12m": round(sum(inc[k] for k in last12), 2), "fees_12m": round(sum(fees[k] for k in last12), 2)}


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
    q = ",".join("?" * len(ids))
    first_tx = conn.execute(f"SELECT MIN(date) FROM inv_transactions WHERE account_id IN ({q})", ids).fetchone()[0] if ids else None
    start = today - timedelta(days=days)
    if first_tx and not sf_ids:
        start = max(start, date.fromisoformat(first_tx) - timedelta(days=1))
    dates = [(start + timedelta(days=i)).isoformat() for i in range((today - start).days + 1)]
    n = len(dates)
    secs = {r["id"]: r for r in db.rows(conn.execute("SELECT * FROM securities"))}
    book = _PriceBook(conn, start)
    value = [0.0] * n
    flows = [0.0] * n

    for aid in ids:
        hold = db.rows(conn.execute("SELECT * FROM holdings WHERE account_id=?", (aid,)))
        txs = db.rows(conn.execute("SELECT * FROM inv_transactions WHERE account_id=? AND date>? AND type<>'cancel' ORDER BY date",
                                   (aid, dates[0])))
        cash_now = sum((h["value"] if h["value"] is not None else h["quantity"] or 0) for h in hold
                       if secs.get(h["security_id"], {}).get("is_cash"))
        if not hold:
            bal = conn.execute("SELECT balance FROM inv_accounts WHERE id=?", (aid,)).fetchone()[0] or 0.0
            cash_now = bal  # balance-only account: treat as cash-like
        qty_now = {h["security_id"]: h["quantity"] or 0 for h in hold if not secs.get(h["security_id"], {}).get("is_cash")}
        cur_price = {h["security_id"]: h["price"] for h in hold}

        # Per-day changes after each date, applied backwards from today.
        qty_delta: dict[str, list[float]] = defaultdict(lambda: [0.0] * n)
        cash_delta = [0.0] * n
        last_trade_price: dict[str, list[tuple[str, float]]] = defaultdict(list)
        idx = {d: i for i, d in enumerate(dates)}
        for t in txs:
            i = idx.get(t["date"])
            if i is None:
                continue
            sec = secs.get(t["security_id"] or "", {})
            is_cash_sec = bool(sec.get("is_cash"))
            ttype, sub = (t["type"] or "").lower(), (t["subtype"] or "").lower()
            # Only trades and transfers change how many shares you hold. (Some institutions attach a share count to
            # plain cash deposits or dividends; applying it would invent or remove shares.)
            if t["security_id"] and not is_cash_sec and t["quantity"] and ttype in ("buy", "sell", "transfer"):
                qty_delta[t["security_id"]][i] += t["quantity"]
                if t["price"]:
                    last_trade_price[t["security_id"]].append((t["date"], t["price"]))
            # Buying or selling a money-market "cash" fund is just moving cash around; everything else changes cash.
            if not (is_cash_sec and ttype in ("buy", "sell")):
                cash_delta[i] += -(t["amount"] or 0)  # amount > 0 means cash left the account
            if ttype == "cash" and sub in FLOW_SUBTYPES:
                flows[i] += -(t["amount"] or 0)
            elif ttype == "transfer" and sub in CORPORATE_ACTIONS:
                pass  # splits, spin-offs and the like change share counts, not how much you've put in
            elif ttype == "transfer":
                if t["security_id"] and not is_cash_sec and t["quantity"]:
                    px = t["price"] or book.price({"id": t["security_id"], **sec}, t["date"], cur_price.get(t["security_id"]))
                    flows[i] += (t["quantity"] or 0) * (px or 0)
                    cash_delta[i] += (t["amount"] or 0)  # in-kind transfers don't move cash
                else:
                    flows[i] += -(t["amount"] or 0)

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
                sec = {"id": s, **secs.get(s, {})}
                fallback = cur_price.get(s)
                trades = last_trade_price.get(s)
                if trades:
                    before = [p for td, p in trades if td <= d]
                    fallback = before[-1] if before else trades[0][1]
                px = book.price(sec, d, fallback) or 0.0
                v += qv * px
            acct_values[i] = v
            # step back to the end of the previous day
            for s in all_secs:
                qty[s] -= qty_delta[s][i] if s in qty_delta else 0.0
            cash -= cash_delta[i]
        # Today's value is what the institution reports right now.
        today_total = sum((h["value"] if h["value"] is not None else 0) for h in hold) if hold else cash_now
        acct_values[-1] = today_total
        for i in range(n):
            value[i] += acct_values[i]

    estimated_before = None
    for aid in sf_ids:
        vals, fl, first_snap = _snapshot_series(conn, aid, dates, secs, book)
        for i in range(n):
            value[i] += vals[i]
            flows[i] += fl[i]
        if first_snap and first_snap > dates[0]:
            estimated_before = max(estimated_before or first_snap, first_snap)

    # Time-weighted return: each day's growth with that day's deposits/withdrawals taken out.
    twr = [0.0] * n
    growth = 1.0
    for i in range(1, n):
        # Skip days the portfolio was (nearly) empty: a first deposit into a $0 account isn't a return.
        if value[i - 1] > 1.0 and value[i] - flows[i] > 0:
            growth *= (value[i] - flows[i]) / value[i - 1]
        twr[i] = growth - 1.0
    invested = [0.0] * n
    running = value[0]
    for i in range(n):
        if i:
            running += flows[i]
        invested[i] = running
    return {"dates": dates, "value": [round(v, 2) for v in value], "flows": [round(f, 2) for f in flows],
            "invested": [round(v, 2) for v in invested], "twr": [round(r, 6) for r in twr],
            "missing_prices": sorted(book.missing), "estimated_before": estimated_before}


def _snapshot_series(conn, aid: str, dates: list[str], secs: dict, book: "_PriceBook") -> tuple[list[float], list[float], str | None]:
    """Daily value and external flows for an account we only have position snapshots for."""
    rows = db.rows(conn.execute("SELECT * FROM holding_snapshots WHERE account_id=? ORDER BY date", (aid,)))
    if not rows:  # nothing saved yet: treat today's holdings as the only snapshot
        rows = [{"date": dates[-1], "security_id": h["security_id"], "quantity": h["quantity"], "value": h["value"]}
                for h in db.rows(conn.execute("SELECT * FROM holdings WHERE account_id=?", (aid,)))]
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
    """Average monthly spending from Runway's own transactions over the last 6 full months."""
    start = date(today.year, today.month, 1) - relativedelta(months=6)
    end = date(today.year, today.month, 1)
    rows = conn.execute(
        f"SELECT substr(t.posted,1,7) AS m, SUM(t.amount) AS s FROM {splits.PARTS} t JOIN accounts a ON a.id=t.account_id "
        "JOIN categories c ON c.name=t.category WHERE c.is_transfer=0 AND c.is_income=0 AND a.hidden=0 "
        "AND a.kind IN ('checking','savings','credit') AND t.posted>=? AND t.posted<? GROUP BY m",
        (start.isoformat(), end.isoformat())).fetchall()
    months = [-(r["s"] or 0) for r in rows if (r["s"] or 0) < 0]
    return round(statistics.mean(months), 2) if months else 0.0


def xray(conn, hold: list[dict], alloc: dict, inc: dict, today: date) -> list[dict]:
    total = sum(h["value"] for h in hold)
    rules = []
    if total <= 0:
        return rules
    noncash = [h for h in hold if not h["is_cash"] and h["asset_class"] != "Not reported"]
    if noncash:
        top = noncash[0]
        share = top["value"] / total
        fund = top["asset_class"] in ("ETFs", "Mutual funds")
        rules.append({"name": "Largest single holding", "ok": share <= 0.25 or fund,
                      "detail": f"{top['ticker'] or top['name']} is {share:.0%} of the portfolio"
                                + (" (a fund, so it's diversified inside)" if fund and share > 0.25 else "")
                                + ("" if share <= 0.25 or fund else ". Above 25% in one stock is a lot of single-company risk.")})
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
    spend = monthly_spending(conn, today)
    primary = db.get_setting(conn, "primary_account")
    row = conn.execute("SELECT balance FROM accounts WHERE id=?", (primary,)).fetchone() if primary else None
    if spend > 0 and row:
        months = (row["balance"] or 0) / spend
        rules.append({"name": "Emergency fund", "ok": months >= 3,
                      "detail": f"Your primary account covers {months:.1f} months of your average spending (${spend:,.0f}/month); 3–6 months is the usual guide."})
    missing_basis = [h for h in noncash if h["gain"] is None]
    if missing_basis:
        rules.append({"name": "Cost basis", "ok": False, "info": True,
                      "detail": f"{len(missing_basis)} holding{'s' if len(missing_basis) != 1 else ''} have no cost basis from the institution, so their gain isn't counted."})
    return rules


# The assumptions behind the financial-independence projection. Runway works out the first two from your own
# spending and saving; change any of them and your figure is kept (in settings) until you reset it.
FIRE_FIELDS = {"annual_spending": (0.0, 1e9), "yearly_savings": (0.0, 1e9),
               "expected_return": (-0.5, 0.5), "withdrawal_rate": (0.001, 0.5)}


def fire_saved(conn) -> dict:
    """The assumptions you've changed by hand."""
    out = {}
    for field in FIRE_FIELDS:
        value = db.get_setting(conn, f"fire_{field}")
        if value not in (None, ""):
            try:
                out[field] = float(value)
            except ValueError:
                pass
    return out


def save_fire(conn, values: dict) -> dict:
    """Keep the assumptions you typed. A field set to None goes back to Runway's own figure."""
    for field, value in values.items():
        if field not in FIRE_FIELDS:
            raise ValueError(f"Unknown assumption: {field}")
        if value is None:
            db.set_setting(conn, f"fire_{field}", None)
            continue
        low, high = FIRE_FIELDS[field]
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{field.replace('_', ' ').capitalize()} must be a number")
        if not low <= number <= high:
            raise ValueError(f"{field.replace('_', ' ').capitalize()} is out of range")
        db.set_setting(conn, f"fire_{field}", repr(round(number, 6)))
    return fire_saved(conn)


def fire_defaults(conn, hist: dict, today: date) -> dict:
    spend = monthly_spending(conn, today)
    flows = hist.get("flows") or []
    dates = hist.get("dates") or []
    cutoff = (today - timedelta(days=365)).isoformat()
    yearly_savings = sum(f for d, f in zip(dates, flows) if d > cutoff)
    computed = {"annual_spending": round(spend * 12, 2), "yearly_savings": round(max(0.0, yearly_savings), 2),
                "withdrawal_rate": 0.04, "expected_return": 0.05}
    saved = fire_saved(conn)
    return {**computed, **saved, "current": hist["value"][-1] if hist.get("value") else 0.0,
            "computed": computed, "saved": sorted(saved)}


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
        "xray": xray(conn, hold, alloc, inc, today), "fire": fire_defaults(conn, hist, today),
        "accounts": [a for a in _accounts(conn) if not a["duplicate_of"]], "activity": activity(conn, 300),
    }
