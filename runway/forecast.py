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
import itertools
from collections import defaultdict
import statistics
from datetime import date, datetime, timedelta

from dateutil.relativedelta import relativedelta
from dateutil.rrule import MONTHLY, WEEKLY, YEARLY, rrule, rruleset

from . import bankdays, db, plaidbank, splits
from . import categories as catmod
from . import recurring as rec

SPEND_WINDOW_DAYS = 90
AVG_CYCLES = 3           # statement cycles averaged to estimate a card's future statements
ONE_OFF_LIMIT = 1000.0   # single outflows larger than this are treated as one-offs, not everyday spending


# ------------------------------------------------------------------------------------------------ dates

def _d(s: str) -> date:
    return date.fromisoformat(s[:10])


def clamp_day(year: int, month: int, day: int) -> date:
    """That day of the month, or the month's last day if it's shorter (Feb 31 -> Feb 28)."""
    return date(year, month, 1) + relativedelta(day=day)


def add_months(d: date, n: int, day: int | None = None) -> date:
    return date(d.year, d.month, 1) + relativedelta(months=n, day=day or d.day)


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


def _monthly_rule(freq: int, interval: int, dtstart: datetime, day: int, **kw) -> rrule:
    """A rule on `day` of the month that falls back to the last day in shorter months (the 31st -> Feb 28)."""
    if day > 28:
        return rrule(freq, interval=interval, dtstart=dtstart, bymonthday=(day, -1), bysetpos=1, **kw)
    return rrule(freq, interval=interval, dtstart=dtstart, bymonthday=day, **kw)


def schedule(item: dict) -> rruleset:
    """A recurring item's dates, as a dateutil rule set starting at its anchor date."""
    anchor = datetime.combine(_d(item["anchor_date"]), datetime.min.time())
    freq = item["frequency"]
    rules = rruleset()
    if freq in ("weekly", "biweekly"):
        rules.rrule(rrule(WEEKLY, interval=1 if freq == "weekly" else 2, dtstart=anchor))
    elif freq in ("monthly", "quarterly", "semiannual", "yearly"):
        months = {"monthly": 1, "quarterly": 3, "semiannual": 6, "yearly": 12}[freq]
        rules.rrule(_monthly_rule(MONTHLY, months, anchor, anchor.day))
    elif freq in ("semimonthly", "dates"):
        # A list of days each month ("1,15") or of dates each year ("04-15,10-15").
        for month, day in parse_dates(item.get("dates") or "", freq):
            if freq == "semimonthly":
                rules.rrule(_monthly_rule(MONTHLY, 1, anchor, day))
            else:
                rules.rrule(_monthly_rule(YEARLY, 1, anchor, day, bymonth=month))
    return rules


def scheduled(item: dict, start: date, end: date) -> list[date]:
    """The dates in (start, end] a recurring item is scheduled for, before moving any off weekends and holidays."""
    stop = min(end, _d(item["end_date"])) if item.get("end_date") else end
    if stop <= start:
        return []
    lo = datetime.combine(start + timedelta(days=1), datetime.min.time())
    hi = datetime.combine(stop, datetime.min.time())
    return [d.date() for d in schedule(item).between(lo, hi, inc=True)]


def occurrences(item: dict, start: date, end: date) -> list[date]:
    """Dates in (start, end] on which a recurring item's money actually moves: its scheduled dates, moved off weekends
    and bank holidays (money in to the business day before, money out to the one after; see bankdays)."""
    stop = min(end, _d(item["end_date"])) if item.get("end_date") else end
    if stop <= start:
        return []
    money_in = (item.get("amount") or 0) > 0
    # Look a few days beyond the window: a date just outside it can move inside, and the other way round.
    lo = datetime.combine(start - timedelta(days=6), datetime.min.time())
    hi = datetime.combine(stop + timedelta(days=6), datetime.min.time())
    out = []
    for d in schedule(item).between(lo, hi, inc=True):
        nominal = d.date()
        if item.get("end_date") and nominal > _d(item["end_date"]):
            continue
        moved = bankdays.settles(nominal, money_in)
        if start < moved <= end:
            out.append(moved)   # two dates can move to the same business day: that's still two payments
    return sorted(out)


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


