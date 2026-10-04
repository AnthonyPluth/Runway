"""Transactions: the list and its filters, categorizing and splitting them, and the AI model's suggestions."""
from __future__ import annotations

import re
import urllib.parse
import uuid
from datetime import date, timedelta

from sqlalchemy import and_, delete, func, insert, or_, select, update
from sqlalchemy.orm import aliased

from ... import categories, categorize, db, forecast, merchants, retail, splits, validate
from ... import settings_keys as sk
from ...models import Account, AiLog, Category, Recurring, RetailCharge, Transaction, TxSplit
from ...money import CENT
from ..common import ApiError, _month_range, query_int, row_id, text

# A manual transaction's id: its account's, then this and a random part (a bank's are "|<its id>" or "|pl:<its id>").
MANUAL = "|manual:"


def tx_logos(conn, items: list[dict], paid: dict[str, str] | None = None) -> dict[str, str]:
    """{transaction id: the URL of its merchant's logo}: Logo.dev's (by the merchant's website or name; a sync, or adding
    the key, fetches it), else Plaid's, and a logo you chose for the merchant over both (or none at all). Logo.dev's
    come first because they're fetched for a dark background; Plaid's are opaque squares, often dark on white.
    A card's payment (`paid`: forecast.paid_cards) has no merchant: it shows its card's bank's logo (the app has those),
    unless you chose one for it."""
    paid = forecast.paid_cards(conn, items) if paid is None else paid
    auto = [t for t in items if t["id"] not in paid]
    logos = merchants.logo_dev_logos(conn, auto)
    logos.update(merchants.for_transactions(conn, [t for t in auto if t["id"] not in logos]))
    for tid, mid in merchants.chosen_for(conn, items).items():
        if mid:
            logos[tid] = mid
        else:
            logos.pop(tid, None)
    return {tid: f"/api/merchants/{urllib.parse.quote(mid, safe='')}/logo" for tid, mid in logos.items()}


# The kinds of transaction the list can show (`kind`): money in, money out (neither counts transfers) and transfers
# (between your own accounts; what's marked Ignore has its own switch).
KINDS = ("in", "out", "transfer")


def _transfers(conn) -> tuple[list[str], list[str]]:
    """The transfer categories that are real transfers (Transfer, Credit Card Payment, ...), and Ignore with its
    subcategories (a transfer category too, so it isn't counted, but it isn't money moving)."""
    ignore = ["Ignore", *categories.descendants(conn, "Ignore")]
    moves = [r[0] for r in conn.execute(select(Category.name).where(Category.is_transfer == 1)) if r[0] not in ignore]
    return moves, ignore


# The list's filters (?from=, ?min=, ...): a day, or an amount of money in or out (unsigned, not rounded).
_filters = validate.Validator(ApiError, drop="", not_date="Dates must look like 2026-09-30", not_number="Amounts must be numbers",
                              too_large="Amounts must be less than a billion")


def _date(q, key: str) -> str:
    return _filters.day(q.get(key, [""])[0], key) or ""


def _number(q, key: str) -> float | None:
    n = _filters.amount(q.get(key, [""])[0], key, cents=False)
    return None if n is None else abs(n)


def _as_amount(text: str) -> float | None:
    """A search that's an amount ("59.28", "$1,234.50", "-12"): the amount, unsigned; else None."""
    s = text.strip().replace("$", "").replace(",", "").replace("\u2212", "-").lstrip("+-").strip()
    if not re.fullmatch(r"\d+(\.\d{1,2})?|\.\d{1,2}", s):
        return None
    return float(s)


