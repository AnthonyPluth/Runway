"""Splitting one transaction across several categories.

A $100 run to Target can be $60 of Groceries and $40 of Shopping. The transaction itself is untouched — it keeps
its amount, its account and the category it had — but it's marked `is_split`, and the parts in `tx_splits` are what
budgets and reports count. Remove the parts and the transaction goes back to counting as itself.

Anything that adds up spending by category reads `parts()` instead of the transactions table: it's the same rows,
except a split transaction appears once per part, with that part's amount and category.
"""
from __future__ import annotations

from sqlalchemy import Integer, Text, delete, func, insert, literal_column, select, union_all, update

from ..storage.models import Category, Transaction, TxSplit
from ..money import CENT


def parts(name: str = "t"):
    """The transactions, a split one as its parts (category_source 'split', needs_review 0), as a subquery:
    `p = splits.parts(); select(p.c.category, func.sum(p.c.amount))...`."""
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
    s = TxSplit
    out: dict[str, list[dict]] = {}
    for r in conn.execute(select(s.id, s.tx_id, s.amount, s.category, s.note).where(s.tx_id.in_(tx_ids))
                          .order_by(s.tx_id, s.position, s.id)):
        out.setdefault(r["tx_id"], []).append(
            {"id": r["id"], "amount": r["amount"], "category": r["category"], "note": r["note"]})
    return out


def get(conn, tx_id: str) -> list[dict]:
    return of(conn, [tx_id]).get(tx_id, [])


def clear(conn, tx_id: str) -> None:
    """Undo a split: the transaction counts as itself again, under the category it already had."""
    conn.execute(delete(TxSplit).where(TxSplit.tx_id == tx_id))
    conn.execute(update(Transaction).where(Transaction.id == tx_id).values(is_split=0))


def carry_over(conn, old_id: str, new_id: str, amount: float) -> None:
    """A pending transaction posts under a new id: its parts follow it, as long as the amount hasn't changed."""
    total = conn.execute(select(func.sum(TxSplit.amount)).where(TxSplit.tx_id == old_id)).scalar()
    if total is not None and abs(round(total, 2) - round(amount, 2)) <= CENT:
        conn.execute(update(TxSplit).where(TxSplit.tx_id == old_id).values(tx_id=new_id))
        conn.execute(update(Transaction).where(Transaction.id == new_id).values(is_split=1))
    else:
        conn.execute(delete(TxSplit).where(TxSplit.tx_id == old_id))


def follow_amount(conn, tx_id: str, amount: float) -> None:
    """The bank changed a split transaction's amount in place (a tip added when it posted, say): the parts are
    scaled to the new amount, in whole cents that add up exactly, and it goes to Review to check."""
    parts = conn.execute(select(TxSplit.id, TxSplit.amount).where(TxSplit.tx_id == tx_id)
                         .order_by(TxSplit.position, TxSplit.id)).fetchall()
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
            conn.execute(update(TxSplit).where(TxSplit.id == p["id"]).values(amount=n / 100))
    conn.execute(update(Transaction).where(Transaction.id == tx_id).values(needs_review=1))


def recategorize(conn, tx_id: str, family: set[str], category: str) -> bool:
    """Give the parts in these categories (`family`: a category and its subcategories) `category` instead, leaving the
    other parts as they are. When every part then has one category the transaction isn't split any more: it takes
    that category, as if you'd picked it for the whole thing. Returns whether any part was in `family`."""
    s = TxSplit
    if not conn.execute(update(s).where(s.tx_id == tx_id, s.category.in_(family)).values(category=category)).rowcount:
        return False
    left = {r[0] for r in conn.execute(select(s.category).where(s.tx_id == tx_id))}
    if left == {category}:
        clear(conn, tx_id)
        conn.execute(update(Transaction).where(Transaction.id == tx_id)
                     .values(category=category, category_source="manual", confidence=1))
    conn.execute(update(Transaction).where(Transaction.id == tx_id).values(needs_review=0))
    return True


def prune(conn) -> None:
    """Drop parts whose transaction is gone (the bank took it back, say)."""
    conn.execute(delete(TxSplit).where(TxSplit.tx_id.not_in(select(Transaction.id))))


def set_splits(conn, tx_id: str, parts: list[dict]) -> list[dict]:
    """Replace a transaction's parts. An empty list undoes the split. Each part is {amount, category, note}, and
    the parts must add up to the transaction's amount."""
    tx = conn.execute(select(Transaction.amount).where(Transaction.id == tx_id)).fetchone()
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
        if not conn.execute(select(Category.name).where(Category.name == category)).fetchone():
            raise SplitError(f"Unknown category: {category}")
        clean.append((amount, category, (p.get("note") or "").strip() or None))
    total = round(sum(a for a, _c, _n in clean), 2)
    if abs(total - tx["amount"]) > CENT:
        raise SplitError(f"The parts add up to ${abs(total):,.2f}, but the transaction is ${abs(tx['amount']):,.2f}")

    conn.execute(delete(TxSplit).where(TxSplit.tx_id == tx_id))
    for i, (amount, category, note) in enumerate(clean):
        conn.execute(insert(TxSplit).values(tx_id=tx_id, amount=amount, category=category, note=note, position=i))
    conn.execute(update(Transaction).where(Transaction.id == tx_id).values(is_split=1, needs_review=0))
    return get(conn, tx_id)
