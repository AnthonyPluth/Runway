"""Cash-flow projection.

Model, per cash account (checking/savings marked "in forecast"):
  start balance (the bank's posted balance plus what's pending)
  + recurring items (paychecks, mortgage, bills) on their dates
  - the budgets paid from the account, spent day by day on banking days (a weekend's or holiday's on the next one)
  - each credit card's payment on its due date, sized to the statement balance

Nothing is taken out for spending that isn't scheduled or budgeted: there's no average of past spending.

A card's statement balance comes from the issuer (or it's the amount you entered, if you know it). Statements that
haven't closed yet are estimated, and flagged as estimates: what's been charged since the last one closed, plus the
budgets paid with the card (spent on it day by day), its recurring charges and any annual fee. A charge on the card in
the category of a budget charged to it is in that budget already; any other is on the statement, and comes off its
category's budget wherever that's paid from, so it counts once. A card with no statement yet that has budgets, recurring
charges or a fee charged to it is taken to close at each month's end (NO_STATEMENT_DUE_DAYS). Each estimated statement
says what it's made of (estimate_parts). An amount you've changed is yours, not an estimate.

A budget goes on its category's account (Settings → Categories), else the one used most for it. A subcategory's budget
with an account of its own goes on that account, and only the rest of its parent's budget on the parent's (budget_days),
so the budgets still come to the same each day.

Each card is paid the way you pay it (Settings → Accounts): the whole statement (the default), the issuer's minimum, or
a fixed amount. What isn't paid carries into the next statement, with a month's interest at the card's APR if you've
entered one.

A churning card's annual fee (churning.py) is a charge on its card in its anniversary month: it's added to the statement
it lands on, so it reaches cash through that statement's payment, and it's listed under "fees" (see annual_fees).
"""
from __future__ import annotations

import calendar
import itertools
import json
import re
from collections import defaultdict
import statistics
import urllib.parse
from collections.abc import Iterable, Iterator
from datetime import date, datetime, timedelta
from typing import Literal

from dateutil.rrule import MONTHLY, WEEKLY, YEARLY, rrule, rruleset
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import OperationalError

from . import bankdays, budgets, churning, db, plaidapi, plaidbank, simplefin, splits, statements
from .dates import month_end, month_start, next_after, parse_day
from .money import CENT, allocate_cents, cents, is_zero, same_amount
from . import categories as catmod
from . import settings_keys as sk
from . import recurring as rec
from .models import Account, Budget, Category, ChurnCard, Override, PlaidAccount, Recurring, Transaction

SPEND_WINDOW_DAYS = 90
ONE_OFF_LIMIT = 1000.0   # single outflows larger than this are treated as one-offs, not everyday spending
PAY_MODES = ("full", "minimum", "fixed")   # how a card's statements are paid (payment_plan)
MIN_PAYMENT_FLOOR = 25.0   # without the issuer's minimum: the larger of this and MIN_PAYMENT_RATE of the statement plus
MIN_PAYMENT_RATE = 0.01    # its interest, as issuers work it out (so paying the minimum never lets the balance grow)
NO_STATEMENT_DUE_DAYS = 25   # a statement with no due date to go by is due this long after it closes (a card with no
                             # statement yet: after its assumed month-end close)
FEE_CATEGORY = "Fees & Interest"   # an annual fee's category, when there's one by that name
FEE_EARLY_DAYS = 45        # a fee charged from this many days before its anniversary on is that anniversary's
FEE_TEXT = re.compile(r"(?:annual|membership)\s+(?:membership\s+)?fee", re.I)   # "Annual Fee", "Annual Membership Fee"


# ------------------------------------------------------------------------------------------------ dates

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
    anchor = datetime.combine(parse_day(item["anchor_date"]), datetime.min.time())
    freq = item["frequency"]
    rules = rruleset()
    if freq in ("weekly", "biweekly"):
        rules.rrule(rrule(WEEKLY, interval=1 if freq == "weekly" else 2, dtstart=anchor))
    elif freq in ("monthly", "quarterly", "semiannual", "yearly"):
        months = {"monthly": 1, "quarterly": 3, "semiannual": 6, "yearly": 12}[freq]
        rules.rrule(_monthly_rule(MONTHLY, months, anchor, anchor.day))
    elif freq == "once":
        rules.rdate(anchor)   # a one-time item: just its date
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
    stop = min(end, parse_day(item["end_date"])) if item.get("end_date") else end
    if stop <= start:
        return []
    lo = datetime.combine(start + timedelta(days=1), datetime.min.time())
    hi = datetime.combine(stop, datetime.min.time())
    return [d.date() for d in schedule(item).between(lo, hi, inc=True)]


def occurrences(item: dict, start: date, end: date) -> list[date]:
    """Dates in (start, end] on which a recurring item's money actually moves: its scheduled dates, moved off weekends
    and bank holidays (money in to the business day before, money out to the one after; see bankdays)."""
    stop = min(end, parse_day(item["end_date"])) if item.get("end_date") else end
    if stop <= start:
        return []
    money_in = (item.get("amount") or 0) > 0
    # Look a few days beyond the window: a date just outside it can move inside, and the other way round.
    lo = datetime.combine(start - timedelta(days=6), datetime.min.time())
    hi = datetime.combine(stop + timedelta(days=6), datetime.min.time())
    out = []
    for d in schedule(item).between(lo, hi, inc=True):
        nominal = d.date()
        if item.get("end_date") and nominal > parse_day(item["end_date"]):
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
    it: its payee or description has one of the item's texts and its amount is within the item's amount range, if it has
    one (recurring.fits_amount, as matching does), and for a one-time item it's around its date (recurring.near_date). So
    a "Prime" item matching "amazon" with a range leaves the rest of the Amazon orders alone."""
    rules = [(texts, r) for r in items if (texts := rec.match_texts(r))]

    def test(t: dict) -> bool:
        hay = f"{t['payee'] or ''} {t['description'] or ''}".lower()
        return any(any(m in hay for m in texts) and rec.fits_amount(r, t["amount"]) and rec.near_date(r, t["posted"])
                   for texts, r in rules)
    return test


def large_one_offs(conn, account_ids: list[str], today: date, recurring: list[dict]) -> list[dict]:
    """Outflows over ONE_OFF_LIMIT in the last SPEND_WINDOW_DAYS from the forecast's accounts that no recurring item
    accounts for: not transfers, not linked to an item (nor marked "not recurring"), and not a likely payment for one."""
    transfers = _transfer_categories(conn)
    T = Transaction
    txs = db.rows(conn.execute(
        select(T.account_id, T.posted, T.amount, T.payee, T.description, T.category)
        .where(T.account_id.in_(account_ids), T.posted > (today - timedelta(days=SPEND_WINDOW_DAYS)).isoformat(),
               T.posted <= today.isoformat(), T.pending == 0, T.amount < -ONE_OFF_LIMIT, T.recurring_id.is_(None))
        .order_by(T.amount)))
    tests = {a: paid_by_recurring([r for r in recurring if r["account_id"] == a]) for a in account_ids}
    return [t for t in txs if t["category"] not in transfers and not tests[t["account_id"]](t)]


def pending_total(conn, account: dict, today: date) -> float:
    """What's pending on an account (money out negative) that its balance doesn't have yet. SimpleFIN's pending rows
    count from the last simplefin.REFRESH_DAYS only: older ones aren't re-read by a sync, so one that dropped off or
    posted under a new id could linger. Plaid's are kept accurate (a sync deletes the ones it no longer has), so an old
    one is a real hold and counts.

    Most banks' balance leaves pending out, but some include it. Banks take pending debits out of the available balance
    (not pending credits, which are always added), so when it's at or below the balance and doesn't reflect the debits,
    the balance has them already and they aren't added. An available balance above the balance (one with an overdraft
    line in it) says nothing about them: they're added, as they are without one."""
    T = Transaction
    recent = or_(T.id.like(plaidbank.PLAID_IDS), T.posted >= (today - timedelta(days=simplefin.REFRESH_DAYS)).isoformat())
    out, came_in = (conn.execute(select(func.coalesce(func.sum(T.amount), 0.0))
                                 .where(T.account_id == account["id"], T.pending == 1, cond, recent)).scalar() or 0.0
                    for cond in (T.amount < 0, T.amount > 0))
    available, balance = account.get("available"), account["balance"]
    if available is not None and available <= balance + CENT and available > balance + out + CENT:
        out = 0.0
    return out + came_in


def statement_override(conn, card_id: str, close: date) -> float | None:
    r = conn.execute(select(Override.amount).where(Override.key == f"stmt:{card_id}:{close.isoformat()}")).fetchone()
    return abs(r["amount"]) if r else None


STATEMENT_FIELDS = ("last_statement_balance", "last_statement_date", "next_due_date", "minimum_payment", "purchase_apr")


