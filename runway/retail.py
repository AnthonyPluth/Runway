"""Amazon and Target orders: read them, match them to card transactions, and split each transaction by what was in it.

Neither store offers an API for your order history, so Runway's browser extension (extension/) reads it with the
sign-in you already have in your browser, the way the stores' own pages do, and sends what it gets here. All the
reading of those pages happens on this side, so when a store changes its pages the fix is an update to Runway
rather than to the extension:

- Amazon: the Payments → Transactions pages list every card charge with its order number (Amazon charges each
  shipment separately), and each order's details page lists its items. Both are read with the `amazon-orders`
  library's parsers.
- Target: its order history API, for online orders and in-store purchases (anything tied to your Target account:
  Target Circle, a saved card, the Wallet barcode). Each order counts as one charge of its total.

Each charge is matched to a bank transaction with the same amount a few days later, from a merchant that looks like
the store. The order's items are categorized (what you picked for the same item before, then the AI model if one is
set up), and the transaction is split across those categories in proportion to what the items cost, so tax,
shipping and discounts are shared out fairly. If everything lands in one category the transaction just gets it.

A transaction you categorized or split yourself is left alone.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time
from datetime import date, datetime, timedelta

from . import categorize, db, splits

RETAILERS = ("amazon", "target")
NAMES = {"amazon": "Amazon", "target": "Target"}
# How each store's charges read on a statement: "AMAZON MKTPL*2K3AB1", "Amazon.com*RT4", "AMZN Mktp US", "TARGET 00012345".
MERCHANT = {
    "amazon": re.compile(r"amazon|amzn|\bamz\b", re.I),
    "target": re.compile(r"\btarget\b|\btgt\b|target\.com", re.I),
}
FIRST_IMPORT_DAYS = 180      # how far back the first import reads
OVERLAP_DAYS = 30            # later imports start this long before the last one (orders ship, charges post)
MATCH_BEFORE, MATCH_AFTER = 3, 10   # a bank transaction may post this many days before / after the store's charge date
CENT = 0.005
MAX_ATTEMPTS = 3             # stop asking for an order's details page after this many that couldn't be read
AI_BATCH = 50
MAX_RAW = 200_000            # characters of a Target order's API reply kept for troubleshooting


class RetailError(ValueError):
    pass


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
        from dateutil import parser
        return parser.parse(s).date().isoformat()
    except (ValueError, OverflowError):
        return None


def item_key(title: str | None) -> str:
    return " ".join((title or "").lower().split())[:160]


# ------------------------------------------------------------------------------------------------ the extension's key

def new_token(conn) -> str:
    """A new key for the browser extension (replacing any earlier one). Only a hash of it is kept."""
    token = "rwx_" + secrets.token_urlsafe(32)
    db.set_setting(conn, "retail_token_hash", hashlib.sha256(token.encode()).hexdigest())
    db.set_setting(conn, "retail_token_created", datetime.now().isoformat(timespec="seconds"))
    return token


def remove_token(conn) -> None:
    db.set_setting(conn, "retail_token_hash", None)
    db.set_setting(conn, "retail_token_created", None)


def check_token(conn, authorization: str | None) -> bool:
    want = db.get_setting(conn, "retail_token_hash")
    m = re.match(r"Bearer\s+(\S+)$", (authorization or "").strip())
    if not want or not m:
        return False
    return hmac.compare_digest(hashlib.sha256(m.group(1).encode()).hexdigest(), want)


# ------------------------------------------------------------------------------------------------ storing orders

def _save_order(conn, retailer: str, number: str, **fields) -> str:
    oid = order_key(retailer, number)
    fields = {k: v for k, v in fields.items() if v is not None}
    if conn.execute("SELECT 1 FROM retail_orders WHERE id=?", (oid,)).fetchone():
        if fields:
            sets = ", ".join(f"{k}=?" for k in fields)
            conn.execute(f"UPDATE retail_orders SET {sets}, updated=? WHERE id=?",
                         (*fields.values(), datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"), oid))
    else:
        cols = ["id", "retailer", "order_number", *fields]
        conn.execute(f"INSERT INTO retail_orders({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                     (oid, retailer, number, *fields.values()))
    return oid


def _save_items(conn, oid: str, items: list[dict]) -> None:
    """Replace an order's items, keeping the category of any item you had already set by hand."""
    kept = {item_key(r["title"]): (r["category"], r["category_source"], r["confidence"])
            for r in conn.execute("SELECT title, category, category_source, confidence FROM retail_items "
                                  "WHERE order_id=? AND category_source IS NOT NULL", (oid,))}
    conn.execute("DELETE FROM retail_items WHERE order_id=?", (oid,))
    for i, it in enumerate(items):
        cat, src, conf = kept.get(item_key(it["title"]), (None, None, None))
        conn.execute("INSERT INTO retail_items(order_id, position, title, quantity, amount, department, category, "
                     "category_source, confidence) VALUES (?,?,?,?,?,?,?,?,?)",
                     (oid, i, it["title"], it.get("quantity") or 1, round(it["amount"], 2), it.get("department"),
                      cat, src, conf))


