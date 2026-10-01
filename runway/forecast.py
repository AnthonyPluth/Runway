"""Cash-flow projection.

Model, per cash account (checking/savings marked "in forecast"):
  start balance (the bank's posted balance plus what's pending)
  + recurring items (paychecks, mortgage, bills) on their dates
  - each credit card's payment on its due date, sized to the statement balance
  - average everyday spending, spread evenly per day

A card's statement balance comes from the issuer (or it's the amount you entered, if you know it). Statements that
haven't closed yet are estimated from the card's average spending over its last 3 statement cycles (for the cycle in
progress, what's already been charged plus the average's share of the days left), plus the recurring charges on the
card the average doesn't have, and flagged as estimates.

Each card is paid the way you pay it (Settings → Accounts): the whole statement (the default), the issuer's minimum, or
a fixed amount. What isn't paid carries into the next statement, with a month's interest at the card's APR if you've
entered one.
"""
from __future__ import annotations

import calendar
import itertools
import json
from collections import defaultdict
import statistics
from datetime import date, datetime, timedelta
from typing import Literal

from dateutil.relativedelta import relativedelta
from dateutil.rrule import MONTHLY, WEEKLY, YEARLY, rrule, rruleset
from sqlalchemy import func, or_, select, update

from . import bankdays, budgets, db, plaidbank, splits
from . import categories as catmod
from . import settings_keys as sk
from . import recurring as rec
from .models import Account, Budget, Category, Override, PlaidAccount, Recurring, Transaction

SPEND_WINDOW_DAYS = 90
AVG_CYCLES = 3           # statement cycles averaged to estimate a card's future statements
ONE_OFF_LIMIT = 1000.0   # single outflows larger than this are treated as one-offs, not everyday spending
PAY_MODES = ("full", "minimum", "fixed")   # how a card's statements are paid (payment_plan)
MIN_PAYMENT_FLOOR = 25.0   # without the issuer's minimum: the larger of this and MIN_PAYMENT_RATE of the statement
MIN_PAYMENT_RATE = 0.02


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


def _monthly_rule(freq: Literal[0, 1], interval: int, dtstart: datetime, day: int, **kw) -> rrule:
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
    return {r["name"] for r in conn.execute(select(Category.name).where(Category.is_transfer == 1))}


def owed(account: dict, balance: float | None = None) -> float:
    b = account["balance"] if balance is None else balance
    return b if account.get("owed_positive") else -b


def paid_by_recurring(items: list[dict]):
    """A test for whether a transaction looks like a payment for one of these recurring items without being linked to
    it: its payee or description has the item's match text and its amount is close to the item's (recurring.amount_range,
    the tolerance linking uses; any amount for an item without one). So a "Prime" item matching "amazon" leaves the rest
    of the Amazon orders alone."""
    rules = [(m, rec.amount_range(r)) for r in items if (m := rec.match_text(r))]

    def test(t: dict) -> bool:
        hay = f"{t['payee'] or ''} {t['description'] or ''}".lower()
        return any(m in hay and (span is None or span[0] <= abs(t["amount"]) <= span[1]) for m, span in rules)
    return test


def daily_spend_rate(conn, account_id: str, today: date, recurring: list[dict] | None = None) -> float:
    """Average everyday outflow per day over the last SPEND_WINDOW_DAYS (or the history available), leaving out
    payments for the account's recurring items (`recurring`), which the forecast has on their own dates."""
    transfers = _transfer_categories(conn)
    since = today - timedelta(days=SPEND_WINDOW_DAYS)
    T = Transaction
    txs = db.rows(conn.execute(
        select(T.posted, T.amount, T.payee, T.description, T.category)
        .where(T.account_id == account_id, T.posted > since.isoformat(), T.posted <= today.isoformat(), T.pending == 0,
               func.coalesce(T.recurring_id, 0) == 0)
    ))
    if not txs:
        return 0.0
    first = min(_d(t["posted"]) for t in txs)
    days = max(14, (today - first).days + 1)  # avoid inflating the rate from a few days of data
    is_recurring = paid_by_recurring(recurring or [])
    total = 0.0
    for t in txs:
        if t["category"] in transfers or is_recurring(t):
            continue
        if t["amount"] < 0 and -t["amount"] <= ONE_OFF_LIMIT:
            total += -t["amount"]
        elif t["amount"] > 0 and t["category"] == "Refunds":
            total -= t["amount"]
    return max(0.0, total / days)


