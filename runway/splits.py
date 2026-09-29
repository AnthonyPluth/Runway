"""Splitting one transaction across several categories.

A $100 run to Target can be $60 of Groceries and $40 of Shopping. The transaction itself is untouched — it keeps
its amount, its account and the category it had — but it's marked `is_split`, and the parts in `tx_splits` are what
budgets and reports count. Remove the parts and the transaction goes back to counting as itself.

Anything that adds up spending by category reads `PARTS` (in SQL text) or `parts()` (in SQLAlchemy statements)
instead of the transactions table: it's the same rows, except a split transaction appears once per part, with that
part's amount and category.
"""
from __future__ import annotations

from sqlalchemy import Integer, Text, func, literal_column, select, union_all

from .models import Transaction, TxSplit

CENT = 0.005

PARTS = """(
    SELECT id, account_id, posted, amount, payee, description, category, category_source, needs_review,
           pending, recurring_id
      FROM transactions WHERE COALESCE(is_split, 0)=0
    UNION ALL
    SELECT t.id, t.account_id, t.posted, s.amount, t.payee, t.description, s.category, 'split', 0,
           t.pending, t.recurring_id
      FROM transactions t JOIN tx_splits s ON s.tx_id=t.id WHERE t.is_split=1
)"""


def parts(name: str = "t"):
    """PARTS as a SQLAlchemy subquery: `p = splits.parts(); select(p.c.category, func.sum(p.c.amount))...`."""
    t, s = Transaction, TxSplit
    whole = select(t.id, t.account_id, t.posted, t.amount, t.payee, t.description, t.category, t.category_source,
                   t.needs_review, t.pending, t.recurring_id).where(func.coalesce(t.is_split, 0) == 0)
    split = (select(t.id, t.account_id, t.posted, s.amount, t.payee, t.description, s.category,
                    literal_column("'split'", Text), literal_column("0", Integer), t.pending, t.recurring_id)
             .join(s, s.tx_id == t.id).where(t.is_split == 1))
    return union_all(whole, split).subquery(name)


class SplitError(ValueError):
    pass


def of(conn, tx_ids: list[str]) -> dict[str, list[dict]]:
    """The parts of each of these transactions, by transaction id (split ones only)."""
    if not tx_ids:
        return {}
    q = ",".join("?" * len(tx_ids))
    out: dict[str, list[dict]] = {}
    for r in conn.execute(f"SELECT id, tx_id, amount, category, note FROM tx_splits WHERE tx_id IN ({q}) "
                          "ORDER BY tx_id, position, id", tx_ids):
        out.setdefault(r["tx_id"], []).append(
            {"id": r["id"], "amount": r["amount"], "category": r["category"], "note": r["note"]})
    return out


def get(conn, tx_id: str) -> list[dict]:
    return of(conn, [tx_id]).get(tx_id, [])


def clear(conn, tx_id: str) -> None:
    """Undo a split: the transaction counts as itself again, under the category it already had."""
    conn.execute("DELETE FROM tx_splits WHERE tx_id=?", (tx_id,))
    conn.execute("UPDATE transactions SET is_split=0 WHERE id=?", (tx_id,))


def carry_over(conn, old_id: str, new_id: str, amount: float) -> None:
    """A pending transaction posts under a new id: its parts follow it, as long as the amount hasn't changed."""
    total = conn.execute("SELECT SUM(amount) FROM tx_splits WHERE tx_id=?", (old_id,)).fetchone()[0]
    if total is not None and abs(round(total, 2) - round(amount, 2)) <= CENT:
        conn.execute("UPDATE tx_splits SET tx_id=? WHERE tx_id=?", (new_id, old_id))
        conn.execute("UPDATE transactions SET is_split=1 WHERE id=?", (new_id,))
    else:
        conn.execute("DELETE FROM tx_splits WHERE tx_id=?", (old_id,))


def follow_amount(conn, tx_id: str, amount: float) -> None:
    """The bank changed a split transaction's amount in place (a tip added when it posted, say): the parts are
    scaled to the new amount, in whole cents that add up exactly, and it goes to Review to check."""
    parts = conn.execute("SELECT id, amount FROM tx_splits WHERE tx_id=? ORDER BY position, id", (tx_id,)).fetchall()
    old = round(sum(p["amount"] for p in parts), 2)
    if not parts or abs(old - round(amount, 2)) <= CENT:
        return
    if abs(old) < CENT or (old < 0) != (amount < 0):   # nothing to scale by: undo the split instead
        clear(conn, tx_id)
    else:
        cents = round(amount * 100)
        shares = [p["amount"] * 100 * amount / old for p in parts]
        new = [int(x) for x in shares]   # toward zero; the cents left over go to the parts that lost the most
        left = cents - sum(new)
        step = 1 if left > 0 else -1
        for i in sorted(range(len(parts)), key=lambda i: -abs(shares[i] - new[i]))[:abs(left)]:
            new[i] += step
        for p, n in zip(parts, new, strict=True):
            conn.execute("UPDATE tx_splits SET amount=? WHERE id=?", (n / 100, p["id"]))
    conn.execute("UPDATE transactions SET needs_review=1 WHERE id=?", (tx_id,))


def prune(conn) -> None:
    """Drop parts whose transaction is gone (the bank took it back, say)."""
    conn.execute("DELETE FROM tx_splits WHERE tx_id NOT IN (SELECT id FROM transactions)")


def set_splits(conn, tx_id: str, parts: list[dict]) -> list[dict]:
    """Replace a transaction's parts. An empty list undoes the split. Each part is {amount, category, note}, and
    the parts must add up to the transaction's amount."""
    tx = conn.execute("SELECT amount FROM transactions WHERE id=?", (tx_id,)).fetchone()
    if not tx:
        raise SplitError("Transaction not found")
    if not parts:
        clear(conn, tx_id)
        return []
    if len(parts) < 2:
        raise SplitError("A split needs at least two parts")
    clean = []
    for p in parts:
        try:
            amount = round(float(p.get("amount")), 2)  # type: ignore[arg-type]  # a missing amount raises TypeError, handled below
        except (TypeError, ValueError):
            raise SplitError("Every part needs an amount") from None
        if not abs(amount) >= CENT or abs(amount) == float("inf"):   # also refuses "nan", which fails every comparison
            raise SplitError("Every part needs an amount")
        category = (p.get("category") or "").strip()
        if not category:
            raise SplitError("Every part needs a category")
        if not conn.execute("SELECT 1 FROM categories WHERE name=?", (category,)).fetchone():
            raise SplitError(f"Unknown category: {category}")
        clean.append((amount, category, (p.get("note") or "").strip() or None))
    total = round(sum(a for a, _c, _n in clean), 2)
    if abs(total - tx["amount"]) > CENT:
        raise SplitError(f"The parts add up to ${abs(total):,.2f}, but the transaction is ${abs(tx['amount']):,.2f}")

    conn.execute("DELETE FROM tx_splits WHERE tx_id=?", (tx_id,))
    for i, (amount, category, note) in enumerate(clean):
        conn.execute("INSERT INTO tx_splits(tx_id, amount, category, note, position) VALUES (?,?,?,?,?)",
                     (tx_id, amount, category, note, i))
    conn.execute("UPDATE transactions SET is_split=1, needs_review=0 WHERE id=?", (tx_id,))
    return get(conn, tx_id)