def _save_charge(conn, cid: str, oid: str, day: str, amount: float, payment: str | None) -> None:
    conn.execute(
        "INSERT INTO retail_charges(id, order_id, date, amount, payment) VALUES (?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET date=excluded.date, amount=excluded.amount, payment=excluded.payment",
        (cid, oid, day, round(amount, 2), payment))


def _need(conn, retailer: str, numbers) -> list[str]:
    """Which of these orders still need their details read."""
    out = []
    for n in dict.fromkeys(numbers):
        r = conn.execute("SELECT details, attempts FROM retail_orders WHERE id=?", (order_key(retailer, n),)).fetchone()
        if r and not r["details"] and (r["attempts"] or 0) < MAX_ATTEMPTS:
            out.append(n)
    return out


def since(conn, retailer: str) -> str:
    """The earliest date the extension should read back to this time."""
    last = db.get_setting(conn, f"retail_last_{retailer}")
    if last:
        start = datetime.fromisoformat(last).date() - timedelta(days=OVERLAP_DAYS)
    else:
        start = date.today() - timedelta(days=FIRST_IMPORT_DAYS)
    return start.isoformat()


# ------------------------------------------------------------------------------------------------ Amazon

def _payment(method: str | None, last4: str | None) -> str | None:
    """"Visa ****1234" (Amazon's own wording usually has the last digits already)."""
    method = " ".join((method or "").split())
    if last4 and last4 not in method:
        method = f"{method} {last4}".strip()
    return method or None


def _amazon_config():
    import os
    import tempfile
    from amazonorders.conf import AmazonOrdersConfig
    # A config file that doesn't exist: nothing is read from (or written to) ~/.config/amazonorders.
    return AmazonOrdersConfig(config_path=os.path.join(tempfile.gettempdir(), "runway-amazonorders-none.yml"),
                              data={"warn_on_missing_required_field": True})


def _signed_out(html: str) -> bool:
    return bool(re.search(r"<form[^>]+name=['\"]signIn['\"]|ap/signin", html[:200_000])) and "apx-transaction" not in html


AMAZON_ORDER = re.compile(r"^(?:\d{3}|D\d{2})-\d{7}-\d{7}$")


def amazon_transactions(conn, html: str) -> dict:
    """One page of Amazon's Payments → Transactions. Saves each charge and refund against its order.

    Returns what the extension should do next: `next_form` (post it back to the same page for the next page, or
    None when this page reaches back past `since`) and `orders`, the order numbers whose details it should send."""
    from amazonorders.exception import AmazonOrdersError
    from amazonorders.transactions import _parse_transactions_page
    from bs4 import BeautifulSoup
    if _signed_out(html):
        raise RetailError("Amazon asked to sign in. Sign in to Amazon in this browser, then try again.")
    cfg = _amazon_config()
    try:
        txs, next_form = _parse_transactions_page(BeautifulSoup(html, cfg.bs4_parser), cfg)
    except AmazonOrdersError as e:
        raise RetailError(f"Runway couldn't read Amazon's transactions page: {e}")
    start = since(conn, "amazon")
    seen: dict[tuple, int] = {}
    numbers, oldest = [], None
    for t in txs:
        day = _day(t.completed_date)
        oldest = min(oldest or day, day) if day else oldest
        number = (t.order_number or "").strip()
        if not day or not AMAZON_ORDER.match(number) or t.grand_total is None:
            continue   # a payment that isn't for an order (a gift card reload, a bank refund line, ...)
        if day < start:
            continue
        oid = _save_order(conn, "amazon", number)
        k = (number, day, round(t.grand_total, 2))
        seen[k] = seen.get(k, 0) + 1   # the same amount twice on one day for one order: two charges
        pay = _payment(t.payment_method, t.payment_method_last_4)
        _save_charge(conn, f"amazon|{number}|{day}|{round(t.grand_total * 100)}|{seen[k]}", oid, day,
                     float(t.grand_total), pay)
        numbers.append(number)
    more = bool(next_form) and bool(oldest) and oldest >= start
    return {"next_form": next_form if more else None, "orders": _need(conn, "amazon", numbers), "read": len(txs)}


