"""Recurring items: matching them to real transactions, and working out what the next one will be."""
from __future__ import annotations

import statistics
from datetime import date, timedelta

from sqlalchemy import func, insert, or_, select, update

from . import db
from .models import Account, Recurring, RecurringDismissed, Transaction

# How far a real payment can land from its expected date and still count as that occurrence.
MATCH_WINDOW_DAYS = {"weekly": 2, "biweekly": 4, "semimonthly": 4, "monthly": 6, "quarterly": 10,
                     "semiannual": 12, "yearly": 12, "dates": 12}
AMOUNT_MODES = {"fixed", "last", "avg3"}
# How far a payment's amount can be from the item's and still be linked to it automatically: a fixed bill barely
# moves; one that varies (electricity) can swing more. Linking one Amazon charge to Prime shouldn't link every order.
AMOUNT_TOLERANCE = {"fixed": 0.3}
DEFAULT_TOLERANCE = 0.6


def match_text(item: dict) -> str:
    return (item.get("match") or item.get("name") or "").strip().lower()


def amount_range(item: dict) -> tuple[float, float] | None:
    """The amounts (as positive dollars) a payment can have and still be linked to this item on its own."""
    amount = abs(item.get("amount") or 0)
    if amount < 0.005:
        return None
    tol = AMOUNT_TOLERANCE.get(item.get("amount_mode") or "fixed", DEFAULT_TOLERANCE)
    return round(amount * (1 - tol), 2), round(amount * (1 + tol), 2)


def auto_match(conn, recurring_ids: list[int] | None = None) -> int:
    """Link unlinked transactions to recurring items by merchant text, when the amount is close to the item's (see
    amount_range). Money-in items only match money in, and money-out items only match money out.
    Transactions marked 'never match' (recurring_id = 0) and ones already linked are left alone."""
    q = select(Recurring).where(Recurring.active == 1)
    if recurring_ids:
        q = q.where(Recurring.id.in_(list(recurring_ids)))
    items = sorted(db.rows(conn.execute(q)), key=lambda r: -len(match_text(r)))  # most specific first
    linked = 0
    t = Transaction
    for item in items:
        m = match_text(item)
        if len(m) < 3:
            continue
        span = amount_range(item)
        where = [t.recurring_id.is_(None), t.account_id == item["account_id"], t.amount > 0 if item["amount"] > 0 else t.amount < 0,
                 or_(db.instr(func.lower(t.payee), m) > 0, db.instr(func.lower(t.description), m) > 0)]
        if span:
            where += [func.abs(t.amount) >= span[0], func.abs(t.amount) <= span[1]]
        linked += conn.execute(update(t).where(*where).values(recurring_id=item["id"])).rowcount
    return linked


def link(conn, tx_id: str, recurring_id: int | None) -> None:
    """Link a transaction to a recurring item (None = mark as not recurring). The item learns the merchant
    text from the transaction if it doesn't have one, so future payments match on their own."""
    tx = _transaction(conn, tx_id)
    if not tx:
        raise ValueError("Transaction not found")
    if recurring_id is None:
        conn.execute(update(Transaction).where(Transaction.id == tx_id).values(recurring_id=0))
        return
    item = conn.execute(select(Recurring).where(Recurring.id == recurring_id)).fetchone()
    if not item:
        raise ValueError("Recurring item not found")
    conn.execute(update(Transaction).where(Transaction.id == tx_id).values(recurring_id=recurring_id))
    if not item["match"] and (tx["payee"] or tx["description"]):
        conn.execute(update(Recurring).where(Recurring.id == recurring_id)
                     .values(match=(tx["payee"] or tx["description"]).lower()))
        auto_match(conn, [recurring_id])


def _transaction(conn, tx_id: str):
    return conn.execute(select(Transaction).where(Transaction.id == tx_id)).fetchone()


