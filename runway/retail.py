"""Amazon, Target and Costco orders: read them, match them to card transactions, and split each transaction by what was in it.

None of these stores offers an API for your order history, so Runway's browser extension (extension/) reads it with the
sign-in you already have in your browser, the way the stores' own pages do, and sends what it gets here. All the
reading of those pages happens on this side, so when a store changes its pages the fix is an update to Runway
rather than to the extension:

- Amazon: the Payments → Transactions pages list every card charge with its order number (Amazon charges each
  shipment separately), and each order's details page lists its items. Both are read with the `amazon-orders`
  library's parsers.
- Target: its order history API, for online orders and in-store purchases (anything tied to your Target account:
  Target Circle, a saved card, the Wallet barcode). Each order counts as one charge of its total.
- Costco: the receipts costco.com's Orders & Purchases page lists (warehouse and gas station), which its GraphQL
  service returns with every line in one reply. Each receipt counts as one charge of its total.

Each charge is matched to a bank transaction with the same amount a few days later, from a merchant that looks like
the store. The order's items are categorized (what you picked for the same item before, then the AI model if one is
set up), and the transaction is split across those categories in proportion to what the items cost, so tax,
shipping and discounts are shared out fairly. If everything lands in one category the transaction just gets it.

A transaction you categorized or split yourself is left alone.
"""
from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import re
import secrets
import tempfile
import time
from datetime import date, datetime, timedelta
from typing import Any

from dateutil import parser as dateparser
from sqlalchemy import case, delete, func, insert, select, update

from . import categorize, db, monitoring, oidc, splits
from . import settings_keys as sk
from .models import Account, Category, RetailCharge, RetailItem, RetailItemMemory, RetailOrder, Transaction

RETAILERS = ("amazon", "target", "costco")
NAMES = {"amazon": "Amazon", "target": "Target", "costco": "Costco"}
# How each store's charges read on a statement: "AMAZON MKTPL*2K3AB1", "Amazon.com*RT4", "AMZN Mktp US", "TARGET 00012345".
MERCHANT = {
    "amazon": re.compile(r"amazon|amzn|\bamz\b", re.I),
    "target": re.compile(r"\btarget\b|\btgt\b|target\.com", re.I),
    # "COSTCO WHSE #0123", "COSTCO GAS #0123", "COSTCO.COM"; not a payment to the Costco Anywhere Visa
    "costco": re.compile(r"\bcostco\b(?!\s+anywhere)", re.I),
}
FIRST_IMPORT_DAYS = 180      # how far back the first import reads
OVERLAP_DAYS = 30            # later imports start this long before the last one (orders ship, charges post)
MATCH_BEFORE, MATCH_AFTER = 3, 10   # a bank transaction may post this many days before / after the store's charge date
CENT = 0.005
MAX_ATTEMPTS = 3             # stop asking for an order's details page after this many that couldn't be read
AI_BATCH = 50
MAX_RAW = 200_000            # characters of a Target order's API reply kept for troubleshooting


class RetailError(ValueError):
    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code   # "signin" or "robot": the store stopped answering, so the extension stops this import


def order_key(retailer: str, number: str) -> str:
    return f"{retailer}:{number}"


