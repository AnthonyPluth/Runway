"""Categorizing an order's items: what you picked for the same item before, then the AI model (if one is set up), then
the store's own department; and the AI's suggestions for an order, and the category you pick for an item yourself."""
from __future__ import annotations

import json
import re
import time

from sqlalchemy import func, select, update

from .. import categorize, db, monitoring
from .. import settings_keys as sk
from ..models import Category, RetailCharge, RetailItem, RetailItemMemory, RetailOrder
from .split import apply
from .store import NAMES, RetailError, item_key

AI_BATCH = 50

# What a Costco receipt adds to an item's name that says nothing about what it is: its price-code and the warehouse's
# shelf and sale codes ("P=120", "P432", "#00123", "SL60", "DOM120", "T6H7P504", "CU38").
_COSTCO_CODES = re.compile(r"\bP=?\d{2,4}\b|#\d+|\b(?:SL|DOM|CU)\d+\b|\b[A-Z]\d{1,2}[A-Z]\d[A-Z]?\d*\b")


def ai_title(retailer: str, title: str | None) -> str:
    """An item's name as the AI model reads it: Costco's receipt codes taken off (the rest is left as it is)."""
    s = title or ""
    if retailer == "costco":
        s = " ".join(_COSTCO_CODES.sub(" ", s).split()) or s
    return s


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
                items: list[dict], allow_new: bool = False) -> str:
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
    if "Costco" in retailer_names:
        lines += ["- Costco receipts abbreviate item names: KS is Kirkland Signature (the store brand), ORG is organic, PK is a pack, "
                  "and the rest is often cut short (\"GRLCPEPWINGS\" is garlic pepper wings). Work out the product and categorize that."]
    if allow_new:
        lines += ["- If none of the categories is a good fit, you may propose a new one instead: set \"category\" to null and add",
                  "  \"new_category\": \"<short name, Title Case>\" and optionally \"parent\": \"<an existing category it belongs under>\".",
                  "  Only when nothing fits; never a near-duplicate of an existing category; reuse one new name for similar items."]
    if examples:
        lines += ["", "How this person has categorized items before:"]
        lines += [f"- {e['title']} -> {e['category']}" for e in examples]
    lines += ["", "Items (JSON):", json.dumps(items, ensure_ascii=False), "",
              'Reply with only a JSON array, one object per item: [{"i": <i>, "category": "<category>", "confidence": <0-1>}]'
              + (' (or {"i": <i>, "category": null, "new_category": "<name>", "parent": "<existing or null>", "confidence": <0-1>})'
                 if allow_new else "")]
    return "\n".join(lines)


def categorize_items(conn, use_ai: bool = True, caller=None) -> dict:
    """Give a category to every item that doesn't have one: what you picked for the same item before, then the AI
    model (if set up), then the store's department. Items nothing decides are left for the transaction's own
    category."""
    caller = caller or categorize.call_llm
    counts = {"memory": 0, "ai": 0, "department": 0, "left": 0}
    memory = {r["key"]: r["category"] for r in conn.execute(select(RetailItemMemory.key, RetailItemMemory.category))}
    have = {r["name"] for r in conn.execute(select(Category.name))}
    spend = {r["name"] for r in conn.execute(select(Category.name).where(Category.is_transfer == 0, Category.is_income == 0))}
    i, o = RetailItem, RetailOrder
    todo = db.rows(conn.execute(
        select(i.id, i.title, i.amount, i.department, o.retailer).join(o, o.id == i.order_id)
        .where(i.category_source.is_(None)).order_by(i.id)))
    left = []
    for it in todo:
        cat = memory.get(item_key(it["title"]))
        if cat in have:
            conn.execute(update(RetailItem).where(RetailItem.id == it["id"])
                         .values(category=cat, category_source="memory", confidence=1))
            counts["memory"] += 1
        else:
            left.append(it)

    api_key = db.get_setting(conn, sk.OPENROUTER_API_KEY)
    if left and use_ai and api_key and (db.get_setting(conn, sk.RETAIL_AI, "1") or "1") == "1":
        counts["ai"] += _items_with_ai(conn, left, caller, api_key, spend)
    for it in left:
        if it.get("done"):
            continue
        cat = _department_category(it["department"], have)
        if cat:
            conn.execute(update(RetailItem).where(RetailItem.id == it["id"])
                         .values(category=cat, category_source="department", confidence=0.6))
            counts["department"] += 1
        else:
            counts["left"] += 1
    return counts