def create_from_transaction(conn, tx_id: str, frequency: str = "monthly") -> int:
    tx = _transaction(conn, tx_id)
    if not tx:
        raise ValueError("Transaction not found")
    name = tx["payee"] or tx["description"] or "Recurring item"
    rid = conn.execute(insert(Recurring).values(
        name=name, account_id=tx["account_id"], amount=tx["amount"], frequency=frequency, anchor_date=tx["posted"],
        match=name.lower(), amount_mode="fixed")).lastrowid
    conn.execute(update(Transaction).where(Transaction.id == tx_id).values(recurring_id=rid))
    auto_match(conn, [rid])
    return rid


def matched(conn, recurring_id: int, limit: int = 12) -> list[dict]:
    t = Transaction
    return db.rows(conn.execute(
        select(t.id, t.posted, t.amount, t.description, t.pending, t.category)
        .where(t.recurring_id == recurring_id).order_by(t.posted.desc()).limit(limit)))


def with_account_name():
    """Every recurring item's columns plus its account's name (account_name; None if the account is gone)."""
    return (select(Recurring, db.account_label_expr().label("account_name"))
            .outerjoin(Account, Account.id == Recurring.account_id))


def expected_amount(item: dict, history: list[dict]) -> float:
    """Fixed amount, or learn it from recent real payments (handy for bills that vary)."""
    posted = [t["amount"] for t in history if not t["pending"]]
    mode = item.get("amount_mode") or "fixed"
    if mode == "last" and posted:
        return round(posted[0], 2)
    if mode == "avg3" and posted:
        return round(statistics.mean(posted[:3]), 2)
    return round(item["amount"], 2)


def already_happened(item: dict, occurrence: date, history: list[dict], today: date) -> bool:
    """True if a real payment for this occurrence has already shown up (early or on time)."""
    window = MATCH_WINDOW_DAYS.get(item["frequency"], 6)
    if occurrence - timedelta(days=window) > today:
        return False  # too far out for anything to have posted yet
    lo = (occurrence - timedelta(days=window)).isoformat()
    hi = today.isoformat()
    return any(lo <= t["posted"] <= hi for t in history)


LOOKBACK_DAYS = 60


def missed(conn, today: date | None = None, lookback: int = LOOKBACK_DAYS) -> list[dict]:
    """Scheduled occurrences in the last `lookback` days with no matching transaction by now.

    An occurrence counts as missed once its matching window has passed (e.g. 6 days for a monthly bill) without a
    linked transaction within that window either side of the date. Occurrences before the item's start date, before
    the account's synced history begins, or that you've dismissed are skipped."""
    from . import forecast   # forecast imports this module

    today = today or date.today()
    dismissed = set(conn.execute(select(RecurringDismissed.key)).scalars())
    out = []
    t = Transaction
    for item in db.rows(conn.execute(with_account_name().where(Recurring.active == 1))):
        window = MATCH_WINDOW_DAYS.get(item["frequency"], 6)
        first_tx = conn.execute(select(func.min(t.posted)).where(t.account_id == item["account_id"])).scalar()
        if not first_tx:
            continue
        start = max(today - timedelta(days=lookback), date.fromisoformat(item["anchor_date"]) - timedelta(days=1),
                    date.fromisoformat(first_tx) + timedelta(days=window))
        hist = conn.execute(select(t.posted).where(t.recurring_id == item["id"],
                                                   t.posted >= (start - timedelta(days=40)).isoformat())).scalars()
        for occ in forecast.occurrences(item, start, today - timedelta(days=window + 1)):
            key = f"rec:{item['id']}:{occ.isoformat()}"
            if key in dismissed:
                continue
            lo, hi = (occ - timedelta(days=window)).isoformat(), (occ + timedelta(days=window)).isoformat()
            if any(lo <= p <= hi for p in hist):
                continue
            out.append({"key": key, "recurring_id": item["id"], "name": item["name"], "date": occ.isoformat(),
                        "amount": round(item["amount"], 2), "account_id": item["account_id"], "account_name": item["account_name"],
                        "match": item["match"], "window": window})
    out.sort(key=lambda m: m["date"], reverse=True)
    return out


def dismiss(conn, key: str) -> None:
    db.insert_ignore(conn, RecurringDismissed, {"key": key}, key=["key"])