def large_one_offs(conn, account_ids: list[str], today: date, recurring: list[dict]) -> list[dict]:
    """Outflows over ONE_OFF_LIMIT in the last SPEND_WINDOW_DAYS that everyday spending leaves out and no recurring item
    accounts for: not transfers, not linked to an item (nor marked "not recurring"), and not a likely payment for one."""
    transfers = _transfer_categories(conn)
    T = Transaction
    txs = db.rows(conn.execute(
        select(T.account_id, T.amount, T.payee, T.description, T.category)
        .where(T.account_id.in_(account_ids), T.posted > (today - timedelta(days=SPEND_WINDOW_DAYS)).isoformat(),
               T.posted <= today.isoformat(), T.pending == 0, T.amount < -ONE_OFF_LIMIT, T.recurring_id.is_(None))
        .order_by(T.amount)))
    tests = {a: paid_by_recurring([r for r in recurring if r["account_id"] == a]) for a in account_ids}
    return [t for t in txs if t["category"] not in transfers and not tests[t["account_id"]](t)]


def pending_total(conn, account: dict) -> float:
    """What's pending on an account (money out negative) that its balance doesn't have yet. Most banks' balance leaves
    pending out, but some include it. Banks take pending debits out of the available balance but not pending credits,
    so when the bank reports one, pending debits are only added if that brings the balance closer to it (otherwise the
    balance evidently has them already); pending credits are always added."""
    T = Transaction
    out, came_in = (conn.execute(select(func.coalesce(func.sum(T.amount), 0.0))
                                 .where(T.account_id == account["id"], T.pending == 1, cond)).scalar() or 0.0
                    for cond in (T.amount < 0, T.amount > 0))
    available = account.get("available")
    if available is not None and abs(account["balance"] + out - available) >= abs(account["balance"] - available):
        out = 0.0
    return out + came_in


def card_monthly_spend(conn, card: dict, last_close: date) -> dict:
    """Average spending per statement cycle over the last AVG_CYCLES closed cycles (only cycles fully covered by
    the transaction history). Charges minus refunds; payments and transfers don't count."""
    transfers = _transfer_categories(conn)
    first = conn.execute(select(func.min(Transaction.posted)).where(Transaction.account_id == card["id"])).fetchone()[0]
    cycles = []
    end = last_close
    for _ in range(AVG_CYCLES):
        start = add_months(end, -1, card["closing_day"])
        if not first or _d(first) > start + timedelta(days=3):   # history doesn't reach back this far
            break
        txs = conn.execute(select(Transaction.amount, Transaction.category)
                           .where(Transaction.account_id == card["id"], Transaction.posted > start.isoformat(),
                                  Transaction.posted <= end.isoformat(), Transaction.pending == 0)).fetchall()
        spent = -sum(t["amount"] for t in txs if t["category"] not in transfers)
        cycles.append({"start": start.isoformat(), "end": end.isoformat(), "spent": round(max(0.0, spent), 2)})
        end = start
    avg = sum(c["spent"] for c in cycles) / len(cycles) if cycles else None
    return {"average": round(avg, 2) if avg is not None else None, "cycles": cycles}   # newest cycle first