def bank_statement(conn, card: dict, today: date) -> dict | None:
    """The card's latest statement: the issuer's, through Plaid Liabilities (source "plaid"), else the latest one you
    entered (source "manual"; see statements.py, which also says when one is stale). The card's billing cycle follows
    it: "closing_day" and "due_day" are the days of the month of its closing and due dates (a due date that's missing,
    or not after the close, is taken to be NO_STATEMENT_DUE_DAYS after it)."""
    plaid = plaidbank.statement(conn, card["id"], today) if card.get("plaid_account_id") else None
    st = {**{k: plaid[k] for k in STATEMENT_FIELDS}, "source": "plaid"} if plaid else statements.latest(conn, card["id"], today)
    if not st:
        return None
    close = parse_day(st["last_statement_date"])
    due = parse_day(st["next_due_date"]) if st["next_due_date"] else None
    return {**st, "closing_day": close.day,
            "due_day": (due if due and due > close else close + timedelta(days=NO_STATEMENT_DUE_DAYS)).day}


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
        lo, hi = (parse_day(s["posted"]) - timedelta(days=5)).isoformat(), (parse_day(s["posted"]) + timedelta(days=14)).isoformat()
        hit = next((c for c in unclaimed if abs(c["amount"] + s["amount"]) <= CENT and lo <= c["posted"][:10] <= hi), None)
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


def payment_plan(conn, card_id: str, issuer_apr: float | None = None) -> dict:
    """How a card's statements are paid, as set in Settings → Accounts: pay_mode "full" (the default), "minimum" (the
    issuer's minimum payment) or "fixed" (pay_amount toward each statement), and the card's APR in percent: the one you
    entered, else the issuer's purchase APR (`issuer_apr`, through Plaid), else None. apr_source says which ("you",
    "issuer" or None)."""
    return payment_plans(conn, [card_id], {card_id: issuer_apr})[card_id]


def payment_plans(conn, card_ids: list[str], issuer_aprs: dict[str, float | None] | None = None) -> dict[str, dict]:
    """payment_plan() for each of these cards, from one read of their settings: {card id: its plan}."""
    s = db.get_settings(conn, [k(c) for c in card_ids for k in (sk.card_pay_mode, sk.card_apr, sk.card_pay_amount)])
    out = {}
    for c in card_ids:
        mode = s[sk.card_pay_mode(c)] or "full"
        yours = _amount(s[sk.card_apr(c)])
        issuer_apr = (issuer_aprs or {}).get(c)
        apr, source = (yours, "you") if yours is not None else (issuer_apr, "issuer") if issuer_apr is not None else (None, None)
        out[c] = {"pay_mode": mode if mode in PAY_MODES else "full", "pay_amount": _amount(s[sk.card_pay_amount(c)]),
                  "apr": apr, "apr_source": source}
    return out


def statement_payment(plan: dict, statement: float, minimum: float | None = None, charged: float = 0.0) -> float:
    """What a payment plan (payment_plan) pays toward a statement: all of it; the minimum (the issuer's, else the larger
    of MIN_PAYMENT_FLOOR and MIN_PAYMENT_RATE of the statement plus the interest `charged` on it); or the fixed amount
    (all of it until one is entered). Never more than the statement."""
    if plan["pay_mode"] == "minimum":
        due = minimum if minimum is not None else max(MIN_PAYMENT_FLOOR, statement * MIN_PAYMENT_RATE + charged)
    elif plan["pay_mode"] == "fixed" and plan["pay_amount"] is not None:
        due = plan["pay_amount"]
    else:
        due = statement
    return max(0.0, min(statement, due))


def interest(plan: dict, carried: float, charges: float = 0.0) -> float:
    """A statement cycle's interest at the card's APR (none without one), while it carries a balance from the one before:
    a month's on that balance, and, since carrying one ends the grace period, on the cycle's new charges too, as if they
    posted evenly through it (half a month's). Nothing carried, nothing charged: purchases are interest-free until due."""
    if not plan["apr"] or carried <= 0:
        return 0.0
    return (carried + max(0.0, charges) / 2) * plan["apr"] / 100 / 12


def card_cycle(conn, card: dict, today: date, bank: dict) -> dict:
    """Where a card stands in its billing cycle today, from its latest statement at the bank (see bank_statement)."""
    transfers = _transfer_categories(conn)
    last_close = parse_day(bank["last_statement_date"])
    T = Transaction
    # SimpleFIN's pending rows count from its refresh window only, as in pending_total: an older one isn't re-read by a
    # sync, so one that has since posted under a new id would count twice.
    current = or_(T.pending == 0, T.id.like(plaidbank.PLAID_IDS),
                  T.posted >= (today - timedelta(days=simplefin.REFRESH_DAYS)).isoformat())
    txs = db.rows(conn.execute(
        select(T.posted, T.amount, T.category, T.pending)
        .where(T.account_id == card["id"], T.posted > last_close.isoformat(), current).order_by(T.posted)
    ))
    reported = max(0.0, bank["last_statement_balance"] or 0.0)
    known = statement_override(conn, card["id"], last_close)
    statement = known if known is not None else reported   # a figure you entered wins over the bank's
    paid = sum(t["amount"] for t in txs if t["amount"] > 0 and t["category"] in transfers)
    paid += in_transit(conn, card, last_close)
    # Paying more than the statement (the current balance, say) pays off some of the next one already.
    over = max(0.0, paid - statement)
    net = -sum(t["amount"] for t in txs if t["category"] not in transfers) - over
    new_charges = max(0.0, net)
    due = parse_day(bank["next_due_date"]) if bank["next_due_date"] and parse_day(bank["next_due_date"]) > last_close \
        else next_after(last_close, bank["due_day"])
    remaining = max(0.0, statement - paid)
    plan = payment_plan(conn, card["id"], bank.get("purchase_apr"))   # none on a statement you entered
    # What the plan pays toward this statement, less what's been paid since it closed; the rest carries into the next.
    payment = min(remaining, max(0.0, statement_payment(plan, statement, bank["minimum_payment"]) - paid))
    return {
        "last_close": last_close.isoformat(),
        "statement_source": bank.get("source", "plaid"),   # plaid | manual (entered by you)
        "statement_stale": bool(bank.get("stale")),         # manual only: a newer one should have been entered by now
        "statement_balance": round(statement, 2),
        "statement_reported": round(reported, 2),
        "statement_set": known is not None,
        "minimum_payment": bank["minimum_payment"],
        "statement_key": f"stmt:{card['id']}:{last_close.isoformat()}",
        "paid_since_close": round(paid, 2),
        "remaining": round(remaining, 2),
        **plan,
        "payment": round(payment, 2),               # what the forecast pays on the due date
        "carried": round(remaining - payment, 2),   # what that leaves to carry into the next statement
        # paying the minimum, but the issuer didn't say what it is (statement_payment's estimate is used)
        "minimum_estimated": plan["pay_mode"] == "minimum" and bank["minimum_payment"] is None,
        "due_date": due.isoformat(),
        "new_charges": round(new_charges, 2),
        # More refunds (or overpayment) than charges since the close: a credit the issuer takes off the next statement.
        "credit": round(max(0.0, -net), 2),
    }


def fee_posted(conn, account_id: str, day: date, fee: float | None = None) -> bool:
    """Whether a card's annual fee for the anniversary on `day` has been charged already (pending too): a charge from
    FEE_EARLY_DAYS before it on that's named like "Annual Fee" or "Annual Membership Fee" (as churn_found reads them), or
    one of the fee's amount (`fee`, above zero) in FEE_CATEGORY, however it's named. An interest charge, or a purchase
    that happens to cost the same, isn't the fee."""
    T = Transaction
    text = func.lower(func.coalesce(T.payee, "") + " " + func.coalesce(T.description, ""))
    same = [and_(T.category == FEE_CATEGORY, func.abs(T.amount + fee) < CENT)] if fee else []
    rows = conn.execute(select(T.payee, T.description, T.category, T.amount)
                        .where(T.account_id == account_id, T.amount < 0, or_(text.like("%fee%"), *same),
                               T.posted >= (day - timedelta(days=FEE_EARLY_DAYS)).isoformat())).fetchall()
    return any(FEE_TEXT.search(f"{r['payee'] or ''} {r['description'] or ''}")
               or (fee and r["category"] == FEE_CATEGORY and same_amount(r["amount"], -fee)) for r in rows)


