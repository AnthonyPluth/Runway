"""Transactions: the list and its filters, categorizing and splitting them, and the AI model's suggestions."""
from __future__ import annotations

import urllib.parse

from sqlalchemy import and_, func, or_, select, update

from ... import categories, categorize, db, merchants, retail, splits
from ... import settings_keys as sk
from ...models import Account, AiLog, Category, Recurring, Transaction, TxSplit
from ..common import ApiError, _month_range


def tx_logos(conn, items: list[dict]) -> dict[str, str]:
    """{transaction id: the URL of its merchant's logo}: Logo.dev's (by the merchant's website or name; a sync, or adding
    the key, fetches it), else Plaid's, and a logo you chose for the merchant over both (or none at all). Logo.dev's
    come first because they're fetched for a dark background; Plaid's are opaque squares, often dark on white."""
    logos = merchants.logo_dev_logos(conn, items)
    logos.update(merchants.for_transactions(conn, [t for t in items if t["id"] not in logos]))
    for tid, mid in merchants.chosen_for(conn, items).items():
        if mid:
            logos[tid] = mid
        else:
            logos.pop(tid, None)
    return {tid: f"/api/merchants/{urllib.parse.quote(mid, safe='')}/logo" for tid, mid in logos.items()}


def api_transactions(conn, q, _b):
    T = Transaction
    where = [db.not_investment()]
    if q.get("review", ["0"])[0] == "1":
        where.append(T.needs_review == 1)
    if q.get("recurring", [""])[0]:
        where.append(T.recurring_id == int(q["recurring"][0]))
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
    if q.get("month", [""])[0]:   # YYYY-MM
        start, end = _month_range({"month": q["month"]})
        where += [T.posted >= start.isoformat(), T.posted < end.isoformat()]
    if q.get("scope", [""])[0] == "budget":   # the same accounts the Budget page counts
        where.append(T.account_id.in_(select(Account.id).where(Account.hidden == 0,
                                                               Account.kind.in_(["checking", "savings", "credit"]))))
    if q.get("q", [""])[0]:
        like = f"%{q['q'][0].lower()}%"
        where.append(or_(func.lower(T.payee).like(like), func.lower(T.description).like(like)))
    limit = max(1, min(int(q.get("limit", ["200"])[0]), 1000))
    offset = max(0, int(q.get("offset", ["0"])[0]))
    items = db.rows(conn.execute(
        select(T, db.account_label_expr().label("account_name"), Account.kind.label("account_kind"),
               Recurring.name.label("recurring_name"))
        .join(Account, Account.id == T.account_id).outerjoin(Recurring, Recurring.id == T.recurring_id)
        .where(*where).order_by(T.posted.desc(), T.id).limit(limit).offset(offset)))
    parts = splits.of(conn, [t["id"] for t in items if t["is_split"]])
    orders = retail.for_transactions(conn, [t["id"] for t in items])   # the order a charge paid for, or a refund came from
    logos = tx_logos(conn, items)
    for t in items:
        t["splits"] = parts.get(t["id"], [])
        t["retail"] = orders.get(t["id"])
        t["logo"] = logos.get(t["id"])
    total = conn.execute(select(func.count()).select_from(T).where(*where)).fetchone()[0]
    return {"items": items, "total": total}


# What Undo needs to put transactions back as they were: the fields a category, rename, review mark or rule can change.
_WAS = ("id", "category", "category_source", "confidence", "needs_review", "payee")


def snapshot(conn, ids: list[str]) -> list[dict]:
    """These transactions as they are now (before a change), for `restore`. A split one carries its parts."""
    t = Transaction
    out: list[dict] = []
    for i in range(0, len(ids), 500):
        chunk = [str(x) for x in ids[i:i + 500]]
        rows = db.rows(conn.execute(select(*(getattr(t, c) for c in _WAS), t.is_split).where(t.id.in_(chunk))))
        parts = splits.of(conn, [r["id"] for r in rows if r["is_split"]])
        for r in rows:
            r["splits"] = [{"amount": p["amount"], "category": p["category"], "note": p["note"]} for p in parts.get(r["id"], [])]
            r["is_split"] = 1 if r["is_split"] else 0
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
        payee = " ".join(str(r.get("payee") or "").split())[:80] or None
        cur = conn.execute(update(t).where(t.id == r["id"]).values(
            category=cat, category_source=r.get("category_source") or None, confidence=r.get("confidence"),
            needs_review=1 if r.get("needs_review") else 0, payee=payee))
        if not cur.rowcount:
            continue
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
    was = snapshot(conn, [tx_id])
    try:
        remember = bool(body.get("remember"))
        n = categorize.set_category(conn, tx_id, body.get("category", ""), remember)
    except ValueError as e:
        raise ApiError(str(e)) from e
    # Not remembered yet: the app asks whether to use this category for the merchant from now on.
    offer = None if remember else categorize.rule_offer(conn, tx_id, body.get("category", ""))
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
        return {"ok": True, "updated": restore(conn, body["restore"])}
    ids = body.get("ids")
    if not isinstance(ids, list):
        raise ApiError("Select some transactions first")
    was = snapshot(conn, ids)
    try:
        n = categorize.bulk_update(conn, ids, body.get("category") or None, body.get("payee") or None,
                                   bool(body.get("reviewed")))
    except ValueError as e:
        raise ApiError(str(e)) from e
    return {"ok": True, "updated": n, "was": was}


def api_tx_accept(conn, _q, _b, tx_id):
    categorize.accept_suggestion(conn, tx_id)
    return {"ok": True}


def api_ai_suggest(conn, _q, _b):
    if not db.get_setting(conn, sk.OPENROUTER_API_KEY):
        raise ApiError("Add an OpenRouter API key in Settings first.")
    try:
        return categorize.suggest_for_review(conn)
    except RuntimeError as e:
        raise ApiError(str(e), 502) from e


def api_ai_log(conn, _q, _b):
    return db.rows(conn.execute(select(AiLog).order_by(AiLog.id.desc()).limit(25)))


def api_ai_apply(conn, _q, body):
    ids = [str(i) for i in (body.get("tx_ids") or [])]
    category = body.get("category") or ""
    new = body.get("new_category") or None
    created = False
    if new:   # accept an AI-proposed category: create it (unless it exists by now), then use it
        try:
            category, created = categorize.create_proposed(conn, new, is_income=body.get("direction") == "in")
        except ValueError as e:
            raise ApiError(str(e)) from e
    remember = bool(body.get("remember"))
    was = snapshot(conn, ids)
    try:
        n = categorize.apply_to_group(conn, ids, category, remember)
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
