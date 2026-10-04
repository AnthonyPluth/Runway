"""What the app shows of orders: the order under a transaction, an order's page, and the import's status in Settings."""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import case, func, select

from ...storage import db
from ...storage import settings_keys as sk
from ...storage.models import Account, RetailCharge, RetailItem, RetailOrder, Transaction
from .match import unmatched_count
from .store import NAMES, RETAILERS, RetailError
from .token import token_expires, token_problem


def _item_count():
    """How many items an order has, for a query on retail_orders: (SELECT COUNT(*) FROM retail_items ...)."""
    return (select(func.count()).select_from(RetailItem).where(RetailItem.order_id == RetailOrder.id)
            .scalar_subquery())


def for_transactions(conn, tx_ids: list[str]) -> dict[str, dict]:
    """{tx_id: {order_id, retailer, order_number, items}} for transactions that are store charges (or refunds)."""
    if not tx_ids:
        return {}
    c, o = RetailCharge, RetailOrder
    out = {}
    for i in range(0, len(tx_ids), 500):
        chunk = tx_ids[i:i + 500]
        for r in conn.execute(
                select(c.tx_id, c.id.label("charge_id"), o.id, o.retailer, o.order_number, o.channel,
                       _item_count().label("items"))
                .join(o, o.id == c.order_id).where(c.tx_id.in_(chunk))):
            out[r["tx_id"]] = {"order_id": r["id"], "charge_id": r["charge_id"], "retailer": r["retailer"],
                               "order_number": r["order_number"], "channel": r["channel"], "items": r["items"]}
    return out


def order_detail(conn, oid: str) -> dict:
    ro, i, rc, t = RetailOrder, RetailItem, RetailCharge, Transaction
    o = conn.execute(select(ro.id, ro.retailer, ro.order_number, ro.channel, ro.placed, ro.total, ro.subtotal, ro.tax,
                            ro.shipping, ro.payment, ro.details).where(ro.id == oid)).fetchone()
    if not o:
        raise RetailError("Order not found")
    out = dict(o)
    out["items"] = db.rows(conn.execute(
        select(i.id, i.title, i.quantity, i.amount, i.department, i.category, i.category_source, i.confidence)
        .where(i.order_id == oid).order_by(i.position, i.id)))
    charges = db.rows(conn.execute(
        select(rc.id, rc.date, rc.amount, rc.payment, rc.tx_id, rc.match_source, rc.applied, t.posted, t.payee, t.description,
               db.account_label_expr().label("account_name"))
        .outerjoin(t, t.id == rc.tx_id).outerjoin(Account, Account.id == t.account_id)
        .where(rc.order_id == oid).order_by(rc.date, rc.id)))
    for c in charges:
        applied = json.loads(c.pop("applied") or "null")
        c["applied"] = ("split" if applied and applied.get("parts") else "category" if applied else None)
    out["charges"] = charges
    out["url"] = (f"https://www.amazon.com/gp/your-account/order-details?orderID={o['order_number']}" if o["retailer"] == "amazon"
                  else "https://www.costco.com/myaccount/" if o["retailer"] == "costco"
                  else "https://www.target.com/orders")
    return out


def status(conn) -> dict:
    out: dict[str, Any] = {"token": bool(db.get_setting(conn, sk.RETAIL_TOKEN_HASH)), "token_created": db.get_setting(conn, sk.RETAIL_TOKEN_CREATED),
           "token_used": db.get_setting(conn, sk.RETAIL_TOKEN_USED), "token_expires": token_expires(conn),
           "token_problem": token_problem(conn) if db.get_setting(conn, sk.RETAIL_TOKEN_HASH) else None,
           "ai": (db.get_setting(conn, sk.RETAIL_AI, "1") or "1") == "1", "stores": {}}
    c, o = RetailCharge, RetailOrder
    for r in RETAILERS:
        counts = conn.execute(
            select(func.count().label("orders"), func.sum(case((o.details == 1, 1), else_=0)).label("read"))
            .where(o.retailer == r)).fetchone()
        matched = conn.execute(select(func.count()).select_from(c).join(o, o.id == c.order_id)
                               .where(o.retailer == r, c.tx_id.is_not(None))).scalar()
        out["stores"][r] = {"name": NAMES[r], "last": db.get_setting(conn, sk.retail_last(r)),
                            "orders": counts["orders"] or 0, "read": counts["read"] or 0, "matched": matched,
                            "unmatched": unmatched_count(conn, r)}
    def charge_count(*where):
        return select(func.count()).select_from(c).where(c.order_id == o.id, c.amount < 0, *where).scalar_subquery()
    out["recent"] = db.rows(conn.execute(
        select(o.id, o.retailer, o.order_number, o.channel, o.placed, o.total, o.details, _item_count().label("items"),
               charge_count().label("charges"), charge_count(c.tx_id.is_not(None)).label("matched"))
        .order_by(func.coalesce(o.placed, "9999").desc(), o.id).limit(60)))
    return out