def bank_statement(conn, card: dict, today: date):
    """The card's latest statement from its issuer (Plaid Liabilities). The card's billing cycle follows it:
    card["closing_day"] and card["due_day"] are set from the statement's closing and due dates."""
    st = plaidbank.statement(conn, card["id"], today) if card.get("plaid_account_id") else None
    if not st:
        return None
    close = _d(st["last_statement_date"])
    due = _d(st["next_due_date"]) if st["next_due_date"] else None
    card["closing_day"] = close.day
    card["due_day"] = (due if due and due > close else close + timedelta(days=25)).day
    return st


def in_transit(conn, card: dict, last_close: date) -> float:
    """Card payments that have left the paying account since the close but haven't reached the card yet. Only
    counted when that account pays no other card, so a payment can't be mistaken for another card's."""
    payer = card.get("pay_from")
    if not payer or conn.execute("SELECT COUNT(*) FROM accounts WHERE kind='credit' AND hidden=0 AND pay_from=? AND id<>?",
                                 (payer, card["id"])).fetchone()[0]:
        return 0.0
    sent = conn.execute("SELECT posted, amount FROM transactions WHERE account_id=? AND category='Credit Card Payment' "
                        "AND amount<0 AND posted>? ORDER BY posted", (payer, last_close.isoformat())).fetchall()
    if not sent:
        return 0.0
    # The card's payments, from a little before the close: one can reach the card before it leaves the bank.
    unclaimed = db.rows(conn.execute(
        "SELECT posted, amount FROM transactions WHERE account_id=? AND amount>0 AND posted>? AND category IN "
        "(SELECT name FROM categories WHERE is_transfer=1) ORDER BY posted",
        (card["id"], (last_close - timedelta(days=5)).isoformat())))
    total = 0.0
    for s in sent:
        # The card's credit for it: the same amount, from a few days before to a couple of weeks after it left.
        lo, hi = (_d(s["posted"]) - timedelta(days=5)).isoformat(), (_d(s["posted"]) + timedelta(days=14)).isoformat()
        hit = next((c for c in unclaimed if abs(c["amount"] + s["amount"]) <= 0.005 and lo <= c["posted"][:10] <= hi), None)
        if hit:
            unclaimed.remove(hit)
        else:
            total += -s["amount"]
    return total


