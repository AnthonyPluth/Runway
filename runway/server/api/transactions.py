"""Transactions: the list and its filters, categorizing and splitting them, and the AI model's suggestions."""
from __future__ import annotations

import urllib.parse
from typing import Any

from ... import categories, categorize, db, merchants, retail, splits
from ... import settings_keys as sk
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
    where: list[str] = ["t." + db.NOT_INVESTMENT]
    args: list[Any] = []
    if q.get("review", ["0"])[0] == "1":
        where.append("t.needs_review=1")
    if q.get("recurring", [""])[0]:
        where.append("t.recurring_id=?")
        args.append(int(q["recurring"][0]))
    if q.get("account", [""])[0]:
        where.append("t.account_id=?")
        args.append(q["account"][0])
    if q.get("category", [""])[0]:
        cat = q["category"][0]
        if cat == "__none__":
            where.append("t.category IS NULL AND COALESCE(t.is_split, 0)=0")
        else:
            family = [cat, *categories.descendants(conn, cat)]   # a category includes its subcategories
            ph = ",".join("?" * len(family))
            # a split transaction counts under every category it's split into, not the one on the row
            where.append(f"((COALESCE(t.is_split, 0)=0 AND t.category IN ({ph})) OR EXISTS "
                         f"(SELECT 1 FROM tx_splits s WHERE s.tx_id=t.id AND s.category IN ({ph})))")
            args.extend(family * 2)
    if q.get("month", [""])[0]:   # YYYY-MM
        start, end = _month_range({"month": q["month"]})
        where.append("t.posted>=? AND t.posted<?")
        args += [start.isoformat(), end.isoformat()]
    if q.get("scope", [""])[0] == "budget":   # the same accounts the Budget page counts
        where.append("t.account_id IN (SELECT id FROM accounts WHERE hidden=0 AND kind IN ('checking','savings','credit'))")
    if q.get("q", [""])[0]:
        like = f"%{q['q'][0].lower()}%"
        where.append("(lower(t.payee) LIKE ? OR lower(t.description) LIKE ?)")
        args += [like, like]
    limit = max(1, min(int(q.get("limit", ["200"])[0]), 1000))
    offset = max(0, int(q.get("offset", ["0"])[0]))
    sql = (
        "SELECT t.*, " + db.label_sql("a") + " AS account_name, a.kind AS account_kind, r.name AS recurring_name "
        "FROM transactions t JOIN accounts a ON a.id=t.account_id LEFT JOIN recurring r ON r.id=t.recurring_id "
        f"WHERE {' AND '.join(where)} ORDER BY t.posted DESC, t.id LIMIT ? OFFSET ?"
    )
    items = db.rows(conn.execute(sql, (*args, limit, offset)))
    parts = splits.of(conn, [t["id"] for t in items if t["is_split"]])
    orders = retail.for_transactions(conn, [t["id"] for t in items])   # the order a charge paid for, or a refund came from
    logos = tx_logos(conn, items)
    for t in items:
        t["splits"] = parts.get(t["id"], [])
        t["retail"] = orders.get(t["id"])
        t["logo"] = logos.get(t["id"])
    total = conn.execute(
        f"SELECT COUNT(*) FROM transactions t WHERE {' AND '.join(where)}", args
    ).fetchone()[0]
    return {"items": items, "total": total}


def api_tx_category(conn, _q, body, tx_id):
    try:
        remember = bool(body.get("remember"))
        n = categorize.set_category(conn, tx_id, body.get("category", ""), remember)
    except ValueError as e:
        raise ApiError(str(e)) from e
    # Not remembered yet: the app asks whether to use this category for the merchant from now on.
    offer = None if remember else categorize.rule_offer(conn, tx_id, body.get("category", ""))
    return {"ok": True, "also_updated": n, "offer_rule": offer}


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
    ids = body.get("ids")
    if not isinstance(ids, list):
        raise ApiError("Select some transactions first")
    try:
        n = categorize.bulk_update(conn, ids, body.get("category") or None, body.get("payee") or None,
                                   bool(body.get("reviewed")))
    except ValueError as e:
        raise ApiError(str(e)) from e
    return {"ok": True, "updated": n}


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
    return db.rows(conn.execute("SELECT * FROM ai_log ORDER BY id DESC LIMIT 25"))


def api_ai_apply(conn, _q, body):
    ids = [str(i) for i in (body.get("tx_ids") or [])]
    category = body.get("category") or ""
    new = body.get("new_category") or None
    created = False
    if new:   # accept an AI-proposed category: create it (unless it exists by now), then use it
        name = " ".join(str(new.get("name") or "").split())
        existing = conn.execute("SELECT name FROM categories WHERE lower(name)=lower(?)", (name,)).fetchone()
        if existing:
            category = existing["name"]
        else:
            parent = new.get("parent") or None
            if parent and not conn.execute("SELECT 1 FROM categories WHERE name=?", (parent,)).fetchone():
                parent = None
            try:
                categories.add(conn, name, parent, is_income=(body.get("direction") == "in" and not parent))
            except categories.CategoryError as e:
                if parent and "levels deep" in str(e):
                    categories.add(conn, name, None)
                else:
                    raise ApiError(str(e)) from e
            category, created = name, True
    remember = bool(body.get("remember"))
    try:
        n = categorize.apply_to_group(conn, ids, category, remember)
    except ValueError as e:
        raise ApiError(str(e)) from e
    # Applying a suggestion categorizes; the app then asks whether this merchant should always be this category.
    offer = None if remember or not ids else categorize.rule_offer(conn, ids[0], category)
    return {"ok": True, "updated": n, "category": category, "created": created, "offer_rule": offer}


def api_recategorize(conn, _q, _b):
    """Send everything still uncategorized or awaiting review through rules (and the AI model, if set up) again."""
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM transactions WHERE COALESCE(is_split, 0)=0 "
        "AND (category IS NULL OR (needs_review=1 AND COALESCE(category_source, '') <> 'manual'))"
    )]
    conn.execute(
        "UPDATE transactions SET category=NULL, category_source=NULL, confidence=NULL "
        "WHERE needs_review=1 AND COALESCE(category_source, '') <> 'manual'"
    )
    return categorize.categorize(conn, ids)