def tx_where(conn, q) -> tuple[list, list[str]]:
    """The conditions of a list of transactions (GET /api/transactions's query, also a bulk change's `filter`), and the
    category the list is filtered by with its subcategories (empty without one)."""
    T = Transaction
    where = [db.not_investment()]
    family: list[str] = []
    if q.get("review", ["0"])[0] == "1":
        where.append(T.needs_review == 1)
    if q.get("recurring", [""])[0]:
        where.append(T.recurring_id == row_id(q["recurring"][0], "Unknown recurring item", 400))
    if q.get("account", [""])[0]:
        where.append(T.account_id == q["account"][0])
    if q.get("category", [""])[0]:
        cat = q["category"][0]
        if cat == "__none__":
            where.append(and_(T.category.is_(None), func.coalesce(T.is_split, 0) == 0))
        else:
            family = [cat, *categories.descendants(conn, cat)]   # a category includes its subcategories
            # a split transaction counts under every category it's split into, not the one on the row
            where.append(or_(and_(func.coalesce(T.is_split, 0) == 0, T.category.in_(family)),
                             select(TxSplit.id).where(TxSplit.tx_id == T.id, TxSplit.category.in_(family)).exists()))
    elif q.get("ignored", [""])[0] == "0":   # hide what's marked Ignore (unless that's the category asked for)
        where.append(or_(T.category.is_(None), func.coalesce(T.is_split, 0) == 1,
                         T.category.notin_(["Ignore", *categories.descendants(conn, "Ignore")])))
    elif q.get("ignored", [""])[0] == "only":   # just what ignored=0 hides (a split with an Ignore part isn't hidden)
        where.append(and_(func.coalesce(T.is_split, 0) == 0,
                          T.category.in_(["Ignore", *categories.descendants(conn, "Ignore")])))
    if q.get("month", [""])[0]:   # YYYY-MM
        start, end = _month_range({"month": q["month"]})
        where += [T.posted >= start.isoformat(), T.posted < end.isoformat()]
    if since := _date(q, "from"):   # YYYY-MM-DD, both ends included
        where.append(T.posted >= since)
    if until := _date(q, "to"):
        where.append(T.posted < (date.fromisoformat(until) + timedelta(days=1)).isoformat())
    low, high = _number(q, "min"), _number(q, "max")   # the amount, money in or out
    if low is not None:
        where.append(func.abs(T.amount) >= low - CENT)
    if high is not None:
        where.append(func.abs(T.amount) <= high + CENT)
    kind = q.get("kind", [""])[0]
    if kind:
        if kind not in KINDS:
            raise ApiError("Unknown kind of transaction")
        moves, ignore = _transfers(conn)
        whole = func.coalesce(T.is_split, 0) == 0
        moving = or_(and_(whole, T.category.in_(moves)),
                     select(TxSplit.id).where(TxSplit.tx_id == T.id, TxSplit.category.in_(moves)).exists())
        if kind == "transfer":
            where.append(moving)
        else:   # money in or out: not a transfer, nor anything else that isn't counted
            where += [T.amount > 0 if kind == "in" else T.amount < 0,
                      or_(T.category.is_(None), func.coalesce(T.is_split, 0) == 1, T.category.notin_(moves + ignore))]
    if q.get("scope", [""])[0] == "budget":   # the same accounts the Budget page counts
        where.append(T.account_id.in_(select(Account.id).where(*db.SPENDING_ACCOUNTS)))
    if text := q.get("q", [""])[0].strip():
        # The merchant, the bank's text, your note, the category (or a split's), or the amount typed as a number. The
        # text as typed: % and _ are themselves, not LIKE's wildcards; any letter matches its capital (db.py makes
        # SQLite's lower() fold more than ASCII, as Postgres's does).
        needle = text.lower()
        has = lambda col: func.lower(col).contains(needle, autoescape=True)
        found = [has(T.payee), has(T.description), has(T.notes), and_(func.coalesce(T.is_split, 0) == 0, has(T.category)),
                 select(TxSplit.id).where(TxSplit.tx_id == T.id, has(TxSplit.category)).exists()]
        amount = _as_amount(text)
        if amount is not None:
            found.append(func.abs(T.amount).between(amount - CENT, amount + CENT))
        where.append(or_(*found))
    return where, family


def _net(conn, where: list, family: list[str], kind: str) -> float:
    """What the transactions matching `where` add up to, as the day totals count them: transfers (and what's marked
    Ignore) left out, except when they are what's asked for (kind=transfer); a split one by its parts (only those in
    `family`, under a category filter)."""
    T = Transaction
    moves, ignore = _transfers(conn)
    left_out = ignore if kind == "transfer" else moves + ignore
    whole = conn.execute(select(func.coalesce(func.sum(T.amount), 0.0)).where(
        *where, func.coalesce(T.is_split, 0) == 0, or_(T.category.is_(None), T.category.notin_(left_out)),
        *([T.category.in_(moves)] if kind == "transfer" else []))).scalar() or 0.0
    P = aliased(TxSplit)   # not TxSplit itself, which the filters' own subqueries name
    part = [or_(P.category.is_(None), P.category.notin_(left_out))]
    if kind == "transfer":
        part.append(P.category.in_(moves))
    if family:
        part.append(P.category.in_(family))
    parts = conn.execute(select(func.coalesce(func.sum(P.amount), 0.0)).select_from(P).join(T, T.id == P.tx_id)
                         .where(*where, T.is_split == 1, *part)).scalar() or 0.0
    return round(whole + parts, 2)


