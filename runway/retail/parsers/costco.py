"""Costco: the receipts costco.com's Orders & Purchases page lists (warehouse and gas station), which its GraphQL
service returns with every line in one reply. Each receipt counts as one charge of its total.

costco.com's Orders & Purchases page reads your receipts (in the warehouse, at the gas station, the car wash) from a
GraphQL service. One request for a range of dates returns every receipt in it with all of its lines, so there's
no second request per order for the details, as there is for Amazon and Target. The query is kept here rather
than in the extension, so a change on Costco's side is an update to Runway. (Orders placed on costco.com are a
different query and aren't read yet: only what you bought in a warehouse or at a gas station.)"""
from __future__ import annotations

import html
import json
import re

from ..store import MAX_ATTEMPTS, RetailError, save_charge, save_items, save_order
from .common import MAX_ORDERS, MAX_RAW, read_day, read_money

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
        amount = read_money(it.get("amount"))
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
        qty = abs(read_money(it.get("unit")) or 0)
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
    placed = read_day(r.get("transactionDate") or r.get("transactionDateTime"))
    total = read_money(r.get("total"))
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
    # next import start further back looking for its details (see `store.since`).
    oid = save_order(conn, "costco", number, channel="store", placed=placed, total=abs(total),
                     subtotal=read_money(r.get("subTotal")), tax=read_money(r.get("taxes")), payment=payment, raw=raw,
                     details=1 if lines else 0, attempts=0 if lines else MAX_ATTEMPTS)
    if lines:
        save_items(conn, oid, lines)
    if total:
        save_charge(conn, f"costco|{number}", oid, placed, abs(total) if refund else -abs(total), payment)
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
