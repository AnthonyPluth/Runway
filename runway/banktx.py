"""A bank or card transaction, whichever provider brings it in (SimpleFIN or Plaid): what storing one works the same
way for both. Fetching, and how each provider says which pending transaction a posted one replaces, stay in
simplefin.py and plaidbank.py.

  - A transaction the sync already has: the bank's date and amount are written again, except where you changed one
    (bank_values).
  - An account that switched provider: the history both have is matched up (the same amount within OVERLAP_DAYS) so
    nothing is counted twice (duplicate).
  - A pending transaction that posts under a new id: what you did with it goes over to the posted one (store), so it
    doesn't go back through review or lose its name, its note, its parts or its recurring item.
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import insert, select

from . import payees, splits
from .models import Transaction

PLAID_IDS = "%|pl:%"   # LIKE pattern for the ids of transactions from Plaid ("<account>|pl:<Plaid's id>")
OVERLAP_DAYS = 3       # the same transaction can post a few days apart at two providers
# An account switched to Plaid takes Plaid's history from this long before the switch (matched up with SimpleFIN's,
# which may not have the last few days yet); anything older is SimpleFIN's. The other way round, SimpleFIN checks what
# it re-reads on a routine sync against Plaid's (simplefin.SINCE_PLAID_DAYS).
SINCE_SIMPLEFIN_DAYS = 7


def near(posted: str, amount: float) -> tuple:
    """Conditions for the same transaction at the other provider: the same amount, posted within OVERLAP_DAYS."""
    d = date.fromisoformat(posted)
    return (Transaction.posted >= (d - timedelta(days=OVERLAP_DAYS)).isoformat(),
            Transaction.posted <= (d + timedelta(days=OVERLAP_DAYS)).isoformat(),
            Transaction.amount > amount - 0.005, Transaction.amount < amount + 0.005)


def bank_values(row, posted: str, amount: float) -> dict:
    """What a sync writes for a transaction it already has: the bank's date and amount, except where you changed one
    (then the bank's goes beside it, in bank_posted or bank_amount, and yours stays)."""
    return {"bank_posted" if row["bank_posted"] is not None else "posted": posted,
            "bank_amount" if row["bank_amount"] is not None else "amount": amount}


def duplicate(conn, account_id: str, posted: str, amount: float, from_plaid: bool, claimed: set) -> bool:
    """Whether the other provider already brought this transaction in (same account and amount, within a few
    days). Each earlier transaction stands in for one new one only (claimed)."""
    other = Transaction.id.not_like(PLAID_IDS) if from_plaid else Transaction.id.like(PLAID_IDS)
    for tid in conn.execute(select(Transaction.id).where(Transaction.account_id == account_id, *near(posted, amount), other)
                            .order_by(Transaction.posted)).scalars():
        if tid not in claimed:
            claimed.add(tid)
            return True
    return False


# What a posted transaction takes over from its pending version: always (a note you wrote, the recurring item you linked
# it to, or that you said it isn't one: recurring_id 0), and once you've dealt with it (categorized or split it).
ALWAYS_KEPT = ("notes", "recurring_id", "recurring_linked_by")
KEPT_ONCE_DONE = ("category", "category_source", "confidence", "needs_review")
# The pending version's columns to read for store(): select(*PENDING).
PENDING = (Transaction.id, Transaction.description, Transaction.payee, Transaction.amount, Transaction.is_split,
           *(getattr(Transaction, c) for c in ALWAYS_KEPT + KEPT_ONCE_DONE))


def _done(prior) -> bool:
    return bool(prior["category"] or prior["is_split"])


def kept_payee(prior, payee: str | None) -> str | None:
    """The name a posted transaction goes by: its pending version's when you'd dealt with that (a rule may have renamed
    it) or gave it a name that isn't the bank's; otherwise its own (the bank may name the merchant only once it posts)."""
    if prior["payee"] and (_done(prior) or not payees.from_bank(prior["payee"], prior["description"])):
        return prior["payee"]
    return payee


def store(conn, row: dict, prior=None) -> bool:
    """Save a new transaction (row: id, account_id, posted, amount, description, payee, pending), taking over what you
    did with its pending version `prior` (a row of PENDING, already deleted) if it had one. Returns whether it needs
    categorizing: it's new, or its pending version was never categorized or split."""
    if prior is None:
        conn.execute(insert(Transaction).values(**row))
        return True
    done = _done(prior)
    kept = ALWAYS_KEPT + KEPT_ONCE_DONE if done else ALWAYS_KEPT
    conn.execute(insert(Transaction).values({**row, "payee": kept_payee(prior, row.get("payee")), **{c: prior[c] for c in kept}}))
    splits.carry_over(conn, prior["id"], row["id"], row["amount"])
    return not done
