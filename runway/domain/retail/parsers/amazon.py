"""Amazon: the Payments → Transactions pages list every card charge with its order number (Amazon charges each
shipment separately), and each order's details page lists its items. Both are read with the `amazon-orders` library's
parsers."""
from __future__ import annotations

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
    start = store.since(conn, "amazon")
    seen = {str(k): int(v) for k, v in (seen or {}).items() if isinstance(v, int)}
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
            "seen": seen}


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