def fee_recurring(recurring: list[dict], account_id: str, day: date) -> bool:
    """Whether you've made a recurring item for a card's annual fee: one on the card's account, money out, with "fee" in
    its name or what it matches, due within FEE_EARLY_DAYS of the anniversary. The forecast has that one on its own date
    (on the card, so in the card's statement), so the fee isn't added again."""
    lo, hi = day - timedelta(days=FEE_EARLY_DAYS + 1), day + timedelta(days=FEE_EARLY_DAYS)
    for r in recurring:
        words = " ".join([r.get("name") or "", *rec.match_texts(r)])
        if r["account_id"] == account_id and (r.get("amount") or 0) < 0 and re.search(r"\bfees?\b", words, re.I) \
                and scheduled(r, lo, hi):
            return True
    return False


def annual_fees(conn, card: dict, today: date, end: date, recurring: list[dict]) -> list[dict]:
    """A churning card's (churn_cards row) annual fees from today through `end`: one, or two with a horizon over a year.

    The fee posts on the account's anniversary, the day of the month it was opened (for a product change, the original
    card's: `_anniversary`, churning.anniversaries), from the first anniversary on (a shorter
    month's last day: Feb 29 -> Feb 28): churning.fee_anniversaries, as the Churning page's next_fee counts them. Issuers charge it on the anniversary, so it's on whichever statement that day
    falls in: the one closing that month when the anniversary is on or before the closing day, else the next one (the
    Churning page dates it the same way). An anniversary earlier this month whose fee hasn't been charged yet is still
    coming: today, as late. That's only for a card linked to an account, where it can be seen whether it was.

    Nothing for a card that isn't open or has no fee, nor from the day it's closed (closed_on) or planned to be closed or
    changed (churning.plan_active: by its plan_date, else before the fee), nor for a fee already charged (fee_posted) or
    one you've made a recurring item for (fee_recurring)."""
    fee = round(card.get("annual_fee") or 0.0, 2)
    if fee < CENT or (card.get("status") or "open") != "open":
        return []
    opened = parse_day(card.get("_anniversary") or card["opened_on"])   # a product change keeps the account's anniversary
    acct = card.get("account_id")
    plan_by = parse_day(card["plan_date"]) if card.get("plan_date") else None
    out = []
    for day in churning.fee_anniversaries(opened, month_start(today)):   # from this month's on (one may be late)
        if day > end:
            break
        if card.get("closed_on") and card["closed_on"] <= day.isoformat():
            break
        if churning.plan_active(card) and (plan_by is None or plan_by <= day):
            break   # you mean to close or change it first
        if day < today and not acct:
            continue   # not linked to an account: no telling whether it's been charged
        if acct and (fee_posted(conn, acct, day, fee) or fee_recurring(recurring, acct, day)):
            continue
        out.append({"date": max(day, today).isoformat(), "amount": -fee, "kind": "fee", "churn_card_id": card["id"],
                    "name": f"{card['product']} annual fee", **({"late_from": day.isoformat()} if day < today else {})})
    return out


def to_cents(values: list[float], total: float) -> list[float]:
    """Each value rounded to the cent so that they add up to `total` rounded to the cent: the cents rounding leaves over
    go to the values nearest to rounding the other way (largest remainder)."""
    return [c / 100 for c in allocate_cents([v * 100 for v in values], cents(total))]


def estimate_parts(*, close: date, due: date, budgets: dict[str, dict[str, float]], start: date, recurring: list[dict],
                   fees: list[dict], carried: float, interest: float, statement: float, payment: float, plan: dict,
                   charged_so_far: float | None = None, owed_now: float | None = None, assumed: bool = False) -> dict:
    """What an estimated statement (build) is made of, for the Overview to show: only what went into it, each part
    rounded so that they add up to the statement to the cent (to_cents), as the event's amount is rounded.

    Its parts: what's been charged since the last statement closed (`charged_so_far`, the cycle in progress) or, on a
    card with no statement yet (`assumed`: its cycle taken to end with the month), what it owes today (`owed_now`); the
    budgets paid with the card, each one's spending after `start` to the close (`budgets`: {category: {date: amount}});
    its `recurring` charges (events) and annual `fees`, as many as aren't in a budget charged to the card; what the
    statement before left unpaid (`carried`, below zero for a credit) and its `interest`. A statement that isn't paid in
    full pays `payment` of it (total), the way `plan` (payment_plan) says, and carries the rest."""
    lines: dict[str, float] = {}
    named: list[tuple[str, str, list[tuple[str, float]]]] = []   # parts made of named items: (part, item's key, items)
    if charged_so_far is not None:
        lines["charged_so_far"] = charged_so_far
    if owed_now is not None:
        lines["owed_now"] = owed_now
    spent = {c: sum(v for d, v in days.items() if start.isoformat() < d <= close.isoformat()) for c, days in budgets.items()}
    spent = {c: v for c, v in spent.items() if v >= CENT}
    if spent:
        lines["budgets_total"] = sum(spent.values())
        named.append(("budgets", "category", sorted(spent.items(), key=lambda kv: -kv[1])))
    extra = max(0.0, -sum(e["amount"] for e in recurring))
    if extra >= CENT:
        lines["recurring_total"] = extra
        named.append(("recurring", "name", [(e["name"], -e["amount"]) for e in recurring]))
    if fees:
        lines["fees_total"] = -sum(f["amount"] for f in fees)
        named.append(("fees", "name", [(f["name"], -f["amount"]) for f in fees]))
    if not is_zero(carried):
        lines["carried"] = carried
    if interest >= CENT:
        lines["interest"] = interest
    out: dict = {"close": close.isoformat(), "due": due.isoformat(), **({"assumed_cycle": True} if assumed else {}),
                 **dict(zip(lines, to_cents(list(lines.values()), statement), strict=True))}
    for part, label, items in named:
        out[part] = [{label: k, "amount": v}
                     for (k, _), v in zip(items, to_cents([v for _, v in items], out[f"{part}_total"]), strict=True)]
    if interest >= CENT:
        out["apr"] = plan["apr"]
    out.update(statement=round(statement, 2), total=round(payment, 2))
    if out["total"] != out["statement"]:
        out["pay_mode"] = plan["pay_mode"]   # paying the minimum or a fixed amount: the rest carries
    return out


def statement_href(card_id: str) -> str:
    """Where a card's statement is entered: its row in Settings → Accounts, opened at its Statement section."""
    return "#setup/accounts?account=" + urllib.parse.quote(card_id, safe="")


# ------------------------------------------------------------------------------------------------ forecast

def build(conn, today: date | None = None, horizon_days: int = 90) -> dict:
    """The forecast from today through `horizon_days` from now (see the top of this file). It only reads: a card
    payment's edit saved under its old key is applied where it belongs, but moving it to its new key is the caller's to
    do (project, move_old_keys)."""
    return project(conn, today, horizon_days)[0]