def api_transactions(conn, q, _b):
    T = Transaction
    where, family = tx_where(conn, q)
    limit = query_int(q, "limit", 200, 1, 1000)
    offset = query_int(q, "offset", 0, 0, 10 ** 9)
    items = db.rows(conn.execute(
        select(T, db.account_label_expr().label("account_name"), Account.kind.label("account_kind"),
               Recurring.name.label("recurring_name"))
        .join(Account, Account.id == T.account_id).outerjoin(Recurring, Recurring.id == T.recurring_id)
        .where(*where).order_by(T.posted.desc(), T.id).limit(limit).offset(offset)))
    parts = splits.of(conn, [t["id"] for t in items if t["is_split"]])
    orders = retail.for_transactions(conn, [t["id"] for t in items])   # the order a charge paid for, or a refund came from
    paid = forecast.paid_cards(conn, items)   # card payments: the card's bank's logo, from the app's brands
    logos = tx_logos(conn, items, paid)
    for tid, mid in merchants.chosen_for(conn, [t for t in items if t["id"] in paid]).items():
        if not mid:
            paid.pop(tid)   # you chose no logo for it: its letter, not the bank's
    for t in items:
        t["splits"] = parts.get(t["id"], [])
        if family and t["splits"]:   # filtered by a category: the part of a split one that's in it (see _match)
            t["match"] = _match(t["splits"], family)
        t["retail"] = orders.get(t["id"])
        t["logo"] = logos.get(t["id"])
        t["logo_account"] = paid.get(t["id"])   # whose institution's logo (or letter) stands in for a merchant's
        t["brand"] = categorize.brand_choice(t)
        t["source"] = source_of(t["id"])
    total = conn.execute(select(func.count()).select_from(T).where(*where)).fetchone()[0]
    # `sum`: what they add up to, as the day totals count them (_net). `family`: the category and its subcategories,
    # so a receipt can show just their items.
    return {"items": items, "total": total, "sum": _net(conn, where, family, q.get("kind", [""])[0]),
            **({"family": family} if family else {})}


def source_of(tx_id: str) -> str:
    """Where a transaction came from: "manual" (added by you), "plaid" or "simplefin" (by its id)."""
    return "manual" if MANUAL in tx_id else "plaid" if "|pl:" in tx_id else "simplefin"


def _match(parts: list[dict], family: list[str]) -> dict:
    """The parts of a split transaction in these categories: what they add up to, and their categories. The list shows
    that much of it under a category filter, the same amount the category's budget counts."""
    mine = [p for p in parts if p["category"] in family]
    return {"amount": round(sum(p["amount"] for p in mine), 2), "categories": list(dict.fromkeys(p["category"] for p in mine))}


def _family(conn, only) -> set[str]:
    """`only` (a category filter the change was made under) and its subcategories; empty without one."""
    if not isinstance(only, str) or not only or only == "__none__":
        return set()
    return {only, *categories.descendants(conn, only)}


def set_parts(conn, tx_ids: list[str], family: set[str], category: str) -> list[str]:
    """Under a category filter a split transaction shows only its part in that category, so a category picked for it
    changes just that part: through its order's items when an order split it, else the part itself. Either way, when
    every part ends up in one category the transaction takes it. Returns the ids changed this way (the others, not
    split or with no part in `family`, are for the caller to change as a whole)."""
    if not family:
        return []
    if not conn.execute(select(Category.name).where(Category.name == category)).fetchone():
        raise ValueError(f"Unknown category: {category}")
    done = []
    for tid in [r[0] for r in conn.execute(select(Transaction.id).where(Transaction.id.in_(tx_ids), Transaction.is_split == 1))]:
        if retail.recategorize_part(conn, tid, family, category) or splits.recategorize(conn, tid, family, category):
            done.append(tid)
    return done


# What Undo needs to put transactions back as they were: the fields a category, rename, review mark or rule can change.
_WAS = ("id", "category", "category_source", "confidence", "needs_review", "payee")