def card_cycle(conn, card: dict, today: date, bank) -> dict:
    """Where a card stands in its billing cycle today, from its latest statement at the bank (see bank_statement)."""
    transfers = _transfer_categories(conn)
    last_close = _d(bank["last_statement_date"])
    txs = db.rows(conn.execute(
        "SELECT posted, amount, category, pending FROM transactions WHERE account_id=? AND posted>? ORDER BY posted",
        (card["id"], last_close.isoformat()),
    ))
    reported = max(0.0, bank["last_statement_balance"] or 0.0)
    known = statement_override(conn, card["id"], last_close)
    statement = known if known is not None else reported   # a figure you entered wins over the bank's
    paid = sum(t["amount"] for t in txs if t["amount"] > 0 and t["category"] in transfers)
    paid += in_transit(conn, card, last_close)
    new_charges = max(0.0, -sum(t["amount"] for t in txs if t["category"] not in transfers))
    due = _d(bank["next_due_date"]) if bank["next_due_date"] and _d(bank["next_due_date"]) > last_close \
        else next_after(last_close, card["due_day"])
    spend = card_monthly_spend(conn, card, last_close)
    return {
        "last_close": last_close.isoformat(),
        "statement_balance": round(statement, 2),
        "statement_reported": round(reported, 2),
        "statement_set": known is not None,
        "minimum_payment": bank["minimum_payment"],
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
    unlinked: list[dict] = []   # cards without statements from the issuer (not linked through Plaid yet)

    for item in recurring:
        if item["account_id"] not in by_id:
            continue
        history = rec.matched(conn, item["id"])
        amount = rec.expected_amount(item, history)
        cat_row = conn.execute(
            "SELECT category FROM transactions WHERE recurring_id=? AND category IS NOT NULL "
            "GROUP BY category ORDER BY COUNT(*) DESC, MAX(posted) DESC LIMIT 1", (item["id"],)).fetchone()
        if not cat_row:   # nothing linked to it yet: the category of what it matches on its account
            like = "%" + (item["match"] or item["name"] or "").lower().replace("%", "").replace("_", "") + "%"
            cat_row = conn.execute(
                "SELECT category FROM transactions WHERE account_id=? AND category IS NOT NULL AND length(?) > 4 "
                "AND (lower(payee) LIKE ? OR lower(description) LIKE ?) "
                "GROUP BY category ORDER BY COUNT(*) DESC, MAX(posted) DESC LIMIT 1",
                (item["account_id"], like, like, like)).fetchone()
        # Anything due in the last matching window that hasn't shown up yet is still coming: it goes on today, as
        # late (older than the window, it's "missed" in Recurring instead). Due today counts as due, not late.
        window = rec.MATCH_WINDOW_DAYS.get(item["frequency"], 6)
        first_tx = conn.execute("SELECT MIN(posted) FROM transactions WHERE account_id=?", (item["account_id"],)).fetchone()[0]
        since = max(today - timedelta(days=window + 1), _d(first_tx) + timedelta(days=window) if first_tx else today)
        for d in occurrences(item, min(since, today - timedelta(days=1)), end):
            if rec.already_happened(item, d, history, today):
                continue  # this one already posted (possibly early), don't count it twice
            events.append({"date": max(d, today).isoformat(), "account_id": item["account_id"], "name": item["name"],
                           "amount": amount, "kind": "recurring", "estimated": (item.get("amount_mode") or "fixed") != "fixed",
                           "recurring_id": item["id"], "key": f"rec:{item['id']}:{d.isoformat()}",
                           "category": cat_row["category"] if cat_row else None, **({"late_from": d.isoformat()} if d < today else {})})

    for card in cards:
        label = db.account_label(card)
        bank = bank_statement(conn, card, today)
        if not bank:
            unlinked.append({"id": card["id"], "name": label, "owed_now": round(max(0.0, owed(card)), 2),
                             "linked": bool(card.get("plaid_account_id"))})
            continue
        info = card_cycle(conn, card, today, bank)
        info.update({"id": card["id"], "name": label, "owed_now": round(max(0.0, owed(card)), 2)})
        card_status.append(info)
        payer = by_id.get(card["pay_from"] or "")
        if not payer:
            warnings.append(f"{label}: choose which account pays it in Settings.")
            continue
        if payer not in cash:
            continue  # paid from an account that isn't being forecast
        due = _d(info["due_date"])
        pays = bankdays.next_business_day(due)   # a due date on a weekend or holiday is paid the next business day
        if pays >= today and info["remaining"] > 0.005:
            events.append({"date": pays.isoformat(), "account_id": payer["id"], "name": f"{label} statement",
                           "amount": -info["remaining"], "kind": "card", "estimated": False,
                           "key": f"card:{card['id']}:{due.isoformat()}", "category": "Credit Card Payment", "card_id": card["id"]})
        elif pays < today and info["remaining"] > 0.005:
            warnings.append(f"{label}: ${info['remaining']:,.2f} was due {due:%b %-d} and no payment has shown up yet.")
        # Future statements: the card's average spending per cycle over its last few statements (for the cycle
        # in progress, at least what's been charged already). Without enough history, the recent daily rate.
        close = next_after(_d(info["last_close"]), card["closing_day"])
        prev_close = _d(info["last_close"])
        first = True
        avg = info["avg_monthly_spend"]
        stale = False
        while True:
            due_k = next_after(close, card["due_day"])
            if due_k > end:
                break
            if avg is not None:
                est = max(info["new_charges"], avg) if first else avg
            else:
                days_in_cycle = max(0, (close - max(today, prev_close)).days)
                # The recent daily rate leaves out recurring charges on the card, so add the ones due this cycle.
                upcoming = -sum(e["amount"] for e in events if e["account_id"] == card["id"] and e["kind"] == "recurring"
                                and max(today, prev_close).isoformat() < e["date"] <= close.isoformat())
                est = (info["new_charges"] if first else 0.0) + info["daily_rate"] * days_in_cycle + max(0.0, upcoming)
            pays_k = bankdays.next_business_day(due_k)
            if pays_k < today:
                stale = True   # the issuer's latest statement is older than this one; nothing to put on the chart
            elif est > 0.005:
                events.append({"date": pays_k.isoformat(), "account_id": payer["id"], "name": f"{label} statement",
                               "amount": -round(est, 2), "kind": "card", "estimated": True,
                               "key": f"card:{card['id']}:{due_k.isoformat()}", "category": "Credit Card Payment", "card_id": card["id"]})
            prev_close, close, first = close, next_after(close, card["closing_day"]), False
        if stale:
            warnings.append(f"{label}: the bank hasn't sent the statement after {_d(info['last_close']):%b %-d} yet, so its "
                            "payment isn't in the forecast.")

    def listed(names):
        return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    not_linked = [c["name"] for c in unlinked if not c["linked"]]
    no_statement = [c["name"] for c in unlinked if c["linked"]]
    if not_linked:
        waiting = conn.execute(
            "SELECT COUNT(*) FROM plaid_accounts p WHERE p.type='credit' AND p.ignored=0 AND p.plaid_account_id NOT IN "
            "(SELECT plaid_account_id FROM accounts WHERE plaid_account_id IS NOT NULL)").fetchone()[0]
        one = len(not_linked) == 1
        warnings.append(f"{listed(not_linked)} {'isn’t' if one else 'aren’t'} linked through Plaid yet, so "
                        f"{'its payments aren’t' if one else 'their payments aren’t'} in the forecast. "
                        + (f"Plaid has {waiting} card{'s' if waiting != 1 else ''} waiting to be matched: in Settings → Connections, "
                           f"choose “Same as …” for each." if waiting else f"Link {'it' if one else 'them'} to get statements and due dates."))
    if no_statement:
        one = len(no_statement) == 1
        warnings.append(f"Plaid hasn’t sent a statement for {listed(no_statement)} yet, so {'its payments aren’t' if one else 'their payments aren’t'} "
                        "in the forecast. It usually arrives with the next sync.")

    # One-off edits you've made to specific upcoming items.
    overrides = {r["key"]: r["amount"] for r in conn.execute("SELECT key, amount FROM overrides")}
    for e in events:
        if e.get("key") in overrides:
            e["original_amount"], e["amount"], e["overridden"] = e["amount"], round(overrides[e["key"]], 2), True
    # Only what lands on the chart, today through its last day (a payment moved off a weekend can land past it).
    events = [e for e in events if today.isoformat() <= e["date"] <= end.isoformat()]

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
        bal = a["balance"] + by_day.get(today.isoformat(), 0.0)   # anything due today that hasn't posted yet
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

    scenario = budget_scenario(conn, today, horizon_days, dates, cash, by_id, card_status, events)

    cash_ids = {a["id"] for a in cash}
    events = sorted((e for e in events if e["account_id"] in cash_ids), key=lambda e: (e["date"], e["amount"]))
    # Balance of the item's account right after it lands (same-day items apply in the order listed).
    index = {d: i for i, d in enumerate(dates)}
    running: dict[tuple, float] = {}
    for e in events:
        i, acct = index[e["date"]], e["account_id"]
        k = (acct, e["date"])
        if k not in running:
            running[k] = by_id[acct]["balance"] if i == 0 else series_by_acct[acct][i - 1] - rates.get(acct, 0.0)
        running[k] += e["amount"]
        e["balance_after"] = round(running[k], 2)
        e["account"] = db.account_label(by_id[acct])

    return {
        "today": today.isoformat(),
        "primary_id": cash[0]["id"] if len(cash) == 1 else None,
        "dates": dates,
        "accounts": [
            {"id": a["id"], "name": db.account_label(a), "kind": a["kind"], "balance": round(a["balance"], 2),
             "daily_spend": rates.get(a["id"], 0.0), "series": series_by_acct[a["id"]],
             "low": low(series_by_acct[a["id"]])}
            for a in cash
        ],
        "total": total,
        "low": low(total),
        "events": events,
        "cards": card_status,
        "unlinked_cards": unlinked,
        "warnings": warnings,
        "budget": scenario,
    }


# ------------------------------------------------------------------------------------------------ sticking to the budget

def budget_plan(conn, today: date) -> list[dict]:
    """Each budget that counts (a parent's budget covers its subcategories), with what's been spent this month and the
    account it's paid with: the one you chose, else the account used most for it over the last 90 days."""
    cats = catmod.all_categories(conn)
    by_name = {c["name"]: c for c in cats}
    budgets = {r["category"]: r for r in db.rows(conn.execute("SELECT * FROM budgets"))}
    month_start = today.replace(day=1).isoformat()
    since = (today - timedelta(days=90)).isoformat()
    out = []
    for name, b in budgets.items():
        c = by_name.get(name)
        if not c or c["is_transfer"] or c["is_income"] or any(p in budgets for p in c["path"][:-1]):
            continue
        names = [name] + [k["name"] for k in cats if name in k["path"][:-1]]
        q = ",".join("?" * len(names))
        base = (f"FROM {splits.PARTS} t JOIN accounts a ON a.id=t.account_id WHERE t.category IN ({q}) AND a.hidden=0 "
                "AND a.kind IN ('checking','savings','credit')")
        spent = -(conn.execute(f"SELECT COALESCE(SUM(t.amount), 0) {base} AND t.posted>=? AND t.posted<=?",
                               (*names, month_start, today.isoformat())).fetchone()[0] or 0.0)
        usual = conn.execute(f"SELECT t.account_id, SUM(-t.amount) AS s {base} AND t.posted>? AND t.amount<0 "
                             "GROUP BY t.account_id ORDER BY s DESC LIMIT 1", (*names, since)).fetchone()
        out.append({"category": name, "amount": b["amount"], "names": names, "spent": round(max(0.0, spent), 2),
                    "pay_with": b.get("pay_with"), "usual": usual["account_id"] if usual else None})
    return out


def budget_scenario(conn, today: date, horizon_days: int, dates: list[str], cash: list[dict], by_id: dict,
                    card_status: list[dict], events: list[dict]) -> dict | None:
    """The forecast if you spend exactly your budgets: budgeted spending is charged day by day to each category's
    account; spending on cards is paid on each card's due date. Recurring items and statements that have already closed
    stay as they are; estimated future statements are replaced by the budgeted charges.

    A budget includes its category's recurring payments: the ones the forecast already takes out of its accounts are
    subtracted from it each month (this month: from what's left after what's been spent), so they aren't counted twice,
    and only the rest is spread over the days. A budget they cover entirely adds nothing."""
    plan = budget_plan(conn, today)
    if not plan or not cash:
        return None
    cash_ids = {a["id"] for a in cash}
    cards = {c["id"]: c for c in card_status}
    spend: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))   # account -> date -> amount
    used, skipped = [], []
    changes: list[dict] = []   # what this scenario takes out of the forecast's accounts, day by day (for the table)
    # The recurring payments the forecast takes out of its accounts, by category and month (from today on).
    recurring: dict[tuple[str, str], float] = defaultdict(float)
    for e in events:
        if e["kind"] == "recurring" and e["amount"] < 0 and e["account_id"] in cash_ids and e.get("category"):
            recurring[(e["category"], e["date"][:7])] += -e["amount"]
    this_month = today.isoformat()[:7]

    def covered(p: dict, month: str) -> float:
        return sum(recurring.get((n, month), 0.0) for n in p["names"])

    def left(p: dict, month: str) -> float:
        """The month's budget not already covered by its recurring payments (this month: nor spent)."""
        return max(0.0, p["amount"] - (p["spent"] if month == this_month else 0.0) - covered(p, month))

    for p in plan:
        acct = p["pay_with"] or p["usual"] or cash[0]["id"]
        if acct not in cash_ids and acct not in cards:
            skipped.append({"category": p["category"], "reason": "its account isn't in the forecast"})
            continue
        months = sorted({(today + timedelta(days=i)).isoformat()[:7] for i in range(1, horizon_days + 1)})
        if all(left(p, m) < 0.005 for m in months if m != this_month) and any(covered(p, m) for m in months):
            skipped.append({"category": p["category"], "reason": "a recurring item already covers it"})
            continue
        for i in range(1, horizon_days + 1):
            d = today + timedelta(days=i)
            dim = calendar.monthrange(d.year, d.month)[1]
            month = d.isoformat()[:7]
            if month == this_month:   # this month: whatever's left, over the days left
                per_day = left(p, month) / (dim - today.day)
            else:
                per_day = left(p, month) / dim
            spend[acct][d.isoformat()] += per_day
            if acct in cash_ids and per_day > 0.005:
                changes.append({"date": d.isoformat(), "account_id": acct, "kind": "budget", "category": p["category"],
                                "name": p["category"], "amount": -round(per_day, 2)})
        used.append({"category": p["category"], "amount": p["amount"], "account_id": acct,
                     "account": db.account_label(by_id[acct]), "chosen": bool(p["pay_with"])})

    base = [e for e in events if not (e["kind"] == "card" and e.get("estimated"))]
    extra: list[tuple[str, str, float]] = []   # (account, date, amount)
    for acct, days in spend.items():
        if acct in cash_ids:
            extra += [(acct, d, -v) for d, v in days.items()]
    # Each card's open statement (charges so far plus budgeted charges) and later ones, paid on their due dates.
    for cid, info in cards.items():
        card = by_id[cid]
        payer = card["pay_from"]
        if payer not in cash_ids:
            continue
        days = spend.get(cid, {})
        prev = _d(info["last_close"])
        close, first = next_after(prev, card["closing_day"]), True
        while True:
            due = next_after(close, card["due_day"])
            if due.isoformat() > dates[-1]:
                break
            amt = sum(v for d, v in days.items() if prev.isoformat() < d <= close.isoformat()) + (info["new_charges"] if first else 0.0)
            if amt > 0.005:
                paid = bankdays.next_business_day(due).isoformat()
                extra.append((payer, paid, -round(amt, 2)))
                if paid <= dates[-1]:
                    changes.append({"date": paid, "account_id": payer, "kind": "card", "name": f"{db.account_label(card)} statement",
                                    "amount": -round(amt, 2), "account": db.account_label(by_id[payer]),
                                    # what it's made of: charges already on the card (first statement), plus budgeted ones
                                    "charged": round(info["new_charges"], 2) if first else 0.0})
            prev, close, first = close, next_after(close, card["closing_day"]), False

    by_day: dict[tuple, float] = defaultdict(float)
    for e in base:
        by_day[(e["account_id"], e["date"])] += e["amount"]
    for acct, d, v in extra:
        by_day[(acct, d)] += v
    total = [0.0] * len(dates)
    for a in cash:
        bal = a["balance"] + by_day.get((a["id"], dates[0]), 0.0)
        total[0] += bal
        for i in range(1, len(dates)):
            bal += by_day.get((a["id"], dates[i]), 0.0)
            total[i] += bal
    total = [round(v, 2) for v in total]
    i = min(range(len(total)), key=lambda k: total[k])
    changes.sort(key=lambda c: (c["date"], c["amount"]))
    return {"total": total, "low": {"date": dates[i], "balance": total[i]}, "used": used, "skipped": skipped,
            "monthly": round(sum(u["amount"] for u in used), 2), "changes": changes}


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
        gaps = [(b - a).days for a, b in itertools.pairwise(ds) if (b - a).days > 0]
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