def project(conn, today: date | None = None, horizon_days: int = 90) -> tuple[dict, dict[str, str]]:
    """The forecast (build), and the card payment edits it found saved while their key was the due date, which it
    applied: {new key: old key}, for move_old_keys to move to their new keys."""
    today = today or date.today()
    end = today + timedelta(days=horizon_days)
    accounts = db.rows(conn.execute(select(Account).where(Account.hidden == 0).order_by(Account.name)))
    by_id = {a["id"]: a for a in accounts}
    cash, pending = forecast_accounts(conn, accounts, today)
    by_id.update({a["id"]: a for a in cash})
    cash_ids = {a["id"] for a in cash}
    cards = [a for a in accounts if a["kind"] == "credit"]
    recurring = db.rows(conn.execute(select(Recurring).where(Recurring.active == 1)))
    # One-off edits you've made to specific upcoming items.
    overrides = {r["key"]: r["amount"] for r in conn.execute(select(Override.key, Override.amount))}
    events = recurring_events(conn, recurring, by_id, overrides, today, end)

    # Each card's latest statement (bank_statement, which says its billing cycle) and its annual fees, ahead of the
    # budgets: a fee is spent on its card, and the budget for its category needs to know (budget_days).
    banks = {c["id"]: bank_statement(conn, c, today) for c in cards}
    on_cards = card_fees(conn, by_id, cards, banks, today, end, recurring)

    # Budgets: each one is spent day by day (budget_days) on the account or card it's paid with. One paid from a
    # forecast account comes out of it on banking days: a weekend's or a bank holiday's share goes out on the next
    # business day (one past the chart's last day isn't on it). One paid with a card is charged to it any day, so it's
    # in the card's statements, paid on their due dates.
    plan = budget_plan(conn, today) if cash else []
    default = cash[0]["id"] if cash else ""   # no forecast account: no budgets either
    covers = budget_covers(plan, by_id, cash_ids, default)
    # What's scheduled on the cards whose statements the forecast pays (not out of date), that no budget on the card has.
    estimated = [c["id"] for c in cards if c["pay_from"] in cash_ids and not (banks[c["id"]] or {}).get("stale")]
    on_card_charges = [x for cid in estimated
                       for x in [*on_cards[cid], *(e for e in events if e["account_id"] == cid and e["kind"] == "recurring")]
                       if x.get("category") not in covers[cid]]
    days_of = budget_days(conn, today, horizon_days, plan, events, cash_ids, on_card_charges) if plan else {}
    spend, spend_of, used, skipped = spend_budgets(plan, days_of, by_id, cash_ids, default)

    paid = card_payments(conn, cards, banks, on_cards, by_id=by_id, cash=cash, events=events, overrides=overrides,
                         spend=spend, spend_of=spend_of, covers=covers, today=today, end=end)
    events += paid["events"]
    warnings = paid["warnings"]
    # A budget on a card with an out-of-date statement isn't spent: nothing is estimated on that card.
    for u in [u for u in used if u["account_id"] in paid["stale_cards"]]:
        used.remove(u)
        skipped.append({"category": u["category"], "reason": "its card's statement is out of date"})
    events += assumed_cycle_payments(conn, paid["no_statement"], by_id=by_id, cash_ids=cash_ids, events=events,
                                     spend=spend, spend_of=spend_of, covers=covers, today=today, end=end)

    # Churning cards not linked to a credit card account here (or to a hidden one): their fees are listed, on the
    # anniversary's day, but aren't in any balance, since there's no card whose statement they'd be on.
    fees = [f for c in cards for f in on_cards[c["id"]]] + on_cards[None]
    for f in fees:
        f.pop("_on", None)
    fees.sort(key=lambda f: (f["date"], f["amount"]))
    warnings += setup_warnings(conn, paid["unlinked"], cash, today, recurring)

    # A card payment's edit saved while its key was the due date still applies, as if it were under its key now; the
    # caller moves it there (move_old_keys), so putting it back, which removes the event's key, removes it.
    moving = {new: old for new, old in paid["old_keys"].items() if old in overrides and new not in overrides}
    for new, old in moving.items():
        overrides[new] = overrides[old]
    apply_edits(events, overrides)
    # Only what lands on the chart, today through its last day (a payment moved off a weekend can land past it).
    events = [e for e in events if today.isoformat() <= e["date"] <= end.isoformat()]

    dates = [(today + timedelta(days=i)).isoformat() for i in range(horizon_days + 1)]
    series_by_acct = {a["id"]: balance_series(a, events, spend.get(a["id"], {}), dates) for a in cash}
    total = [round(sum(s[i] for s in series_by_acct.values()), 2) for i in range(horizon_days + 1)] if cash else []

    # Recurring charges on cards: their statements pay them, so they're not in `events` or the balances, but they're
    # listed with what's coming up (Transactions) and counted in what a budget still expects this month.
    card_charges = sorted(({**e, "account": db.account_label(by_id[e["account_id"]])} for e in events
                           if e["kind"] == "recurring" and by_id[e["account_id"]]["kind"] == "credit"),
                          key=lambda e: (e["date"], e["amount"]))
    events = sorted((e for e in events if e["account_id"] in cash_ids), key=lambda e: (e["date"], e["amount"]))
    balances_after(events, by_id, series_by_acct, spend, dates)

    return {
        "today": today.isoformat(),
        "primary_id": cash[0]["id"] if len(cash) == 1 else None,
        "dates": dates,
        "accounts": [
            {"id": a["id"], "name": db.account_label(a), "kind": a["kind"], "balance": round(a["balance"], 2),
             "pending": pending[a["id"]],   # in the balance already: the bank's posted balance plus this
             "series": series_by_acct[a["id"]],
             "low": low(series_by_acct[a["id"]], dates),
             "spend": budgeted_out(spend, [a["id"]], dates)}
            for a in cash
        ],
        "total": total,
        "low": low(total, dates),
        # What the budgets paid from the forecast's accounts take out each day (positive), in the balances already.
        "spend": budgeted_out(spend, [a["id"] for a in cash], dates),
        "events": events,
        "charges": card_charges,
        # Churning cards' annual fees, for the lists of what's coming up: charges on cards, not on the forecast's
        # accounts, so not in `events` or the balances. Each is in its card's statement payment on paid_on (paid from
        # paid_from), when that's in the forecast.
        "fees": fees,
        "cards": paid["card_status"],
        "unlinked_cards": paid["unlinked"],
        "warnings": [w["text"] for w in warnings],   # as plain text, as before (MCP clients read these)
        "warning_links": warnings,
        # The budgets the forecast spends, and the ones it leaves out (and why).
        "budget": {"used": used, "skipped": skipped, "monthly": round(sum(u["amount"] for u in used), 2)} if plan else None,
    }, moving


def move_old_keys(conn, moving: dict[str, str]) -> None:
    """Move card payment edits saved while their key was the due date to the key the forecast found for each now
    ({new key: old key}, from project), so putting one back, which removes the event's key, removes it. A page load
    mustn't wait on a sync's write lock: if the move can't get it, it's tried again the next time."""
    if not moving:
        return
    try:
        with conn.sa.begin_nested():
            for new, old in moving.items():
                conn.execute(update(Override).where(Override.key == old).values(key=new))
    except OperationalError:
        pass


def warning(text: str, href: str, setting: bool = True) -> dict:
    """What's wrong, the page about it, and whether a setting there puts it right."""
    return {"text": text, "href": href, "setting": setting}


def forecast_accounts(conn, accounts: list[dict], today: date) -> tuple[list[dict], dict[str, float]]:
    """The cash accounts the forecast follows (the primary account, else the only checking account, else the ones
    marked "in forecast"), each with the balance it starts from, and what's pending on each: {account id: amount}.

    Each account starts from the bank's balance plus what's pending on it that the balance doesn't have yet, money in
    and out (see pending_total). A pending payment already linked to a recurring item counts as having happened (see
    recurring_events), so it's in the balance and not again as an event."""
    cash_like = [a for a in accounts if a["kind"] in ("checking", "savings")]
    primary = {a["id"]: a for a in accounts}.get(db.get_setting(conn, sk.PRIMARY_ACCOUNT) or "")
    if primary and primary["kind"] in ("checking", "savings"):
        cash = [primary]
    elif len([a for a in cash_like if a["kind"] == "checking"]) == 1:
        cash = [a for a in cash_like if a["kind"] == "checking"]  # only one checking account: that's the primary
    else:
        cash = [a for a in cash_like if a["in_forecast"]]
    pending = {a["id"]: round(pending_total(conn, a, today), 2) for a in cash}
    return [dict(a, balance=a["balance"] + pending[a["id"]]) for a in cash], pending


def recurring_events(conn, recurring: list[dict], by_id: dict[str, dict], overrides: dict[str, float], today: date,
                     end: date) -> list[dict]:
    """The recurring items' occurrences through `end` on the accounts here (`by_id`), as events: what's still due of
    each, with your one-off edits (`overrides`, rec:<id>:<date>) applied."""
    T = Transaction
    most_used = (func.count().desc(), func.max(T.posted).desc())   # the category used most (then most recently)
    rec_overrides = {k: v for k, v in overrides.items() if k.startswith("rec:")}
    events: list[dict] = []
    for item in recurring:
        if item["account_id"] not in by_id:
            continue
        history = rec.matched(conn, item["id"])
        amount = rec.expected_amount(item, history)
        cat_row = conn.execute(
            select(T.category).where(T.recurring_id == item["id"], T.category.is_not(None))
            .group_by(T.category).order_by(*most_used).limit(1)).fetchone()
        if not cat_row:   # nothing linked to it yet: the category of what it matches on its account
            like = "%" + next(iter(rec.match_texts(item)), "").replace("%", "").replace("_", "") + "%"
            cat_row = conn.execute(
                select(T.category).where(T.account_id == item["account_id"], T.category.is_not(None), func.length(like) > 4,
                                         or_(func.lower(T.payee).like(like), func.lower(T.description).like(like)))
                .group_by(T.category).order_by(*most_used).limit(1)).fetchone()
        # Anything due in the last matching window that hasn't shown up yet is still coming: it goes on today, as
        # late (older than the window, it's "missed" in Recurring instead). Due today counts as due, not late.
        # One that's partly paid (a paycheck in two deposits, early or on time) leaves the rest expected the same way,
        # until the window closes; what's paid is in the balance already (see recurring.still_due).
        window = rec.MATCH_WINDOW_DAYS.get(item["frequency"], 6)
        first_tx = conn.execute(select(func.min(T.posted)).where(T.account_id == item["account_id"])).fetchone()[0]
        since = max(today - timedelta(days=window + 1), parse_day(first_tx) + timedelta(days=window) if first_tx else today)
        paid = rec.paid_by_occurrence(item, history)
        for d in occurrences(item, min(since, today - timedelta(days=1)), end):
            key = f"rec:{item['id']}:{d.isoformat()}"
            # A one-off edit is what this occurrence comes to in all, so what's paid toward it comes off the edit too.
            edited = rec_overrides.get(key)
            left = rec.still_due(item, d, paid, today, amount if edited is None else round(edited, 2), edited is not None)
            if left is None:
                continue  # this one already posted (possibly early), don't count it twice
            usual_left = rec.still_due(item, d, paid, today, amount)
            events.append({"date": max(d, today).isoformat(), "account_id": item["account_id"], "name": item["name"],
                           # an amount you've set for this one is yours, not an estimate
                           "amount": left, "kind": "recurring",
                           "estimated": (item.get("amount_mode") or "fixed") != "fixed" and edited is None,
                           "recurring_id": item["id"], "key": key,
                           "category": cat_row["category"] if cat_row else None, **({"late_from": d.isoformat()} if d < today else {}),
                           **({"paid_so_far": paid[d]} if not is_zero(paid.get(d, 0.0)) else {}),
                           **({"original_amount": usual_left if usual_left is not None else 0.0, "overridden": True} if edited is not None else {})})
    return events