def snapshot(conn, ids: list[str], orders: bool = False) -> list[dict]:
    """These transactions as they are now (before a change), for `restore`. A split one carries its parts. With
    `orders`, the change also sets the categories of the items of the transactions' orders (see
    `retail.set_transaction_category`): the other transactions of those orders come along, and each one with an order
    carries its items' categories."""
    t = Transaction
    out: list[dict] = []
    if orders:
        ids = [str(x) for x in ids]
        ids += retail.order_mates(conn, ids)
    for i in range(0, len(ids), 500):
        chunk = [str(x) for x in ids[i:i + 500]]
        rows = db.rows(conn.execute(select(*(getattr(t, c) for c in _WAS), t.is_split).where(t.id.in_(chunk))))
        parts = splits.of(conn, [r["id"] for r in rows if r["is_split"]])
        for r in rows:
            r["splits"] = [{"amount": p["amount"], "category": p["category"], "note": p["note"]} for p in parts.get(r["id"], [])]
            r["is_split"] = 1 if r["is_split"] else 0
        if orders:
            items = retail.items_of_transactions(conn, [r["id"] for r in rows])
            charges = retail.charges_of_transactions(conn, [r["id"] for r in rows])
            for r in rows:
                if r["id"] in items:
                    r["items"] = items[r["id"]]
                if r["id"] in charges:
                    r["charges"] = charges[r["id"]]
        out += rows
    return out


def restore(conn, rows: list) -> int:
    """Put transactions back from a `snapshot`. Returns how many were restored; anything gone or no longer valid is skipped."""
    t = Transaction
    done = 0
    for r in rows:
        if not isinstance(r, dict) or not isinstance(r.get("id"), str):
            continue
        cat = r.get("category") or None
        if cat and not conn.execute(select(Category.name).where(Category.name == cat)).fetchone():
            cat = None
        # As it was, not through the rename's tidying: a bank's payee can be long or double-spaced, and rules and
        # recurring items may match on that exact text.
        payee = r.get("payee") if isinstance(r.get("payee"), str) and r.get("payee") else None
        cur = conn.execute(update(t).where(t.id == r["id"]).values(
            category=cat, category_source=r.get("category_source") or None, confidence=r.get("confidence"),
            needs_review=1 if r.get("needs_review") else 0, payee=payee))
        if not cur.rowcount:
            continue
        if isinstance(r.get("items"), list):   # the items of its order, which a category change had set too
            retail.restore_items(conn, r["items"])
        if isinstance(r.get("charges"), list):   # and what Runway had given it from them
            retail.restore_charges(conn, r["id"], r["charges"])
        if not r.get("is_split"):   # it wasn't split before (a split made since goes away)
            splits.clear(conn, r["id"])
        elif r.get("splits"):   # it was: the parts a category change removed come back
            try:
                splits.set_splits(conn, r["id"], r["splits"])
            except splits.SplitError:   # the amount or a category changed since: it stays whole
                splits.clear(conn, r["id"])
        done += 1
    return done


def api_tx_category(conn, _q, body, tx_id):
    was = snapshot(conn, [tx_id], orders=True)
    category = text(body.get("category"), "category")
    try:
        if set_parts(conn, [tx_id], _family(conn, body.get("only")), category):
            return {"ok": True, "also_updated": 0, "offer_rule": None, "was": was, "part": True}
        remember = validate.on(body.get("remember"))
        n = categorize.set_category(conn, tx_id, category, remember)
        retail.set_transaction_category(conn, [tx_id], category)
    except ValueError as e:
        raise ApiError(str(e)) from e
    # Not remembered yet: the app asks whether to use this category for the merchant from now on.
    offer = None if remember else categorize.rule_offer(conn, tx_id, category)
    return {"ok": True, "also_updated": n, "offer_rule": offer, "was": was}


def api_tx_split(conn, _q, body, tx_id):
    """Split one transaction across categories, or (with no parts) put it back together."""
    parts = body.get("splits")
    if not isinstance(parts, list):
        raise ApiError("Send the parts to split this into")
    try:
        saved = splits.set_splits(conn, tx_id, parts)
    except splits.SplitError as e:
        raise ApiError(str(e)) from e
    return {"ok": True, "splits": saved}