@monitoring.ai_agent("Order item categorizer", "orders")
def _items_with_ai(conn, left: list[dict], caller, api_key: str, spend: set[str]) -> int:
    """Ask the model about items, AI_BATCH at a time, and save its answers. Items it answered are marked done.
    Stops at the first failed request (recorded in the AI log); what's left falls back to departments. Returns how
    many items it categorized."""
    model = categorize.llm_model(conn)
    cats = sorted(spend)
    subs = [h for h in categorize.subcategory_hints(conn) if h.split(" > ")[-1] in spend]
    examples = [{"title": r["title"][:80], "category": r["category"]} for r in conn.execute(
        select(RetailItem.title, RetailItem.category).where(RetailItem.category_source == "manual")
        .order_by(RetailItem.id.desc()).limit(40))]
    names = " and ".join(sorted({NAMES.get(it["retailer"], it["retailer"]) for it in left}))
    conn.commit()   # don't hold the database while the model thinks
    done = 0
    for start in range(0, len(left), AI_BATCH):
        batch = left[start:start + AI_BATCH]
        payload = [{"i": i, "item": ai_title(it["retailer"], it["title"])[:200], "price": round(it["amount"] or 0, 2),
                    **({"department": it["department"]} if it["department"] else {})} for i, it in enumerate(batch)]
        began, reply = time.time(), None
        try:
            reply = caller(api_key, model, item_prompt(names, cats, subs, examples, payload))
            answers = categorize.parse_ai_reply(reply, cats)
            answered = sum(1 for a in answers.values() if a[0])
            categorize.log_call(conn, "orders", model, len(batch), answered, 0, True, time.time() - began,
                            f"Categorized {answered} of {len(batch)} items from {names} orders", reply)
        except Exception as e:   # recorded in the AI log; these fall back to departments
            said = monitoring.public_text(str(e))   # its text quotes the provider's answer: kept scrubbed, as categorize does
            categorize.log_call(conn, "orders", model, len(batch), 0, 0, False, time.time() - began, said[:500], reply)
            conn.commit()
            break
        for i, it in enumerate(batch):
            cat, conf = answers.get(i, (None, 0.0))
            if cat:
                conn.execute(update(RetailItem).where(RetailItem.id == it["id"], RetailItem.category_source.is_(None))
                             .values(category=cat, category_source="ai", confidence=conf))
                done += 1
                it["done"] = True
        conn.commit()
    return done


def suggest_for_order(conn, order_id: str, caller=None) -> list[dict]:
    """The AI's category for each item of an order that has none yet, proposing a new category where nothing fits.
    Nothing is saved: you apply each one (see set_item_category and api_retail_item)."""
    caller = caller or categorize.call_llm
    api_key = db.get_setting(conn, sk.OPENROUTER_API_KEY)
    if not api_key:
        raise RetailError("Add an OpenRouter key in Settings → Connections first")
    i, o = RetailItem, RetailOrder
    items = db.rows(conn.execute(
        select(i.id, i.title, i.amount, i.department, o.retailer).join(o, o.id == i.order_id)
        .where(i.order_id == order_id, i.category.is_(None)).order_by(i.id)))
    if not items:
        return []
    spend = {r["name"] for r in conn.execute(select(Category.name).where(Category.is_transfer == 0, Category.is_income == 0))}
    cats = sorted(spend)
    subs = [h for h in categorize.subcategory_hints(conn) if h.split(" > ")[-1] in spend]
    examples = [{"title": r["title"][:80], "category": r["category"]} for r in conn.execute(
        select(RetailItem.title, RetailItem.category).where(RetailItem.category_source == "manual")
        .order_by(RetailItem.id.desc()).limit(40))]
    names = " and ".join(sorted({NAMES.get(it["retailer"], it["retailer"]) for it in items}))
    model = categorize.llm_model(conn)
    payload = [{"i": n, "item": ai_title(it["retailer"], it["title"])[:200], "price": round(it["amount"] or 0, 2),
                **({"department": it["department"]} if it["department"] else {})} for n, it in enumerate(items[:AI_BATCH])]
    conn.commit()   # don't hold the database while the model thinks
    began, reply = time.time(), None
    try:
        with monitoring.ai_agent("Order item categorizer", "orders"):
            reply = caller(api_key, model, item_prompt(names, cats, subs, examples, payload, allow_new=True))
        answers = categorize.parse_ai_reply(reply, cats, allow_new=True)
    except Exception as e:   # network or API error (its text quotes the provider's answer: kept scrubbed)
        said = monitoring.public_text(str(e))
        categorize.log_call(conn, "orders", model, len(payload), 0, 0, False, time.time() - began, said[:500], reply)
        conn.commit()
        raise RetailError(f"The AI request failed: {said[:300]}") from e
    out = [{"item_id": it["id"], "category": ans[0], "new_category": ans[2], "confidence": round(ans[1], 2)}
           for n, it in enumerate(items[:AI_BATCH]) if (ans := answers.get(n)) and (ans[0] or ans[2])]
    new_cats = len({a["new_category"]["name"].lower() for a in out if a["new_category"]})
    categorize.log_call(conn, "orders", model, len(payload), len(out), new_cats, True, time.time() - began,
                    f"Suggested a category for {len(out)} of {len(payload)} items from {names} orders", reply)
    return out


def set_item_category(conn, item_id: int, category: str, remember: bool = True) -> dict:
    """You picked a category for an item: remember it for the same item in other orders, and re-split the
    transactions it's in."""
    if not conn.execute(select(Category.name).where(Category.name == category)).fetchone():
        raise RetailError(f"Unknown category: {category}")
    i = RetailItem
    it = conn.execute(select(i.id, i.order_id, i.title).where(i.id == item_id)).fetchone()
    if not it:
        raise RetailError("Item not found")
    conn.execute(update(i).where(i.id == item_id).values(category=category, category_source="manual", confidence=1))
    orders = {it["order_id"]}
    if remember and item_key(it["title"]):
        key = item_key(it["title"])
        db.upsert(conn, RetailItemMemory, {"key": key, "category": category}, key=["key"])
        for r in conn.execute(select(i.id, i.order_id, i.title)
                              .where(i.id != item_id, func.coalesce(i.category_source, "") != "manual")).fetchall():
            if item_key(r["title"]) == key:
                conn.execute(update(i).where(i.id == r["id"]).values(category=category, category_source="memory", confidence=1))
                orders.add(r["order_id"])
    redone = 0
    for ch in conn.execute(select(RetailCharge.id).where(RetailCharge.order_id.in_(list(orders)),
                                                         RetailCharge.tx_id.is_not(None))).fetchall():
        redone += apply(conn, ch["id"]) in ("split", "category")
    return {"orders": len(orders), "resplit": redone}