def card_fees(conn, by_id: dict[str, dict], cards: list[dict], banks: dict[str, dict | None], today: date, end: date,
              recurring: list[dict]) -> dict[str | None, list[dict]]:
    """Churning cards' annual fees (annual_fees), by the credit card account each is on: {card id: its fees} for each
    of `cards`, and under None the ones on no card here (not linked to one, or to a hidden one).

    A fee is a charge on its card: it's added to the statement it lands on (card_payments, assumed_cycle_payments), and
    so reaches cash with that statement's payment (paid_on), never on its own. One whose card's payments aren't in the
    forecast (not paid from a forecast account, a statement out of date, past the horizon) is only listed. Each fee on
    a card has `_on`, the day it's on the card's statements from: its date, or, on a card with a statement, the day
    after the latest one closed if that's later (a fee charged on the day it closed, today, is on the next one)."""
    linked_to: dict[str | None, list[dict]] = defaultdict(list)
    anniversary = churning.anniversaries(conn)
    for c in db.rows(conn.execute(select(ChurnCard).where(func.coalesce(ChurnCard.status, "open") == "open",
                                                          ChurnCard.annual_fee > 0).order_by(ChurnCard.id))):
        c["_anniversary"] = anniversary.get(c["id"])
        linked = by_id.get(c.get("account_id") or "")
        linked_to[linked["id"] if linked and linked["kind"] == "credit" else None].append(c)
    category = FEE_CATEGORY if conn.execute(select(Category.name).where(Category.name == FEE_CATEGORY)).fetchone() else None
    out: dict[str | None, list[dict]] = {}
    for account in [*cards, None]:
        aid = account["id"] if account else None
        out[aid] = [{**f, "account_id": aid, "account": db.account_label(account) if account else None, "category": category,
                     "paid_on": None, "paid_from": None}
                    for c in linked_to.get(aid, []) for f in annual_fees(conn, c, today, end, recurring)]
        if account:
            bank = banks[account["id"]]
            after = (parse_day(bank["last_statement_date"]) + timedelta(days=1)).isoformat() if bank else ""
            for f in out[aid]:
                f["_on"] = max(f["date"], after)
    return out


# ------------------------------------------------------------------------------------------------ budgets in the forecast

def share_account(q: dict, by_id: dict[str, dict], cash_ids: set[str], default: str) -> tuple[str, dict | None, str | None]:
    """The account a budget's share (or a part's) is spent on (the one it's paid with, else the one used most for it,
    else `default`, the first forecast account), and why it's left out, if it is."""
    acct = q["pay_with"] or q["usual"] or default
    a = by_id.get(acct)
    if not a or (acct not in cash_ids and a["kind"] != "credit"):
        return acct, a, "its account isn't in the forecast"
    if a["kind"] == "credit" and a["pay_from"] not in cash_ids:
        return acct, a, "its card isn't paid from a forecast account"
    return acct, a, None


def budget_covers(plan: list[dict], by_id: dict[str, dict], cash_ids: set[str], default: str) -> dict[str, set[str]]:
    """The categories whose budget is charged to each card: {card id: categories}. A charge on the card in one of them
    (a recurring one, or an annual fee) is that budget's spending, on the card already. Any other charge on a card goes
    on its statement on top of its budgets, and comes off its category's budget wherever that's paid from
    (budget_days), so it counts once."""
    covers: dict[str, set[str]] = defaultdict(set)
    for p in plan:
        for q, monthly in zip([p, *p["parts"]], monthly_shares(p), strict=True):
            acct, a, why = share_account(q, by_id, cash_ids, default)
            if a and why is None and a["kind"] == "credit" and not (p["parts"] and monthly < CENT):
                covers[acct].update(q["names"] if q is not p
                                    else [n for n in p["names"] if not any(n in r["names"] for r in p["parts"])])
    return covers


def spend_budgets(plan: list[dict], days_of: dict, by_id: dict[str, dict], cash_ids: set[str],
                  default: str) -> tuple[dict, dict, list[dict], list[dict]]:
    """Each budget's spending day by day (`days_of`, from budget_days) put on the account or card it's paid with:
    (spend: {account or card: {date: amount}}, spend_of: {card: {budget: {date: amount}}} for its statements'
    breakdown, used: the budgets spent, skipped: the ones left out and why). The budget's own share, then each part's
    (a subcategory's budget on its own account, budget_plan): each is spent on its account, or left out on its own."""
    spend: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    spend_of: dict[str, dict[str, dict[str, float]]] = defaultdict(dict)
    used: list[dict] = []
    skipped: list[dict] = []
    for p in plan:
        ways = days_of[p["category"]]
        if ways is None:
            skipped.append({"category": p["category"], "reason": "a recurring item already covers it"})
            continue
        for q, way, monthly in zip([p, *p["parts"]], ways, monthly_shares(p), strict=True):
            if p["parts"] and monthly < CENT and not any(v > CENT for v in way["days"].values()):
                continue   # its parts take the whole budget (or the earlier parts take this one's): nothing to spend
            acct, a, why = share_account(q, by_id, cash_ids, default)
            if not a or why:
                skipped.append({"category": q["category"], "reason": why})
                continue
            for day, v in way["days"].items():
                spend[acct][bankdays.next_business_day(parse_day(day)).isoformat() if acct in cash_ids else day] += v
            spend_of[acct][q["category"]] = way["days"]   # each share's days, for the statement's estimate breakdown
            used.append({"category": q["category"], "amount": round(monthly, 2), "account_id": acct,
                         "account": db.account_label(a), "chosen": bool(q["pay_with"])})
    return spend, spend_of, used, skipped


# ------------------------------------------------------------------------------------------------ card statements

def paid_toward(plan: dict, planned: float, shown: bool, edit: float | None) -> float:
    """What's paid toward a card statement, for what carries into the next one (what it owes, less this): what its
    payment plan pays (`planned`, which its payment event is built from), or, when that event is `shown` and you've
    edited it on the Overview (`edit`), what you entered (the edit itself is applied to the event with the other edits,
    apply_edits). An edit above what's owed (clearing the current balance, say) leaves a credit, which comes off the
    next statement like any other. A card paid in full carries nothing either way, as before."""
    return abs(edit) if shown and plan["pay_mode"] != "full" and edit is not None else planned


def statement_cycles(last_close: date, closing_day: int, due_day: int, end: date) -> Iterator[tuple[date, date]]:
    """The statements after the one that closed on `last_close`, (close, due) for each due by `end`: closing on
    `closing_day` each month, due on the next `due_day` after."""
    close = next_after(last_close, closing_day)
    while (due := next_after(close, due_day)) <= end:
        yield close, due
        close = next_after(close, closing_day)


def month_end_cycles(today: date, end: date) -> Iterator[tuple[date, date]]:
    """A card with no statement yet: its statements taken to close at each month's end from this one, (close, due) for
    each, due NO_STATEMENT_DUE_DAYS later, while that's paid (on the next business day) by `end`."""
    close = month_end(today)
    while bankdays.next_business_day(close + timedelta(days=NO_STATEMENT_DUE_DAYS)) <= end:
        yield close, close + timedelta(days=NO_STATEMENT_DUE_DAYS)
        close = month_end(month_start(close, 1))


