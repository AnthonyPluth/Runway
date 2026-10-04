"""Pairing each store charge with its bank transaction (the same amount a few days later, from a merchant that looks
like the store), by itself or as you pick, and what an import does once the extension has sent everything."""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta

from sqlalchemy import func, select, update

from .. import db, splits
from .. import settings_keys as sk
from ..models import Account, RetailCharge, RetailOrder, Transaction
from ..money import CENT, same_amount
from .items import categorize_items
from .split import apply, is_ours
from .store import FIRST_IMPORT_DAYS, RETAILERS, RetailError

# How each store's charges read on a statement: "AMAZON MKTPL*2K3AB1", "Amazon.com*RT4", "AMZN Mktp US", "TARGET 00012345".
# Not brands.MERCHANT_PATTERNS, on purpose: those pick a logo and a payee's name, where a near miss costs little, while
# these decide which transaction an order's items split, so they also take the shorter forms a statement uses ("AMZ",
# "TGT") and leave out what isn't a purchase at the store, such as a payment to the Costco Anywhere Visa card.
MERCHANT = {
    "amazon": re.compile(r"amazon|amzn|\bamz\b", re.I),
    "target": re.compile(r"\btarget\b|\btgt\b|target\.com", re.I),
    # "COSTCO WHSE #0123", "COSTCO GAS #0123", "COSTCO.COM"; not a payment to the Costco Anywhere Visa
    "costco": re.compile(r"\bcostco\b(?!\s+anywhere)", re.I),
}
MATCH_BEFORE, MATCH_AFTER = 3, 10   # a bank transaction may post this many days before / after the store's charge date


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


def unlink(conn, charge_id: str) -> None:
    """This charge isn't that transaction: undo what Runway did to it and don't pair them again."""
    ch = conn.execute(select(RetailCharge).where(RetailCharge.id == charge_id)).fetchone()
    if not ch or not ch["tx_id"]:
        return
    applied = json.loads(ch["applied"]) if ch["applied"] else None
    tx = conn.execute(select(Transaction).where(Transaction.id == ch["tx_id"])).fetchone()
    if tx and applied:
        ours = is_ours(conn, tx["id"], applied)
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
    rows = [r for r in rows if same_amount(r["amount"], ch["amount"]) or rx.search(f"{r['payee'] or ''} {r['description'] or ''}")]
    rows.sort(key=lambda r: (not same_amount(r["amount"], ch["amount"]), abs((date.fromisoformat(r["posted"]) - d).days)))
    return rows[:25]