def statement_override(conn, card_id: str, close: date) -> float | None:
    r = conn.execute(select(Override.amount).where(Override.key == f"stmt:{card_id}:{close.isoformat()}")).fetchone()
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
    counted when that account pays no other card, so a payment can't be mistaken for another card's (a card that
    hasn't been given a paying account yet could be paid from it too)."""
    payer = card.get("pay_from")
    if not payer or conn.execute(
            select(func.count()).select_from(Account)
            .where(Account.kind == "credit", Account.hidden == 0, or_(Account.pay_from == payer, Account.pay_from.is_(None)),
                   Account.id != card["id"])).fetchone()[0]:
        return 0.0
    T = Transaction
    sent = conn.execute(select(T.posted, T.amount)
                        .where(T.account_id == payer, T.category == "Credit Card Payment", T.amount < 0,
                               T.posted > last_close.isoformat()).order_by(T.posted)).fetchall()
    if not sent:
        return 0.0
    # The card's payments, from a little before the close: one can reach the card before it leaves the bank.
    unclaimed = db.rows(conn.execute(
        select(T.posted, T.amount)
        .where(T.account_id == card["id"], T.amount > 0, T.posted > (last_close - timedelta(days=5)).isoformat(),
               T.category.in_(select(Category.name).where(Category.is_transfer == 1))).order_by(T.posted)))
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


def _amount(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


def payment_plan(conn, card_id: str) -> dict:
    """How a card's statements are paid, as set in Settings → Accounts: pay_mode "full" (the default), "minimum" (the
    issuer's minimum payment) or "fixed" (pay_amount toward each statement), and the card's APR in percent (None if it
    hasn't been entered)."""
    mode = db.get_setting(conn, sk.card_pay_mode(card_id)) or "full"
    return {"pay_mode": mode if mode in PAY_MODES else "full",
            "pay_amount": _amount(db.get_setting(conn, sk.card_pay_amount(card_id))),
            "apr": _amount(db.get_setting(conn, sk.card_apr(card_id)))}


def statement_payment(plan: dict, statement: float, minimum: float | None = None) -> float:
    """What a payment plan (payment_plan) pays toward a statement: all of it; the minimum (the issuer's, else the larger
    of MIN_PAYMENT_FLOOR and MIN_PAYMENT_RATE of the statement); or the fixed amount (all of it until one is entered).
    Never more than the statement."""
    if plan["pay_mode"] == "minimum":
        due = minimum if minimum is not None else max(MIN_PAYMENT_FLOOR, statement * MIN_PAYMENT_RATE)
    elif plan["pay_mode"] == "fixed" and plan["pay_amount"] is not None:
        due = plan["pay_amount"]
    else:
        due = statement
    return max(0.0, min(statement, due))


def interest(plan: dict, carried: float) -> float:
    """A statement cycle's interest on a balance carried from the one before, at the card's APR (none without one)."""
    return carried * plan["apr"] / 100 / 12 if plan["apr"] and carried > 0 else 0.0


def card_cycle(conn, card: dict, today: date, bank) -> dict:
    """Where a card stands in its billing cycle today, from its latest statement at the bank (see bank_statement)."""
    transfers = _transfer_categories(conn)
    last_close = _d(bank["last_statement_date"])
    T = Transaction
    txs = db.rows(conn.execute(
        select(T.posted, T.amount, T.category, T.pending)
        .where(T.account_id == card["id"], T.posted > last_close.isoformat()).order_by(T.posted)
    ))
    reported = max(0.0, bank["last_statement_balance"] or 0.0)
    known = statement_override(conn, card["id"], last_close)
    statement = known if known is not None else reported   # a figure you entered wins over the bank's
    paid = sum(t["amount"] for t in txs if t["amount"] > 0 and t["category"] in transfers)
    paid += in_transit(conn, card, last_close)
    # Paying more than the statement (the current balance, say) pays off some of the next one already.
    over = max(0.0, paid - statement)
    new_charges = max(0.0, -sum(t["amount"] for t in txs if t["category"] not in transfers) - over)
    due = _d(bank["next_due_date"]) if bank["next_due_date"] and _d(bank["next_due_date"]) > last_close \
        else next_after(last_close, card["due_day"])
    spend = card_monthly_spend(conn, card, last_close)
    remaining = max(0.0, statement - paid)
    plan = payment_plan(conn, card["id"])
    # What the plan pays toward this statement, less what's been paid since it closed; the rest carries into the next.
    payment = min(remaining, max(0.0, statement_payment(plan, statement, bank["minimum_payment"]) - paid))
    return {
        "last_close": last_close.isoformat(),
        "statement_balance": round(statement, 2),
        "statement_reported": round(reported, 2),
        "statement_set": known is not None,
        "minimum_payment": bank["minimum_payment"],
        "statement_key": f"stmt:{card['id']}:{last_close.isoformat()}",
        "avg_monthly_spend": spend["average"],
        "avg_cycles": len(spend["cycles"]),
        # the close of the oldest cycle averaged: a recurring charge first paid after it isn't in every cycle of the average
        "avg_first_close": spend["cycles"][-1]["end"] if spend["cycles"] else None,
        "paid_since_close": round(paid, 2),
        "remaining": round(remaining, 2),
        **plan,
        "payment": round(payment, 2),               # what the forecast pays on the due date
        "carried": round(remaining - payment, 2),   # what that leaves to carry into the next statement
        # paying the minimum, but the issuer didn't say what it is (statement_payment's estimate is used)
        "minimum_estimated": plan["pay_mode"] == "minimum" and bank["minimum_payment"] is None,
        "due_date": due.isoformat(),
        "new_charges": round(new_charges, 2),
        "daily_rate": round(daily_spend_rate(conn, card["id"], today), 2),
    }


# ------------------------------------------------------------------------------------------------ forecast

def build(conn, today: date | None = None, horizon_days: int = 90) -> dict:
    today = today or date.today()
    end = today + timedelta(days=horizon_days)
    accounts = db.rows(conn.execute(select(Account).where(Account.hidden == 0).order_by(Account.name)))
    by_id = {a["id"]: a for a in accounts}
    cash_like = [a for a in accounts if a["kind"] in ("checking", "savings")]
    primary = by_id.get(db.get_setting(conn, sk.PRIMARY_ACCOUNT) or "")
    if primary and primary["kind"] in ("checking", "savings"):
        cash = [primary]
    elif len([a for a in cash_like if a["kind"] == "checking"]) == 1:
        cash = [a for a in cash_like if a["kind"] == "checking"]  # only one checking account: that's the primary
    else:
        cash = [a for a in cash_like if a["in_forecast"]]
    # Each account starts from the bank's balance plus what's pending on it that the balance doesn't have yet, money in
    # and out (see pending_total). A pending payment
    # already linked to a recurring item counts as having happened (see below), so it's in the balance and not again
    # as an event.
    pending = {a["id"]: round(pending_total(conn, a), 2) for a in cash}
    cash = [dict(a, balance=a["balance"] + pending[a["id"]]) for a in cash]
    by_id.update({a["id"]: a for a in cash})
    cards = [a for a in accounts if a["kind"] == "credit"]
    # Link anything that's come in since the last sync's matching (a payment that posted today), so it isn't
    # forecast again as still to come. One UPDATE per item, over unlinked transactions.
    rec.auto_match(conn)
    recurring = db.rows(conn.execute(select(Recurring).where(Recurring.active == 1)))
    items_by_id = {r["id"]: r for r in recurring}
    T = Transaction
    most_used = (func.count().desc(), func.max(T.posted).desc())   # the category used most (then most recently)

    events: list[dict] = []
    warnings: list[dict] = []   # {"text", "href"}: what's wrong, and the page where it's put right
    card_status: list[dict] = []
    old_keys: dict[str, str] = {}   # card payment key -> the key it had before keys followed the closing date
    first_paid: dict[int, str | None] = {}   # recurring item -> its first linked payment's date
    unlinked: list[dict] = []   # cards without statements from the issuer (not linked through Plaid yet)

    def warn(text: str, href: str) -> None:
        warnings.append({"text": text, "href": href})

    # One-off edits you've made to specific upcoming items.
    overrides = {r["key"]: r["amount"] for r in conn.execute(select(Override.key, Override.amount))}

    def paying(info: dict, owes: float, planned: float, key: str, old_key: str) -> float:
        """What's paid toward a card statement that still owes `owes`, for what carries into the next one: what its
        payment plan pays (`planned`), or, when you've edited that payment on the Overview, what you entered, up to what's
        owed. A card paid in full carries nothing either way, as before."""
        edit = overrides.get(key, overrides.get(old_key))
        return min(owes, abs(edit)) if info["pay_mode"] != "full" and edit is not None else planned

    def not_averaged(item_id: int, since: str) -> bool:
        """Whether a card's spending average (over cycles from the one closing on `since`) leaves out this recurring
        charge: it comes less often than monthly (a yearly insurance premium), or its first payment came after that
        oldest cycle (a new subscription)."""
        if items_by_id[item_id]["frequency"] not in ("weekly", "biweekly", "semimonthly", "monthly"):
            return True
        if item_id not in first_paid:
            first_paid[item_id] = conn.execute(select(func.min(T.posted)).where(T.recurring_id == item_id)).scalar()
        started = first_paid[item_id]
        return not started or started[:10] > since

    for item in recurring:
        if item["account_id"] not in by_id:
            continue
        history = rec.matched(conn, item["id"])
        amount = rec.expected_amount(item, history)
        cat_row = conn.execute(
            select(T.category).where(T.recurring_id == item["id"], T.category.is_not(None))
            .group_by(T.category).order_by(*most_used).limit(1)).fetchone()
        if not cat_row:   # nothing linked to it yet: the category of what it matches on its account
            like = "%" + (item["match"] or item["name"] or "").lower().replace("%", "").replace("_", "") + "%"
            cat_row = conn.execute(
                select(T.category).where(T.account_id == item["account_id"], T.category.is_not(None), func.length(like) > 4,
                                         or_(func.lower(T.payee).like(like), func.lower(T.description).like(like)))
                .group_by(T.category).order_by(*most_used).limit(1)).fetchone()
        # Anything due in the last matching window that hasn't shown up yet is still coming: it goes on today, as
        # late (older than the window, it's "missed" in Recurring instead). Due today counts as due, not late.
        window = rec.MATCH_WINDOW_DAYS.get(item["frequency"], 6)
        first_tx = conn.execute(select(func.min(T.posted)).where(T.account_id == item["account_id"])).fetchone()[0]
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
        due = _d(info["due_date"])
        key = f"cardclose:{card['id']}:{info['last_close']}"
        old_keys[key] = f"card:{card['id']}:{due.isoformat()}"
        payment = paying(info, info["remaining"], info["payment"], key, old_keys[key])
        info.update(payment=round(payment, 2), carried=round(info["remaining"] - payment, 2))
        card_status.append(info)
        payer = by_id.get(card["pay_from"] or "")
        if not payer:
            warn(f"{label}: choose which account pays it in Settings.", "#setup/accounts")
            continue
        if payer not in cash:
            continue  # paid from an account that isn't being forecast
        if info["pay_mode"] == "fixed" and info["pay_amount"] is None:
            warn(f"{label}: no amount entered for its fixed payment, so the forecast pays each statement in full.", "#setup/accounts")
        if info["minimum_estimated"] and info["remaining"] > 0.005:
            warn(f"{label}: the bank didn’t report a minimum payment, so the forecast pays the larger of "
                 f"${MIN_PAYMENT_FLOOR:,.0f} and {MIN_PAYMENT_RATE:.0%} of the statement.", "#setup/accounts")
        pays = bankdays.next_business_day(due)   # a due date on a weekend or holiday is paid the next business day
        if pays >= today and info["payment"] > 0.005:
            events.append({"date": pays.isoformat(), "account_id": payer["id"], "name": f"{label} statement",
                           "amount": -info["payment"], "kind": "card", "estimated": False,
                           "key": key, "category": "Credit Card Payment", "card_id": card["id"]})
        elif pays < today and info["payment"] > 0.005:
            warn(f"{label}: ${info['payment']:,.2f} was due {due:%b %-d} and no payment has shown up yet.", "#setup/accounts")
        # Future statements: the card's average spending per cycle over its last few statements (for the cycle in
        # progress, what's been charged already plus the average's share of the days left). Without enough history,
        # the recent daily rate. Either way, plus the recurring charges on the card the estimate doesn't already have.
        # A statement that isn't paid in full carries the rest into the next one, with a month's interest on it.
        close = next_after(_d(info["last_close"]), card["closing_day"])
        prev_close = _d(info["last_close"])
        first = True
        avg = info["avg_monthly_spend"]
        stale = False
        carried = info["carried"]
        carries = carried > 0.005   # whether any statement in the forecast carries a balance
        while True:
            due_k = next_after(close, card["due_day"])
            if due_k > end:
                break
            # The card's recurring charges due this cycle that haven't posted yet (events are dated today or later).
            upcoming = [e for e in events if e["account_id"] == card["id"] and e["kind"] == "recurring"
                        and prev_close.isoformat() < e["date"] <= close.isoformat()]
            if avg is not None:
                if first:
                    left = max(0, (close - today).days) / max(1, (close - prev_close).days)
                    est = info["new_charges"] + avg * min(1.0, left)
                else:
                    est = avg
                est += max(0.0, -sum(e["amount"] for e in upcoming if not_averaged(e["recurring_id"], info["avg_first_close"])))
            else:
                days_in_cycle = max(0, (close - max(today, prev_close)).days)
                # The recent daily rate leaves out recurring charges on the card, so add all of them.
                est = (info["new_charges"] if first else 0.0) + info["daily_rate"] * days_in_cycle \
                    + max(0.0, -sum(e["amount"] for e in upcoming))
            statement = carried + interest(info, carried) + est
            key = f"cardclose:{card['id']}:{close.isoformat()}"
            old_keys[key] = f"card:{card['id']}:{due_k.isoformat()}"
            pay = paying(info, statement, statement_payment(info, statement), key, old_keys[key])
            carried = statement - pay
            carries = carries or carried > 0.005
            pays_k = bankdays.next_business_day(due_k)
            if pays_k < today:
                stale = True   # the issuer's latest statement is older than this one; nothing to put on the chart
            elif pay > 0.005:
                events.append({"date": pays_k.isoformat(), "account_id": payer["id"], "name": f"{label} statement",
                               "amount": -round(pay, 2), "kind": "card", "estimated": True,
                               "key": key, "category": "Credit Card Payment", "card_id": card["id"]})
            prev_close, close, first = close, next_after(close, card["closing_day"]), False
        if stale:
            warn(f"{label}: the bank hasn't sent the statement after {_d(info['last_close']):%b %-d} yet, so its "
                 "payment isn't in the forecast.", "#setup/connections")
        if carries and info["apr"] is None:
            warn(f"{label}: the forecast carries part of its statements to the next one, but doesn’t count the interest "
                 "on it: enter the card’s APR in Settings.", "#setup/accounts")

    def listed(names):
        return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    not_linked = [c["name"] for c in unlinked if not c["linked"]]
    no_statement = [c["name"] for c in unlinked if c["linked"]]
    if not_linked:
        waiting = conn.execute(
            select(func.count()).select_from(PlaidAccount)
            .where(PlaidAccount.type == "credit", PlaidAccount.ignored == 0, PlaidAccount.plaid_account_id.not_in(
                select(Account.plaid_account_id).where(Account.plaid_account_id.is_not(None))))).fetchone()[0]
        one = len(not_linked) == 1
        # Cards Plaid already has are matched in Settings → Accounts; otherwise the bank needs connecting first.
        warn(f"{listed(not_linked)} {'isn’t' if one else 'aren’t'} linked through Plaid yet, so "
             f"{'its payments aren’t' if one else 'their payments aren’t'} in the forecast. "
             + (f"Plaid has {waiting} card{'s' if waiting != 1 else ''} waiting to be matched: in Settings → Accounts, "
                f"choose “Same as …” for each under “New from Plaid”." if waiting else f"Link {'it' if one else 'them'} to get statements and due dates."),
             "#setup/accounts" if waiting else "#setup/connections")
    if no_statement:
        one = len(no_statement) == 1
        warn(f"Plaid hasn’t sent a statement for {listed(no_statement)} yet, so {'its payments aren’t' if one else 'their payments aren’t'} "
             "in the forecast. It usually arrives with the next sync.", "#setup/connections")
    # Everyday spending leaves out big one-off payments; if they come back (rent paid by hand, tuition), they need to
    # be recurring items to be in the forecast.
    big = large_one_offs(conn, [a["id"] for a in cash], today, recurring)
    if big:
        payees = [p for p in dict.fromkeys((t["payee"] or t["description"] or "").strip() for t in big) if p]   # biggest first
        one = len(big) == 1
        named = f" ({', '.join(payees[:3])}{'…' if len(payees) > 3 else ''})" if payees else ""
        warn(f"{len(big)} payment{'' if one else 's'} over ${ONE_OFF_LIMIT:,.0f} in the last {SPEND_WINDOW_DAYS} days "
             f"{'isn’t' if one else 'aren’t'} in the forecast{named}; add {'it as a recurring item' if one else 'them as recurring items'}.",
             "#budget/recurring")

    for new, old in old_keys.items():
        # A card payment's edit saved while its key was the due date moves to its key now, so it still applies (and
        # putting it back, which removes the event's key, removes it).
        if old in overrides and new not in overrides:
            conn.execute(update(Override).where(Override.key == old).values(key=new))
            overrides[new] = overrides.pop(old)
    for e in events:
        if e.get("key") in overrides:
            e["original_amount"], e["amount"], e["overridden"] = e["amount"], round(overrides[e["key"]], 2), True
    # Only what lands on the chart, today through its last day (a payment moved off a weekend can land past it).
    events = [e for e in events if today.isoformat() <= e["date"] <= end.isoformat()]

    series_by_acct: dict[str, list[float]] = {}
    rates: dict[str, float] = {}
    usual: dict[str, float] = {}   # each account's everyday spending, taken out or not (for "about $42 a day")
    for a in cash:
        spent = daily_spend_rate(conn, a["id"], today, [r for r in recurring if r["account_id"] == a["id"]])
        usual[a["id"]] = round(spent, 2)
        rate = spent if a["daily_spend"] else 0.0
        rates[a["id"]] = round(rate, 2)
        by_day: dict[str, float] = {}
        for e in events:
            if e["account_id"] == a["id"]:
                by_day[e["date"]] = by_day.get(e["date"], 0.0) + e["amount"]
        bal = a["balance"] + by_day.get(today.isoformat(), 0.0)   # anything due today that hasn't posted yet
        series = [round(bal, 2)]
        for i in range(1, horizon_days + 1):
            when = (today + timedelta(days=i)).isoformat()
            bal += by_day.get(when, 0.0) - rate
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
             "pending": pending[a["id"]],   # in the balance already: the bank's posted balance plus this
             "daily_spend": rates.get(a["id"], 0.0), "daily_spend_on": bool(a["daily_spend"]),
             "daily_spend_estimate": usual[a["id"]], "series": series_by_acct[a["id"]],
             "low": low(series_by_acct[a["id"]])}
            for a in cash
        ],
        "total": total,
        "low": low(total),
        "events": events,
        "cards": card_status,
        "unlinked_cards": unlinked,
        "warnings": [w["text"] for w in warnings],   # as plain text, as before (MCP clients read these)
        "warning_links": warnings,
        "budget": scenario,
    }


