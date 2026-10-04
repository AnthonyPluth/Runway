"""Splitting a store charge's transaction across its order's categories, in proportion to what the items cost (so tax,
shipping and discounts are shared out fairly), or giving it the one category every item has. A transaction you
categorized or split yourself is left alone."""
from __future__ import annotations

import json

from sqlalchemy import select, update

from ...storage import db
from .. import splits
from ...storage.models import Category, RetailCharge, RetailItem, RetailOrder, Transaction
from ...money import allocate_cents
from .store import orders_of


def _fallback_category(conn, tx) -> str | None:
    """For items nothing categorized: the transaction's own category if it's spending, else Shopping."""
    if tx["category"]:
        r = conn.execute(select(Category.is_transfer, Category.is_income).where(Category.name == tx["category"])).fetchone()
        if r and not r["is_transfer"] and not r["is_income"]:
            return tx["category"]
    if conn.execute(select(Category.name).where(Category.name == "Shopping")).fetchone():
        return "Shopping"
    return tx["category"]


def _shipment(items: list[dict], charge: float, order_total: float | None) -> list[dict]:
    """Amazon charges each shipment on its own. When a charge is less than the order, find the items that add up to
    it (with the order's tax and shipping spread over them); failing that, every item counts in proportion."""
    lines = sum(i["amount"] for i in items)
    if not order_total or lines <= 0 or abs(charge) >= order_total - 0.01 or len(items) > 14:
        return items
    ratio = order_total / lines
    tolerance = max(0.03, abs(charge) * 0.01)
    best, best_diff, ties = None, None, 0
    n = len(items)
    for mask in range(1, (1 << n) - 1):
        total = sum(items[j]["amount"] for j in range(n) if mask >> j & 1) * ratio
        diff = abs(total - abs(charge))
        if diff <= tolerance:
            if best_diff is None or diff < best_diff - 0.001:
                best, best_diff, ties = mask, diff, 0
            elif abs(diff - best_diff) <= 0.001:
                ties += 1
    if best is None or ties:
        return items   # no set of items fits, or more than one does equally well
    return [items[j] for j in range(n) if best >> j & 1]


def allocate(amount: float, items: list[dict], fallback: str | None, order_total: float | None = None) -> list[dict]:
    """Spread a charge over its items' categories in proportion to what they cost. Returns [{category, amount, note}]
    adding up to `amount` exactly (same sign), largest first."""
    items = [i for i in items if (i.get("amount") or 0) > 0]
    if not items:
        return []
    items = _shipment(items, amount, order_total)
    by_cat: dict[str, dict] = {}
    for it in items:
        cat = it.get("category") or fallback
        if not cat:
            return []
        g = by_cat.setdefault(cat, {"weight": 0.0, "titles": []})
        g["weight"] += it["amount"]
        g["titles"].append(it.get("title") or "")
    total_w = sum(g["weight"] for g in by_cat.values())
    sign, cents = (-1 if amount < 0 else 1), abs(round(amount * 100))
    groups = sorted(by_cat.items(), key=lambda kv: -kv[1]["weight"])
    # Whole cents: each part's share rounded down, and the ones that leaves over to the parts that lost the most to it.
    got = allocate_cents([cents * g["weight"] / total_w for _, g in groups], cents, start=int)
    out = []
    for (cat, g), n in zip(groups, got, strict=True):
        if n == 0:
            continue
        note = "; ".join(t for t in g["titles"] if t)
        out.append({"category": cat, "amount": sign * n / 100, "note": (note[:197] + "…") if len(note) > 200 else note})
    return out


def is_ours(conn, tx_id: str, applied: dict | None) -> bool:
    """Whether the transaction's split is still exactly the one Runway made (so you haven't changed it)."""
    if not applied or not applied.get("parts"):
        return False
    now = [(p["category"], round(p["amount"], 2)) for p in splits.get(conn, tx_id)]
    return now == [(p["category"], round(p["amount"], 2)) for p in applied["parts"]]


def _yours(tx, ours: bool, force: bool) -> str | None:
    """Why the transaction is yours to leave alone ("user-split" or "manual"), unless you asked to redo it."""
    if force:
        return None
    if tx["is_split"] and not ours:
        return "user-split"
    if not tx["is_split"] and tx["category_source"] == "manual":
        return "manual"
    return None


def _set_one_category(conn, tx, category: str) -> str:
    """Every item came out the same category: undo any split and give the transaction that category. Returns
    "same" when it already had it (from Runway or you) and "category" otherwise."""
    if tx["is_split"]:
        splits.clear(conn, tx["id"])
    if tx["category"] == category and tx["category_source"] in ("retail", "manual") and not tx["is_split"]:
        return "same"
    conn.execute(update(Transaction).where(Transaction.id == tx["id"])
                 .values(category=category, category_source="retail", confidence=1, needs_review=0))
    return "category"


