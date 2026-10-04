"""Credit card statements you enter by hand, one per statement, for a card Plaid sends none for.

A card's statement (balance, closing date, due date, minimum payment) is how the forecast knows what the card owes and
when. Plaid Liabilities sends it for a card whose bank shares it (plaidbank.statement); that one always wins, and
Settings shows it read only. Otherwise the latest statement you entered stands in for it, in the same shape, so the
forecast, the card's due date, its "payment due" notification and the statement-balance correction (`stmt:` overrides)
work the same either way. Earlier ones are kept, as a record.

Staleness: a statement you entered says nothing about the next one, so it's only trusted for a month or so.

  - Its next statement is expected to close a month after it did, on the same day of the month (the 31st: the
    month's last day). That's next_close().
  - Up to GRACE_DAYS after that (statements take a few days to show up at the bank), it's fresh: the forecast treats
    it exactly as it would Plaid's: its balance (less what's been paid since) is paid on its due date, and the
    statements after it are estimated from the card's spending, closing on the same day of each month.
  - After that, with no newer one entered, it's stale: the forecast still counts its own payment, up to its due date,
    but invents nothing after it (no estimated statements), and Overview asks for the latest statement.
  - A statement can't close after today, and its due date comes after its closing date.
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import delete, select

from . import db
from .dates import add_months, parse_day
from .models import Account, ManualStatement

GRACE_DAYS = 5          # days after the next expected close before a statement you entered counts as stale
MAX_DUE_DAYS = 90       # a due date further than this after the close is surely a typo


def next_close(close: date) -> date:
    """When the statement after one that closed on `close` is expected to close: the same day, a month later."""
    return add_months(close, 1)


def is_stale(close: date, today: date) -> bool:
    return today > next_close(close) + timedelta(days=GRACE_DAYS)


def _day(value, what: str) -> date:
    try:
        return parse_day(str(value or ""))
    except ValueError:
        raise ValueError(f"Enter the {what} (YYYY-MM-DD).") from None


def _amount(value, what: str, required: bool) -> float | None:
    if value in (None, ""):
        if required:
            raise ValueError(f"Enter the {what}.")
        return None
    try:
        n = db.number(value)
    except (TypeError, ValueError):
        raise ValueError(f"The {what} must be a number.") from None
    if n < 0:
        raise ValueError(f"The {what} can't be negative.")
    return round(n, 2)


def add(conn, account_id: str, body: dict, today: date | None = None) -> dict:
    """Save a statement you entered for a card (a second one with the same closing date replaces the first).
    Raises LookupError for an account that isn't there, ValueError for anything else wrong with it."""
    today = today or date.today()
    acct = conn.execute(select(Account.kind).where(Account.id == account_id)).fetchone()
    if not acct:
        raise LookupError("Account not found")
    if acct["kind"] != "credit":
        raise ValueError("Statements are for credit cards only.")
    close = _day(body.get("statement_date"), "closing date")
    due = _day(body.get("due_date"), "due date")
    if close > today:
        raise ValueError("That statement hasn't closed yet: its closing date is after today.")
    if due <= close:
        raise ValueError("The due date comes after the closing date.")
    if (due - close).days > MAX_DUE_DAYS:
        raise ValueError(f"The due date is more than {MAX_DUE_DAYS} days after the closing date. Check the dates.")
    balance = _amount(body.get("balance"), "statement balance", True)
    minimum = _amount(body.get("minimum_payment"), "minimum payment", False)
    row = {"account_id": account_id, "statement_date": close.isoformat(), "balance": balance, "due_date": due.isoformat(),
           "minimum_payment": minimum}
    db.upsert(conn, ManualStatement, row, key=["account_id", "statement_date"],
              update=["balance", "due_date", "minimum_payment"])
    return row


def remove(conn, account_id: str, statement_date: str) -> bool:
    return conn.execute(delete(ManualStatement).where(ManualStatement.account_id == account_id,
                                                      ManualStatement.statement_date == statement_date)).rowcount > 0


def history(conn, account_id: str) -> list[dict]:
    """The statements you entered for a card, newest first."""
    m = ManualStatement
    return db.rows(conn.execute(select(m.statement_date, m.balance, m.due_date, m.minimum_payment, m.entered_at)
                                .where(m.account_id == account_id).order_by(m.statement_date.desc())))


def latest(conn, account_id: str, today: date) -> dict | None:
    """The latest statement you entered for a card, in the shape of Plaid's (plaidbank.statement), with source "manual",
    whether it's stale, and when the next one is expected to close."""
    m = ManualStatement
    r = conn.execute(select(m).where(m.account_id == account_id, m.statement_date <= today.isoformat())
                     .order_by(m.statement_date.desc()).limit(1)).fetchone()
    if not r:
        return None
    close = date.fromisoformat(r["statement_date"])
    return {"last_statement_balance": r["balance"], "last_statement_date": r["statement_date"], "next_due_date": r["due_date"],
            "minimum_payment": r["minimum_payment"], "source": "manual", "stale": is_stale(close, today),
            "next_close": next_close(close).isoformat()}