# ------------------------------------------------------------------------------------------------ sticking to the budget

def budget_plan(conn, today: date) -> list[dict]:
    """Each budget that counts (a parent's budget covers its subcategories), with what's been spent this month and the
    account it's paid with: the one you chose, else the account used most for it over the last 90 days."""
    cats = catmod.all_categories(conn)
    by_name = {c["name"]: c for c in cats}
    budgets = {r["category"]: r for r in db.rows(conn.execute(select(Budget)))}
    p = splits.parts()
    month_start = today.replace(day=1).isoformat()
    since = (today - timedelta(days=90)).isoformat()
    out = []
    for name, b in budgets.items():
        c = by_name.get(name)
        if not c or c["is_transfer"] or c["is_income"] or any(p in budgets for p in c["path"][:-1]):
            continue
        names = [name] + [k["name"] for k in cats if name in k["path"][:-1]]
        base = (p.c.category.in_(names), Account.hidden == 0, Account.kind.in_(["checking", "savings", "credit"]))
        spent = -(conn.execute(
            select(func.coalesce(func.sum(p.c.amount), 0)).select_from(p).join(Account, Account.id == p.c.account_id)
            .where(*base, p.c.posted >= month_start, p.c.posted <= today.isoformat())).fetchone()[0] or 0.0)
        s = func.sum(-p.c.amount).label("s")
        usual = conn.execute(
            select(p.c.account_id, s).join(Account, Account.id == p.c.account_id)
            .where(*base, p.c.posted > since, p.c.amount < 0).group_by(p.c.account_id).order_by(s.desc()).limit(1)).fetchone()
        out.append({"category": name, "amount": b["amount"], "names": names, "spent": round(max(0.0, spent), 2),
                    "pay_with": b.get("pay_with"), "usual": usual["account_id"] if usual else None})
    return out


