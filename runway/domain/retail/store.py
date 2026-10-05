"""What every part of the order import shares: the stores, the error a store's page or Runway's reading of it gives,
and keeping orders, their items and their charges in the database as the parsers read them."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete, func, insert, select, update

from ...storage import db
from ...storage import settings_keys as sk
from ...storage.models import RetailCharge, RetailItem, RetailOrder

RETAILERS = ("amazon", "target", "costco")
NAMES = {"amazon": "Amazon", "target": "Target", "costco": "Costco"}
FIRST_IMPORT_DAYS = 180      # how far back the first import reads
OVERLAP_DAYS = 30            # later imports start this long before the last one (orders ship, charges post)
MAX_ATTEMPTS = 3             # stop asking for an order's details page after this many that couldn't be read


class RetailError(ValueError):
    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code   # "signin" or "robot": the store stopped answering, so the extension stops this import


def order_key(retailer: str, number: str) -> str:
    return f"{retailer}:{number}"


def item_key(title: str | None) -> str:
    return " ".join((title or "").lower().split())[:160]


# ------------------------------------------------------------------------------------------------ storing orders

def save_order(conn, retailer: str, number: str, **fields) -> str:
    oid = order_key(retailer, number)
    fields = {k: v for k, v in fields.items() if v is not None}
    if conn.execute(select(RetailOrder.id).where(RetailOrder.id == oid)).fetchone():
        if fields:
            conn.execute(update(RetailOrder).where(RetailOrder.id == oid)
                         .values(**fields, updated=datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")))
    else:
        conn.execute(insert(RetailOrder).values(id=oid, retailer=retailer, order_number=number, **fields))
    return oid


def save_items(conn, oid: str, items: list[dict]) -> None:
    """Replace an order's items, keeping the category of any item you had already set by hand."""
    i = RetailItem
    kept = {item_key(r["title"]): (r["category"], r["category_source"], r["confidence"])
            for r in conn.execute(select(i.title, i.category, i.category_source, i.confidence)
                                  .where(i.order_id == oid, i.category_source.is_not(None)))}
    conn.execute(delete(RetailItem).where(RetailItem.order_id == oid))
    for n, it in enumerate(items):
        cat, src, conf = kept.get(item_key(it["title"]), (None, None, None))
        conn.execute(insert(RetailItem).values(order_id=oid, position=n, title=it["title"], quantity=it.get("quantity") or 1,
                                               amount=round(it["amount"], 2), department=it.get("department"),
                                               category=cat, category_source=src, confidence=conf))


def save_charge(conn, cid: str, oid: str, day: str, amount: float, payment: str | None) -> None:
    db.upsert(conn, RetailCharge, {"id": cid, "order_id": oid, "date": day, "amount": round(amount, 2), "payment": payment},
              key=["id"], update=["date", "amount", "payment"])


def need_details(conn, retailer: str, numbers) -> list[str]:
    """Which of these orders still need their details read."""
    out = []
    for n in dict.fromkeys(numbers):
        r = conn.execute(select(RetailOrder.details, RetailOrder.attempts)
                         .where(RetailOrder.id == order_key(retailer, n))).fetchone()
        if r and not r["details"] and (r["attempts"] or 0) < MAX_ATTEMPTS:
            out.append(n)
    return out


def since(conn, retailer: str) -> str:
    """The earliest date the extension should read back to this time."""
    last = db.get_setting(conn, sk.retail_last(retailer))
    if last:
        start = datetime.fromisoformat(last).date() - timedelta(days=OVERLAP_DAYS)
    else:
        start = date.today() - timedelta(days=FIRST_IMPORT_DAYS)
    # Back far enough for orders whose items are still to be read (an import that couldn't read them, say), so
    # they're listed again and their details asked for. An Amazon order only learns when it was placed from its
    # details, so until then its first charge stands in.
    o = RetailOrder
    first_charge = select(func.min(RetailCharge.date)).where(RetailCharge.order_id == o.id).scalar_subquery()
    r = conn.execute(select(func.min(func.coalesce(o.placed, first_charge)).label("placed"))
                     .where(o.retailer == retailer, func.coalesce(o.details, 0) == 0,
                            func.coalesce(o.attempts, 0) < MAX_ATTEMPTS)).fetchone()
    if r and r["placed"] and r["placed"] < start.isoformat():
        return r["placed"]
    return start.isoformat()


def tried(conn, oid: str, final: bool) -> dict:
    """An order's details couldn't be read. Only the extension's last try at it in an import counts towards giving
    up on it, so one import (reading an order more than one way) uses at most one of its tries."""
    if final:
        conn.execute(update(RetailOrder).where(RetailOrder.id == oid)
                     .values(attempts=func.coalesce(RetailOrder.attempts, 0) + 1))
    return {"read": False}


def orders_of(conn, tx_ids: list[str], refunds: bool = True) -> dict[str, list[str]]:
    """{tx id: the orders its charges pay for (or, with `refunds`, its refunds came from)}. A charge pairs with one
    transaction and a transaction with one charge (`match` and `link` see to both), so this is one order each; an
    order paid in several charges shows up under each of their transactions."""
    c = RetailCharge
    out: dict[str, list[str]] = {}
    for i in range(0, len(tx_ids), 500):
        q = select(c.tx_id, c.order_id).where(c.tx_id.in_(tx_ids[i:i + 500]))
        if not refunds:
            q = q.where(c.amount <= 0)
        for r in conn.execute(q.order_by(c.id)):
            out.setdefault(r["tx_id"], []).append(r["order_id"])
    return out
