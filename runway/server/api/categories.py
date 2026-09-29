"""Categories and the rules that assign them."""
from __future__ import annotations

from sqlalchemy import delete, func, select

from ... import categories, db, rules, splits
from ...models import Account, Rule
from ..common import ApiError


def api_categories(conn, _q, _b):
    cats = categories.all_categories(conn)
    p = splits.parts()
    counts = {r["category"]: r["n"] for r in conn.execute(select(p.c.category, func.count().label("n")).group_by(p.c.category))}
    for c in cats:
        c["transactions"] = counts.get(c["name"], 0)
    return cats


def api_category_add(conn, _q, body):
    try:
        categories.add(conn, body.get("name") or "", body.get("parent") or None,
                       bool(body.get("is_transfer")), bool(body.get("is_income")))
    except categories.CategoryError as e:
        raise ApiError(str(e)) from e
    return {"ok": True}


def api_category_rename(conn, _q, body):
    try:
        categories.rename(conn, body.get("name") or "", body.get("new_name") or "")
    except categories.CategoryError as e:
        raise ApiError(str(e)) from e
    return {"ok": True}


def api_category_move(conn, _q, body):
    try:
        categories.move(conn, body.get("name") or "", body.get("parent") or None)
    except categories.CategoryError as e:
        raise ApiError(str(e)) from e
    return {"ok": True}


def api_category_look(conn, _q, body):
    try:
        categories.set_look(conn, body.get("name") or "", body.get("icon"), body.get("color"))
    except categories.CategoryError as e:
        raise ApiError(str(e)) from e
    return {"ok": True}


def api_category_remove(conn, _q, body):
    try:
        n = categories.remove(conn, body.get("name") or "", body.get("move_to") or None)
    except categories.CategoryError as e:
        raise ApiError(str(e)) from e
    return {"ok": True, "moved": n}


def api_rules(conn, _q, _b):
    names = {r["id"]: db.account_label(r) for r in conn.execute(select(Account.id, Account.name, Account.display_name, Account.owner))}
    out = []
    for r in sorted(rules.load(conn), key=lambda r: (r["match"] or "~", r["id"])):
        r["summary"] = rules.describe(r, names)
        out.append(r)
    return out


def api_rule_add(conn, _q, body):
    try:
        rid = rules.save(conn, body)
        return {"ok": True, "id": rid, "updated": rules.apply_rule(conn, rid) if body.get("apply") else 0}
    except rules.RuleError as e:
        raise ApiError(str(e)) from e


def api_rule_update(conn, _q, body, rule_id):
    try:
        rules.save(conn, body, int(rule_id))
    except rules.RuleError as e:
        raise ApiError(str(e)) from e
    return {"ok": True}


def api_rule_preview(conn, _q, body):
    """What a rule you're writing would match, before you save it."""
    return rules.preview(conn, body)


def api_rule_apply(conn, _q, _b, rule_id):
    try:
        return {"ok": True, "updated": rules.apply_rule(conn, int(rule_id))}
    except rules.RuleError as e:
        raise ApiError(str(e), 404) from e


def api_rule_delete(conn, _q, _b, rule_id):
    conn.execute(delete(Rule).where(Rule.id == int(rule_id)))
    return {"ok": True}
