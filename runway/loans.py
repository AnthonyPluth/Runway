"""Loans: what's still owed on one at a future date, for the retirement planner's home sales.

A loan's terms (annual interest rate and monthly payment) come from the lender through Plaid Liabilities for mortgages
and student loans (loan_terms), or from you on the account in Settings → Accounts (accounts.interest_rate and
monthly_payment) for any other loan. With a rate but no payment, the payment is the one that pays the loan off by its
maturity date (Plaid's), else what's been paid into the account in a typical recent month. With no rate, nothing is
guessed: what's owed stays at today's balance.

The balance is amortized month by month from today's: B' = B·(1 + rate/12) − payment, until it's paid off.
"""
from __future__ import annotations

import statistics
from datetime import date

from sqlalchemy import select

from . import db, forecast
from .models import Account, LoanTerms, Transaction

MAX_YEARS = 100          # as far as the planner reaches (a sale up to 100 years out)
INFER_MONTHS = 6         # recent whole months of payments to infer a monthly payment from
INFER_MIN_MONTHS = 2     # ... of which at least this many must have payments


def _add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    return date(d.year + y, m + 1, 1)


def _months_until(today: date, until: date) -> int:
    return (until.year - today.year) * 12 + (until.month - today.month)


def payment_to_pay_off(balance: float, annual_rate: float, months: int) -> float:
    """The level monthly payment that pays `balance` off in `months` months at this annual rate (percent)."""
    r = annual_rate / 100 / 12
    if months <= 0:
        return balance
    if r == 0:
        return balance / months
    return balance * r / (1 - (1 + r) ** -months)


def project(balance: float, annual_rate: float, payment: float, years: int = MAX_YEARS) -> tuple[list[float], bool]:
    """What's owed 0, 1, 2… years from today, until it's paid off (the last entry, 0, holds from then on).
    The second value is True when the payment doesn't cover the interest: the balance would only grow, so it's held at
    today's instead (the list is then just today's balance)."""
    if balance <= 0:
        return [round(balance, 2)], False
    r = annual_rate / 100 / 12
    if payment <= balance * r or payment <= 0:
        return [round(balance, 2)], True
    out, b = [round(balance, 2)], balance
    for _year in range(years):
        for _month in range(12):
            b = b * (1 + r) - payment
            if b <= 0:
                break
        b = max(0.0, b)
        out.append(round(b, 2))
        if b == 0:
            break
    return out, False


def inferred_payments(conn, account_ids: list[str], today: date) -> dict[str, float]:
    """A typical month's payments into each of these loan accounts (money in: it lowers what's owed), over the last
    INFER_MONTHS whole months: the median of the months that had any. Accounts without enough history are left out."""
    if not account_ids:
        return {}
    start, end = _add_months(today, -INFER_MONTHS), date(today.year, today.month, 1)
    by_month: dict[str, dict[str, float]] = {}
    for t in conn.execute(select(Transaction.account_id, Transaction.posted, Transaction.amount)
                          .where(Transaction.account_id.in_(account_ids), Transaction.amount > 0, Transaction.pending == 0,
                                 Transaction.posted >= start.isoformat(), Transaction.posted < end.isoformat())):
        months = by_month.setdefault(t["account_id"], {})
        months[t["posted"][:7]] = months.get(t["posted"][:7], 0.0) + t["amount"]
    return {aid: round(statistics.median(m.values()), 2) for aid, m in by_month.items() if len(m) >= INFER_MIN_MONTHS}


def terms(conn, today: date, account_ids: list[str] | None = None) -> dict[str, dict]:
    """Each loan account's terms, by account id: {rate, payment, maturity, source, plaid, plaid_payment, set_rate,
    set_payment, inferred_payment}. `rate` (annual, percent) and `payment` are what a projection uses (None when
    unknown); `source` says where the payment came from ("plaid", "manual" or "inferred"; None without both).
    Each figure is Plaid's when Plaid has it, and then can't be set: `plaid` is True when the rate is Plaid's,
    `plaid_payment` when the payment is. What Plaid leaves out (a new loan's payment, say) you can set, so
    `set_rate`/`set_payment` are what you set; `inferred_payment` is what recent payments into the account suggest."""
    q = (select(Account.id, Account.kind, Account.balance, Account.owed_positive, Account.interest_rate,
                Account.monthly_payment, LoanTerms.interest_rate.label("plaid_rate"),
                LoanTerms.monthly_payment.label("plaid_payment"), LoanTerms.maturity_date)
         .outerjoin(LoanTerms, LoanTerms.plaid_account_id == Account.plaid_account_id).where(Account.kind == "loan"))
    if account_ids is not None:
        q = q.where(Account.id.in_(account_ids))
    loans = db.rows(conn.execute(q))
    hints = inferred_payments(conn, [a["id"] for a in loans], today)
    out = {}
    for a in loans:
        plaid = a["plaid_rate"] is not None
        # Plaid's payment can be 0 (a student loan in deferment): that's not one to project with, so it's yours to set
        plaid_payment = bool(a["plaid_payment"])
        rate = a["plaid_rate"] if plaid else a["interest_rate"]
        payment = a["plaid_payment"] if plaid_payment else a["monthly_payment"]
        source = ("plaid" if plaid_payment else "manual") if payment is not None else None
        maturity = a["maturity_date"]
        if rate is not None and payment is None and maturity:
            try:
                months = _months_until(today, date.fromisoformat(maturity))
            except ValueError:
                months = 0
            if months > 0:
                owed = forecast.owed({**a, "balance": a["balance"] or 0.0})
                payment, source = round(payment_to_pay_off(max(0.0, owed), rate, months), 2), "plaid"
        hint = hints.get(a["id"])
        if rate is not None and payment is None and hint:
            payment, source = hint, "inferred"
        out[a["id"]] = {"rate": rate, "payment": payment, "maturity": maturity, "source": source if rate is not None else None,
                        "plaid": plaid, "plaid_payment": plaid_payment, "set_rate": a["interest_rate"], "set_payment": a["monthly_payment"],
                        "inferred_payment": hint}
    return out


def owed_by_year(owed_today: float, t: dict | None) -> tuple[list[float], dict]:
    """What's owed 0, 1, 2… years from today on a loan with these terms (from terms()), and what it was based on:
    {rate, payment, source, note}. note: "no_rate" (nothing to project with: today's balance stays), "no_payment"
    (a rate but no payment, set or seen), "payment_below_interest" (the payment doesn't cover the interest), or None."""
    if not t or t["rate"] is None:
        return [round(owed_today, 2)], {"rate": None, "payment": None, "source": None, "note": "no_rate"}
    if t["payment"] is None:
        return [round(owed_today, 2)], {"rate": t["rate"], "payment": None, "source": None, "note": "no_payment"}
    years, short = project(owed_today, t["rate"], t["payment"])
    return years, {"rate": t["rate"], "payment": t["payment"], "source": t["source"],
                   "note": "payment_below_interest" if short else None}