def api_tx_bulk(conn, _q, body, *_):
    """Change many transactions at once (the checkboxes on Transactions)."""
    if isinstance(body.get("restore"), list):   # Undo: the `was` an earlier change sent back
        keep = body.get("keep_bank")   # ... and whether a brand kept the bank's name before (api_tx_brand_name)
        if isinstance(keep, dict) and isinstance(keep.get("brand"), str) and keep["brand"]:
            categorize.keep_bank_name(conn, keep["brand"], validate.on(keep.get("keep")))
        return {"ok": True, "updated": restore(conn, body["restore"])}
    ids = body.get("ids")
    if isinstance(body.get("filter"), dict):   # every transaction a list's filters match ("Select all 212")
        ids = filtered_ids(conn, body["filter"])
    if not isinstance(ids, list):
        raise ApiError("Select some transactions first")
    category = text(body.get("category"), "category") or None
    was = snapshot(conn, ids, orders=bool(category))
    try:
        ids = list(dict.fromkeys(str(i) for i in ids))
        if not ids or len(ids) > categorize.MAX_BULK:   # (bulk_update says which)
            categorize.bulk_update(conn, ids, category, None, False)
        payee, reviewed = text(body.get("payee"), "payee") or None, validate.on(body.get("reviewed"))
        # Under a category filter, a split one changes only its part in that category (see set_parts).
        parts = set(set_parts(conn, ids, _family(conn, body.get("only")), category)) if category else set()
        rest = [i for i in ids if i not in parts]
        n = len(parts)
        if parts and (payee or reviewed):
            categorize.bulk_update(conn, list(parts), None, payee, reviewed)
        if rest:
            n += categorize.bulk_update(conn, rest, category, payee, reviewed)
        if category and rest:
            retail.set_transaction_category(conn, rest, category)
    except ValueError as e:
        raise ApiError(str(e)) from e
    # All one merchant: the app asks whether to use this category for it from now on, as after a single change.
    offer = categorize.bulk_rule_offer(conn, rest, category) if category and rest and not parts else None
    return {"ok": True, "updated": n, "was": was, "offer_rule": offer}


def filtered_ids(conn, f: dict) -> list[str]:
    """The ids of the transactions a list's filters (GET /api/transactions's query, as a dict) match, up to
    categorize.MAX_BULK; more than that is refused."""
    q = {k: [str(v)] for k, v in f.items() if isinstance(k, str) and isinstance(v, (str, int, float)) and not isinstance(v, bool)}
    where, _ = tx_where(conn, q)
    ids = list(conn.execute(select(Transaction.id).where(*where).order_by(Transaction.posted.desc(), Transaction.id)
                            .limit(categorize.MAX_BULK + 1)).scalars())
    if len(ids) > categorize.MAX_BULK:
        raise ApiError(f"Change at most {categorize.MAX_BULK:,} transactions at once")
    return ids


# ------------------------------------------------------------------------------------------ editing and adding

# What an edit can change, and so what Undo puts back exactly (with the category and splits, from `snapshot`).
_EDITS = ("payee", "posted", "amount", "notes", "bank_posted", "bank_amount")
MAX_NOTE = 1000
DATE = "Enter a date like 2026-09-30"
# A transaction's date and amount, as you enter or edit one: an amount is to the cent, under validate.MAX_AMOUNT.
_v = validate.Validator(ApiError, drop="", missing="Enter the {label}", not_number="Enter the {label} as a number",
                        too_large="Enter the {label} as a number", not_date=DATE)


def _valid_date(v) -> str:
    day = _v.day(v if isinstance(v, str) else "", "date")
    if day is None:
        raise ApiError(DATE)
    return day


def _valid_amount(v) -> float:
    return _v.amount(v, "amount", required=True)


def _name(v) -> str | None:
    return " ".join(str(v or "").split())[:80] or None


def _note(v) -> str | None:
    if v is not None and not isinstance(v, str):
        raise ApiError("A note is text")
    return (v or "").strip()[:MAX_NOTE] or None


def _tx(conn, tx_id: str) -> dict:
    row = conn.execute(select(Transaction).where(Transaction.id == tx_id)).fetchone()
    if not row:
        raise ApiError("Transaction not found", 404)
    return dict(row)


def edit_was(conn, tx_id: str) -> dict:
    """A transaction as it is before an edit: its category and splits (`snapshot`) and the fields an edit changes."""
    was = snapshot(conn, [tx_id])[0]
    row = conn.execute(select(*(getattr(Transaction, c) for c in _EDITS)).where(Transaction.id == tx_id)).fetchone()
    return {**was, **dict(row)}