def _money(v) -> float | None:
    """12.3, "12.30", "$1,234.56", {"amount": 12.3} or {"value": "12.30"} -> float."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, dict):
        for k in ("amount", "value", "total", "price"):
            if k in v:
                return _money(v[k])
        return None
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", str(v))
    return float(m.group(0).replace(",", "")) if m else None


def _day(v) -> str | None:
    """A date in whatever form a store sends it -> YYYY-MM-DD."""
    if v is None:
        return None
    if isinstance(v, (date, datetime)):
        return v.isoformat()[:10]
    s = str(v).strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return m.group(0)
    try:
        return dateparser.parse(s).date().isoformat()
    except (ValueError, OverflowError):
        return None


# What a Costco receipt adds to an item's name that says nothing about what it is: its price-code and the warehouse's
# shelf and sale codes ("P=120", "P432", "#00123", "SL60", "DOM120", "T6H7P504", "CU38").
_COSTCO_CODES = re.compile(r"\bP=?\d{2,4}\b|#\d+|\b(?:SL|DOM|CU)\d+\b|\b[A-Z]\d{1,2}[A-Z]\d[A-Z]?\d*\b")


def ai_title(retailer: str, title: str | None) -> str:
    """An item's name as the AI model reads it: Costco's receipt codes taken off (the rest is left as it is)."""
    s = title or ""
    if retailer == "costco":
        s = " ".join(_COSTCO_CODES.sub(" ", s).split()) or s
    return s


def item_key(title: str | None) -> str:
    return " ".join((title or "").lower().split())[:160]


# ------------------------------------------------------------------------------------------------ the extension's key

TOKEN_DAYS = 90     # a key works this long, then the extension needs a new one from Settings (like a refresh token)
TOUCH_EVERY = 60    # seconds between notes of when the key was last used

def new_token(conn, owner: dict | None = None) -> str:
    """A new key for the browser extension (replacing any earlier one). Only a hash of it is kept, with who made it
    (`owner`: the signed-in person, {sub, email}): the key ends when they can no longer sign in (oidc.access_lapsed),
    as their sessions and the assistants they approved do, and TOKEN_DAYS after it was made in any case."""
    token = "rwx_" + secrets.token_urlsafe(32)
    db.set_setting(conn, sk.RETAIL_TOKEN_HASH, hashlib.sha256(token.encode()).hexdigest())
    db.set_setting(conn, sk.RETAIL_TOKEN_CREATED, datetime.now().isoformat(timespec="seconds"))
    db.set_setting(conn, sk.RETAIL_TOKEN_OWNER,
                   json.dumps({"sub": owner["sub"], "email": owner.get("email")}) if owner and owner.get("sub") else None)
    db.set_setting(conn, sk.RETAIL_TOKEN_USED, None)
    return token


def remove_token(conn) -> None:
    for key in (sk.RETAIL_TOKEN_HASH, sk.RETAIL_TOKEN_CREATED, sk.RETAIL_TOKEN_OWNER, sk.RETAIL_TOKEN_USED):
        db.set_setting(conn, key, None)


def _when(text: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(text) if text else None
    except ValueError:
        return None


def token_expires(conn) -> str | None:
    """When the key stops working (TOKEN_DAYS after it was made), or None without a key."""
    made = _when(db.get_setting(conn, sk.RETAIL_TOKEN_CREATED))
    return (made + timedelta(days=TOKEN_DAYS)).isoformat(timespec="seconds") if made else None


def token_problem(conn, now: datetime | None = None) -> str | None:
    """Why the key no longer works, or None: "expired", or "owner_gone" (the person who made it can no longer sign in).
    A key made before owners were kept has none, and ends only by expiring."""
    now = now or datetime.now()
    made = _when(db.get_setting(conn, sk.RETAIL_TOKEN_CREATED))
    if made and now - made >= timedelta(days=TOKEN_DAYS):
        return "expired"
    try:
        owner = json.loads(db.get_setting(conn, sk.RETAIL_TOKEN_OWNER) or "null")
    except ValueError:
        owner = None
    if isinstance(owner, dict) and oidc.access_lapsed(conn, owner.get("sub"), owner.get("email")):
        return "owner_gone"
    return None


def token_check(conn, authorization: str | None) -> str | None:
    """Why a call's key is refused, or None if it's the key and it still works: "unknown" (no key, or not this one),
    else token_problem's reason. A working key's last use is noted (at most every TOUCH_EVERY seconds)."""
    want = db.get_setting(conn, sk.RETAIL_TOKEN_HASH)
    m = re.match(r"Bearer\s+(\S+)$", (authorization or "").strip())
    if not want or not m or not hmac.compare_digest(hashlib.sha256(m.group(1).encode()).hexdigest(), want):
        return "unknown"
    now = datetime.now()
    problem = token_problem(conn, now)
    if problem:
        return problem
    used = _when(db.get_setting(conn, sk.RETAIL_TOKEN_USED))
    if not used or now - used > timedelta(seconds=TOUCH_EVERY):
        db.set_setting(conn, sk.RETAIL_TOKEN_USED, now.isoformat(timespec="seconds"))
    return None


def check_token(conn, authorization: str | None) -> bool:
    return token_check(conn, authorization) is None


REFUSALS = {
    "expired": f"This key has expired (a key lasts {TOKEN_DAYS} days). Make a new one under Settings → Connections.",
    "owner_gone": "The person who made this key can no longer sign in to Runway. Make a new one under Settings → Connections.",
    "unknown": "Runway doesn't know this key. Make a new one under Settings → Connections.",
}


# ------------------------------------------------------------------------------------------------ storing orders

def _save_order(conn, retailer: str, number: str, **fields) -> str:
    oid = order_key(retailer, number)
    fields = {k: v for k, v in fields.items() if v is not None}
    if conn.execute(select(RetailOrder.id).where(RetailOrder.id == oid)).fetchone():
        if fields:
            conn.execute(update(RetailOrder).where(RetailOrder.id == oid)
                         .values(**fields, updated=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")))
    else:
        conn.execute(insert(RetailOrder).values(id=oid, retailer=retailer, order_number=number, **fields))
    return oid


def _save_items(conn, oid: str, items: list[dict]) -> None:
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


def _save_charge(conn, cid: str, oid: str, day: str, amount: float, payment: str | None) -> None:
    db.upsert(conn, RetailCharge, {"id": cid, "order_id": oid, "date": day, "amount": round(amount, 2), "payment": payment},
              key=["id"], update=["date", "amount", "payment"])


def _need(conn, retailer: str, numbers) -> list[str]:
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


# ------------------------------------------------------------------------------------------------ Amazon

def _payment(method: str | None, last4: str | None) -> str | None:
    """"Visa ****1234" (Amazon's own wording usually has the last digits already)."""
    method = " ".join((method or "").split())
    if last4 and last4 not in method:
        method = f"{method} {last4}".strip()
    return method or None


def _amazon_config():
    from amazonorders.conf import AmazonOrdersConfig   # amazon-orders is heavy: loaded only when an Amazon page comes in
    # A config file that doesn't exist: nothing is read from (or written to) ~/.config/amazonorders.
    return AmazonOrdersConfig(config_path=os.path.join(tempfile.gettempdir(), "runway-amazonorders-none.yml"),
                              data={"warn_on_missing_required_field": True})


def _signed_out(html: str) -> bool:
    return bool(re.search(r"<form[^>]+name=['\"]signIn['\"]|ap/signin", html[:200_000])) and "apx-transaction" not in html


def _amazon_blocked(html: str) -> RetailError | None:
    """Amazon showed its sign-in or robot-check page instead of an order (its order pages link to sign-in anyway, so
    only the sign-in form itself counts)."""
    head = html[:200_000]
    if re.search(r"/errors/validateCaptcha|captchacharacters|<title>[^<]*Robot Check", head, re.I):
        return RetailError("Amazon asked to check you're not a robot. Open amazon.com in this browser, answer it, then "
                           "import again.", "robot")
    if re.search(r"<form[^>]+name=['\"]signIn['\"]|id=['\"]ap_email['\"]", head):
        return RetailError("Amazon asked to sign in. Sign in to Amazon in this browser, then try again.", "signin")
    return None


AMAZON_ORDER = re.compile(r"^(?:\d{3}|D\d{2})-\d{7}-\d{7}$")


def amazon_transactions(conn, html: str, seen: dict | None = None) -> dict:
    """One page of Amazon's Payments → Transactions. Saves each charge and refund against its order.

    Returns what the extension should do next: `next_form` (post it back to the same page for the next page, or
    None when this page reaches back past `since`), `orders`, the order numbers whose details it should send, and
    `seen`, which it sends back with the next page (so the same charge twice across two pages is still two charges)."""
    # amazon-orders (and BeautifulSoup) are heavy: loaded only when an Amazon page comes in.
    from amazonorders.exception import AmazonOrdersError
    from amazonorders.transactions import _parse_transactions_page
    from bs4 import BeautifulSoup
    if _signed_out(html):
        raise RetailError("Amazon asked to sign in. Sign in to Amazon in this browser, then try again.", "signin")
    blocked = _amazon_blocked(html)
    if blocked:
        raise blocked
    cfg = _amazon_config()
    try:
        txs, next_form = _parse_transactions_page(BeautifulSoup(html, cfg.bs4_parser), cfg)
    except AmazonOrdersError as e:
        raise RetailError(f"Runway couldn't read Amazon's transactions page: {e}") from e
    start = since(conn, "amazon")
    seen = {str(k): int(v) for k, v in (seen or {}).items() if isinstance(v, int)}
    numbers: list[str] = []
    oldest: str | None = None
    for t in txs:
        day = _day(t.completed_date)
        oldest = min(oldest or day, day) if day else oldest
        number = (t.order_number or "").strip()
        if not day or not AMAZON_ORDER.match(number) or t.grand_total is None:
            continue   # a payment that isn't for an order (a gift card reload, a bank refund line, ...)
        if day < start:
            continue
        oid = _save_order(conn, "amazon", number)
        k = f"{number}|{day}|{round(t.grand_total * 100)}"
        seen[k] = seen.get(k, 0) + 1   # the same amount twice on one day for one order: two charges
        pay = _payment(t.payment_method, t.payment_method_last_4)
        _save_charge(conn, f"amazon|{number}|{day}|{round(t.grand_total * 100)}|{seen[k]}", oid, day,
                     float(t.grand_total), pay)
        numbers.append(number)
    more = bool(next_form and oldest and oldest >= start)
    return {"next_form": next_form if more else None, "orders": _need(conn, "amazon", numbers), "read": len(txs),
            "seen": seen}


def _tried(conn, oid: str, final: bool) -> dict:
    """An order's details couldn't be read. Only the extension's last try at it in an import counts towards giving
    up on it, so one import (reading an order more than one way) uses at most one of its tries."""
    if final:
        conn.execute(update(RetailOrder).where(RetailOrder.id == oid)
                     .values(attempts=func.coalesce(RetailOrder.attempts, 0) + 1))
    return {"read": False}


def amazon_order(conn, number: str, html: str, final: bool = True) -> dict:
    """An Amazon order's details page: its items and totals. A sign-in or robot-check page raises (with its code)
    without using up one of the order's tries."""
    from amazonorders.exception import AmazonOrdersError   # heavy: loaded only when an Amazon page comes in
    from amazonorders.orders import AmazonOrders
    number = (number or "").strip()
    if not AMAZON_ORDER.match(number):
        raise RetailError("That isn't an Amazon order number")
    blocked = _amazon_blocked(html)
    if blocked:
        raise blocked
    oid = _save_order(conn, "amazon", number)
    try:
        order = AmazonOrders.parse_order_details(html, _amazon_config(), order_number=number)
        items = [{"title": " ".join((i.title or "").split()) or "Item", "quantity": i.quantity or 1,
                  "amount": (i.price or 0) * (i.quantity or 1)} for i in order.items]
    except (AmazonOrdersError, AttributeError, TypeError, ValueError):
        return _tried(conn, oid, final)
    if not items:
        return _tried(conn, oid, final)
    pay = _payment(order.payment_method, order.payment_method_last_4)
    _save_order(conn, "amazon", number, channel="online", placed=_day(order.order_placed_date),
                total=order.grand_total, subtotal=order.subtotal, tax=order.estimated_tax,
                shipping=order.shipping_total, payment=pay, details=1)
    _save_items(conn, oid, items)
    return {"read": True, "items": len(items)}


# ------------------------------------------------------------------------------------------------ Target
#
# Target's order history API isn't documented, so its replies are read loosely: an order is any object with an order
# number, its items the first list of objects that have a description, and so on. The reply is kept with the order,
# so a reading that misses something can be fixed and re-read later.

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
MAX_ORDERS = 500     # orders taken from one page of history (a real page has a few dozen)


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
        qty = float(_money(_first(line, _QTY)) or 1)
    except (TypeError, ValueError):
        qty = 1.0
    amount = _money(_first(line, _LINE_TOTAL, ("summary", "totals", "price", "pricing")))
    if amount is None:
        unit = _money(_first(line, _UNIT, ("item", "product", "price", "pricing")))
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
    placed = _day(_first(o, _DATE, ()))
    total = _money(_first(o, _TOTAL))
    kind = str(o.get("order_purchase_type") or o.get("orderPurchaseType") or channel or "").upper()
    ch = ("store" if kind.startswith("STORE") or "store" in str(o.get("order_type", "")).lower()
          else "online" if kind else None)   # a details reply may not say; keep what the history said
    lines = [x for x in (_target_line(l) for l in _target_lines(o)) if x]
    raw = json.dumps(o, separators=(",", ":"))[:MAX_RAW]
    # An order that cost nothing (Target cancelled it, or it was paid for some other way) has no charge to split, so
    # there are no items to read: it's done, and mustn't make the next import start further back looking for them.
    nothing = total is not None and abs(total) < CENT and not lines
    oid = _save_order(conn, "target", number, channel=ch, placed=placed, total=abs(total) if total is not None else None,
                      subtotal=_money(_first(o, _SUBTOTAL)), tax=_money(_first(o, _TAX)), raw=raw,
                      details=1 if lines else None, attempts=MAX_ATTEMPTS if nothing else None)
    if lines:
        _save_items(conn, oid, lines)
    if placed and total:
        _save_charge(conn, f"target|{number}", oid, placed, -abs(total), None)
    return number, bool(lines)


def target_history(conn, data, purchase_type: str | None = None) -> dict:
    """A page of Target's order history (online or in store). Returns whether to read the next page, and which
    orders need their details read."""
    orders = _find_orders(data)[:MAX_ORDERS]
    start = since(conn, "target")
    numbers, older = [], False
    for o in orders:
        placed = _day(_first(o, _DATE, ()))
        if placed and placed < start:
            older = True
            continue
        number, _ = _target_order(conn, o, purchase_type)
        if number:
            numbers.append(number)
    return {"more": bool(orders) and not older and not _last_page(data), "orders": _need(conn, "target", numbers),
            "read": len(orders)}


def target_order(conn, number: str, data, final: bool = True) -> dict:
    """The details of one Target order (online order or store receipt). `final` is the extension's last try at it
    in this import, the only one that counts towards giving up on it."""
    number = str(number or "").strip()
    if not number:
        raise RetailError("Which order is this?")
    oid = order_key("target", number)
    if not data:   # the extension found nowhere to read it: keeps what the history said
        return _tried(conn, oid, final)
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
    return {"read": True} if read else _tried(conn, oid, final)


# ------------------------------------------------------------------------------------------------ Costco
#
# costco.com's Orders & Purchases page reads your receipts (in the warehouse, at the gas station, the car wash) from a
# GraphQL service. One request for a range of dates returns every receipt in it with all of its lines, so there's
# no second request per order for the details, as there is for Amazon and Target. The query is kept here rather
# than in the extension, so a change on Costco's side is an update to Runway. (Orders placed on costco.com are a
# different query and aren't read yet: only what you bought in a warehouse or at a gas station.)

COSTCO_GRAPHQL = "https://ecom-api.costco.com/ebusiness/order/v1/orders/graphql"
COSTCO_MAX_DAYS = 90    # the longest stretch one request asks for (Costco's own page offers three months at a time)
COSTCO_QUERY = """query receiptsWithCounts($startDate: String!, $endDate: String!, $documentType: String!, $documentSubType: String!) {
  receiptsWithCounts(startDate: $startDate, endDate: $endDate, documentType: $documentType, documentSubType: $documentSubType) {
    receipts {
      warehouseName receiptType documentType transactionDate transactionDateTime transactionBarcode transactionType
      total subTotal taxes totalItemCount
      itemArray { itemNumber itemDescription01 itemDescription02 itemIdentifier unit amount taxFlag itemUnitPriceAmount }
      tenderArray { tenderTypeCode tenderDescription amountTender }
    }
  }
}"""
# An instant savings is a line of its own: a negative amount, and "/<the item number it takes money off>" (padded with
# spaces some ways). A coupon for the whole receipt has no item to point at: "/ COUPON".
_COSTCO_DISCOUNT = re.compile(r"^\s*/\s*(\d+)")
_COSTCO_RECEIPT_DISCOUNT = re.compile(r"^\s*/")


def _costco_receipts(data) -> list[dict]:
    """The receipts in a reply: the first list called `receipts` (however deep Costco nests it)."""
    if isinstance(data, dict):
        if isinstance(data.get("receipts"), list):
            return [r for r in data["receipts"] if isinstance(r, dict)]
        for v in data.values():
            if isinstance(v, (dict, list)):
                found = _costco_receipts(v)
                if found:
                    return found
    elif isinstance(data, list):
        for x in data:
            found = _costco_receipts(x)
            if found:
                return found
    return []


def _costco_error(data) -> RetailError | None:
    """Costco's service answered with an error instead of receipts (usually a sign-in that has run out)."""
    errors = data.get("errors") if isinstance(data, dict) else None
    if not errors or _costco_receipts(data):
        return None
    first = errors[0] if isinstance(errors, list) and errors else errors
    msg = str((first.get("message") if isinstance(first, dict) else first) or "")[:200]
    if re.search(r"unauthori[sz]ed|forbidden|not authenticated|token|expired|\b40[13]\b", msg, re.I):
        return RetailError("Costco asked to sign in. Sign in to Costco in this browser, then try again.", "signin")
    return RetailError(f"Costco's order service answered with an error: {msg or 'no message'}")


def _costco_lines(r: dict, refund: bool, dept: str | None) -> list[dict]:
    """A receipt's items, with each instant savings taken off the item it's for, and a coupon for the whole receipt
    shared out over all the items in proportion to their prices (so the items add up to the receipt's subtotal).

    On a refund, quantities and amounts are negative, and the savings that were taken off the returned items come
    back as positive lines that aren't items (no unit price, no tax flag, no "/"): they're left out, and only the
    returned items are listed (their amounts, positive)."""
    lines: list[dict] = []
    discounts: list[tuple[str, float]] = []
    general = 0.0
    for it in r.get("itemArray") or []:
        if not isinstance(it, dict):
            continue
        amount = _money(it.get("amount"))
        if amount is None:
            continue
        number = str(it.get("itemNumber") or "").strip()
        d1 = " ".join(str(it.get("itemDescription01") or "").split())
        d2 = " ".join(str(it.get("itemDescription02") or "").split())
        m = _COSTCO_DISCOUNT.match(d1)
        if m:
            discounts.append((m.group(1), amount))
            continue
        if _COSTCO_RECEIPT_DISCOUNT.match(d1):
            general += amount
            continue
        if refund and amount > 0:
            continue   # a returned item is a negative line; a positive one on a refund is savings taken back
        qty = abs(_money(it.get("unit")) or 0)
        lines.append({"number": number, "title": html.unescape(f"{d1} {d2}".strip())[:300] or f"Item {number}".strip(),
                      "quantity": qty or 1, "amount": amount, "department": dept})
    for parent, amount in discounts:
        same = [l for l in lines if l["number"] == parent]
        if same:   # a sale: the first line it still fits (a discount can't take an item below nothing)
            target = same[0] if refund else next((l for l in same if l["amount"] + amount > 0), same[0])
            target["amount"] += amount
    out = [{"title": l["title"], "quantity": l["quantity"], "amount": round(abs(l["amount"]) if refund else l["amount"], 2),
            "department": l["department"]} for l in lines]
    out = [l for l in out if l["amount"] > 0]
    base = sum(l["amount"] for l in out)
    if general and not refund and base > 0 and base + general > 0:
        cents = round((base + general) * 100)
        for l in out:
            l["amount"] = round(l["amount"] * (base + general) / base, 2)
        drift = cents - round(sum(l["amount"] for l in out) * 100)   # whole cents left over by rounding: to the biggest item
        biggest = max(out, key=lambda l: l["amount"])
        biggest["amount"] = round(biggest["amount"] + drift / 100, 2)
    return out


def _costco_receipt(conn, r: dict) -> str | None:
    """Save one receipt from Costco. Returns its number (None when it has no number, date or total to go by)."""
    number = str(r.get("transactionBarcode") or "").strip()
    placed = _day(r.get("transactionDate") or r.get("transactionDateTime"))
    total = _money(r.get("total"))
    if not number or not placed or total is None:
        return None
    kind = f"{r.get('transactionType') or ''} {r.get('documentType') or ''} {r.get('receiptType') or ''}"
    refund = total < 0 or bool(re.search(r"refund|return", kind, re.I))
    dept = "Gas station" if re.search(r"gas|fuel", kind, re.I) else None
    lines = _costco_lines(r, refund, dept)
    tenders = [str(t.get("tenderDescription") or "").strip().title() for t in (r.get("tenderArray") or []) if isinstance(t, dict)]
    payment = ", ".join(dict.fromkeys(t for t in tenders if t)) or None
    raw = json.dumps(r, separators=(",", ":"))[:MAX_RAW]
    # The receipt is all there is to read: an order with no items never gets a second try, and mustn't make the
    # next import start further back looking for its details (see `since`).
    oid = _save_order(conn, "costco", number, channel="store", placed=placed, total=abs(total),
                      subtotal=_money(r.get("subTotal")), tax=_money(r.get("taxes")), payment=payment, raw=raw,
                      details=1 if lines else 0, attempts=0 if lines else MAX_ATTEMPTS)
    if lines:
        _save_items(conn, oid, lines)
    if total:
        _save_charge(conn, f"costco|{number}", oid, placed, abs(total) if refund else -abs(total), payment)
    return number


def costco_history(conn, data) -> dict:
    """One reply from Costco's receipts service (a stretch of dates). Every receipt in it comes with its items, so
    there's nothing more to ask for: the extension asks for the next stretch back until it passes `since`."""
    err = _costco_error(data)
    if err:
        raise err
    receipts = _costco_receipts(data)[:MAX_ORDERS]
    saved = sum(1 for r in receipts if _costco_receipt(conn, r))
    return {"more": False, "orders": [], "read": len(receipts), "saved": saved}


# ------------------------------------------------------------------------------------------------ categorizing items

# The store's own department, when it says, for items the AI hasn't seen (or when there's no AI): first category
# here that you have.
DEPARTMENTS = [
    (re.compile(r"grocer|food|beverage|snack|produce|dairy|meat|bakery|frozen|pantry|deli", re.I), ["Groceries"]),
    (re.compile(r"pharm|health|medicine|vitamin|first aid|otc", re.I), ["Pharmacy", "Medical"]),
    (re.compile(r"electronic|computer|video game|phone|tech", re.I), ["Technology", "Shopping"]),
    (re.compile(r"home improvement|hardware|tools?\b|paint", re.I), ["Home Improvement"]),
    (re.compile(r"gift card", re.I), ["Gifts & Donations"]),
    (re.compile(r"book|movie|music|toy|game", re.I), ["Entertainment", "Shopping"]),
]


def _department_category(dept: str | None, have: set[str]) -> str | None:
    for rx, cats in DEPARTMENTS:
        if dept and rx.search(dept):
            return next((c for c in cats if c in have), None)
    return None


def item_prompt(retailer_names: str, categories: list[str], subcategories: list[str], examples: list[dict],
                items: list[dict], allow_new: bool = False) -> str:
    lines = [
        f"You sort items from someone's {retailer_names} orders into their budget categories.",
        "Pick exactly one category for each item from this list:",
        ", ".join(categories),
    ]
    if subcategories:
        lines += ["Some are subcategories (parent > sub). Prefer the most specific one that fits:", "; ".join(subcategories)]
    lines += [
        "",
        "Guidance:",
        "- Categorize the item itself, not the store: food and drink for home is groceries even from Amazon or Target.",
        "- Everyday household supplies (paper towels, detergent, trash bags) go with groceries or household, whichever "
        "this person has; clothing, decor and general merchandise go to shopping unless something more specific fits.",
        "- Never pick a transfer, card payment, income or refund category.",
        "- confidence is 0 to 1: how sure you are that a careful person would pick the same category.",
    ]
    if "Costco" in retailer_names:
        lines += ["- Costco receipts abbreviate item names: KS is Kirkland Signature (the store brand), ORG is organic, PK is a pack, "
                  "and the rest is often cut short (\"GRLCPEPWINGS\" is garlic pepper wings). Work out the product and categorize that."]
    if allow_new:
        lines += ["- If none of the categories is a good fit, you may propose a new one instead: set \"category\" to null and add",
                  "  \"new_category\": \"<short name, Title Case>\" and optionally \"parent\": \"<an existing category it belongs under>\".",
                  "  Only when nothing fits; never a near-duplicate of an existing category; reuse one new name for similar items."]
    if examples:
        lines += ["", "How this person has categorized items before:"]
        lines += [f"- {e['title']} -> {e['category']}" for e in examples]
    lines += ["", "Items (JSON):", json.dumps(items, ensure_ascii=False), "",
              'Reply with only a JSON array, one object per item: [{"i": <i>, "category": "<category>", "confidence": <0-1>}]'
              + (' (or {"i": <i>, "category": null, "new_category": "<name>", "parent": "<existing or null>", "confidence": <0-1>})'
                 if allow_new else "")]
    return "\n".join(lines)


def categorize_items(conn, use_ai: bool = True, caller=None) -> dict:
    """Give a category to every item that doesn't have one: what you picked for the same item before, then the AI
    model (if set up), then the store's department. Items nothing decides are left for the transaction's own
    category."""
    caller = caller or categorize.call_llm
    counts = {"memory": 0, "ai": 0, "department": 0, "left": 0}
    memory = {r["key"]: r["category"] for r in conn.execute(select(RetailItemMemory.key, RetailItemMemory.category))}
    have = {r["name"] for r in conn.execute(select(Category.name))}
    spend = {r["name"] for r in conn.execute(select(Category.name).where(Category.is_transfer == 0, Category.is_income == 0))}
    i, o = RetailItem, RetailOrder
    todo = db.rows(conn.execute(
        select(i.id, i.title, i.amount, i.department, o.retailer).join(o, o.id == i.order_id)
        .where(i.category_source.is_(None)).order_by(i.id)))
    left = []
    for it in todo:
        cat = memory.get(item_key(it["title"]))
        if cat in have:
            conn.execute(update(RetailItem).where(RetailItem.id == it["id"])
                         .values(category=cat, category_source="memory", confidence=1))
            counts["memory"] += 1
        else:
            left.append(it)

    api_key = db.get_setting(conn, sk.OPENROUTER_API_KEY)
    if left and use_ai and api_key and (db.get_setting(conn, sk.RETAIL_AI, "1") or "1") == "1":
        counts["ai"] += _items_with_ai(conn, left, caller, api_key, spend)
    for it in left:
        if it.get("done"):
            continue
        cat = _department_category(it["department"], have)
        if cat:
            conn.execute(update(RetailItem).where(RetailItem.id == it["id"])
                         .values(category=cat, category_source="department", confidence=0.6))
            counts["department"] += 1
        else:
            counts["left"] += 1
    return counts


@monitoring.ai_agent("Order item categorizer", "orders")
def _items_with_ai(conn, left: list[dict], caller, api_key: str, spend: set[str]) -> int:
    """Ask the model about items, AI_BATCH at a time, and save its answers. Items it answered are marked done.
    Stops at the first failed request (recorded in the AI log); what's left falls back to departments. Returns how
    many items it categorized."""
    model = db.get_setting(conn, sk.LLM_MODEL, categorize.DEFAULT_MODEL) or categorize.DEFAULT_MODEL
    cats = sorted(spend)
    subs = [h for h in categorize._subcategory_hints(conn) if h.split(" > ")[-1] in spend]
    examples = [{"title": r["title"][:80], "category": r["category"]} for r in conn.execute(
        select(RetailItem.title, RetailItem.category).where(RetailItem.category_source == "manual")
        .order_by(RetailItem.id.desc()).limit(40))]
    names = " and ".join(sorted({NAMES.get(it["retailer"], it["retailer"]) for it in left}))
    conn.commit()   # don't hold the database while the model thinks
    done = 0
    for start in range(0, len(left), AI_BATCH):
        batch = left[start:start + AI_BATCH]
        payload = [{"i": i, "item": ai_title(it["retailer"], it["title"])[:200], "price": round(it["amount"] or 0, 2),
                    **({"department": it["department"]} if it["department"] else {})} for i, it in enumerate(batch)]
        began, reply = time.time(), None
        try:
            reply = caller(api_key, model, item_prompt(names, cats, subs, examples, payload))
            answers = categorize.parse_ai_reply(reply, cats)
            answered = sum(1 for a in answers.values() if a[0])
            categorize._log(conn, "orders", model, len(batch), answered, 0, True, time.time() - began,
                            f"Categorized {answered} of {len(batch)} items from {names} orders", reply)
        except Exception as e:   # recorded in the AI log; these fall back to departments
            categorize._log(conn, "orders", model, len(batch), 0, 0, False, time.time() - began, str(e)[:500], reply)
            conn.commit()
            break
        for i, it in enumerate(batch):
            cat, conf = answers.get(i, (None, 0.0))
            if cat:
                conn.execute(update(RetailItem).where(RetailItem.id == it["id"], RetailItem.category_source.is_(None))
                             .values(category=cat, category_source="ai", confidence=conf))
                done += 1
                it["done"] = True
        conn.commit()
    return done


def suggest_for_order(conn, order_id: str, caller=None) -> list[dict]:
    """The AI's category for each item of an order that has none yet, proposing a new category where nothing fits.
    Nothing is saved: you apply each one (see set_item_category and api_retail_item)."""
    caller = caller or categorize.call_llm
    api_key = db.get_setting(conn, sk.OPENROUTER_API_KEY)
    if not api_key:
        raise RetailError("Add an OpenRouter key in Settings → Services first")
    i, o = RetailItem, RetailOrder
    items = db.rows(conn.execute(
        select(i.id, i.title, i.amount, i.department, o.retailer).join(o, o.id == i.order_id)
        .where(i.order_id == order_id, i.category.is_(None)).order_by(i.id)))
    if not items:
        return []
    spend = {r["name"] for r in conn.execute(select(Category.name).where(Category.is_transfer == 0, Category.is_income == 0))}
    cats = sorted(spend)
    subs = [h for h in categorize._subcategory_hints(conn) if h.split(" > ")[-1] in spend]
    examples = [{"title": r["title"][:80], "category": r["category"]} for r in conn.execute(
        select(RetailItem.title, RetailItem.category).where(RetailItem.category_source == "manual")
        .order_by(RetailItem.id.desc()).limit(40))]
    names = " and ".join(sorted({NAMES.get(it["retailer"], it["retailer"]) for it in items}))
    model = db.get_setting(conn, sk.LLM_MODEL, categorize.DEFAULT_MODEL) or categorize.DEFAULT_MODEL
    payload = [{"i": n, "item": ai_title(it["retailer"], it["title"])[:200], "price": round(it["amount"] or 0, 2),
                **({"department": it["department"]} if it["department"] else {})} for n, it in enumerate(items[:AI_BATCH])]
    conn.commit()   # don't hold the database while the model thinks
    began, reply = time.time(), None
    try:
        with monitoring.ai_agent("Order item categorizer", "orders"):
            reply = caller(api_key, model, item_prompt(names, cats, subs, examples, payload, allow_new=True))
        answers = categorize.parse_ai_reply(reply, cats, allow_new=True)
    except Exception as e:
        categorize._log(conn, "orders", model, len(payload), 0, 0, False, time.time() - began, str(e)[:500], reply)
        conn.commit()
        raise RetailError(f"The AI request failed: {e}") from e
    out = [{"item_id": it["id"], "category": ans[0], "new_category": ans[2], "confidence": round(ans[1], 2)}
           for n, it in enumerate(items[:AI_BATCH]) if (ans := answers.get(n)) and (ans[0] or ans[2])]
    new_cats = len({a["new_category"]["name"].lower() for a in out if a["new_category"]})
    categorize._log(conn, "orders", model, len(payload), len(out), new_cats, True, time.time() - began,
                    f"Suggested a category for {len(out)} of {len(payload)} items from {names} orders", reply)
    return out


def set_item_category(conn, item_id: int, category: str, remember: bool = True) -> dict:
    """You picked a category for an item: remember it for the same item in other orders, and re-split the
    transactions it's in."""
    if not conn.execute(select(Category.name).where(Category.name == category)).fetchone():
        raise RetailError(f"Unknown category: {category}")
    i = RetailItem
    it = conn.execute(select(i.id, i.order_id, i.title).where(i.id == item_id)).fetchone()
    if not it:
        raise RetailError("Item not found")
    conn.execute(update(i).where(i.id == item_id).values(category=category, category_source="manual", confidence=1))
    orders = {it["order_id"]}
    if remember and item_key(it["title"]):
        key = item_key(it["title"])
        db.upsert(conn, RetailItemMemory, {"key": key, "category": category}, key=["key"])
        for r in conn.execute(select(i.id, i.order_id, i.title)
                              .where(i.id != item_id, func.coalesce(i.category_source, "") != "manual")).fetchall():
            if item_key(r["title"]) == key:
                conn.execute(update(i).where(i.id == r["id"]).values(category=category, category_source="memory", confidence=1))
                orders.add(r["order_id"])
    redone = 0
    for ch in conn.execute(select(RetailCharge.id).where(RetailCharge.order_id.in_(list(orders)),
                                                         RetailCharge.tx_id.is_not(None))).fetchall():
        redone += apply(conn, ch["id"]) in ("split", "category")
    return {"orders": len(orders), "resplit": redone}


# ------------------------------------------------------------------------------------------------ matching

def _unlink(conn, ch) -> None:
    conn.execute(update(RetailCharge).where(RetailCharge.id == ch["id"]).values(tx_id=None, match_source=None))


def match(conn) -> int:
    """Pair each store charge with its bank transaction. Returns how many new pairs were made.

    Two orders' charges of the same amount a few days apart can each be either transaction, and the wrong pairing
    would give each the other's items, so a transaction that charges of more than one order could be is left for
    you to pick (the order's page lists it)."""
    # A transaction that's gone (a pending one that posted under a new id, say) frees its charge to match again.
    c, o, t = RetailCharge, RetailOrder, Transaction
    conn.execute(update(c).where(c.tx_id.is_not(None), c.tx_id.not_in(select(t.id))).values(tx_id=None, match_source=None))
    used = {r["tx_id"] for r in conn.execute(select(c.tx_id).where(c.tx_id.is_not(None)))}
    charges = db.rows(conn.execute(
        select(c.id, c.order_id, c.date, c.amount, c.not_tx, o.retailer).join(o, o.id == c.order_id)
        .where(c.tx_id.is_(None)).order_by(c.date, c.id)))
    options: dict[str, list[tuple[int, str]]] = {}   # charge -> [(score, tx id)], best first
    wanted: dict[str, set[str]] = {}                  # tx id -> the orders whose charges could be it
    for ch in charges:
        d = date.fromisoformat(ch["date"])
        lo, hi = (d - timedelta(days=MATCH_BEFORE)).isoformat(), (d + timedelta(days=MATCH_AFTER)).isoformat()
        rejected = set(json.loads(ch["not_tx"] or "[]"))
        found = []
        for tx in conn.execute(select(t.id, t.posted, t.payee, t.description)
                               .where(t.amount.between(ch["amount"] - CENT, ch["amount"] + CENT),
                                      t.posted.between(lo, hi))).fetchall():
            if tx["id"] in used or tx["id"] in rejected:
                continue
            if not MERCHANT[ch["retailer"]].search(f"{tx['payee'] or ''} {tx['description'] or ''}"):
                continue
            gap = (date.fromisoformat(tx["posted"]) - d).days
            found.append((gap if gap >= 0 else -gap * 2 + 1, tx["id"]))   # posting after the charge is usual; before, less so
            wanted.setdefault(tx["id"], set()).add(ch["order_id"])
        options[ch["id"]] = sorted(found)
    made = 0
    for ch in charges:
        found = options[ch["id"]]
        if any(len(wanted[t]) > 1 for _, t in found):
            continue   # another order's charge could be the same transaction: yours to pick
        best = next((t for _, t in found if t not in used), None)
        if best:
            conn.execute(update(c).where(c.id == ch["id"]).values(tx_id=best, match_source="auto"))
            used.add(best)
            made += 1
    return made


# ------------------------------------------------------------------------------------------------ splitting

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
    parts = []
    for cat, g in sorted(by_cat.items(), key=lambda kv: -kv[1]["weight"]):
        share = cents * g["weight"] / total_w
        parts.append({"category": cat, "cents": int(share), "rest": share - int(share), "titles": g["titles"]})
    # Whole cents: the ones rounding leaves over go to the parts that lost the most to it.
    for p in sorted(parts, key=lambda p: -p["rest"])[:cents - sum(p["cents"] for p in parts)]:
        p["cents"] += 1
    out = []
    for p in parts:
        if p["cents"] == 0:
            continue
        note = "; ".join(t for t in p["titles"] if t)
        out.append({"category": p["category"], "amount": sign * p["cents"] / 100,
                    "note": (note[:197] + "…") if len(note) > 200 else note})
    return out


def _is_ours(conn, tx_id: str, applied: dict | None) -> bool:
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
    ours = _is_ours(conn, tx["id"], applied)
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


def unlink(conn, charge_id: str) -> None:
    """This charge isn't that transaction: undo what Runway did to it and don't pair them again."""
    ch = conn.execute(select(RetailCharge).where(RetailCharge.id == charge_id)).fetchone()
    if not ch or not ch["tx_id"]:
        return
    applied = json.loads(ch["applied"]) if ch["applied"] else None
    tx = conn.execute(select(Transaction).where(Transaction.id == ch["tx_id"])).fetchone()
    if tx and applied:
        ours = _is_ours(conn, tx["id"], applied)
        if ours:
            splits.clear(conn, tx["id"])
        if tx["category_source"] == "retail" or ours:
            prev = applied.get("prev") or {}
            conn.execute(update(Transaction).where(Transaction.id == tx["id"])
                         .values(category=prev.get("category"), category_source=prev.get("source")))
    rejected = set(json.loads(ch["not_tx"] or "[]")) | {ch["tx_id"]}
    conn.execute(update(RetailCharge).where(RetailCharge.id == ch["id"])
                 .values(tx_id=None, match_source=None, applied=None, not_tx=json.dumps(sorted(rejected))))


def link(conn, charge_id: str, tx_id: str) -> str:
    """You said which transaction a charge is."""
    ch = conn.execute(select(RetailCharge).where(RetailCharge.id == charge_id)).fetchone()
    if not ch:
        raise RetailError("Charge not found")
    if not conn.execute(select(Transaction.id).where(Transaction.id == tx_id)).fetchone():
        raise RetailError("Transaction not found")
    other = conn.execute(select(RetailCharge.id).where(RetailCharge.tx_id == tx_id, RetailCharge.id != charge_id)).fetchone()
    if other:
        unlink(conn, other["id"])
    if ch["tx_id"] and ch["tx_id"] != tx_id:
        unlink(conn, charge_id)
    rejected = set(json.loads(ch["not_tx"] or "[]")) - {tx_id}
    conn.execute(update(RetailCharge).where(RetailCharge.id == charge_id)
                 .values(tx_id=tx_id, match_source="manual", not_tx=json.dumps(sorted(rejected))))
    return apply(conn, charge_id, force=True)


def match_and_apply(conn) -> dict:
    """Pair any new charges with transactions, and split what can be split (after a bank sync, say). No AI calls."""
    made = match(conn)
    out = {"matched": made, "split": 0, "category": 0}
    for ch in conn.execute(select(RetailCharge.id).where(RetailCharge.tx_id.is_not(None),
                                                         RetailCharge.applied.is_(None))).fetchall():
        r = apply(conn, ch["id"])
        if r in out:
            out[r] += 1
    return out


def finish(conn, retailer: str, caller=None, complete: bool = True, categorize_now: bool = True) -> dict:
    """The extension has sent everything: categorize the new items, then match and split.

    `complete` is False when the extension couldn't read all of the store's history (the store stopped answering,
    or there were more pages than it reads): then the last import's date stays where it was, so the next import
    reads the same stretch again. With `categorize_now` False only the matching is done here, and
    categorize_and_apply does the rest (the server runs it after answering, as the AI model can take a while)."""
    if retailer not in RETAILERS:
        raise RetailError("Unknown store")
    if complete:
        db.set_setting(conn, sk.retail_last(retailer), datetime.now().isoformat(timespec="seconds"))
    if not categorize_now:
        out = match_and_apply(conn)
        out["items"] = None
        return _summarize(conn, retailer, out)
    return categorize_and_apply(conn, retailer, caller)


def categorize_and_apply(conn, retailer: str, caller=None) -> dict:
    """Categorize new items, then match and split (and re-split what the new categories change)."""
    items = categorize_items(conn, caller=caller)
    out = match_and_apply(conn)
    # Items that just got a category change the split of transactions matched earlier, too.
    c, o = RetailCharge, RetailOrder
    for ch in conn.execute(select(c.id).join(o, o.id == c.order_id)
                           .where(c.tx_id.is_not(None), c.applied.is_not(None), o.retailer == retailer)).fetchall():
        r = apply(conn, ch["id"])
        if r in ("split", "category"):
            out[r] += 1
    out["items"] = items
    return _summarize(conn, retailer, out)


def _summarize(conn, retailer: str, out: dict) -> dict:
    out["orders"] = conn.execute(select(func.count()).select_from(RetailOrder).where(RetailOrder.retailer == retailer)).scalar()
    out["unmatched"] = unmatched_count(conn, retailer)
    db.set_setting(conn, sk.retail_summary(retailer), json.dumps(out))
    return out


def unmatched_count(conn, retailer: str | None = None) -> int:
    """Charges from the last few months with no transaction (their card may not be in Runway)."""
    cutoff = (date.today() - timedelta(days=FIRST_IMPORT_DAYS)).isoformat()
    c, o = RetailCharge, RetailOrder
    q = (select(func.count()).select_from(c).join(o, o.id == c.order_id)
         .where(c.tx_id.is_(None), c.amount < 0, c.date >= cutoff,
                c.date <= (date.today() - timedelta(days=MATCH_AFTER)).isoformat()))
    if retailer:
        q = q.where(o.retailer == retailer)
    return conn.execute(q).scalar()


# ------------------------------------------------------------------------------------------------ for the app

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


def candidates(conn, charge_id: str) -> list[dict]:
    """Transactions a charge might be, for picking by hand: same amount within a month, or any from the store near
    the date."""
    c, t = RetailCharge, Transaction
    ch = conn.execute(select(c.date, c.amount, RetailOrder.retailer).join(RetailOrder, RetailOrder.id == c.order_id)
                      .where(c.id == charge_id)).fetchone()
    if not ch:
        raise RetailError("Charge not found")
    d = date.fromisoformat(ch["date"])
    rows = db.rows(conn.execute(
        select(t.id, t.posted, t.amount, t.payee, t.description, db.account_label_expr().label("account_name"))
        .join(Account, Account.id == t.account_id)
        .where(t.posted.between((d - timedelta(days=10)).isoformat(), (d + timedelta(days=30)).isoformat()), t.amount < 0)
        .order_by(t.posted)))
    rx = MERCHANT[ch["retailer"]]
    rows = [r for r in rows if abs(r["amount"] - ch["amount"]) < CENT or rx.search(f"{r['payee'] or ''} {r['description'] or ''}")]
    rows.sort(key=lambda r: (abs(r["amount"] - ch["amount"]) >= CENT, abs((date.fromisoformat(r["posted"]) - d).days)))
    return rows[:25]


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