def apply(conn, charge_id: str, force: bool = False) -> str:
    """Categorize or split a charge's transaction by its order's items. Returns what happened:
    split | category | same | no-items | waiting | unmatched | manual | user-split."""
    ch = conn.execute(select(RetailCharge).where(RetailCharge.id == charge_id)).fetchone()
    if not ch or not ch["tx_id"]:
        return "unmatched"
    tx = conn.execute(select(Transaction).where(Transaction.id == ch["tx_id"])).fetchone()
    if not tx:
        return "unmatched"
    if ch["amount"] > 0:
        return "same"   # refunds keep their category
    order = conn.execute(select(RetailOrder.total).where(RetailOrder.id == ch["order_id"])).fetchone()
    i = RetailItem
    items = db.rows(conn.execute(select(i.title, i.amount, i.category).where(i.order_id == ch["order_id"])
                                 .order_by(i.position, i.id)))
    if not items:
        return "no-items"
    if not any(i["category"] for i in items):
        return "waiting"   # nothing has decided any item yet: leave the transaction as it is until something does
    applied = json.loads(ch["applied"]) if ch["applied"] else None
    ours = is_ours(conn, tx["id"], applied)
    yours = _yours(tx, ours, force)
    if yours:
        return yours
    prev = (applied or {}).get("prev") or {"category": tx["category"], "source": tx["category_source"]}
    fallback = _fallback_category(conn, {"category": prev["category"]})
    parts = allocate(tx["amount"], items, fallback, order["total"] if order else None)
    if not parts:
        return "no-items"
    if len(parts) == 1:
        result = _set_one_category(conn, tx, parts[0]["category"])
        parts_saved = []
    else:
        if ours and applied and [(p["category"], p["amount"]) for p in applied["parts"]] == [(p["category"], p["amount"]) for p in parts]:
            return "same"
        splits.set_splits(conn, tx["id"], parts)
        if not tx["category"]:
            conn.execute(update(Transaction).where(Transaction.id == tx["id"]).values(category=parts[0]["category"]))
        result, parts_saved = "split", parts
    conn.execute(update(RetailCharge).where(RetailCharge.id == ch["id"]).values(
        applied=json.dumps({"parts": parts_saved, "category": parts[0]["category"] if not parts_saved else None, "prev": prev})))
    return result


def recategorize_part(conn, tx_id: str, family: set[str], category: str) -> bool:
    """A transaction split by its order's items (and still as Runway split it): its items in these categories
    (`family`: a category and its subcategories) get `category`, and the order's charges are split again, so only that
    part changes. When every item then has one category the transaction takes it and isn't split any more. Returns
    False when the transaction isn't split by an order's items, for the caller to change its parts itself."""
    for ch in conn.execute(select(RetailCharge.id, RetailCharge.order_id, RetailCharge.applied)
                           .where(RetailCharge.tx_id == tx_id)).fetchall():
        applied = json.loads(ch["applied"]) if ch["applied"] else None
        if not is_ours(conn, tx_id, applied):
            continue
        tx = conn.execute(select(Transaction.category).where(Transaction.id == tx_id)).fetchone()
        prev = (applied or {}).get("prev") or {"category": tx["category"] if tx else None}
        fallback = _fallback_category(conn, {"category": prev["category"]})
        i = RetailItem
        ids = [r["id"] for r in conn.execute(select(i.id, i.category).where(i.order_id == ch["order_id"]))
               if (r["category"] or fallback) in family]
        if not ids:
            return False
        conn.execute(update(i).where(i.id.in_(ids)).values(category=category, category_source="manual", confidence=1))
        for other in conn.execute(select(RetailCharge.id).where(RetailCharge.order_id == ch["order_id"],
                                                                RetailCharge.tx_id.is_not(None))).fetchall():
            apply(conn, other["id"])
        conn.execute(update(Transaction).where(Transaction.id == tx_id).values(needs_review=0))
        return True
    return False


def set_transaction_category(conn, tx_ids: list[str], category: str) -> int:
    """You gave these transactions one category: every item of their orders gets it too, so the items agree with the
    transaction instead of re-splitting it the next time something re-applies them. Returns how many items changed.

    The items count as picked by hand, but nothing is remembered for the same item in other orders (that's what
    picking an item's own category is for). An order paid in several charges has the same category on all its items,
    so its other transactions follow it unless they're yours (a category or split you made yourself stays); `apply`
    decides that, and a transaction only ever pairs with one charge, so there's no one-transaction, many-orders case.
    A refund's category says nothing about what was bought: its order's items stay as they are."""
    orders = sorted({o for v in orders_of(conn, list(dict.fromkeys(tx_ids)), refunds=False).values() for o in v})
    if not orders:
        return 0
    i, c = RetailItem, RetailCharge
    n = conn.execute(update(i).where(i.order_id.in_(orders)).values(category=category, category_source="manual", confidence=1)).rowcount
    have = set(tx_ids)
    for ch in conn.execute(select(c.id, c.tx_id).where(c.order_id.in_(orders), c.tx_id.is_not(None))).fetchall():
        if ch["tx_id"] not in have:
            apply(conn, ch["id"])
    return n