def budget_scenario(conn, today: date, horizon_days: int, dates: list[str], cash: list[dict], by_id: dict,
                    card_status: list[dict], events: list[dict]) -> dict | None:
    """The forecast if you spend exactly your budgets: budgeted spending is charged day by day to each category's
    account; spending on cards is paid on each card's due date, as much of each statement as the card's payment plan
    pays (payment_plan; the rest carries over, as in the forecast). Recurring items and statements that have already closed
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
    # A budget that rolls over has this month's carry-over to spend too, as the Budget page's "available" says.
    carried = budgets.budget_carry(conn, [c for c in catmod.all_categories(conn) if not c["is_transfer"] and not c["is_income"]],
                                   {r["category"]: r for r in db.rows(conn.execute(select(Budget)))}, today.replace(day=1))

    def covered(p: dict, month: str) -> float:
        return sum(recurring.get((n, month), 0.0) for n in p["names"])

    def left(p: dict, month: str) -> float:
        """The month's budget not already covered by its recurring payments (this month: plus what's carried over, less
        what's been spent)."""
        if month == this_month:
            return max(0.0, p["amount"] + carried.get(p["category"], 0.0) - p["spent"] - covered(p, month))
        return max(0.0, p["amount"] - covered(p, month))

    for p in plan:
        acct = p["pay_with"] or p["usual"] or cash[0]["id"]
        if acct not in cash_ids and acct not in cards:
            skipped.append({"category": p["category"], "reason": "its account isn't in the forecast"})
            continue
        if acct in cards and by_id[acct]["pay_from"] not in cash_ids:
            skipped.append({"category": p["category"], "reason": "its card isn't paid from a forecast account"})
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
        carried = info["carried"]   # paid the way the forecast pays it: what that doesn't pay carries over, with interest
        while True:
            due = next_after(close, card["due_day"])
            if due.isoformat() > dates[-1]:
                break
            amt = sum(v for d, v in days.items() if prev.isoformat() < d <= close.isoformat()) + (info["new_charges"] if first else 0.0)
            statement = carried + interest(info, carried) + amt
            pay = statement_payment(info, statement)
            carried = statement - pay
            if pay > 0.005:
                paid = bankdays.next_business_day(due).isoformat()
                extra.append((payer, paid, -round(pay, 2)))
                if paid <= dates[-1]:
                    changes.append({"date": paid, "account_id": payer, "kind": "card", "name": f"{db.account_label(card)} statement",
                                    "amount": -round(pay, 2), "account": db.account_label(by_id[payer]),
                                    # what it's made of: charges already on the card (first statement), plus budgeted ones
                                    "charged": round(info["new_charges"], 2) if first else 0.0})
            prev, close, first = close, next_after(close, card["closing_day"]), False

    by_day: dict[tuple, float] = defaultdict(float)
    for e in base:
        by_day[(e["account_id"], e["date"])] += e["amount"]
    for acct, when, v in extra:
        by_day[(acct, when)] += v
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