def api_tx_update(conn, _q, body, tx_id):
    """Change a transaction's name, date, amount or note (any of them), or take its category away ({"category": null}).
    A synced one's date and amount are yours from then on: the bank's are kept beside them (bank_posted, bank_amount)
    and a sync updates those instead, and changing one back to the bank's makes it the bank's again. A pending one's
    can't be changed: the bank sets them when it posts. Sends back what Undo needs (`was`), which {"restore": was} puts
    back exactly, and the transaction as it is now (`tx`)."""
    tx = _tx(conn, tx_id)
    t = Transaction
    if isinstance(body.get("restore"), dict):
        was = body["restore"]
        back = {k: was.get(k) for k in _EDITS if k in was}
        if "posted" in back:
            back["posted"] = _valid_date(back["posted"])
        if "amount" in back:
            back["amount"] = _valid_amount(back["amount"])
        if back.get("bank_posted") is not None:
            back["bank_posted"] = _valid_date(back["bank_posted"])
        if back.get("bank_amount") is not None:
            back["bank_amount"] = _valid_amount(back["bank_amount"])
        if "payee" in back:   # as it was, not tidied: rules and recurring items may match on that exact text
            back["payee"] = back["payee"] if isinstance(back["payee"], str) and back["payee"] else None
        if "notes" in back:
            back["notes"] = back["notes"] if isinstance(back["notes"], str) and back["notes"] else None
        if back:   # (a `was` with none of them puts back only the category and splits)
            conn.execute(update(t).where(t.id == tx_id).values(**back))
        restore(conn, [{**was, "id": tx_id}])
        return {"ok": True}
    was = edit_was(conn, tx_id)
    manual = MANUAL in tx_id
    values: dict = {}
    if "payee" in body:
        values["payee"] = _name(body["payee"])
        if manual and not values["payee"]:
            raise ApiError("Give it a name")
    if "notes" in body:
        values["notes"] = _note(body["notes"])
    if ("posted" in body or "amount" in body) and tx["pending"] and not manual:
        raise ApiError("It’s pending: the bank can still change it. Edit it once it posts.")
    if "posted" in body:
        values["posted"] = _valid_date(body["posted"])
        if not manual and values["posted"] != tx["posted"]:
            bank = tx["bank_posted"] or tx["posted"]
            values["bank_posted"] = None if values["posted"] == bank else bank
    if "amount" in body:
        values["amount"] = _valid_amount(body["amount"])
        if not manual and abs(values["amount"] - tx["amount"]) >= CENT:
            bank = tx["bank_amount"] if tx["bank_amount"] is not None else tx["amount"]
            values["bank_amount"] = None if abs(values["amount"] - bank) < CENT else bank
    if "category" in body:
        if body["category"] is not None:
            raise ApiError("Set a category with /category")
        values.update(category=None, category_source=None, confidence=None, needs_review=1)
        splits.clear(conn, tx_id)
    if not values:
        raise ApiError("Choose what to change")
    conn.execute(update(t).where(t.id == tx_id).values(**values))
    if "amount" in values and tx["is_split"]:   # the parts follow, as when a bank changes it (but it stays reviewed)
        splits.follow_amount(conn, tx_id, values["amount"])
        conn.execute(update(t).where(t.id == tx_id).values(needs_review=tx["needs_review"]))
    # And as it is now, for the app to show while the list loads again (or if it has left the list's filters).
    return {"ok": True, "was": was, "tx": _tx(conn, tx_id)}


def api_tx_create(conn, _q, body):
    """Add a transaction by hand (cash, a cheque the bank hasn't shown yet): an account, a date, a name and an amount
    (positive = money in), and optionally a category and a note. It counts like a synced one, in reports and budgets."""
    acct = conn.execute(select(Account.id, Account.kind).where(Account.id == str(body.get("account") or ""))).fetchone()
    if not acct or acct["kind"] == "investment":
        raise ApiError("Choose an account")
    posted = _valid_date(body.get("posted") or "")
    if "amount" not in body or body.get("amount") in ("", None):
        raise ApiError("Enter the amount")
    amount = _valid_amount(body["amount"])
    payee = _name(body.get("payee"))
    if not payee:
        raise ApiError("Give it a name")
    category = body.get("category") or None
    if category is not None and not conn.execute(select(Category.name).where(Category.name == str(category))).fetchone():
        raise ApiError(f"Unknown category: {category}")
    tx_id = f"{acct['id']}{MANUAL}{uuid.uuid4().hex[:16]}"
    conn.execute(insert(Transaction).values(
        id=tx_id, account_id=acct["id"], posted=posted, amount=amount, payee=payee, notes=_note(body.get("notes")),
        category=category, category_source="manual" if category else None, confidence=1 if category else None,
        needs_review=0 if category else 1, pending=0))
    return {"ok": True, "id": tx_id}