def simulate_statements(cycles: Iterable[tuple[date, date]], *, card_id: str, start: date, owing: float, plan: dict,
                        today: date, budgets: dict[str, float], charges: list[dict], fees: list[dict], covers: set[str],
                        spent_on: dict[str, dict[str, float]], charged_so_far: float | None = None,
                        owed_now: bool = False, edits: dict[str, float] | None = None) -> list[dict]:
    """A card's statements, cycle by cycle (`cycles`: (close, due) each, from statement_cycles or month_end_cycles),
    from `start`, the day the one before closed: each the charges on it plus what the one before it left (`owing`,
    below zero for a credit) and a month's interest on that, paid as `plan` (payment_plan) says; what isn't paid
    carries into the next.

    A statement's charges: the budgets paid with the card (`budgets`: {date: amount}), its recurring charges (`charges`,
    events) and annual fees (`fees`, by their `_on` day), each in the cycle it lands in, as many as aren't in a budget
    charged to the card (`covers`), and in the first cycle, what's been charged since the last statement closed
    (`charged_so_far`). With `owed_now`, what it owes in the first cycle is this cycle's charges (a card with no
    statement yet, whose cycle is taken to end with the month), not a balance carried from a statement: no interest on
    it. With `edits` (your edits on the Overview, for a card whose statements have keys), an edit of a statement's
    payment is what's paid toward it (paid_toward).

    Each cycle: close, due, pays (the business day it's paid on), key and old_key (its payment's, by closing and by due
    date), statement, interest, planned (what the plan pays), pay (what's paid, with any edit), carried (what's left),
    fees (the ones on it), and, when it's paid from today on and there's something to pay, estimate (estimate_parts)."""
    out = []
    prev, first = start, True
    for close, due in cycles:
        # The card's recurring charges due this cycle that haven't posted yet (events are dated today or later); one
        # (or a fee) in the category of a budget charged to this card is in that budget already.
        on_top = [e for e in charges if prev.isoformat() < e["date"] <= close.isoformat() and e.get("category") not in covers]
        est = (charged_so_far if first and charged_so_far is not None else 0.0) \
            + sum(v for d, v in budgets.items() if prev.isoformat() < d <= close.isoformat()) \
            + max(0.0, -sum(e["amount"] for e in on_top))
        fees_here = [f for f in fees if (first or prev.isoformat() < f["_on"]) and f["_on"] <= close.isoformat()]
        fees_on_top = [f for f in fees_here if f["category"] not in covers]
        est += -sum(f["amount"] for f in fees_on_top)
        owed_interest = 0.0 if first and owed_now else interest(plan, owing, est)
        statement = owing + owed_interest + est
        key, old_key = f"cardclose:{card_id}:{close.isoformat()}", f"card:{card_id}:{due.isoformat()}"
        pays = bankdays.next_business_day(due)
        planned = statement_payment(plan, statement, charged=owed_interest)
        edit = edits.get(key, edits.get(old_key)) if edits is not None else None
        pay = paid_toward(plan, planned, pays >= today and planned > CENT, edit)
        cycle = {"close": close, "due": due, "pays": pays, "key": key, "old_key": old_key, "statement": statement,
                 "interest": owed_interest, "planned": planned, "pay": pay, "carried": statement - pay, "fees": fees_here}
        if pays >= today and planned > CENT:
            cycle["estimate"] = estimate_parts(
                close=close, due=due, start=prev, budgets=spent_on, recurring=on_top, fees=fees_on_top,
                carried=0.0 if first and owed_now else owing, interest=owed_interest, statement=statement, payment=planned,
                plan=plan, charged_so_far=charged_so_far if first else None, owed_now=owing if first and owed_now else None,
                assumed=owed_now)
        out.append(cycle)
        owing = statement - pay
        prev, first = close, False
    return out


def card_payments(conn, cards: list[dict], banks: dict[str, dict | None], on_cards: dict[str | None, list[dict]], *,
                  by_id: dict[str, dict], cash: list[dict], events: list[dict], overrides: dict[str, float], spend: dict,
                  spend_of: dict, covers: dict[str, set[str]], today: date, end: date) -> dict:
    """The cards with a statement (`banks`, from bank_statement): where each stands (card_cycle), its closed statement's
    payment and the estimated statements after it, paid from its paying account when that's in the forecast (`cash`).

    Future statements: what's been charged since the last one closed (the statement in progress), the budgets charged
    to the card, its recurring charges and any annual fee, each in the cycle it lands in (simulate_statements). A
    statement that isn't paid in full carries the rest into the next one, with a month's interest on it.

    Returns {"card_status": each card's cycle, "events": the payments, "warnings", "old_keys": {payment key: the key it
    had before keys followed the closing date}, "no_statement": {card id: its annual fees} for the cards without one,
    "unlinked": those cards, for the Overview, "stale_cards": the ones whose statement you entered a while ago}."""
    out: dict = {"card_status": [], "events": [], "warnings": [], "old_keys": {}, "no_statement": {}, "unlinked": [],
                 "stale_cards": set()}
    warnings, old_keys = out["warnings"], out["old_keys"]
    for card in cards:
        label = db.account_label(card)
        bank, on_card = banks[card["id"]], on_cards[card["id"]]
        if not bank:
            out["no_statement"][card["id"]] = on_card
            out["unlinked"].append({"id": card["id"], "name": label, "owed_now": round(max(0.0, owed(card)), 2),
                                    "linked": bool(card.get("plaid_account_id"))})
            continue
        info = card_cycle(conn, card, today, bank)
        info.update({"id": card["id"], "name": label, "owed_now": round(max(0.0, owed(card)), 2)})
        info["annual_fees"] = [{"date": f["_on"], "amount": f["amount"], "category": f["category"]} for f in on_card]
        due = parse_day(info["due_date"])
        key = f"cardclose:{card['id']}:{info['last_close']}"
        old_keys[key] = f"card:{card['id']}:{due.isoformat()}"
        pays = bankdays.next_business_day(due)   # a due date on a weekend or holiday is paid the next business day
        planned = info["payment"]   # what the payment plan pays: the event's amount, before any edit of yours
        payment = paid_toward(info, planned, pays >= today and planned > CENT, overrides.get(key, overrides.get(old_keys[key])))
        info.update(payment=round(payment, 2), carried=round(info["remaining"] - payment, 2))
        out["card_status"].append(info)
        if info["statement_stale"]:
            warnings.append(warning(f"Enter {label}’s latest statement so its payment stays in the forecast.",
                                    statement_href(card["id"])))
        payer = by_id.get(card["pay_from"] or "")
        if not payer:
            warnings.append(warning(f"{label}: choose which account pays it in Settings.", "#setup/accounts"))
            continue
        if payer not in cash:
            continue  # paid from an account that isn't being forecast
        if info["pay_mode"] == "fixed" and info["pay_amount"] is None:
            warnings.append(warning(f"{label}: no amount entered for its fixed payment, so the forecast pays each statement "
                                    "in full.", "#setup/accounts"))
        if info["minimum_estimated"] and info["remaining"] > CENT:
            warnings.append(warning(f"{label}: the bank didn’t report a minimum payment, so the forecast pays the larger of "
                                    f"${MIN_PAYMENT_FLOOR:,.0f} and {MIN_PAYMENT_RATE:.0%} of the statement plus its "
                                    "interest.", "#setup/accounts"))
        if pays >= today and planned > CENT:
            out["events"].append({"date": pays.isoformat(), "account_id": payer["id"], "name": f"{label} statement",
                                  "amount": -planned, "kind": "card", "estimated": False,
                                  "key": key, "category": "Credit Card Payment", "card_id": card["id"]})
        elif pays < today and info["payment"] > CENT:
            warnings.append(warning(f"{label}: ${info['payment']:,.2f} was due {due:%b %-d} and no payment has shown up yet.",
                                    "#setup/accounts", setting=False))   # paying the card puts it right, not a setting
        if info["statement_stale"]:
            out["stale_cards"].add(card["id"])
            continue   # a statement you entered a while ago: its own payment, but nothing estimated from it
        # What carries into the statement in progress: what the closed one leaves unpaid, less any credit on the card.
        # A statement that comes out at or below zero pays nothing and carries its credit on to the next one, in every
        # mode (a card paid in full too, as the issuer does).
        last_close = parse_day(info["last_close"])
        cycles = simulate_statements(
            statement_cycles(last_close, bank["closing_day"], bank["due_day"], end), card_id=card["id"], start=last_close,
            owing=info["carried"] - info["credit"], plan=info, today=today, budgets=spend.get(card["id"], {}),
            charges=[e for e in events if e["account_id"] == card["id"] and e["kind"] == "recurring"], fees=on_card,
            covers=covers[card["id"]], spent_on=spend_of.get(card["id"], {}), charged_so_far=info["new_charges"],
            edits=overrides)
        for c in cycles:
            old_keys[c["key"]] = c["old_key"]
            if "estimate" in c:
                out["events"].append({"date": c["pays"].isoformat(), "account_id": payer["id"], "name": f"{label} statement",
                                      "amount": -round(c["planned"], 2), "kind": "card", "estimated": True,
                                      "key": c["key"], "category": "Credit Card Payment", "card_id": card["id"],
                                      "estimate": c["estimate"]})
                for f in c["fees"]:
                    f.update(paid_on=c["pays"].isoformat(), paid_from=db.account_label(payer))
        if any(c["pays"] < today for c in cycles):   # the issuer's latest statement is older than one of these
            warnings.append(warning(f"{label}: the bank hasn't sent the statement after {last_close:%b %-d} yet, so its "
                                    "payment isn't in the forecast.", "#setup/connections", setting=False))   # the bank's to send
        if (info["carried"] > CENT or any(c["carried"] > CENT for c in cycles)) and info["apr"] is None:
            warnings.append(warning(f"{label}: the forecast carries part of its statements to the next one, but doesn’t "
                                    "count the interest on it: enter the card’s APR in Settings.", "#setup/accounts"))
    return out