def amazon_order(conn, number: str, html: str) -> dict:
    """An Amazon order's details page: its items and totals."""
    from amazonorders.exception import AmazonOrdersError
    from amazonorders.orders import AmazonOrders
    number = (number or "").strip()
    if not AMAZON_ORDER.match(number):
        raise RetailError("That isn't an Amazon order number")
    oid = _save_order(conn, "amazon", number)
    try:
        order = AmazonOrders.parse_order_details(html, _amazon_config(), order_number=number)
        items = [{"title": " ".join((i.title or "").split()) or "Item", "quantity": i.quantity or 1,
                  "amount": (i.price or 0) * (i.quantity or 1)} for i in order.items]
    except (AmazonOrdersError, AttributeError, TypeError, ValueError):
        conn.execute("UPDATE retail_orders SET attempts=COALESCE(attempts, 0)+1 WHERE id=?", (oid,))
        return {"read": False}
    if not items:
        conn.execute("UPDATE retail_orders SET attempts=COALESCE(attempts, 0)+1 WHERE id=?", (oid,))
        return {"read": False}
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

_NUMBER = ("order_number", "orderNumber", "order_id", "orderId", "receipt_id", "receiptId", "transaction_id")
_DATE = ("placed_date", "placedDate", "order_date", "orderDate", "order_placed_date", "purchase_date", "purchaseDate",
         "transaction_date", "transactionDate", "date")
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


def _find_orders(data) -> list[dict]:
    """The order objects in a reply: the first list of objects that carry an order number."""
    if isinstance(data, list):
        if data and all(isinstance(x, dict) for x in data) and any(_first(x, _NUMBER, ()) for x in data):
            return data
        for x in data:
            found = _find_orders(x)
            if found:
                return found
    elif isinstance(data, dict):
        for k in ("orders", "order_history", "orderHistory", "data", "result"):
            if k in data:
                found = _find_orders(data[k])
                if found:
                    return found
        for v in data.values():
            if isinstance(v, (list, dict)):
                found = _find_orders(v)
                if found:
                    return found
    return []


def _find_lines(obj) -> list[dict]:
    if isinstance(obj, dict):
        for k in _LINES:
            v = obj.get(k)
            if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
                return v
        for v in obj.values():
            if isinstance(v, (dict, list)):
                found = _find_lines(v)
                if found:
                    return found
    elif isinstance(obj, list):
        for x in obj:
            found = _find_lines(x)
            if found:
                return found
    return []


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
    return {"title": " ".join(title.split())[:300], "quantity": qty, "amount": abs(amount),
            "department": dept if isinstance(dept, str) else None}


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
    lines = [x for x in (_target_line(l) for l in _find_lines(o)) if x]
    raw = json.dumps(o, separators=(",", ":"))[:MAX_RAW]
    oid = _save_order(conn, "target", number, channel=ch, placed=placed, total=abs(total) if total is not None else None,
                      subtotal=_money(_first(o, _SUBTOTAL)), tax=_money(_first(o, _TAX)), raw=raw,
                      details=1 if lines else None)
    if lines:
        _save_items(conn, oid, lines)
    if placed and total:
        _save_charge(conn, f"target|{number}", oid, placed, -abs(total), None)
    return number, bool(lines)


def target_history(conn, data, purchase_type: str | None = None) -> dict:
    """A page of Target's order history (online or in store). Returns whether to read the next page, and which
    orders need their details read."""
    orders = _find_orders(data)
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
    return {"more": bool(orders) and not older, "orders": _need(conn, "target", numbers), "read": len(orders)}