MAX_IMPORT = 500   # transactions in one import


def api_tx_import(conn, _q, body):
    """Add many transactions to one account at once, as from a statement: {"account": id, "transactions": [{posted,
    amount, payee, category?, notes?}, ...]}, each checked as api_tx_create checks one. A row that's refused is reported
    and the rest are still added, together, in this request's one transaction. A row is a duplicate (and skipped) when
    the account already has a transaction on that day for the same amount, to the cent, and the same payee as names are
    compared (categorize.text_key), or when the same row came earlier in this import. Rows without a category go through
    the rules and Runway's own guesses as a sync's new transactions do (not the AI model); what's still uncategorized
    waits in Review. Reply: {ok, added, skipped, rows: [{i, status: added (with id) | duplicate (of: the one it
    repeats) | error (with error)}]}."""
    acct = conn.execute(select(Account.id, Account.kind).where(Account.id == str(body.get("account") or ""))).fetchone()
    if not acct or acct["kind"] == "investment":
        raise ApiError("Choose an account")
    rows = body.get("transactions")
    if not isinstance(rows, list) or not rows:
        raise ApiError("Send the transactions to add")
    if len(rows) > MAX_IMPORT:
        raise ApiError(f"Send at most {MAX_IMPORT} transactions at once")
    known = set(conn.execute(select(Category.name)).scalars())
    out: list[dict] = []
    good: list[tuple[int, dict]] = []
    for i, r in enumerate(rows):
        try:
            if not isinstance(r, dict):
                raise ApiError("Each transaction is an object")
            posted = _valid_date(r.get("posted") or "")
            if r.get("amount") in ("", None):
                raise ApiError("Enter the amount")
            amount = _valid_amount(r["amount"])
            payee = _name(r.get("payee"))
            if not payee:
                raise ApiError("Give it a name")
            category = r.get("category") or None
            if category is not None and str(category) not in known:
                raise ApiError(f"Unknown category: {category}")
            good.append((i, {"posted": posted, "amount": amount, "payee": payee, "notes": _note(r.get("notes")),
                             "category": None if category is None else str(category)}))
        except ApiError as e:
            out.append({"i": i, "status": "error", "error": str(e)})

    def key(posted, amount, payee) -> tuple:
        return str(posted)[:10], round((amount or 0) * 100), categorize.text_key(payee)
    seen: dict[tuple, str] = {}
    if good:
        days = sorted({g["posted"] for _i, g in good})
        t = Transaction
        for r in conn.execute(select(t.id, t.posted, t.amount, t.payee).where(
                t.account_id == acct["id"], t.posted >= days[0], t.posted < (date.fromisoformat(days[-1]) + timedelta(days=1)).isoformat())):
            seen.setdefault(key(r["posted"], r["amount"], r["payee"]), r["id"])
    new: list[dict] = []
    for i, g in good:
        k = key(g["posted"], g["amount"], g["payee"])
        if k in seen:
            out.append({"i": i, "status": "duplicate", "of": seen[k]})
            continue
        tx_id = seen[k] = f"{acct['id']}{MANUAL}{uuid.uuid4().hex[:16]}"
        new.append({"id": tx_id, "account_id": acct["id"], **g, "category_source": "manual" if g["category"] else None,
                    "confidence": 1 if g["category"] else None, "needs_review": 0 if g["category"] else 1, "pending": 0})
        out.append({"i": i, "status": "added", "id": tx_id})
    if new:
        conn.execute(insert(Transaction), new)
        open_ = [n["id"] for n in new if not n["category"]]
        if open_:
            categorize.categorize(conn, open_, use_ai=False)
    return {"ok": True, "added": len(new), "skipped": len(rows) - len(new), "rows": sorted(out, key=lambda r: r["i"])}


