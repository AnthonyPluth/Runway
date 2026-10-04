"""What Undo needs from orders: the state an item's or a charge's change can alter, saved before it and put back
exactly as it was."""
from __future__ import annotations

from sqlalchemy import delete, select, update

from ...storage import db
from ...storage.models import Category, RetailCharge, RetailItem, RetailItemMemory, Transaction
from .store import item_key, orders_of


def item_undo_state(conn, item_id: int) -> dict:
    """What picking a category for this item can change, as Undo puts it back (`restore_item_state`): the memory of its
    title, and every item with that title (the others take it from memory). The transactions those items' orders paid
    with are the caller's to save (`item_transactions`)."""
    i = RetailItem
    it = conn.execute(select(i.title).where(i.id == item_id)).fetchone()
    key = item_key(it["title"]) if it else ""
    mem = conn.execute(select(RetailItemMemory.category).where(RetailItemMemory.key == key)).fetchone() if key else None
    items = [dict(r) for r in conn.execute(select(i.id, i.title, i.category, i.category_source, i.confidence))
             if r["id"] == item_id or (key and item_key(r["title"]) == key)]
    return {"key": key, "memory": mem["category"] if mem else None,
            "items": [{k: r[k] for k in ("id", "category", "category_source", "confidence")} for r in items]}


def item_transactions(conn, item_ids: list[int]) -> list[str]:
    """The transactions that paid for the orders these items are in."""
    orders = {r["order_id"] for r in conn.execute(select(RetailItem.order_id).where(RetailItem.id.in_(item_ids or [-1])))}
    return [r["tx_id"] for r in conn.execute(select(RetailCharge.tx_id).where(RetailCharge.order_id.in_(sorted(orders) or [""]),
                                                                              RetailCharge.tx_id.is_not(None)).order_by(RetailCharge.id))]


def restore_item_state(conn, state: dict) -> None:
    """Put back what `item_undo_state` saw: the title's memory (or none) and the items' categories, exactly."""
    key = state.get("key")
    if isinstance(key, str) and key:
        mem = state.get("memory")
        if isinstance(mem, str) and mem and conn.execute(select(Category.name).where(Category.name == mem)).fetchone():
            db.upsert(conn, RetailItemMemory, {"key": key, "category": mem}, key=["key"])
        else:
            conn.execute(delete(RetailItemMemory).where(RetailItemMemory.key == key))
    if isinstance(state.get("items"), list):
        restore_items(conn, state["items"])


_CHARGE_STATE = ("tx_id", "match_source", "not_tx", "applied")


def charge_state(conn, charge_id: str) -> dict | None:
    """A charge's pairing with its transaction, as Undo puts it back (`restore_charge`)."""
    c = RetailCharge
    row = conn.execute(select(c.id, *(getattr(c, k) for k in _CHARGE_STATE)).where(c.id == charge_id)).fetchone()
    return dict(row) if row else None


def restore_charge(conn, state: dict) -> bool:
    """Put a charge's pairing back exactly as `charge_state` saw it (a transaction gone since leaves it unpaired)."""
    if not isinstance(state, dict) or not isinstance(state.get("id"), str):
        return False
    values = {k: state.get(k) if isinstance(state.get(k), str) else None for k in _CHARGE_STATE}
    if values["tx_id"] and not conn.execute(select(Transaction.id).where(Transaction.id == values["tx_id"])).fetchone():
        values.update(tx_id=None, applied=None)
    if values["tx_id"]:   # a transaction pairs with one charge
        conn.execute(update(RetailCharge).where(RetailCharge.tx_id == values["tx_id"], RetailCharge.id != state["id"])
                     .values(tx_id=None, applied=None))
    return conn.execute(update(RetailCharge).where(RetailCharge.id == state["id"]).values(**values)).rowcount > 0


def order_mates(conn, tx_ids: list[str]) -> list[str]:
    """The other transactions that paid for (or were refunded by) the same orders as these ones, for Undo to cover."""
    orders = {o for v in orders_of(conn, tx_ids).values() for o in v}
    if not orders:
        return []
    have = set(tx_ids)
    c = RetailCharge
    return [r["tx_id"] for r in conn.execute(select(c.tx_id).where(c.order_id.in_(sorted(orders)), c.tx_id.is_not(None))
                                             .order_by(c.id)) if r["tx_id"] not in have]


def items_of_transactions(conn, tx_ids: list[str]) -> dict[str, list[dict]]:
    """{tx id: the category state of every item of its order(s)}, as Undo needs to put them back (see `restore_items`)."""
    by_tx = orders_of(conn, tx_ids)
    orders = sorted({o for v in by_tx.values() for o in v})
    i = RetailItem
    items: dict[str, list[dict]] = {}
    for n in range(0, len(orders), 500):
        for r in conn.execute(select(i.id, i.order_id, i.category, i.category_source, i.confidence)
                              .where(i.order_id.in_(orders[n:n + 500])).order_by(i.id)):
            items.setdefault(r["order_id"], []).append(
                {"id": r["id"], "category": r["category"], "category_source": r["category_source"], "confidence": r["confidence"]})
    return {t: [it for o in os for it in items.get(o, [])] for t, os in by_tx.items()}


def charges_of_transactions(conn, tx_ids: list[str]) -> dict[str, list[dict]]:
    """{tx id: its charges and what Runway last gave it (`applied`)}, so Undo can put that back too: otherwise a
    transaction re-split by Undo would look like a split you made, and stop following its items."""
    c = RetailCharge
    out: dict[str, list[dict]] = {}
    for n in range(0, len(tx_ids), 500):
        for r in conn.execute(select(c.id, c.tx_id, c.applied).where(c.tx_id.in_(tx_ids[n:n + 500])).order_by(c.id)):
            out.setdefault(r["tx_id"], []).append({"id": r["id"], "applied": r["applied"]})
    return out


def restore_charges(conn, tx_id: str, charges: list) -> None:
    """Put back each of the transaction's charges' `applied` as `charges_of_transactions` saw it."""
    for ch in charges:
        if not isinstance(ch, dict) or not isinstance(ch.get("id"), str) or not isinstance(ch.get("applied"), (str, type(None))):
            continue
        conn.execute(update(RetailCharge).where(RetailCharge.id == ch["id"], RetailCharge.tx_id == tx_id)
                     .values(applied=ch["applied"]))


def restore_items(conn, items: list) -> None:
    """Put items back as `items_of_transactions` saw them: the category exactly, and who or what chose it."""
    i = RetailItem
    for it in items:
        if not isinstance(it, dict) or not isinstance(it.get("id"), int) or isinstance(it.get("id"), bool):
            continue
        cat = it.get("category") or None
        if cat and not conn.execute(select(Category.name).where(Category.name == cat)).fetchone():
            cat = None
        conn.execute(update(i).where(i.id == it["id"]).values(
            category=cat, category_source=it.get("category_source") or None, confidence=it.get("confidence")))
