"""Target: its order history API, for online orders and in-store purchases (anything tied to your Target account:
Target Circle, a saved card, the Wallet barcode). Each order counts as one charge of its total.

Target's order history API isn't documented, so its replies are read loosely: an order is any object with an order
number, its items the first list of objects that have a description, and so on. The reply is kept with the order,
so a reading that misses something can be fixed and re-read later."""
from __future__ import annotations

import html
import json

from ....money import is_zero
from .. import store
from ..store import MAX_ATTEMPTS, RetailError, need_details, order_key, save_charge, save_items, save_order, tried
from .common import MAX_ORDERS, MAX_RAW, read_day, read_money

_NUMBER = ("order_number", "orderNumber", "order_id", "orderId", "receipt_id", "receiptId", "store_receipt_id",
           "transaction_id")
_DATE = ("placed_date", "placedDate", "order_date", "orderDate", "order_placed_date", "purchase_date", "purchaseDate",
         "transaction_date", "transactionDate", "order_purchase_date", "date")
_TOTAL = ("grand_total", "grandTotal", "order_total", "orderTotal", "total_amount", "totalAmount", "total_charged",
          "amount_paid", "total")
_SUBTOTAL = ("sub_total", "subtotal", "subTotal", "merchandise_subtotal")
_TAX = ("total_tax", "tax", "sales_tax", "taxes", "totalTax")
_LINES = ("order_lines", "orderLines", "lines", "line_items", "lineItems", "items", "receipt_lines")
_TITLE = ("description", "title", "product_description", "item_description", "name", "product_title")
_QTY = ("quantity", "original_quantity", "ordered_quantity", "qty", "item_quantity")
_LINE_TOTAL = ("total_price", "totalPrice", "line_total", "lineTotal", "extended_price", "extendedPrice", "net_price",
               "item_total", "total")
_UNIT = ("unit_price", "unitPrice", "price", "current_price", "sale_price", "list_price", "listPrice", "retail_price")
_DEPT = ("product_type_name", "department_name", "departmentName", "department", "class_name", "merchandise_class",
         "category")


def _first(d: dict, keys, nested=("summary", "order_summary", "orderSummary", "totals", "item", "product", "details")):
    """The first of these keys with a value, looking in the object and then in its usual sub-objects."""
    for k in keys:
        if isinstance(d, dict) and d.get(k) not in (None, "", [], {}):
            return d[k]
    for n in nested:
        sub = d.get(n) if isinstance(d, dict) else None
        if isinstance(sub, dict):
            for k in keys:
                if sub.get(k) not in (None, "", [], {}):
                    return sub[k]
    return None


def _deep(d, keys, depth: int = 3):
    """The first of these keys with a text value, anywhere up to `depth` objects down (nearest first)."""
    level = [d]
    for _ in range(depth + 1):
        nxt = []
        for obj in level:
            if not isinstance(obj, dict):
                continue
            for k in keys:
                if isinstance(obj.get(k), str) and obj[k].strip():
                    return obj[k].strip()
            nxt += [v for v in obj.values() if isinstance(v, dict)]
        level = nxt
    return None


MAX_DEPTH = 40       # how deep into a reply the searches below look (a store's replies are a few levels; a crafted one isn't)


def _find_orders(data, depth: int = 0) -> list[dict]:
    """The order objects in a reply: the first list of objects that carry an order number."""
    if depth > MAX_DEPTH:
        return []
    if isinstance(data, list):
        if data and all(isinstance(x, dict) for x in data) and any(_first(x, _NUMBER, ()) for x in data):
            return data
        for x in data:
            found = _find_orders(x, depth + 1)
            if found:
                return found
    elif isinstance(data, dict):
        for k in ("orders", "order_history", "orderHistory", "data", "result"):
            if k in data:
                found = _find_orders(data[k], depth + 1)
                if found:
                    return found
        for v in data.values():
            if isinstance(v, (list, dict)):
                found = _find_orders(v, depth + 1)
                if found:
                    return found
    return []


def _find_lines(obj, depth: int = 0) -> list[dict]:
    if depth > MAX_DEPTH:
        return []
    if isinstance(obj, dict):
        for k in _LINES:
            v = obj.get(k)
            if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
                return v
        for v in obj.values():
            if isinstance(v, (dict, list)):
                found = _find_lines(v, depth + 1)
                if found:
                    return found
    elif isinstance(obj, list):
        for x in obj:
            found = _find_lines(x, depth + 1)
            if found:
                return found
    return []


def _cancelled(obj: dict) -> bool:
    """A line or package Target cancelled (out of stock, say): nothing was charged for it."""
    if obj.get("cancellation"):
        return True
    status = [(obj.get("grouping_metadata") or {}).get("status"), ((obj.get("fulfillment") or {}).get("status") or {}).get("key"),
              obj.get("status")]
    return any(isinstance(s, str) and "CANCEL" in s.upper() for s in status)


