"""Amazon, Target and Costco orders: the Settings page's calls, and the browser extension's (/api/ext/...), which bring the
orders in."""
from __future__ import annotations

import io
import os
import threading
import zipfile

from sqlalchemy import select

from ...providers import carta_web
from ...domain import categorize, retail
from ...storage import db
from ... import monitoring, validate
from ...storage import settings_keys as sk
from ...storage.models import RetailCharge
from ..common import ApiError, Response, _current, download, own_session, row_id, text
from . import transactions


def api_retail(conn, _q, _b):
    return retail.status(conn)


def api_retail_token(conn, _q, _b):
    """A new key for the browser extension; shown once. It's the signed-in person's: it ends with their access."""
    return {"token": retail.new_token(conn, getattr(_current, "user", None))}


def api_retail_token_remove(conn, _q, _b):
    retail.remove_token(conn)
    return {"ok": True}


def api_retail_settings(conn, _q, body):
    if "ai" in body:
        db.set_setting(conn, sk.RETAIL_AI, str(validate.flag(body.get("ai"))))
    return {"ok": True}


def api_retail_match(conn, _q, _b):
    """Categorize items still waiting (with the AI, if set up), then match and split again."""
    items = retail.categorize_items(conn)
    out = retail.match_and_apply(conn)
    for ch in conn.execute(select(RetailCharge.id).where(RetailCharge.tx_id.is_not(None),
                                                         RetailCharge.applied.is_not(None))).fetchall():
        r = retail.apply(conn, ch["id"])
        if r in ("split", "category"):
            out[r] += 1
    out["items"] = items
    return out


def api_retail_order(conn, _q, _b, oid):
    try:
        return retail.order_detail(conn, oid)
    except retail.RetailError as e:
        raise ApiError(str(e), 404) from e


def _item_id(item_id) -> int:
    return row_id(item_id, "Item not found")


def api_retail_item(conn, _q, body, item_id):
    """Pick an item's category, or accept the AI's proposed new one (`new_category`: {name, parent}): it's created, then used.
    Sends back what Undo needs (`was`: the items and memory it changes, and the transactions it re-splits), which
    POST /api/retail/items/{id}/restore puts back."""
    category, created = text(body.get("category"), "category"), False
    iid = _item_id(item_id)
    state = retail.item_undo_state(conn, iid)
    was = {**state, "tx": transactions.snapshot(conn, retail.item_transactions(conn, [i["id"] for i in state["items"]]), orders=True)}
    remember = "remember" not in body or validate.on(body["remember"])   # (on unless it's switched off)
    try:
        if body.get("new_category"):
            if not isinstance(body["new_category"], dict):
                raise ApiError("The suggested category has no name")
            category, created = categorize.create_proposed(conn, body["new_category"])
        return {**retail.set_item_category(conn, iid, category, remember),
                "category": category, "created": created, "was": was}
    except (retail.RetailError, ValueError) as e:
        raise ApiError(str(e)) from e


def api_retail_item_restore(conn, _q, body, _item_id):
    """Undo a category picked for an item: the `was` its reply sent."""
    if not isinstance(body.get("was"), dict):
        raise ApiError("Nothing to put back")
    retail.restore_item_state(conn, body["was"])
    if isinstance(body["was"].get("tx"), list):
        transactions.restore(conn, body["was"]["tx"])
    return {"ok": True}


def api_retail_suggest(conn, _q, _b, order_id):
    """The AI's category (or a new one) for each item of the order that has none. Nothing is saved."""
    try:
        return retail.suggest_for_order(conn, order_id)
    except retail.RetailError as e:
        raise ApiError(str(e), 502) from e


def _charge_was(conn, charge_id: str) -> dict:
    """A charge's pairing and its transaction (with its order's items), as Undo puts them back (api_retail_charge_restore)."""
    state = retail.charge_state(conn, charge_id)
    if not state:
        raise ApiError("Charge not found", 404)
    return {"charge": state, "tx": transactions.snapshot(conn, [state["tx_id"]], orders=True) if state["tx_id"] else []}


def api_retail_unlink(conn, _q, _b, charge_id):
    was = _charge_was(conn, charge_id)
    retail.unlink(conn, charge_id)
    return {"ok": True, "was": was}


def api_retail_charge_restore(conn, _q, body, charge_id):
    """Undo "Not this transaction" or "Split by items": the charge paired as it was, and its transaction as it was."""
    was = body.get("was")
    if not isinstance(was, dict) or not isinstance(was.get("charge"), dict) or was["charge"].get("id") != charge_id:
        raise ApiError("Nothing to put back")
    retail.restore_charge(conn, was["charge"])
    if isinstance(was.get("tx"), list):
        transactions.restore(conn, was["tx"])
    return {"ok": True}


