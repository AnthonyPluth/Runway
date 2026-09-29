"""Amazon and Target orders: the Settings page's calls, and the browser extension's (/api/ext/...), which bring the
orders in."""
from __future__ import annotations

import io
import os
import threading
import zipfile

from ... import carta_web, db, monitoring, retail
from ... import settings_keys as sk
from ..common import ApiError


def api_retail(conn, _q, _b):
    return retail.status(conn)


def api_retail_token(conn, _q, _b):
    """A new key for the browser extension; shown once."""
    return {"token": retail.new_token(conn)}


def api_retail_token_remove(conn, _q, _b):
    retail.remove_token(conn)
    return {"ok": True}


def api_retail_settings(conn, _q, body):
    if "ai" in body:
        db.set_setting(conn, sk.RETAIL_AI, "1" if body.get("ai") else "0")
    return {"ok": True}


def api_retail_match(conn, _q, _b):
    """Categorize items still waiting (with the AI, if set up), then match and split again."""
    items = retail.categorize_items(conn)
    out = retail.match_and_apply(conn)
    for ch in conn.execute("SELECT id FROM retail_charges WHERE tx_id IS NOT NULL AND applied IS NOT NULL").fetchall():
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


def api_retail_item(conn, _q, body, item_id):
    try:
        return retail.set_item_category(conn, int(item_id), body.get("category") or "", body.get("remember", True) is not False)
    except retail.RetailError as e:
        raise ApiError(str(e)) from e


def api_retail_unlink(conn, _q, _b, charge_id):
    retail.unlink(conn, charge_id)
    return {"ok": True}


def api_retail_link(conn, _q, body, charge_id):
    try:
        return {"result": retail.link(conn, charge_id, body.get("tx_id") or "")}
    except retail.RetailError as e:
        raise ApiError(str(e)) from e


def api_retail_apply(conn, _q, _b, charge_id):
    """Split this charge's transaction by its items even if you had categorized it yourself."""
    return {"result": retail.apply(conn, charge_id, force=True)}


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


def ext_start(conn, body):
    r = body.get("retailer")
    if r not in retail.RETAILERS:
        raise retail.RetailError("Unknown store")
    return {"since": retail.since(conn, r), "detail_urls": TARGET_DETAIL_URLS if r == "target" else None,
            "order_pages": TARGET_ORDER_PAGES if r == "target" else None,
            "version": os.environ.get("RUNWAY_VERSION") or "dev"}


def ext_amazon_transactions(conn, body):
    seen = body.get("seen")
    return retail.amazon_transactions(conn, str(body.get("html") or ""), seen if isinstance(seen, dict) else None)


def ext_amazon_order(conn, body):
    return retail.amazon_order(conn, str(body.get("order_number") or ""), str(body.get("html") or ""),
                               final=body.get("final", True) is not False)


def ext_target_history(conn, body):
    return retail.target_history(conn, body.get("data"), body.get("purchase_type"))


def ext_target_order(conn, body):
    return retail.target_order(conn, str(body.get("order_number") or ""), body.get("data"),
                               final=body.get("final", True) is not False)


_retail_categorize_lock = threading.Lock()


def _categorize_retail(retailer: str) -> None:
    with _retail_categorize_lock:   # one at a time ("Import both" finishes Amazon, then Target)
        try:
            with db.session() as conn:
                retail.categorize_and_apply(conn, retailer)
        except Exception:
            monitoring.report()


def ext_finish(conn, body):
    """Matches and answers straight away; categorizing the new items (the AI model can take a while, longer than
    the browser lets the extension wait) carries on after the answer, once this request's writes are saved."""
    retailer = body.get("retailer")
    out = retail.finish(conn, retailer, complete=body.get("complete", True) is not False, categorize_now=False)
    conn.commit()
    threading.Thread(target=_categorize_retail, args=(retailer,), daemon=True).start()
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