def target_order(conn, number: str, data) -> dict:
    """The details of one Target order (online order or store receipt)."""
    number = str(number or "").strip()
    if not number:
        raise RetailError("Which order is this?")
    found = _find_orders(data)
    o = next((x for x in found if str(_first(x, _NUMBER, ())) == number), None) or (data if isinstance(data, dict) else {})
    o = dict(o)
    o.setdefault("order_number", number)
    oid = order_key("target", number)
    _, read = _target_order(conn, o, None)
    if not read:
        conn.execute("UPDATE retail_orders SET attempts=COALESCE(attempts, 0)+1 WHERE id=?", (oid,))
    return {"read": read}


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
                items: list[dict]) -> str:
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
    if examples:
        lines += ["", "How this person has categorized items before:"]
        lines += [f"- {e['title']} -> {e['category']}" for e in examples]
    lines += ["", "Items (JSON):", json.dumps(items, ensure_ascii=False), "",
              'Reply with only a JSON array, one object per item: [{"i": <i>, "category": "<category>", "confidence": <0-1>}]']
    return "\n".join(lines)


def categorize_items(conn, use_ai: bool = True, caller=None) -> dict:
    """Give a category to every item that doesn't have one: what you picked for the same item before, then the AI
    model (if set up), then the store's department. Items nothing decides are left for the transaction's own
    category."""
    caller = caller or categorize.call_llm
    counts = {"memory": 0, "ai": 0, "department": 0, "left": 0}
    memory = {r["key"]: r["category"] for r in conn.execute("SELECT key, category FROM retail_item_memory")}
    have = {r["name"] for r in conn.execute("SELECT name FROM categories")}
    spend = {r["name"] for r in conn.execute("SELECT name FROM categories WHERE is_transfer=0 AND is_income=0")}
    todo = db.rows(conn.execute(
        "SELECT i.id, i.title, i.amount, i.department, o.retailer FROM retail_items i "
        "JOIN retail_orders o ON o.id=i.order_id WHERE i.category_source IS NULL ORDER BY i.id"))
    left = []
    for it in todo:
        cat = memory.get(item_key(it["title"]))
        if cat in have:
            conn.execute("UPDATE retail_items SET category=?, category_source='memory', confidence=1 WHERE id=?",
                         (cat, it["id"]))
            counts["memory"] += 1
        else:
            left.append(it)

    api_key = db.get_setting(conn, "openrouter_api_key")
    if left and use_ai and api_key and (db.get_setting(conn, "retail_ai", "1") or "1") == "1":
        model = db.get_setting(conn, "llm_model", categorize.DEFAULT_MODEL) or categorize.DEFAULT_MODEL
        cats = sorted(spend)
        subs = [h for h in categorize._subcategory_hints(conn) if h.split(" > ")[-1] in spend]
        examples = [{"title": r["title"][:80], "category": r["category"]} for r in conn.execute(
            "SELECT title, category FROM retail_items WHERE category_source='manual' ORDER BY id DESC LIMIT 40")]
        names = " and ".join(sorted({NAMES.get(it["retailer"], it["retailer"]) for it in left}))
        conn.commit()   # don't hold the database while the model thinks
        for start in range(0, len(left), AI_BATCH):
            batch = left[start:start + AI_BATCH]
            payload = [{"i": i, "item": it["title"][:200], "price": round(it["amount"] or 0, 2),
                        **({"department": it["department"]} if it["department"] else {})} for i, it in enumerate(batch)]
            began, reply = time.time(), None
            try:
                reply = caller(api_key, model, item_prompt(names, cats, subs, examples, payload))
                answers = categorize.parse_ai_reply(reply, cats)
                answered = sum(1 for a in answers.values() if a[0])
                categorize._log(conn, "orders", model, len(batch), answered, 0, True, time.time() - began,
                                f"Categorized {answered} of {len(batch)} items from {names} orders", reply)
            except Exception as e:   # recorded in the AI log; these fall back below
                categorize._log(conn, "orders", model, len(batch), 0, 0, False, time.time() - began, str(e)[:500], reply)
                conn.commit()
                break
            for i, it in enumerate(batch):
                cat, conf = answers.get(i, (None, 0.0))
                if cat:
                    conn.execute("UPDATE retail_items SET category=?, category_source='ai', confidence=? "
                                 "WHERE id=? AND category_source IS NULL", (cat, conf, it["id"]))
                    counts["ai"] += 1
                    it["done"] = True
            conn.commit()
    for it in left:
        if it.get("done"):
            continue
        cat = _department_category(it["department"], have)
        if cat:
            conn.execute("UPDATE retail_items SET category=?, category_source='department', confidence=0.6 WHERE id=?",
                         (cat, it["id"]))
            counts["department"] += 1
        else:
            counts["left"] += 1
    return counts


