"""Recurring items: matching them to real transactions, and working out what the next one will be."""
from __future__ import annotations

import statistics
from datetime import date, timedelta

from . import db

# How far a real payment can land from its expected date and still count as that occurrence.
MATCH_WINDOW_DAYS = {"weekly": 2, "biweekly": 4, "monthly": 6, "yearly": 12}
AMOUNT_MODES = {"fixed", "last", "avg3"}


def match_text(item: dict) -> str:
    return (item.get("match") or item.get("name") or "").strip().lower()


def auto_match(conn, recurring_ids: list[int] | None = None) -> int:
    """Link unlinked transactions to recurring items by merchant text, whatever the amount.
    Money-in items only match money in, and money-out items only match money out.
    Transactions marked 'never match' (recurring_id = 0) and ones already linked are left alone."""
    q = "SELECT * FROM recurring WHERE active=1"
    args: list = []
    if recurring_ids:
        q += f" AND id IN ({','.join('?' * len(recurring_ids))})"
        args = list(recurring_ids)
    items = sorted(db.rows(conn.execute(q, args)), key=lambda r: -len(match_text(r)))  # most specific first
    linked = 0
    for item in items:
        m = match_text(item)
        if len(m) < 3:
            continue
        sign = ">" if item["amount"] > 0 else "<"
        cur = conn.execute(
            f"UPDATE transactions SET recurring_id=? WHERE recurring_id IS NULL AND account_id=? AND amount {sign} 0 "
            "AND (instr(lower(payee), ?) > 0 OR instr(lower(description), ?) > 0)",
            (item["id"], item["account_id"], m, m),
        )
        linked += cur.rowcount
    return linked


def link(conn, tx_id: str, recurring_id: int | None) -> None:
    """Link a transaction to a recurring item (None = mark as not recurring). The item learns the merchant
    text from the transaction if it doesn't have one, so future payments match on their own."""
    tx = conn.execute("SELECT * FROM transactions WHERE id=?", (tx_id,)).fetchone()
    if not tx:
        raise ValueError("Transaction not found")
    if recurring_id is None:
        conn.execute("UPDATE transactions SET recurring_id=0 WHERE id=?", (tx_id,))
        return
    item = conn.execute("SELECT * FROM recurring WHERE id=?", (recurring_id,)).fetchone()
    if not item:
        raise ValueError("Recurring item not found")
    conn.execute("UPDATE transactions SET recurring_id=? WHERE id=?", (recurring_id, tx_id))
    if not item["match"] and (tx["payee"] or tx["description"]):
        conn.execute("UPDATE recurring SET match=? WHERE id=?", ((tx["payee"] or tx["description"]).lower(), recurring_id))
        auto_match(conn, [recurring_id])


def create_from_transaction(conn, tx_id: str, frequency: str = "monthly") -> int:
    tx = conn.execute("SELECT * FROM transactions WHERE id=?", (tx_id,)).fetchone()
    if not tx:
        raise ValueError("Transaction not found")
    name = tx["payee"] or tx["description"] or "Recurring item"
    cur = conn.execute(
        "INSERT INTO recurring(name, account_id, amount, frequency, anchor_date, match, amount_mode) VALUES (?,?,?,?,?,?,?)",
        (name, tx["account_id"], tx["amount"], frequency, tx["posted"], name.lower(), "fixed"),
    )
    rid = cur.lastrowid
    conn.execute("UPDATE transactions SET recurring_id=? WHERE id=?", (rid, tx_id))
    auto_match(conn, [rid])
    return rid


def matched(conn, recurring_id: int, limit: int = 12) -> list[dict]:
    return db.rows(conn.execute(
        "SELECT id, posted, amount, description, pending FROM transactions WHERE recurring_id=? ORDER BY posted DESC LIMIT ?",
        (recurring_id, limit),
    ))


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