def assumed_cycle_payments(conn, no_statement: dict[str, list[dict]], *, by_id: dict[str, dict], cash_ids: set[str],
                           events: list[dict], spend: dict, spend_of: dict, covers: dict[str, set[str]], today: date,
                           end: date) -> list[dict]:
    """The payments of the cards without a statement (`no_statement`: {card id: its annual fees}) that have budgets,
    recurring charges or an annual fee charged to them: those charges (and what the card owes today) on statements taken
    to close at each month's end (month_end_cycles), each paid NO_STATEMENT_DUE_DAYS later (the next business day) as
    the card's payment plan pays it. What isn't paid carries into the next one with interest; a credit comes off the
    next statement and what's left of it carries on, as the bank does. A card not paid from a forecast account has its
    fees only listed."""
    out = []
    charged_cards = {e["account_id"] for e in events if e["kind"] == "recurring" and e["amount"] < 0} \
        | {cid for cid, on_card in no_statement.items() if on_card}
    for cid in sorted(no_statement.keys() & (spend.keys() | charged_cards)):
        card = by_id[cid]
        if card["pay_from"] not in cash_ids:
            continue
        payer = by_id[card["pay_from"]]
        # What it owes today, in the bank's sign, as everywhere (owed_positive): below zero is a credit. An overdue fee
        # is dated today, so the first statement takes it too.
        cycles = simulate_statements(
            month_end_cycles(today, end), card_id=cid, start=today, owing=owed(card), plan=payment_plan(conn, cid),
            today=today, budgets=spend.get(cid, {}),
            charges=[e for e in events if e["account_id"] == cid and e["kind"] == "recurring"], fees=no_statement[cid],
            covers=covers[cid], spent_on=spend_of.get(cid, {}), owed_now=True)
        for c in cycles:
            if "estimate" in c:
                out.append({"date": c["pays"].isoformat(), "account_id": payer["id"], "name": f"{db.account_label(card)} statement",
                            "amount": -round(c["pay"], 2), "kind": "card", "estimated": True, "assumed_cycle": True,
                            "category": "Credit Card Payment", "card_id": cid, "estimate": c["estimate"]})
                for f in c["fees"]:
                    f.update(paid_on=c["pays"].isoformat(), paid_from=db.account_label(payer))
    return out


# ------------------------------------------------------------------------------------------------ the chart

def setup_warnings(conn, unlinked: list[dict], cash: list[dict], today: date, recurring: list[dict]) -> list[dict]:
    """What to set up for the forecast to be complete: a statement for each card without one (`unlinked`), cards Plaid
    has that wait to be matched, and big one-off payments that aren't recurring items."""
    out = []
    # Cards without a statement: you enter the latest one by hand (Settings → Accounts, the card's row), unless Plaid
    # can send it. Plaid is only mentioned when it's set up.
    plaid_on = plaidapi.configured(conn)
    for c in unlinked:
        if c["linked"]:
            out.append(warning(f"Plaid hasn’t sent a statement for {c['name']} yet. Enter its latest statement so its "
                               "payment is in the forecast.", statement_href(c["id"])))
        else:
            out.append(warning(f"Enter {c['name']}’s latest statement{' (or link it through Plaid)' if plaid_on else ''} so "
                               "its payment is in the forecast.", statement_href(c["id"])))
    if plaid_on and any(not c["linked"] for c in unlinked):
        waiting = conn.execute(
            select(func.count()).select_from(PlaidAccount)
            .where(PlaidAccount.type == "credit", PlaidAccount.ignored == 0, PlaidAccount.plaid_account_id.not_in(
                select(Account.plaid_account_id).where(Account.plaid_account_id.is_not(None))))).fetchone()[0]
        if waiting:   # cards Plaid already has are matched in Settings → Accounts, and then send their statements
            out.append(warning(f"Plaid has {waiting} card{'s' if waiting != 1 else ''} waiting to be matched: in Settings → "
                               "Accounts, choose “Same as …” for each under “New from Plaid”.", "#setup/accounts"))
    # Big one-off payments from a forecast account aren't in it; if they come back (rent paid by hand, tuition), they
    # need to be recurring items to be.
    big = large_one_offs(conn, [a["id"] for a in cash], today, recurring)
    if big:
        payees = [p for p in dict.fromkeys((t["payee"] or t["description"] or "").strip() for t in big) if p]   # biggest first
        one = len(big) == 1
        named = f" ({', '.join(payees[:3])}{'…' if len(payees) > 3 else ''})" if payees else ""
        out.append(warning(f"{len(big)} payment{'' if one else 's'} over ${ONE_OFF_LIMIT:,.0f} in the last {SPEND_WINDOW_DAYS} "
                           f"days {'isn’t' if one else 'aren’t'} in the forecast{named}; add "
                           f"{'it as a recurring item' if one else 'them as recurring items'}.", "#recurring"))
    return out


def apply_edits(events: list[dict], overrides: dict[str, float]) -> None:
    """Your one-off edits (`overrides`, by event key) on the events that don't have theirs yet (recurring items' are
    applied as they're made): an amount you've changed is yours, not an estimate."""
    for e in events:
        if e.get("key") in overrides and not e.get("overridden"):
            e["original_amount"], e["amount"], e["overridden"] = e["amount"], round(overrides[e["key"]], 2), True
        if e.get("overridden"):
            e["estimated"] = False
            e.pop("estimate", None)


def balance_series(account: dict, events: list[dict], spend: dict[str, float], dates: list[str]) -> list[float]:
    """An account's balance each day on the chart (`dates`, from today): the events', and what the budgets paid from
    the account spend that day (`spend`; not listed as events)."""
    by_day = {d: -v for d, v in spend.items()}
    for e in events:
        if e["account_id"] == account["id"]:
            by_day[e["date"]] = by_day.get(e["date"], 0.0) + e["amount"]
    bal = account["balance"] + by_day.get(dates[0], 0.0)   # anything due today that hasn't posted yet
    series = [round(bal, 2)]
    for when in dates[1:]:
        bal += by_day.get(when, 0.0)
        series.append(round(bal, 2))
    return series


def low(series: list[float], dates: list[str]) -> dict | None:
    """The lowest balance in a series, and its date (the first, if it's there more than once)."""
    if not series:
        return None
    i = min(range(len(series)), key=lambda k: series[k])
    return {"date": dates[i], "balance": series[i]}


def budgeted_out(spend: dict[str, dict[str, float]], ids: list[str], dates: list[str]) -> dict[str, float]:
    """What the budgets take out of these accounts each day on the chart (for its readout: they aren't events)."""
    by: dict[str, float] = defaultdict(float)
    for acct in ids:
        for d, v in spend.get(acct, {}).items():
            by[d] += v
    return {d: round(by[d], 2) for d in dates if by.get(d, 0.0) > CENT}


def balances_after(events: list[dict], by_id: dict[str, dict], series_by_acct: dict[str, list[float]], spend: dict,
                   dates: list[str]) -> None:
    """Each event's balance_after, the balance of its account right after it lands (same-day items apply in the order
    listed, after that day's budgeted spending), and its account's name."""
    index = {d: i for i, d in enumerate(dates)}
    running: dict[tuple, float] = {}
    for e in events:
        i, acct = index[e["date"]], e["account_id"]
        k = (acct, e["date"])
        if k not in running:
            running[k] = (by_id[acct]["balance"] if i == 0 else series_by_acct[acct][i - 1]) \
                - spend.get(acct, {}).get(e["date"], 0.0)
        running[k] += e["amount"]
        e["balance_after"] = round(running[k], 2)
        e["account"] = db.account_label(by_id[acct])


# ------------------------------------------------------------------------------------------------ budgets