def api_retail_link(conn, _q, body, charge_id):
    try:
        return {"result": retail.link(conn, charge_id, text(body.get("tx_id"), "tx_id"))}
    except retail.RetailError as e:
        raise ApiError(str(e)) from e


def api_retail_apply(conn, _q, _b, charge_id):
    """Split this charge's transaction by its items even if you had categorized it yourself (with what Undo needs)."""
    was = _charge_was(conn, charge_id)
    return {"result": retail.apply(conn, charge_id, force=True), "was": was}


def api_retail_candidates(conn, _q, _b, charge_id):
    try:
        return retail.candidates(conn, charge_id)
    except retail.RetailError as e:
        raise ApiError(str(e), 404) from e


# The browser extension's calls (/api/ext/...), signed with its key rather than a sign-in. Each takes one page the
# extension read from the store, and says what to fetch next.
TARGET_DETAIL_URLS = {
    # {base} is Target's order API as the extension found it on target.com, {key} its API key, {order} the order.
    "store": ["{base}/{order}/store_order_details?key={key}"],
    # An online order's details, as its page on target.com reads them (with its items in packages[].order_lines).
    "online": ["https://api.target.com/post_orders/v1/{order}?key={key}", "https://api.target.com/post_orders/v1/{order}",
               "{base}/{order}/orders?key={key}", "{base}/orders/{order}?key={key}"],
}
# The order's own page on target.com, for orders none of those addresses answer: the extension loads it, sees which
# addresses it called for the order's items, reads them, and remembers them for the next orders.
TARGET_ORDER_PAGES = {
    "store": "https://www.target.com/orders/stores/{order}",
    "online": "https://www.target.com/orders/{order}",
}


# Costco's receipts come from one GraphQL service, which the extension asks for `max_days` of receipts at a time,
# from `since` to today. The requests are made from costco.com's own account page, so they're the site's own kind of
# request: the page keeps its sign-in in localStorage, and `storage_headers` says which header each stored value goes
# in ({header: [localStorage key, text put before the value]}). The sign-in itself never leaves the page: each reply
# is posted to /api/ext/costco/history. If costco.com's page changes, this is what to change (the extension only
# does as it's told here).
COSTCO_GRAPHQL_CONFIG = {
    "url": retail.COSTCO_GRAPHQL,
    "query": retail.COSTCO_QUERY,
    "max_days": retail.COSTCO_MAX_DAYS,
    "variables": {"documentType": "all", "documentSubType": "all"},   # warehouse, gas station and car wash receipts
    # The page that sets up the sign-in in localStorage (costco.com's account app; its own address for Orders &
    # Purchases is .../myaccount/#/app/<id>/ordersandpurchases, which the extension follows a link to if needed).
    "page": "https://www.costco.com/myaccount/",
    "headers": {"Content-Type": "application/json-patch+json", "costco.env": "ecom", "costco.service": "restOrders",
                "client-identifier": "481b1aec-aa3b-454b-b81b-48187e28f205"},   # the site's own, the same for everyone
    "storage_headers": {"costco-x-authorization": ["idToken", "Bearer "], "costco-x-wcs-clientId": ["clientID", ""]},
}


# Target's order history: the address its orders page uses for online orders, which answers for store purchases too,
# 100 orders a page. (The older address, which the page's In-store tab still uses, takes 10 a page and gives a store
# purchase no order number, only a receipt id.) The extension tries `url` first and falls back to `fallback_url` if
# that isn't answered. {base} is the older API's address as the extension found it, {key} its API key.
TARGET_HISTORY = {
    "url": "https://api.target.com/post_orders/v1/orders/history?page_number={page}&page_size={size}"
           "&order_purchase_type={type}&key={key}",
    "page_size": 100,
    "fallback_url": "{base}/order_history?page_number={page}&page_size={size}&order_purchase_type={type}"
                    "&pending_order=true&shipt_status=true&key={key}",
    "fallback_page_size": 10,
}


def ext_start(conn, body):
    r = body.get("retailer")
    if r not in retail.RETAILERS:
        raise retail.RetailError("Unknown store")
    return {"since": retail.since(conn, r), "detail_urls": TARGET_DETAIL_URLS if r == "target" else None,
            "order_pages": TARGET_ORDER_PAGES if r == "target" else None,
            "history": TARGET_HISTORY if r == "target" else None,
            "graphql": COSTCO_GRAPHQL_CONFIG if r == "costco" else None,
            "version": os.environ.get("RUNWAY_VERSION") or "dev"}


