"""Amazon: the Payments → Transactions pages list every card charge with its order number (Amazon charges each
shipment separately), and each order's details page lists its items. Both are read with the `amazon-orders` library's
parsers."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile

from .. import store
from ..store import RetailError, need_details, save_charge, save_items, save_order, tried
from .common import read_day


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


def _page_key(txs, next_form) -> str:
    """What a page of transactions holds, so the same page sent twice in one import is known (Amazon answering a
    request for the next page with the one it already gave)."""
    rows = [(t.order_number, str(t.completed_date), t.grand_total, t.payment_method) for t in txs]
    return "page|" + hashlib.sha256(json.dumps([rows, next_form], sort_keys=True, default=str).encode()).hexdigest()


def amazon_transactions(conn, html: str, seen: dict | None = None, data: dict | None = None,
                        page: dict | None = None) -> dict:
    """One page of Amazon's Payments → Transactions. Saves each charge and refund against its order.

    The first page is always the page's HTML. Amazon's newer Transactions page then gives further pages as data: the
    extension sends that reply as `data`, with `page`, the `next_form` it asked for it with.

    Returns what the extension should do next: `next_form` (what to ask for the next page with: posted back to the
    same page, or, when it has an `exclusiveStartKey`, sent to Amazon's transactions data address; None when this
    page reaches back past `since`, or repeats one already read), `orders`, the order numbers whose details it should
    send, `repeated` when this page was one already read, and `seen`, which it sends back with the next page (so the
    same charge twice across two pages is still two charges)."""
    # amazon-orders (and BeautifulSoup) are heavy: loaded only when an Amazon page comes in.
    from amazonorders.exception import AmazonOrdersError
    from amazonorders.transactions import TransactionsPage
    cfg = _amazon_config()
    if data is not None:
        txs, next_form = _data_page(cfg, data, page)
    else:
        from bs4 import BeautifulSoup
        if _signed_out(html):
            raise RetailError("Amazon asked to sign in. Sign in to Amazon in this browser, then try again.", "signin")
        blocked = _amazon_blocked(html)
        if blocked:
            raise blocked
        try:
            txs, next_form = TransactionsPage(cfg).parse_page(BeautifulSoup(html, cfg.bs4_parser))
        except AmazonOrdersError as e:
            raise RetailError(f"Runway couldn't read Amazon's transactions page: {e}") from e
    seen = {str(k): int(v) for k, v in (seen or {}).items() if isinstance(v, int)}
    key = _page_key(txs, next_form)
    if key in seen:   # read already in this import: saving it again would count each of its charges twice
        return {"next_form": None, "orders": [], "read": len(txs), "seen": seen, "repeated": True}
    seen[key] = 1
    start = store.since(conn, "amazon")
    numbers: list[str] = []
    oldest: str | None = None
    for t in txs:
        day = read_day(t.completed_date)
        oldest = min(oldest or day, day) if day else oldest
        number = (t.order_number or "").strip()
        if not day or not AMAZON_ORDER.match(number) or t.grand_total is None:
            continue   # a payment that isn't for an order (a gift card reload, a bank refund line, ...)
        if day < start:
            continue
        oid = save_order(conn, "amazon", number)
        k = f"{number}|{day}|{round(t.grand_total * 100)}"
        seen[k] = seen.get(k, 0) + 1   # the same amount twice on one day for one order: two charges
        pay = _payment(t.payment_method, t.payment_method_last_4)
        save_charge(conn, f"amazon|{number}|{day}|{round(t.grand_total * 100)}|{seen[k]}", oid, day,
                    float(t.grand_total), pay)
        numbers.append(number)
    more = bool(next_form and oldest and oldest >= start)
    return {"next_form": next_form if more else None, "orders": need_details(conn, "amazon", numbers), "read": len(txs),
            "seen": seen, "repeated": False}


def _data_page(cfg, data: dict, page: dict | None):
    """A further page of Amazon's newer Transactions page: its transactions data reply, read as amazon-orders reads
    it, and what to ask for the page after it with (`page` with the reply's last key)."""
    from amazonorders.exception import AmazonOrdersError
    from amazonorders.transactions import TransactionsPage
    if not isinstance(page, dict) or not page.get("exclusiveStartKey"):
        raise RetailError("Runway couldn't read Amazon's transactions: that page wasn't one it asked for")
    try:
        display = data["displayResponse"]
        # amazon-orders reads this reply in `get_page`, which fetches it too; Runway's extension does the fetching. The
        # method is private, so a release that renames it is read as a changed page (AttributeError), not a crash.
        txs = TransactionsPage(cfg)._parse_transaction_list(display["transactionsList"])
    except (AmazonOrdersError, AttributeError, KeyError, TypeError, ValueError) as e:
        raise RetailError("Runway couldn't read Amazon's transactions: Amazon may have changed the page") from e
    last = display.get("lastEvaluatedKey")
    return txs, ({**page, "exclusiveStartKey": last} if last and txs else None)


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
    oid = save_order(conn, "amazon", number)
    try:
        order = AmazonOrders.parse_order_details(html, _amazon_config(), order_number=number)
        items = [{"title": " ".join((i.title or "").split()) or "Item", "quantity": i.quantity or 1,
                  "amount": (i.price or 0) * (i.quantity or 1)} for i in order.items]
    except (AmazonOrdersError, AttributeError, TypeError, ValueError):
        return tried(conn, oid, final)
    if not items:
        return tried(conn, oid, final)
    pay = _payment(order.payment_method, order.payment_method_last_4)
    save_order(conn, "amazon", number, channel="online", placed=read_day(order.order_placed_date),
               total=order.grand_total, subtotal=order.subtotal, tax=order.estimated_tax,
               shipping=order.shipping_total, payment=pay, details=1)
    save_items(conn, oid, items)
    return {"read": True, "items": len(items)}
