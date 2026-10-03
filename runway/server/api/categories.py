"""Categories and the rules that assign them."""
from __future__ import annotations

import json
from datetime import date

from sqlalchemy import delete, func, select

from ... import categories, db, forecast, rules, splits
from ...models import Account, Budget, Category, RetailItem, Rule, Transaction
from ..common import ApiError


def api_categories(conn, _q, _b):
    """Every category, with how many transactions use it, and what else removing it would change: the rules that set
    it, whether it has a budget, and the order items in it (Settings asks first when there's any of these). And the
    card or account its spending goes on: the one you chose (pay_with), and the one used most for it lately, which the
    forecast goes by without a choice (usual_account)."""
    cats = categories.all_categories(conn)
    pay_with = dict(conn.execute(select(Category.name, Category.pay_with)).fetchall())
    used = forecast.account_use(conn, date.today())
    p = splits.parts()
    counts = {r["category"]: r["n"] for r in conn.execute(select(p.c.category, func.count().label("n")).group_by(p.c.category))}
    in_rules: dict[str, int] = {}
    for cat, split in conn.execute(select(Rule.category, Rule.split)):
        for name in {cat, *(part["category"] for part in (json.loads(split) if split else []))} - {None}:
            in_rules[name] = in_rules.get(name, 0) + 1
    budgeted = set(conn.execute(select(Budget.category)).scalars())
    items = {r[0]: r[1] for r in conn.execute(select(RetailItem.category, func.count())
                                              .where(RetailItem.category.is_not(None)).group_by(RetailItem.category))}
    for c in cats:
        c["transactions"] = counts.get(c["name"], 0)
        c["rules"] = in_rules.get(c["name"], 0)
        c["budgeted"] = c["name"] in budgeted
        c["items"] = items.get(c["name"], 0)
        c["pay_with"] = pay_with.get(c["name"])
        c["usual_account"] = forecast.usual_account(used, [c["name"]] + [k["name"] for k in cats if c["name"] in k["path"][:-1]])
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


def api_category_pay_with(conn, _q, body):
    try:
        categories.set_pay_with(conn, body.get("name") or "", body.get("pay_with") or None)
    except categories.CategoryError as e:
        raise ApiError(str(e)) from e
    return {"ok": True}


def api_category_remove(conn, _q, body):
    try:
        n = categories.remove(conn, body.get("name") or "", body.get("move_to") or None)
    except categories.CategoryError as e:
        raise ApiError(str(e)) from e
    return {"ok": True, "moved": n}

MAX_UNDO = 2000   # the most transactions a rule's Apply sends back for Undo


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
    """Run one rule over past transactions. `changed` says what each one was before (for Undo), up to MAX_UNDO of them."""
    t = Transaction
    cols = (t.id, t.category, t.category_source, t.confidence, t.needs_review, t.payee, t.is_split)

    def state() -> dict[str, tuple]:
        return {r["id"]: tuple(r) for r in conn.execute(select(*cols))}
    before = state()
    try:
        n = rules.apply_rule(conn, int(rule_id))
    except rules.RuleError as e:
        raise ApiError(str(e), 404) from e
    after = state()
    changed = [{"id": i, "was_category": b[1], "was_source": b[2], "was_confidence": b[3], "was_needs_review": 1 if b[4] else 0,
                "was_payee": b[5], "was_split": 1 if b[6] else 0} for i, b in before.items() if after.get(i) != b]
    return {"ok": True, "updated": n, "changed": changed if len(changed) <= MAX_UNDO else [], "undoable": len(changed) <= MAX_UNDO}


def api_rule_delete(conn, _q, _b, rule_id):
    conn.execute(delete(Rule).where(Rule.id == int(rule_id)))
    return {"ok": True}