def ext_amazon_transactions(conn, body):
    seen = body.get("seen")
    return retail.amazon_transactions(conn, str(body.get("html") or ""), seen if isinstance(seen, dict) else None)


def ext_amazon_order(conn, body):
    return retail.amazon_order(conn, str(body.get("order_number") or ""), str(body.get("html") or ""),
                               final=body.get("final", True) is not False)


def ext_costco_history(conn, body):
    return retail.costco_history(conn, body.get("data"))


def ext_target_history(conn, body):
    return retail.target_history(conn, body.get("data"), body.get("purchase_type"))


def ext_target_order(conn, body):
    return retail.target_order(conn, str(body.get("order_number") or ""), body.get("data"),
                               final=body.get("final", True) is not False)


_retail_categorize_lock = threading.Lock()


_categorize_waiting: set[str] = set()   # retailers with a categorizing thread waiting for the lock (one is enough)
_categorize_waiting_lock = threading.Lock()


def _categorize_retail(retailer: str) -> None:
    with _retail_categorize_lock:   # one at a time ("Import both" finishes Amazon, then Target)
        with _categorize_waiting_lock:
            _categorize_waiting.discard(retailer)   # from here on, a new finish needs a run of its own
        try:
            with db.session() as conn:
                retail.categorize_and_apply(conn, retailer)
        except Exception:
            monitoring.report()


def _categorize_later(retailer: str) -> None:
    """Start a categorizing thread for this retailer, unless one is already waiting its turn: it picks up everything
    saved by then, so a burst of finishes doesn't pile up threads (and AI calls)."""
    with _categorize_waiting_lock:
        if retailer in _categorize_waiting:
            return
        _categorize_waiting.add(retailer)
    threading.Thread(target=_categorize_retail, args=(retailer,), daemon=True).start()


def ext_finish(conn, body):
    """Matches and answers straight away; categorizing the new items (the AI model can take a while, longer than
    the browser lets the extension wait) carries on after the answer, once this request's writes are saved."""
    retailer = body.get("retailer")
    out = retail.finish(conn, retailer, complete=body.get("complete", True) is not False, categorize_now=False)
    conn.commit()
    _categorize_later(retailer)
    return out


def ext_carta_data(conn, body):
    try:
        return carta_web.ingest(conn, str(body.get("url") or ""), body.get("data"))
    except carta_web.CartaWebError as e:
        raise retail.RetailError(str(e)) from e


EXT_ROUTES = {
    "/api/ext/ping": lambda conn, body: {"ok": True},
    # Charges still without a transaction, now (you may have matched some yourself since the last import).
    "/api/ext/status": lambda conn, body: {"unmatched": {r: retail.unmatched_count(conn, r) for r in retail.RETAILERS}},
    "/api/ext/carta/start": lambda conn, body: carta_web.start(conn),
    "/api/ext/carta/data": ext_carta_data,
    "/api/ext/carta/finish": lambda conn, body: carta_web.finish(conn),
    "/api/ext/start": ext_start,
    "/api/ext/amazon/transactions": ext_amazon_transactions,
    "/api/ext/amazon/order": ext_amazon_order,
    "/api/ext/target/history": ext_target_history,
    "/api/ext/target/order": ext_target_order,
    "/api/ext/costco/history": ext_costco_history,
    "/api/ext/finish": ext_finish,
}
MAX_EXT_BODY = 16 * 1024 * 1024      # one store page (Amazon's order pages are large)
EXTENSION_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "extension")


def extension_zip() -> bytes | None:
    """The browser extension (extension/ next to runway/), zipped into a folder to load unpacked."""
    if not os.path.isfile(os.path.join(EXTENSION_DIR, "manifest.json")):
        return None
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(EXTENSION_DIR):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for f in sorted(files):
                if f.startswith("."):
                    continue
                full = os.path.join(root, f)
                z.write(full, os.path.join("runway-orders", os.path.relpath(full, EXTENSION_DIR)))
    return buf.getvalue()


@own_session
def api_extension_zip(_conn, _q, _b) -> Response:
    """The browser extension, to download and load unpacked."""
    zipped = extension_zip()
    if zipped is None:
        raise ApiError("The extension isn't included with this copy of Runway.", 404)
    return download(zipped, "application/zip", "runway-orders-extension.zip")