def suggestion_key(account_id: str, match: str, frequency: str) -> str:
    """What identifies a suggestion across visits, so one marked "not recurring" stays gone (amount and dates drift)."""
    return f"{account_id}|{match.strip().lower()}|{frequency}"


def dismissed_suggestions(conn) -> set[str]:
    try:
        keys = json.loads(db.get_setting(conn, sk.RECURRING_SUGGESTIONS_DISMISSED) or "[]")
    except ValueError:
        return set()
    return {k for k in keys if isinstance(k, str)} if isinstance(keys, list) else set()


def dismiss_suggestion(conn, key: str) -> None:
    db.set_setting(conn, sk.RECURRING_SUGGESTIONS_DISMISSED, json.dumps(sorted(dismissed_suggestions(conn) | {key})))


def suggest_recurring(conn, today: date | None = None, lookback_days: int = 150) -> list[dict]:
    """Payees on cash accounts that show up on a regular schedule with similar amounts, minus the ones you've dismissed."""
    today = today or date.today()
    dismissed = dismissed_suggestions(conn)
    transfers = _transfer_categories(conn)
    known = [(r["account_id"], (r["match"] or r["name"]).lower()) for r in conn.execute(select(Recurring))]
    T = Transaction
    txs = db.rows(conn.execute(
        select(T.account_id, T.posted, T.amount, T.payee, T.category).join(Account, Account.id == T.account_id)
        .where(Account.kind.in_(["checking", "savings"]), T.pending == 0, func.coalesce(T.recurring_id, 0) == 0,
               T.posted > (today - timedelta(days=lookback_days)).isoformat())
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
        key = suggestion_key(acct, payee, freq)
        if key in dismissed:
            continue
        out.append({"key": key, "account_id": acct, "name": items[-1]["payee"], "match": payee, "amount": round(med, 2),
                    "frequency": freq, "anchor_date": ds[-1].isoformat(), "count": len(items)})
    out.sort(key=lambda s: -abs(s["amount"]))
    return out