def _target_lines(o: dict) -> list[dict]:
    """An order's item lines. An online order's details split them into packages (one per shipment or pickup), each
    with its own lines; lines and packages Target cancelled are left out, as the order's total leaves them out."""
    packages = o.get("packages")
    if isinstance(packages, list) and any(isinstance(p, dict) and _find_lines(p) for p in packages):
        lines = [l for p in packages if isinstance(p, dict) and not _cancelled(p) for l in _find_lines(p)]
    else:
        lines = _find_lines(o)
    return [l for l in lines if not _cancelled(l)]


def _target_line(line: dict) -> dict | None:
    title = _first(line, _TITLE)
    if isinstance(title, dict):
        title = _first(title, _TITLE, ())
    if not title or not isinstance(title, str):
        return None
    try:
        qty = float(read_money(_first(line, _QTY)) or 1)
    except (TypeError, ValueError):
        qty = 1.0
    amount = read_money(_first(line, _LINE_TOTAL, ("summary", "totals", "price", "pricing")))
    if amount is None:
        unit = read_money(_first(line, _UNIT, ("item", "product", "price", "pricing")))
        amount = unit * qty if unit is not None else None
    if amount is None:
        return None
    dept = _deep(line, _DEPT)
    return {"title": " ".join(html.unescape(title).split())[:300], "quantity": qty, "amount": abs(amount),
            "department": dept if isinstance(dept, str) else None}


def _last_page(data) -> bool:
    """Target's history says which page it is and how many there are: whether this reply is the last one."""
    if not isinstance(data, dict):
        return False
    pages, request = data.get("total_pages"), data.get("request")
    page = request.get("page_number") if isinstance(request, dict) else None
    return isinstance(pages, int) and isinstance(page, int) and not isinstance(pages, bool) and page >= pages


def _target_order(conn, o: dict, channel: str | None) -> tuple[str | None, bool]:
    """Save one order object from Target. Returns (order number, whether its items were in it)."""
    number = _first(o, _NUMBER, ())
    if not number:
        return None, False
    number = str(number).strip()
    placed = read_day(_first(o, _DATE, ()))
    total = read_money(_first(o, _TOTAL))
    kind = str(o.get("order_purchase_type") or o.get("orderPurchaseType") or channel or "").upper()
    ch = ("store" if kind.startswith("STORE") or "store" in str(o.get("order_type", "")).lower()
          else "online" if kind else None)   # a details reply may not say; keep what the history said
    lines = [x for x in (_target_line(l) for l in _target_lines(o)) if x]
    raw = json.dumps(o, separators=(",", ":"))[:MAX_RAW]
    # An order that cost nothing (Target cancelled it, or it was paid for some other way) has no charge to split, so
    # there are no items to read: it's done, and mustn't make the next import start further back looking for them.
    nothing = total is not None and is_zero(total) and not lines
    oid = save_order(conn, "target", number, channel=ch, placed=placed, total=abs(total) if total is not None else None,
                     subtotal=read_money(_first(o, _SUBTOTAL)), tax=read_money(_first(o, _TAX)), raw=raw,
                     details=1 if lines else None, attempts=MAX_ATTEMPTS if nothing else None)
    if lines:
        save_items(conn, oid, lines)
    if placed and total:
        save_charge(conn, f"target|{number}", oid, placed, -abs(total), None)
    return number, bool(lines)


def target_history(conn, data, purchase_type: str | None = None) -> dict:
    """A page of Target's order history (online or in store). Returns whether to read the next page, and which
    orders need their details read."""
    orders = _find_orders(data)[:MAX_ORDERS]
    start = store.since(conn, "target")
    numbers, older = [], False
    for o in orders:
        placed = read_day(_first(o, _DATE, ()))
        if placed and placed < start:
            older = True
            continue
        number, _ = _target_order(conn, o, purchase_type)
        if number:
            numbers.append(number)
    return {"more": bool(orders) and not older and not _last_page(data), "orders": need_details(conn, "target", numbers),
            "read": len(orders)}


def target_order(conn, number: str, data, final: bool = True) -> dict:
    """The details of one Target order (online order or store receipt). `final` is the extension's last try at it
    in this import, the only one that counts towards giving up on it."""
    number = str(number or "").strip()
    if not number:
        raise RetailError("Which order is this?")
    oid = order_key("target", number)
    if not data:   # the extension found nowhere to read it: keeps what the history said
        return tried(conn, oid, final)
    top = _first(data, _NUMBER, ()) if isinstance(data, dict) else None
    if isinstance(data, dict) and str(top) == number:
        o = data   # the reply is the order itself (its packages may name the order too: they aren't it)
    else:
        found = _find_orders(data)
        match = next((x for x in found if str(_first(x, _NUMBER, ())) == number), None)
        o = match or (data if isinstance(data, dict) else {})
    o = dict(o)
    o.setdefault("order_number", number)
    _, read = _target_order(conn, o, None)
    return {"read": True} if read else tried(conn, oid, final)