def set_item_category(conn, item_id: int, category: str, remember: bool = True) -> dict:
    """You picked a category for an item: remember it for the same item in other orders, and re-split the
    transactions it's in."""
    if not conn.execute("SELECT 1 FROM categories WHERE name=?", (category,)).fetchone():
        raise RetailError(f"Unknown category: {category}")
    it = conn.execute("SELECT id, order_id, title FROM retail_items WHERE id=?", (item_id,)).fetchone()
    if not it:
        raise RetailError("Item not found")
    conn.execute("UPDATE retail_items SET category=?, category_source='manual', confidence=1 WHERE id=?", (category, item_id))
    orders = {it["order_id"]}
    if remember and item_key(it["title"]):
        key = item_key(it["title"])
        conn.execute("INSERT INTO retail_item_memory(key, category) VALUES (?,?) "
                     "ON CONFLICT(key) DO UPDATE SET category=excluded.category", (key, category))
        for r in conn.execute("SELECT id, order_id, title FROM retail_items WHERE id<>? AND "
                              "COALESCE(category_source, '') <> 'manual'", (item_id,)).fetchall():
            if item_key(r["title"]) == key:
                conn.execute("UPDATE retail_items SET category=?, category_source='memory', confidence=1 WHERE id=?",
                             (category, r["id"]))
                orders.add(r["order_id"])
    q = ",".join("?" * len(orders))
    redone = 0
    for ch in conn.execute(f"SELECT id FROM retail_charges WHERE order_id IN ({q}) AND tx_id IS NOT NULL", list(orders)).fetchall():
        redone += apply(conn, ch["id"]) in ("split", "category")
    return {"orders": len(orders), "resplit": redone}


# ------------------------------------------------------------------------------------------------ matching

def _unlink(conn, ch) -> None:
    conn.execute("UPDATE retail_charges SET tx_id=NULL, match_source=NULL WHERE id=?", (ch["id"],))


def match(conn) -> int:
    """Pair each store charge with its bank transaction. Returns how many new pairs were made."""
    # A transaction that's gone (a pending one that posted under a new id, say) frees its charge to match again.
    conn.execute("UPDATE retail_charges SET tx_id=NULL, match_source=NULL WHERE tx_id IS NOT NULL "
                 "AND tx_id NOT IN (SELECT id FROM transactions)")
    used = {r["tx_id"] for r in conn.execute("SELECT tx_id FROM retail_charges WHERE tx_id IS NOT NULL")}
    charges = db.rows(conn.execute(
        "SELECT c.id, c.date, c.amount, c.not_tx, o.retailer FROM retail_charges c JOIN retail_orders o ON o.id=c.order_id "
        "WHERE c.tx_id IS NULL ORDER BY c.date, c.id"))
    made = 0
    for ch in charges:
        d = date.fromisoformat(ch["date"])
        lo, hi = (d - timedelta(days=MATCH_BEFORE)).isoformat(), (d + timedelta(days=MATCH_AFTER)).isoformat()
        rejected = set(json.loads(ch["not_tx"] or "[]"))
        best, best_gap = None, None
        for t in conn.execute("SELECT id, posted, payee, description FROM transactions WHERE amount BETWEEN ? AND ? "
                              "AND posted BETWEEN ? AND ?", (ch["amount"] - CENT, ch["amount"] + CENT, lo, hi)).fetchall():
            if t["id"] in used or t["id"] in rejected:
                continue
            if not MERCHANT[ch["retailer"]].search(f"{t['payee'] or ''} {t['description'] or ''}"):
                continue
            gap = (date.fromisoformat(t["posted"]) - d).days
            score = gap if gap >= 0 else -gap * 2 + 1   # posting after the charge is usual; before, less so
            if best_gap is None or score < best_gap:
                best, best_gap = t["id"], score
        if best:
            conn.execute("UPDATE retail_charges SET tx_id=?, match_source='auto' WHERE id=?", (best, ch["id"]))
            used.add(best)
            made += 1
    return made


# ------------------------------------------------------------------------------------------------ splitting

