"""Cash-flow projection.

Model, per cash account (checking/savings marked "in forecast"):
  start balance
  + recurring items (paychecks, mortgage, bills) on their dates
  - each credit card's payment on its due date, sized to the statement balance
  - average everyday spending, spread evenly per day

A card's statement balance is worked out from its transactions: the balance on the closing day equals
today's balance minus everything that posted after the close (or it's the amount you entered, if you know it).
Statements that haven't closed yet are estimated from the card's average spending over its last 3 statement
cycles (for the cycle in progress, at least what's already been charged), and flagged as estimates.
"""
from __future__ import annotations

import calendar
import statistics
from datetime import date, timedelta

from . import db
from . import recurring as rec

SPEND_WINDOW_DAYS = 90
AVG_CYCLES = 3           # statement cycles averaged to estimate a card's future statements
ONE_OFF_LIMIT = 1000.0   # single outflows larger than this are treated as one-offs, not everyday spending


# ------------------------------------------------------------------------------------------------ dates

def _d(s: str) -> date:
    return date.fromisoformat(s[:10])


def clamp_day(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def add_months(d: date, n: int, day: int | None = None) -> date:
    m = d.month - 1 + n
    y = d.year + m // 12
    return clamp_day(y, m % 12 + 1, day or d.day)


def last_on_or_before(today: date, day: int) -> date:
    """Most recent date on or before today whose day-of-month is `day` (clamped to month length)."""
    this_month = clamp_day(today.year, today.month, day)
    return this_month if this_month <= today else add_months(this_month, -1, day)


def next_after(d: date, day: int) -> date:
    """First date strictly after d whose day-of-month is `day` (clamped)."""
    this_month = clamp_day(d.year, d.month, day)
    return this_month if this_month > d else add_months(this_month, 1, day)


MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def parse_dates(text: str, freq: str) -> list[tuple[int, int]]:
    """'04-15, 10-15' or 'Apr 15, Oct 15' -> [(4, 15), (10, 15)] for freq='dates'; '1, 15' -> [(0, 1), (0, 15)]
    for freq='semimonthly' (days of every month). Raises ValueError on anything it can't read."""
    out = []
    for part in [p.strip() for p in (text or "").replace(";", ",").split(",") if p.strip()]:
        if freq == "semimonthly":
            day = int(part.lower().rstrip("stndrh"))
            if not 1 <= day <= 31:
                raise ValueError(part)
            out.append((0, day))
            continue
        bits = part.replace("/", "-").replace(" ", "-").split("-")
        bits = [b for b in bits if b]
        if len(bits) != 2:
            raise ValueError(part)
        a, b = bits
        mo = MONTHS.get(a[:3].lower()) if not a.isdigit() else int(a)
        day = int(b.lower().rstrip("stndrh"))
        if not mo or not 1 <= mo <= 12 or not 1 <= day <= 31:
            raise ValueError(part)
        out.append((mo, day))
    if not out:
        raise ValueError("no dates")
    return sorted(set(out))


def occurrences(item: dict, start: date, end: date) -> list[date]:
    """Dates in (start, end] on which a recurring item happens."""
    anchor = _d(item["anchor_date"])
    stop = min(end, _d(item["end_date"])) if item.get("end_date") else end
    freq = item["frequency"]
    out: list[date] = []
    if freq in ("weekly", "biweekly"):
        step = 7 if freq == "weekly" else 14
        k = (start - anchor).days // step
        d = anchor + timedelta(days=step * k)
        while d <= stop:
            if d > start:
                out.append(d)
            d += timedelta(days=step)
    elif freq in ("semimonthly", "dates"):
        # A list of days each month ("1,15") or of dates each year ("04-15,10-15").
        spec = parse_dates(item.get("dates") or "", freq)
        y, m = start.year, start.month
        while True:
            first = date(y, m, 1)
            if first > stop:
                break
            for mo, dy in spec:
                if freq == "dates" and mo != m:
                    continue
                last = calendar.monthrange(y, m)[1]
                d = date(y, m, min(dy, last))
                if start < d <= stop and d >= anchor:
                    out.append(d)
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        out.sort()
    elif freq in ("monthly", "quarterly", "semiannual", "yearly"):
        months = {"monthly": 1, "quarterly": 3, "semiannual": 6, "yearly": 12}[freq]
        k = ((start.year - anchor.year) * 12 + start.month - anchor.month) // months - 1
        d = add_months(anchor, k * months, anchor.day)
        while d <= stop:
            if d > start:
                out.append(d)
            k += 1
            d = add_months(anchor, k * months, anchor.day)
    return out


# ------------------------------------------------------------------------------------------------ data

def _transfer_categories(conn) -> set[str]:
    return {r["name"] for r in conn.execute("SELECT name FROM categories WHERE is_transfer=1")}


def owed(account: dict, balance: float | None = None) -> float:
    b = account["balance"] if balance is None else balance
    return b if account.get("owed_positive") else -b


def daily_spend_rate(conn, account_id: str, today: date, exclude_matches: list[str] | None = None) -> float:
    """Average everyday outflow per day over the last SPEND_WINDOW_DAYS (or the history available)."""
    transfers = _transfer_categories(conn)
    since = today - timedelta(days=SPEND_WINDOW_DAYS)
    txs = db.rows(conn.execute(
        "SELECT posted, amount, payee, description, category FROM transactions "
        "WHERE account_id=? AND posted>? AND posted<=? AND pending=0 AND COALESCE(recurring_id, 0)=0",
        (account_id, since.isoformat(), today.isoformat()),
    ))
    if not txs:
        return 0.0
    first = min(_d(t["posted"]) for t in txs)
    days = max(14, (today - first).days + 1)  # avoid inflating the rate from a few days of data
    matches = [m.lower() for m in (exclude_matches or []) if m]
    total = 0.0
    for t in txs:
        if t["category"] in transfers:
            continue
        hay = f"{t['payee'] or ''} {t['description'] or ''}".lower()
        if any(m in hay for m in matches):
            continue
        if t["amount"] < 0 and -t["amount"] <= ONE_OFF_LIMIT:
            total += -t["amount"]
        elif t["amount"] > 0 and t["category"] == "Refunds":
            total -= t["amount"]
    return max(0.0, total / days)


def card_monthly_spend(conn, card: dict, last_close: date) -> dict:
    """Average spending per statement cycle over the last AVG_CYCLES closed cycles (only cycles fully covered by
    the transaction history). Charges minus refunds; payments and transfers don't count."""
    transfers = _transfer_categories(conn)
    first = conn.execute("SELECT MIN(posted) FROM transactions WHERE account_id=?", (card["id"],)).fetchone()[0]
    cycles = []
    end = last_close
    for _ in range(AVG_CYCLES):
        start = add_months(end, -1, card["closing_day"])
        if not first or _d(first) > start + timedelta(days=3):   # history doesn't reach back this far
            break
        txs = conn.execute("SELECT amount, category FROM transactions WHERE account_id=? AND posted>? AND posted<=? AND pending=0",
                           (card["id"], start.isoformat(), end.isoformat())).fetchall()
        spent = -sum(t["amount"] for t in txs if t["category"] not in transfers)
        cycles.append({"start": start.isoformat(), "end": end.isoformat(), "spent": round(max(0.0, spent), 2)})
        end = start
    avg = sum(c["spent"] for c in cycles) / len(cycles) if cycles else None
    return {"average": round(avg, 2) if avg is not None else None, "cycles": cycles}


def statement_override(conn, card_id: str, close: date) -> float | None:
    r = conn.execute("SELECT amount FROM overrides WHERE key=?", (f"stmt:{card_id}:{close.isoformat()}",)).fetchone()
    return abs(r["amount"]) if r else None


def card_cycle(conn, card: dict, today: date) -> dict:
    """Where a card stands in its billing cycle today."""
    transfers = _transfer_categories(conn)
    last_close = last_on_or_before(today, card["closing_day"])
    bal_date = _d(card["balance_date"]) if card.get("balance_date") else today
    txs = db.rows(conn.execute(
        "SELECT posted, amount, category, pending FROM transactions WHERE account_id=? AND posted>?",
        (card["id"], last_close.isoformat()),
    ))
    posted_after_close = sum(t["amount"] for t in txs if not t["pending"] and _d(t["posted"]) <= bal_date)
    # Transactions are signed the same way whatever the balance convention (charges negative), so undo them
    # in "money in" terms first, then read the result as an amount owed.
    balance_at_close = card["balance"] + posted_after_close if card.get("owed_positive") else card["balance"] - posted_after_close
    calculated = max(0.0, owed(card, balance_at_close))
    known = statement_override(conn, card["id"], last_close)
    statement = known if known is not None else calculated
    paid = sum(t["amount"] for t in txs if t["amount"] > 0 and t["category"] in transfers)
    new_charges = max(0.0, -sum(t["amount"] for t in txs if t["category"] not in transfers))
    due = next_after(last_close, card["due_day"])
    spend = card_monthly_spend(conn, card, last_close)
    return {
        "last_close": last_close.isoformat(),
        "statement_balance": round(statement, 2),
        "statement_calculated": round(calculated, 2),
        "statement_set": known is not None,
        "statement_key": f"stmt:{card['id']}:{last_close.isoformat()}",
        "avg_monthly_spend": spend["average"],
        "avg_cycles": len(spend["cycles"]),
        "paid_since_close": round(paid, 2),
        "remaining": round(max(0.0, statement - paid), 2),
        "due_date": due.isoformat(),
        "new_charges": round(new_charges, 2),
        "daily_rate": round(daily_spend_rate(conn, card["id"], today), 2),
    }


# ------------------------------------------------------------------------------------------------ forecast

def build(conn, today: date | None = None, horizon_days: int = 90) -> dict:
    today = today or date.today()
    end = today + timedelta(days=horizon_days)
    accounts = db.rows(conn.execute("SELECT * FROM accounts WHERE hidden=0 ORDER BY name"))
    by_id = {a["id"]: a for a in accounts}
    cash_like = [a for a in accounts if a["kind"] in ("checking", "savings")]
    primary = by_id.get(db.get_setting(conn, "primary_account") or "")
    if primary and primary["kind"] in ("checking", "savings"):
        cash = [primary]
    elif len([a for a in cash_like if a["kind"] == "checking"]) == 1:
        cash = [a for a in cash_like if a["kind"] == "checking"]  # only one checking account: that's the primary
    else:
        cash = [a for a in cash_like if a["in_forecast"]]
    cards = [a for a in accounts if a["kind"] == "credit"]
    recurring = db.rows(conn.execute("SELECT * FROM recurring WHERE active=1"))

    events: list[dict] = []
    warnings: list[str] = []
    card_status: list[dict] = []

    for item in recurring:
        if item["account_id"] not in by_id:
            continue
        history = rec.matched(conn, item["id"])
        amount = rec.expected_amount(item, history)
        cat_row = conn.execute(
            "SELECT category FROM transactions WHERE recurring_id=? AND category IS NOT NULL "
            "GROUP BY category ORDER BY COUNT(*) DESC, MAX(posted) DESC LIMIT 1", (item["id"],)).fetchone()
        for d in occurrences(item, today, end):
            if rec.already_happened(item, d, history, today):
                continue  # this one already posted (possibly early), don't count it twice
            events.append({"date": d.isoformat(), "account_id": item["account_id"], "name": item["name"],
                           "amount": amount, "kind": "recurring", "estimated": (item.get("amount_mode") or "fixed") != "fixed",
                           "recurring_id": item["id"], "key": f"rec:{item['id']}:{d.isoformat()}",
                           "category": cat_row["category"] if cat_row else None})

    for card in cards:
        label = card["display_name"] or card["name"]
        if not card["closing_day"] or not card["due_day"]:
            warnings.append(f"{label}: add its statement closing day and due day in Settings so its payments can be forecast.")
            continue
        info = card_cycle(conn, card, today)
        info.update({"id": card["id"], "name": label, "owed_now": round(max(0.0, owed(card)), 2)})
        card_status.append(info)
        payer = by_id.get(card["pay_from"] or "")
        if not payer:
            warnings.append(f"{label}: choose which account pays it in Settings.")
            continue
        if payer not in cash:
            continue  # paid from an account that isn't being forecast
        due = _d(info["due_date"])
        if due >= today and info["remaining"] > 0.005:
            events.append({"date": due.isoformat(), "account_id": payer["id"], "name": f"{label} statement",
                           "amount": -info["remaining"], "kind": "card", "estimated": False,
                           "key": f"card:{card['id']}:{due.isoformat()}", "category": "Credit Card Payment"})
        elif due < today and info["remaining"] > 0.005:
            warnings.append(f"{label}: ${info['remaining']:,.2f} was due {due:%b %-d} and no payment has shown up yet.")
        # Future statements: the card's average spending per cycle over its last few statements (for the cycle
        # in progress, at least what's been charged already). Without enough history, the recent daily rate.
        close = next_after(_d(info["last_close"]), card["closing_day"])
        prev_close = _d(info["last_close"])
        first = True
        avg = info["avg_monthly_spend"]
        while True:
            due_k = next_after(close, card["due_day"])
            if due_k > end:
                break
            if avg is not None:
                est = max(info["new_charges"], avg) if first else avg
            else:
                days_in_cycle = (close - (today if first else prev_close)).days
                est = (info["new_charges"] if first else 0.0) + info["daily_rate"] * days_in_cycle
            if est > 0.005:
                events.append({"date": due_k.isoformat(), "account_id": payer["id"], "name": f"{label} statement",
                               "amount": -round(est, 2), "kind": "card", "estimated": True,
                               "key": f"card:{card['id']}:{due_k.isoformat()}", "category": "Credit Card Payment"})
            prev_close, close, first = close, next_after(close, card["closing_day"]), False

    # One-off edits you've made to specific upcoming items.
    overrides = {r["key"]: r["amount"] for r in conn.execute("SELECT key, amount FROM overrides")}
    for e in events:
        if e.get("key") in overrides:
            e["original_amount"], e["amount"], e["overridden"] = e["amount"], round(overrides[e["key"]], 2), True

    series_by_acct: dict[str, list[float]] = {}
    rates: dict[str, float] = {}
    for a in cash:
        matches = [r["match"] or r["name"] for r in recurring if r["account_id"] == a["id"]]
        rate = daily_spend_rate(conn, a["id"], today, matches) if a["daily_spend"] else 0.0
        rates[a["id"]] = round(rate, 2)
        by_day: dict[str, float] = {}
        for e in events:
            if e["account_id"] == a["id"]:
                by_day[e["date"]] = by_day.get(e["date"], 0.0) + e["amount"]
        bal = a["balance"]
        series = [round(bal, 2)]
        for i in range(1, horizon_days + 1):
            d = (today + timedelta(days=i)).isoformat()
            bal += by_day.get(d, 0.0) - rate
            series.append(round(bal, 2))
        series_by_acct[a["id"]] = series

    dates = [(today + timedelta(days=i)).isoformat() for i in range(horizon_days + 1)]
    total = [round(sum(s[i] for s in series_by_acct.values()), 2) for i in range(horizon_days + 1)] if cash else []

    def low(series: list[float]) -> dict | None:
        if not series:
            return None
        i = min(range(len(series)), key=lambda k: series[k])
        return {"date": dates[i], "balance": series[i]}

    cash_ids = {a["id"] for a in cash}
    events = sorted((e for e in events if e["account_id"] in cash_ids), key=lambda e: (e["date"], e["amount"]))
    # Balance of the item's account right after it lands (same-day items apply in the order listed).
    index = {d: i for i, d in enumerate(dates)}
    running: dict[tuple, float] = {}
    for e in events:
        i, acct = index[e["date"]], e["account_id"]
        k = (acct, e["date"])
        if k not in running:
            running[k] = series_by_acct[acct][i - 1] - rates.get(acct, 0.0)
        running[k] += e["amount"]
        e["balance_after"] = round(running[k], 2)
        e["account"] = by_id[acct]["display_name"] or by_id[acct]["name"]

    return {
        "today": today.isoformat(),
        "primary_id": cash[0]["id"] if len(cash) == 1 else None,
        "dates": dates,
        "accounts": [
            {"id": a["id"], "name": a["display_name"] or a["name"], "kind": a["kind"], "balance": round(a["balance"], 2),
             "daily_spend": rates.get(a["id"], 0.0), "series": series_by_acct[a["id"]],
             "low": low(series_by_acct[a["id"]])}
            for a in cash
        ],
        "total": total,
        "low": low(total),
        "events": events,
        "cards": card_status,
        "warnings": warnings,
    }


# ------------------------------------------------------------------------------------------------ suggestions

def suggest_recurring(conn, today: date | None = None, lookback_days: int = 150) -> list[dict]:
    """Payees on cash accounts that show up on a regular schedule with similar amounts."""
    today = today or date.today()
    transfers = _transfer_categories(conn)
    known = [(r["account_id"], (r["match"] or r["name"]).lower()) for r in conn.execute("SELECT * FROM recurring")]
    txs = db.rows(conn.execute(
        "SELECT t.account_id, t.posted, t.amount, t.payee, t.category FROM transactions t "
        "JOIN accounts a ON a.id=t.account_id "
        "WHERE a.kind IN ('checking','savings') AND t.pending=0 AND COALESCE(t.recurring_id, 0)=0 AND t.posted>?",
        ((today - timedelta(days=lookback_days)).isoformat(),),
    ))
    groups: dict[tuple, list[dict]] = {}
    for t in txs:
        if not t["payee"] or abs(t["amount"]) < 1:
            continue
        if t["category"] in transfers and t["category"] != "Credit Card Payment":
            continue
        if t["category"] == "Credit Card Payment":
            continue  # modeled from statements instead
        groups.setdefault((t["account_id"], t["payee"].lower(), t["amount"] > 0), []).append(t)

    out = []
    for (acct, payee, _incoming), items in groups.items():
        if len(items) < 2 or any(a == acct and (m in payee or payee in m) for a, m in known):
            continue
        items.sort(key=lambda t: t["posted"])
        ds = [_d(t["posted"]) for t in items]
        gaps = [(b - a).days for a, b in zip(ds, ds[1:]) if (b - a).days > 0]
        if not gaps:
            continue
        gap = statistics.median(gaps)
        if 6 <= gap <= 8:
            freq, need = "weekly", 4
        elif 13 <= gap <= 16:
            freq, need = "biweekly", 3
        elif 27 <= gap <= 33:
            freq, need = "monthly", 2
        else:
            continue
        if len(items) < need:
            continue
        amounts = [t["amount"] for t in items]
        med = statistics.median(amounts)
        if any(abs(x - med) > 0.2 * abs(med) for x in amounts[-need:]):
            continue
        expected_gap = {"weekly": 7, "biweekly": 14, "monthly": 30}[freq]
        if (today - ds[-1]).days > expected_gap * 2:
            continue  # stopped happening
        out.append({"account_id": acct, "name": items[-1]["payee"], "match": payee, "amount": round(med, 2),
                    "frequency": freq, "anchor_date": ds[-1].isoformat(), "count": len(items)})
    out.sort(key=lambda s: -abs(s["amount"]))
    return out