def budget_plan(conn, today: date) -> list[dict]:
    """Each budget that counts (a parent's budget covers its subcategories), with what's been spent this month and the
    account it's paid with: its category's (Settings → Categories), else the account used most for it over the last 90
    days.

    A subcategory with a budget and an account of its own is one of its parent's `parts`: its budget is spent on that
    account and only the rest of the parent's on the parent's (budget_days). One without an account of its own goes on
    the parent's, as does a subcategory's spending without a budget. The parent's usual account is then the one used
    most outside those parts."""
    cats = catmod.all_categories(conn)
    by_name = {c["name"]: c for c in cats}
    pay_with = dict(conn.execute(select(Category.name, Category.pay_with)).fetchall())
    budgets = {r["category"]: r for r in db.rows(conn.execute(select(Budget)))}
    p = splits.parts()
    spent_by: dict[str | None, float] = dict(conn.execute(   # this month, by category (refunds come off)
        select(p.c.category, -func.sum(p.c.amount)).select_from(p).join(Account, Account.id == p.c.account_id)
        .where(*db.SPENDING_ACCOUNTS, p.c.posted >= today.replace(day=1).isoformat(), p.c.posted <= today.isoformat())
        .group_by(p.c.category)).fetchall())
    used = account_use(conn, today)

    def subtree(name: str) -> list[str]:
        return [name] + [k["name"] for k in cats if name in k["path"][:-1]]

    def spent(names: list[str]) -> float:
        return round(max(0.0, sum(spent_by.get(n) or 0.0 for n in names)), 2)

    out = []
    for name, b in budgets.items():
        c = by_name.get(name)
        if not c or c["is_transfer"] or c["is_income"] or any(a in budgets for a in c["path"][:-1]):
            continue
        names = subtree(name)
        # Budgeted subcategories with an account of their own (the outermost, if they nest), in tree order.
        own = [k for k in names[1:] if k in budgets and pay_with.get(k)]
        own = [k for k in own if not any(a in own for a in by_name[k]["path"][:-1])]
        parts = [{"category": k, "amount": budgets[k]["amount"], "names": subtree(k), "spent": spent(subtree(k)),
                  "pay_with": pay_with[k], "usual": None} for k in own]
        rest = [n for n in names if not any(n in q["names"] for q in parts)]
        out.append({"category": name, "amount": b["amount"], "names": names, "spent": spent(names),
                    "pay_with": pay_with.get(name), "usual": usual_account(used, rest), "parts": parts})
    return out


def account_use(conn, today: date) -> dict[str | None, dict[str, float]]:
    """What's gone out of each account for each category over the last 90 days: {category: {account id: amount}}."""
    p = splits.parts()
    out: dict[str | None, dict[str, float]] = defaultdict(dict)
    for cat, acct, amount in conn.execute(
            select(p.c.category, p.c.account_id, func.sum(-p.c.amount)).join(Account, Account.id == p.c.account_id)
            .where(*db.SPENDING_ACCOUNTS, p.c.posted > (today - timedelta(days=90)).isoformat(), p.c.amount < 0)
            .group_by(p.c.category, p.c.account_id)).fetchall():
        out[cat][acct] = amount
    return out


def usual_account(used: dict[str | None, dict[str, float]], names: list[str]) -> str | None:
    """The account the most went out of for these categories (account_use), or None when nothing did."""
    total: dict[str, float] = defaultdict(float)
    for n in names:
        for acct, v in used.get(n, {}).items():
            total[acct] += v
    return max(total, key=lambda a: (total[a], a)) if total else None


def budget_days(conn, today: date, horizon_days: int, plan: list[dict], events: list[dict], cash_ids: set[str],
                on_cards: list[dict] | None = None) -> dict:
    """Each budget's spending day by day over the horizon, from tomorrow, by the account it goes on: {category:
    [{"category", "pay_with", "usual", "days": {date: amount}}]}, the budget's own share first, then each of its parts
    (budget_plan). This month it's what's left of the budget (plus what a budget that rolls over carried into it, less
    what's been spent) over the days left; after that, each month's budget over its days. A budget includes its
    category's recurring payments: the ones the forecast already takes out of its accounts are subtracted from it each
    month, so they aren't counted twice, as are the charges in `on_cards` (recurring ones and annual fees on cards, which
    their statements pay; build passes the ones no budget charged to that card has). A budget they cover entirely is
    None.

    A part gets its own budget's worth of each month's (less what it's spent this month, and its own recurring payments),
    as far as the whole budget's month goes; the budget's share is what's left. So the parts never take the budget below
    nothing, and the shares always add up to the budget's month as a whole."""
    # The recurring payments the forecast takes out of its accounts, and what's charged to cards, by category and month
    # (from today on).
    recurring: dict[tuple[str, str], float] = defaultdict(float)
    for e in [*(e for e in events if e["kind"] == "recurring" and e["account_id"] in cash_ids), *(on_cards or [])]:
        if e["amount"] < 0 and e.get("category"):
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

    def shares(p: dict, month: str) -> list[float]:
        """The month's budget (left) split between the budget's own share and each of its parts, in that order."""
        whole = left(p, month)
        got: list[float] = []
        for q in p.get("parts", []):
            want = max(0.0, q["amount"] - covered(q, month) - (q["spent"] if month == this_month else 0.0))
            got.append(min(want, whole - sum(got)))
        return [max(0.0, whole - sum(got)), *got]

    months = sorted({(today + timedelta(days=i)).isoformat()[:7] for i in range(1, horizon_days + 1)})
    out: dict[str, list[dict] | None] = {}
    for p in plan:
        if all(left(p, m) < CENT for m in months if m != this_month) and any(covered(p, m) for m in months):
            out[p["category"]] = None
            continue
        split = {m: shares(p, m) for m in months}
        ways = [p, *p.get("parts", [])]
        days: list[dict[str, float]] = [{} for _ in ways]
        for i in range(1, horizon_days + 1):
            d = today + timedelta(days=i)
            dim = calendar.monthrange(d.year, d.month)[1]
            month = d.isoformat()[:7]
            for k, v in enumerate(split[month]):
                # this month: whatever's left, over the days left
                days[k][d.isoformat()] = v / (dim - today.day) if month == this_month else v / dim
        out[p["category"]] = [{"category": q["category"], "pay_with": q["pay_with"], "usual": q["usual"], "days": dd}
                              for q, dd in zip(ways, days, strict=True)]
    return out


def monthly_shares(p: dict) -> list[float]:
    """A budget's monthly amount split as budget_days splits a month with nothing spent or covered: each part its own
    budget, as far as the whole goes, and the rest the budget's own. Same order as budget_days."""
    out: list[float] = []
    for q in p.get("parts", []):
        out.append(min(q["amount"], p["amount"] - sum(out)))
    return [p["amount"] - sum(out), *out]


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


def restore_suggestion(conn, key: str) -> bool:
    """Take a suggestion off the dismissed list so it can be offered again; False when it wasn't dismissed."""
    keys = dismissed_suggestions(conn)
    if key not in keys:
        return False
    db.set_setting(conn, sk.RECURRING_SUGGESTIONS_DISMISSED, json.dumps(sorted(keys - {key})))
    return True


def list_dismissed_suggestions(conn) -> list[dict]:
    """The suggestions marked "not recurring", as far as their key tells (the amount isn't kept), for putting one back.
    Each is named as its latest transaction names the payee (the key keeps it lowercased), or by its key's text when
    none is left."""
    names = dict(conn.execute(select(Account.id, db.account_label_expr())).fetchall())
    T = Transaction
    out = []
    for key in sorted(dismissed_suggestions(conn)):
        account_id, _, rest = key.partition("|")
        match, _, frequency = rest.rpartition("|")
        if not match:
            continue
        payee = conn.execute(select(T.payee).where(T.account_id == account_id, func.lower(T.payee) == match)
                             .order_by(T.posted.desc()).limit(1)).scalar()
        out.append({"key": key, "account_id": account_id, "account_name": names.get(account_id), "match": match,
                    "name": payee or match, "frequency": frequency})
    return out


def suggest_recurring(conn, today: date | None = None, lookback_days: int = 150) -> list[dict]:
    """Payees on cash accounts that show up on a regular schedule with similar amounts, minus the ones you've dismissed."""
    today = today or date.today()
    dismissed = dismissed_suggestions(conn)
    transfers = _transfer_categories(conn)
    # A one-time item's texts only claim payments around its date, so they don't hide a payee that repeats.
    known = [(r["account_id"], m) for r in db.rows(conn.execute(select(Recurring))) if r["frequency"] != "once"
             for m in rec.match_texts(r)]
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
        ds = [parse_day(t["posted"]) for t in items]
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
        recent = [abs(x) for x in amounts[-6:]]   # what the last few came to, for "$12–$15" when they vary
        out.append({"key": key, "account_id": acct, "name": items[-1]["payee"], "match": payee, "amount": round(med, 2),
                    "amount_low": round(min(recent), 2), "amount_high": round(max(recent), 2),
                    "frequency": freq, "anchor_date": ds[-1].isoformat(), "count": len(items)})
    out.sort(key=lambda s: -abs(s["amount"]))
    return out