def _fallback_category(conn, tx) -> str | None:
    """For items nothing categorized: the transaction's own category if it's spending, else Shopping."""
    if tx["category"]:
        r = conn.execute("SELECT is_transfer, is_income FROM categories WHERE name=?", (tx["category"],)).fetchone()
        if r and not r["is_transfer"] and not r["is_income"]:
            return tx["category"]
    if conn.execute("SELECT 1 FROM categories WHERE name='Shopping'").fetchone():
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


def apply(conn, charge_id: str, force: bool = False) -> str:
    """Categorize or split a charge's transaction by its order's items. Returns what happened:
    split | category | same | no-items | waiting | unmatched | manual | user-split."""
    ch = conn.execute("SELECT * FROM retail_charges WHERE id=?", (charge_id,)).fetchone()
    if not ch or not ch["tx_id"]:
        return "unmatched"
    tx = conn.execute("SELECT * FROM transactions WHERE id=?", (ch["tx_id"],)).fetchone()
    if not tx:
        return "unmatched"
    if ch["amount"] > 0:
        return "same"   # refunds keep their category
    order = conn.execute("SELECT * FROM retail_orders WHERE id=?", (ch["order_id"],)).fetchone()
    items = db.rows(conn.execute("SELECT title, amount, category FROM retail_items WHERE order_id=? ORDER BY position, id",
                                 (ch["order_id"],)))
    if not items:
        return "no-items"
    if not any(i["category"] for i in items):
        return "waiting"   # nothing has decided any item yet: leave the transaction as it is until something does
    applied = json.loads(ch["applied"]) if ch["applied"] else None
    ours = _is_ours(conn, tx["id"], applied)
    if tx["is_split"] and not ours and not force:
        return "user-split"
    if not tx["is_split"] and tx["category_source"] == "manual" and not force:
        return "manual"
    prev = (applied or {}).get("prev") or {"category": tx["category"], "source": tx["category_source"]}
    fallback = _fallback_category(conn, {"category": prev["category"]})
    parts = allocate(tx["amount"], items, fallback, order["total"] if order else None)
    if not parts:
        return "no-items"
    if len(parts) == 1:
        if tx["is_split"]:
            splits.clear(conn, tx["id"])
        if tx["category"] == parts[0]["category"] and tx["category_source"] in ("retail", "manual") and not tx["is_split"]:
            result = "same"
        else:
            conn.execute("UPDATE transactions SET category=?, category_source='retail', confidence=1, needs_review=0 "
                         "WHERE id=?", (parts[0]["category"], tx["id"]))
            result = "category"
        parts_saved = []
    else:
        if ours and [(p["category"], p["amount"]) for p in applied["parts"]] == [(p["category"], p["amount"]) for p in parts]:
            return "same"
        splits.set_splits(conn, tx["id"], parts)
        if not tx["category"]:
            conn.execute("UPDATE transactions SET category=? WHERE id=?", (parts[0]["category"], tx["id"]))
        result, parts_saved = "split", parts
    conn.execute("UPDATE retail_charges SET applied=? WHERE id=?",
                 (json.dumps({"parts": parts_saved, "category": parts[0]["category"] if not parts_saved else None,
                              "prev": prev}), ch["id"]))
    return result


def unlink(conn, charge_id: str) -> None:
    """This charge isn't that transaction: undo what Runway did to it and don't pair them again."""
    ch = conn.execute("SELECT * FROM retail_charges WHERE id=?", (charge_id,)).fetchone()
    if not ch or not ch["tx_id"]:
        return
    applied = json.loads(ch["applied"]) if ch["applied"] else None
    tx = conn.execute("SELECT * FROM transactions WHERE id=?", (ch["tx_id"],)).fetchone()
    if tx and applied:
        ours = _is_ours(conn, tx["id"], applied)
        if ours:
            splits.clear(conn, tx["id"])
        if tx["category_source"] == "retail" or ours:
            prev = applied.get("prev") or {}
            conn.execute("UPDATE transactions SET category=?, category_source=? WHERE id=?",
                         (prev.get("category"), prev.get("source"), tx["id"]))
    rejected = set(json.loads(ch["not_tx"] or "[]")) | {ch["tx_id"]}
    conn.execute("UPDATE retail_charges SET tx_id=NULL, match_source=NULL, applied=NULL, not_tx=? WHERE id=?",
                 (json.dumps(sorted(rejected)), ch["id"]))