def api_tx_delete(conn, _q, _b, tx_id):
    """Delete a transaction you added (a bank's come and go with the bank)."""
    _tx(conn, tx_id)
    if MANUAL not in tx_id:
        raise ApiError("Only a transaction you added can be deleted")
    splits.clear(conn, tx_id)
    conn.execute(update(RetailCharge).where(RetailCharge.tx_id == tx_id).values(tx_id=None, applied=None, match_source=None))
    conn.execute(delete(Transaction).where(Transaction.id == tx_id))
    return {"ok": True}


def api_tx_brand_name(conn, _q, body, tx_id):
    """Name a transaction by the bank's text instead of the brand's name a sync gave it ({"use": "bank"}), or by the
    brand's again ({"use": "brand"}). With {"all": true}, the brand's other transactions too, and the syncs from now on
    (categorize.keep_bank_name). Sends back what Undo needs: the transactions as they were, and the brand's setting."""
    every = validate.on(body.get("all"))
    try:
        brand, names = categorize.brand_renames(conn, tx_id, str(body.get("use") or ""), every)
    except ValueError as e:
        raise ApiError(str(e)) from e
    was = snapshot(conn, list(names))
    kept = brand in categorize.kept_bank_names(conn)
    for tid, payee in names.items():
        conn.execute(update(Transaction).where(Transaction.id == tid).values(payee=payee))
    if every:
        categorize.keep_bank_name(conn, brand, body.get("use") == "bank")
    return {"ok": True, "updated": len(names), "brand": brand, "payee": names[tx_id], "was": was,
            "keep_bank": {"brand": brand, "keep": kept}}


def api_tx_accept(conn, _q, _b, tx_id):
    """Keep the category it has (whoever set it) and take it out of Review; `was` puts it back."""
    was = snapshot(conn, [tx_id])
    if not was:
        raise ApiError("Transaction not found", 404)
    if not categorize.accept_suggestion(conn, tx_id):
        raise ApiError("Choose a category for it first")
    return {"ok": True, "was": was}


def api_ai_suggest(conn, _q, body):
    if not db.get_setting(conn, sk.OPENROUTER_API_KEY):
        raise ApiError("Add an OpenRouter API key in Settings first.")
    skip = body.get("skip") if isinstance(body, dict) else None
    try:
        return categorize.suggest_for_review(conn, skip=[str(m) for m in skip[:1000]] if isinstance(skip, list) else None)
    except RuntimeError as e:
        raise ApiError(str(e), 502) from e


def api_ai_log(conn, _q, _b):
    return db.rows(conn.execute(select(AiLog).order_by(AiLog.id.desc()).limit(25)))


def api_ai_apply(conn, _q, body):
    tx_ids = body.get("tx_ids") or []
    if not isinstance(tx_ids, list):
        raise ApiError("Choose the transactions to categorize")
    ids = [str(i) for i in tx_ids]
    category = text(body.get("category"), "category")
    new = body.get("new_category") or None   # {"name", "parent"}: the AI's proposal
    if new is not None and not isinstance(new, dict):
        raise ApiError("The suggested category has no name")
    created = False
    if new:   # accept an AI-proposed category: create it (unless it exists by now), then use it
        try:
            category, created = categorize.create_proposed(conn, new, is_income=body.get("direction") == "in")
        except ValueError as e:
            raise ApiError(str(e)) from e
    remember = validate.on(body.get("remember"))
    was = snapshot(conn, ids, orders=True)
    try:
        n = categorize.apply_to_group(conn, ids, category, remember)
        retail.set_transaction_category(conn, ids, category)
    except ValueError as e:
        raise ApiError(str(e)) from e
    # Applying a suggestion categorizes; the app then asks whether this merchant should always be this category.
    offer = None if remember or not ids else categorize.rule_offer(conn, ids[0], category)
    return {"ok": True, "updated": n, "category": category, "created": created, "offer_rule": offer, "was": was}


def api_recategorize(conn, _q, _b):
    """Send everything still uncategorized or awaiting review through rules (and the AI model, if set up) again."""
    t = Transaction
    not_manual = func.coalesce(t.category_source, "") != "manual"
    ids = [r["id"] for r in conn.execute(
        select(t.id).where(func.coalesce(t.is_split, 0) == 0, or_(t.category.is_(None), and_(t.needs_review == 1, not_manual))))]
    conn.execute(update(t).where(t.needs_review == 1, not_manual).values(category=None, category_source=None, confidence=None))
    return categorize.categorize(conn, ids)