def link(conn, charge_id: str, tx_id: str) -> str:
    """You said which transaction a charge is."""
    ch = conn.execute("SELECT * FROM retail_charges WHERE id=?", (charge_id,)).fetchone()
    if not ch:
        raise RetailError("Charge not found")
    if not conn.execute("SELECT 1 FROM transactions WHERE id=?", (tx_id,)).fetchone():
        raise RetailError("Transaction not found")
    other = conn.execute("SELECT id FROM retail_charges WHERE tx_id=? AND id<>?", (tx_id, charge_id)).fetchone()
    if other:
        unlink(conn, other["id"])
    if ch["tx_id"] and ch["tx_id"] != tx_id:
        unlink(conn, charge_id)
    rejected = set(json.loads(ch["not_tx"] or "[]")) - {tx_id}
    conn.execute("UPDATE retail_charges SET tx_id=?, match_source='manual', not_tx=? WHERE id=?",
                 (tx_id, json.dumps(sorted(rejected)), charge_id))
    return apply(conn, charge_id, force=True)


def match_and_apply(conn) -> dict:
    """Pair any new charges with transactions, and split what can be split (after a bank sync, say). No AI calls."""
    made = match(conn)
    out = {"matched": made, "split": 0, "category": 0}
    for ch in conn.execute("SELECT id FROM retail_charges WHERE tx_id IS NOT NULL AND applied IS NULL").fetchall():
        r = apply(conn, ch["id"])
        if r in out:
            out[r] += 1
    return out


def finish(conn, retailer: str, caller=None) -> dict:
    """The extension has sent everything: categorize the new items, then match and split."""
    if retailer not in RETAILERS:
        raise RetailError("Unknown store")
    db.set_setting(conn, f"retail_last_{retailer}", datetime.now().isoformat(timespec="seconds"))
    items = categorize_items(conn, caller=caller)
    out = match_and_apply(conn)
    # Items that just got a category change the split of transactions matched earlier, too.
    for ch in conn.execute("SELECT c.id FROM retail_charges c JOIN retail_orders o ON o.id=c.order_id "
                           "WHERE c.tx_id IS NOT NULL AND c.applied IS NOT NULL AND o.retailer=?", (retailer,)).fetchall():
        r = apply(conn, ch["id"])
        if r in ("split", "category"):
            out[r] += 1
    out["items"] = items
    out["orders"] = conn.execute("SELECT COUNT(*) FROM retail_orders WHERE retailer=?", (retailer,)).fetchone()[0]
    out["unmatched"] = unmatched_count(conn, retailer)
    db.set_setting(conn, f"retail_summary_{retailer}", json.dumps(out))
    return out


def unmatched_count(conn, retailer: str | None = None) -> int:
    """Charges from the last few months with no transaction (their card may not be in Runway)."""
    cutoff = (date.today() - timedelta(days=FIRST_IMPORT_DAYS)).isoformat()
    q = ("SELECT COUNT(*) FROM retail_charges c JOIN retail_orders o ON o.id=c.order_id "
         "WHERE c.tx_id IS NULL AND c.amount < 0 AND c.date >= ? AND c.date <= ?")
    args = [cutoff, (date.today() - timedelta(days=MATCH_AFTER)).isoformat()]
    if retailer:
        q += " AND o.retailer=?"
        args.append(retailer)
    return conn.execute(q, args).fetchone()[0]


# ------------------------------------------------------------------------------------------------ for the app

def for_transactions(conn, tx_ids: list[str]) -> dict[str, dict]:
    """{tx_id: {order_id, retailer, order_number, items}} for transactions that are store charges."""
    if not tx_ids:
        return {}
    out = {}
    for i in range(0, len(tx_ids), 500):
        chunk = tx_ids[i:i + 500]
        q = ",".join("?" * len(chunk))
        for r in conn.execute(
                f"SELECT c.tx_id, c.id AS charge_id, o.id, o.retailer, o.order_number, o.channel, "
                f"(SELECT COUNT(*) FROM retail_items i WHERE i.order_id=o.id) AS items "
                f"FROM retail_charges c JOIN retail_orders o ON o.id=c.order_id WHERE c.tx_id IN ({q})", chunk):
            out[r["tx_id"]] = {"order_id": r["id"], "charge_id": r["charge_id"], "retailer": r["retailer"],
                               "order_number": r["order_number"], "channel": r["channel"], "items": r["items"]}
    return out


def order_detail(conn, oid: str) -> dict:
    o = conn.execute("SELECT id, retailer, order_number, channel, placed, total, subtotal, tax, shipping, payment, details "
                     "FROM retail_orders WHERE id=?", (oid,)).fetchone()
    if not o:
        raise RetailError("Order not found")
    out = dict(o)
    out["items"] = db.rows(conn.execute("SELECT id, title, quantity, amount, department, category, category_source, confidence "
                                        "FROM retail_items WHERE order_id=? ORDER BY position, id", (oid,)))
    charges = db.rows(conn.execute(
        "SELECT c.id, c.date, c.amount, c.payment, c.tx_id, c.match_source, c.applied, t.posted, t.payee, t.description, "
        + db.label_sql("a") + " AS account_name FROM retail_charges c LEFT JOIN transactions t ON t.id=c.tx_id "
        "LEFT JOIN accounts a ON a.id=t.account_id WHERE c.order_id=? ORDER BY c.date, c.id", (oid,)))
    for c in charges:
        applied = json.loads(c.pop("applied") or "null")
        c["applied"] = ("split" if applied and applied.get("parts") else "category" if applied else None)
    out["charges"] = charges
    out["url"] = (f"https://www.amazon.com/gp/your-account/order-details?orderID={o['order_number']}" if o["retailer"] == "amazon"
                  else "https://www.target.com/orders")
    return out


def candidates(conn, charge_id: str) -> list[dict]:
    """Transactions a charge might be, for picking by hand: same amount within a month, or any from the store near
    the date."""
    ch = conn.execute("SELECT c.*, o.retailer FROM retail_charges c JOIN retail_orders o ON o.id=c.order_id WHERE c.id=?",
                      (charge_id,)).fetchone()
    if not ch:
        raise RetailError("Charge not found")
    d = date.fromisoformat(ch["date"])
    rows = db.rows(conn.execute(
        "SELECT t.id, t.posted, t.amount, t.payee, t.description, " + db.label_sql("a") + " AS account_name "
        "FROM transactions t JOIN accounts a ON a.id=t.account_id WHERE t.posted BETWEEN ? AND ? AND t.amount < 0 "
        "ORDER BY t.posted", ((d - timedelta(days=10)).isoformat(), (d + timedelta(days=30)).isoformat())))
    rx = MERCHANT[ch["retailer"]]
    rows = [r for r in rows if abs(r["amount"] - ch["amount"]) < CENT or rx.search(f"{r['payee'] or ''} {r['description'] or ''}")]
    rows.sort(key=lambda r: (abs(r["amount"] - ch["amount"]) >= CENT, abs((date.fromisoformat(r["posted"]) - d).days)))
    return rows[:25]


def status(conn) -> dict:
    out = {"token": bool(db.get_setting(conn, "retail_token_hash")), "token_created": db.get_setting(conn, "retail_token_created"),
           "ai": (db.get_setting(conn, "retail_ai", "1") or "1") == "1", "stores": {}}
    for r in RETAILERS:
        counts = conn.execute(
            "SELECT COUNT(*) AS orders, SUM(CASE WHEN details=1 THEN 1 ELSE 0 END) AS read FROM retail_orders WHERE retailer=?",
            (r,)).fetchone()
        matched = conn.execute("SELECT COUNT(*) FROM retail_charges c JOIN retail_orders o ON o.id=c.order_id "
                               "WHERE o.retailer=? AND c.tx_id IS NOT NULL", (r,)).fetchone()[0]
        out["stores"][r] = {"name": NAMES[r], "last": db.get_setting(conn, f"retail_last_{r}"),
                            "orders": counts["orders"] or 0, "read": counts["read"] or 0, "matched": matched,
                            "unmatched": unmatched_count(conn, r)}
    out["recent"] = db.rows(conn.execute(
        "SELECT o.id, o.retailer, o.order_number, o.channel, o.placed, o.total, o.details, "
        "(SELECT COUNT(*) FROM retail_items i WHERE i.order_id=o.id) AS items, "
        "(SELECT COUNT(*) FROM retail_charges c WHERE c.order_id=o.id AND c.amount < 0) AS charges, "
        "(SELECT COUNT(*) FROM retail_charges c WHERE c.order_id=o.id AND c.amount < 0 AND c.tx_id IS NOT NULL) AS matched "
        "FROM retail_orders o ORDER BY COALESCE(o.placed, '9999') DESC, o.id LIMIT 60"))
    return out
