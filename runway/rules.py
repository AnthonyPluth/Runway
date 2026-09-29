"""Rules: when a transaction looks like this, do that.

A rule's conditions are all optional, and all must hold:
- text: the merchant or the bank's description contains (or is, or starts with) this text;
- amount: at least / at most this many dollars (whichever way the money went);
- direction: money out or money in;
- account: only transactions in this account.

Its actions are all optional too: set a category, show the merchant under another name, split the transaction by
percentages, and put it in Review.

More specific rules win: each kind of action comes from the most specific rule that has one (more conditions first,
then exact text before "starts with" before "contains", then longer text). So "venmo, over $1,000 -> Rent" beats
"venmo -> Transfer" for the rent, while a rename-only rule for "venmo" still renames both.
"""
from __future__ import annotations

import json

from . import db, splits

MODES = ("contains", "exact", "starts")
DIRECTIONS = ("out", "in")
FIELDS = ("match", "match_mode", "amount_min", "amount_max", "direction", "account_id", "category", "rename", "review", "split")
CENT = 0.005


class RuleError(ValueError):
    pass


def _specificity(r: dict) -> tuple:
    conditions = sum(1 for k in ("amount_min", "amount_max", "direction", "account_id") if r.get(k) not in (None, ""))
    mode = {"exact": 2, "starts": 1}.get(r.get("match_mode") or "contains", 0)
    return (conditions + (1 if r.get("match") else 0), mode, len(r.get("match") or ""))


def load(conn) -> list[dict]:
    """Every rule, most specific first, with its split parsed."""
    out = []
    for r in db.rows(conn.execute("SELECT * FROM rules")):
        r["match"] = (r["match"] or "").lower()
        r["split"] = json.loads(r["split"]) if r.get("split") else None
        out.append(r)
    out.sort(key=_specificity, reverse=True)
    return out


def _text_matches(r: dict, tx: dict) -> bool:
    m = r["match"]
    if not m:
        return True
    mode = r.get("match_mode") or "contains"
    for field in (tx.get("payee"), tx.get("description")):
        f = " ".join((field or "").lower().split())
        if (mode == "exact" and f == m) or (mode == "starts" and f.startswith(m)) or (mode == "contains" and m in f):
            return True
    return False


def matches(r: dict, tx: dict) -> bool:
    amt = tx.get("amount") or 0
    if (r.get("direction") == "out" and amt >= 0) or (r.get("direction") == "in" and amt <= 0):
        return False
    if r.get("amount_min") is not None and abs(amt) < r["amount_min"] - CENT:
        return False
    if r.get("amount_max") is not None and abs(amt) > r["amount_max"] + CENT:
        return False
    if r.get("account_id") and tx.get("account_id") != r["account_id"]:
        return False
    return _text_matches(r, tx)


def actions_for(tx: dict, rules: list[dict]) -> dict:
    """What the rules say to do with a transaction: {category, rename, split, review, rule_ids}. Each kind of action
    comes from the most specific matching rule that has one."""
    out = {"category": None, "rename": None, "split": None, "review": False, "rule_ids": []}
    for r in rules:
        if not matches(r, tx):
            continue
        out["rule_ids"].append(r["id"])
        if r.get("category") and not out["category"] and not out["split"]:
            out["category"] = r["category"]
        if r.get("split") and not out["split"] and not out["category"]:
            out["split"] = r["split"]
        if r.get("rename") and not out["rename"]:
            out["rename"] = r["rename"]
        if r.get("review"):
            out["review"] = True
    return out


def split_parts(amount: float, split: list[dict]) -> list[dict]:
    """A transaction's amount spread by percentages, in whole cents that add up exactly."""
    cents = round(abs(amount) * 100)
    sign = -1 if amount < 0 else 1
    whole = sum(float(p["percent"]) for p in split) or 100.0   # shares of the parts' own total, in case it isn't quite 100
    shares = [(p["category"], cents * float(p["percent"]) / whole) for p in split]
    parts = [[c, int(s), s - int(s)] for c, s in shares]
    left = cents - sum(p[1] for p in parts)
    for p in sorted(parts, key=lambda p: -p[2])[:max(0, left)]:
        p[1] += 1
    for p in sorted(parts, key=lambda p: -p[1])[:max(0, -left)]:   # never more than the whole
        p[1] -= 1
    return [{"category": c, "amount": sign * n / 100} for c, n, _ in parts if n]


def apply_actions(conn, tx: dict, acts: dict) -> str | None:
    """Carry out a rule's actions on one transaction (a row as a dict). Returns what it did to the category:
    'category', 'split' or None. The caller decides whether the transaction may be changed."""
    if acts.get("rename") and acts["rename"] != tx.get("payee"):
        conn.execute("UPDATE transactions SET payee=? WHERE id=?", (acts["rename"], tx["id"]))
        tx["payee"] = acts["rename"]
    done = None
    if acts.get("split"):
        parts = split_parts(tx["amount"], acts["split"])
        if len(parts) >= 2:
            try:
                splits.set_splits(conn, tx["id"], parts)
            except splits.SplitError:   # one transaction the rule can't split shouldn't stop the rest: ask about it
                conn.execute("UPDATE transactions SET needs_review=1 WHERE id=?", (tx["id"],))
                return None
            conn.execute("UPDATE transactions SET category=COALESCE(category, ?), category_source='rule', confidence=1 "
                         "WHERE id=?", (parts[0]["category"], tx["id"]))
            done = "split"
        elif parts:
            acts = {**acts, "category": parts[0]["category"]}
    if acts.get("category") and done is None:
        conn.execute("UPDATE transactions SET category=?, category_source='rule', confidence=1, needs_review=0 WHERE id=?",
                     (acts["category"], tx["id"]))
        done = "category"
    if acts.get("review"):
        conn.execute("UPDATE transactions SET needs_review=1 WHERE id=?", (tx["id"],))
    return done


# ------------------------------------------------------------------------------------------------ editing

def clean(conn, body: dict) -> dict:
    """A rule from the app, checked. Raises RuleError with something to tell the person."""
    r = _clean_conditions(conn, body)
    r["category"] = body.get("category") or None
    if r["category"] and not _known_category(conn, r["category"]):
        raise RuleError(f"Unknown category: {r['category']}")
    r["rename"] = " ".join(str(body.get("rename") or "").split())[:80] or None
    r["review"] = 1 if body.get("review") else 0
    split = body.get("split") or None
    r["split"] = _clean_split(conn, split) if split else None
    if r["split"]:
        r["category"] = None   # a split decides the categories
    if not (r["category"] or r["rename"] or r["review"] or r["split"]):
        raise RuleError("Choose what the rule should do")
    return r


def _known_category(conn, name: str) -> bool:
    return conn.execute("SELECT 1 FROM categories WHERE name=?", (name,)).fetchone() is not None


def _clean_amount(v) -> float | None:
    """A dollar limit, whichever way the money goes; None when it's left empty."""
    if v in (None, ""):
        return None
    try:
        return round(abs(db.number(v)), 2)
    except (TypeError, ValueError):
        raise RuleError("Amounts must be numbers") from None


def _clean_conditions(conn, body: dict) -> dict:
    """The rule's conditions (text, amounts, direction, account), checked; at least one is needed."""
    r: dict = {}
    r["match"] = " ".join(str(body.get("match") or "").lower().split())
    r["match_mode"] = body.get("match_mode") or "contains"
    if r["match_mode"] not in MODES:
        raise RuleError("Pick how the text should match")
    r["amount_min"] = _clean_amount(body.get("amount_min"))
    r["amount_max"] = _clean_amount(body.get("amount_max"))
    if r["amount_min"] is not None and r["amount_max"] is not None and r["amount_min"] > r["amount_max"]:
        raise RuleError("The smallest amount is bigger than the largest")
    r["direction"] = body.get("direction") or None
    if r["direction"] not in (None, *DIRECTIONS):
        raise RuleError("Direction is money out or money in")
    r["account_id"] = body.get("account_id") or None
    if r["account_id"] and not conn.execute("SELECT 1 FROM accounts WHERE id=?", (r["account_id"],)).fetchone():
        raise RuleError("Unknown account")
    if not r["match"] and not any(r[k] is not None for k in ("amount_min", "amount_max", "direction", "account_id")):
        raise RuleError("Give the rule some text to look for, or another condition")
    if r["match"] and len(r["match"]) < 2:
        raise RuleError("Use at least two letters of text")
    return r


def _clean_split(conn, split) -> str:
    """A split's parts, checked: two or more, each a known category and a positive percentage, adding up to 100%.
    Returned as the JSON the rules table keeps."""
    if not isinstance(split, list) or len(split) < 2:
        raise RuleError("A split needs at least two parts")
    parts = []
    for p in split:
        cat = (p or {}).get("category")
        try:
            pct = round(db.number((p or {}).get("percent")), 2)
        except (TypeError, ValueError):
            raise RuleError("Give every part a percentage") from None
        if not cat or not _known_category(conn, cat):
            raise RuleError("Give every part a category")
        if pct <= 0:
            raise RuleError("Give every part a percentage")
        parts.append({"category": cat, "percent": pct})
    if round(sum(p["percent"] for p in parts), 2) != 100:
        raise RuleError("The parts of a split must add up to 100%")
    return json.dumps(parts)


def save(conn, body: dict, rule_id: int | None = None) -> int:
    r = clean(conn, body)
    if rule_id is None:
        cols = list(r)
        cur = conn.execute(f"INSERT INTO rules({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", list(r.values()))
        return cur.lastrowid
    if not conn.execute("SELECT 1 FROM rules WHERE id=?", (rule_id,)).fetchone():
        raise RuleError("Rule not found")
    conn.execute(f"UPDATE rules SET {', '.join(f'{k}=?' for k in r)} WHERE id=?", (*r.values(), rule_id))
    return rule_id


def remember(conn, match: str, category: str) -> None:
    """"Remember for this merchant": a plain text -> category rule (updating the one for that text, if there is one)."""
    row = conn.execute("SELECT id FROM rules WHERE match=? AND COALESCE(match_mode, 'contains')='contains' AND amount_min IS NULL "
                       "AND amount_max IS NULL AND direction IS NULL AND account_id IS NULL AND split IS NULL", (match,)).fetchone()
    if row:
        conn.execute("UPDATE rules SET category=? WHERE id=?", (category, row["id"]))
    else:
        conn.execute("INSERT INTO rules(match, category) VALUES (?,?)", (match, category))


def _candidates(conn) -> list[dict]:
    return db.rows(conn.execute(
        "SELECT t.id, t.account_id, t.posted, t.amount, t.payee, t.description, t.category, t.category_source, "
        "t.is_split, t.needs_review, " + db.label_sql("a") + " AS account_name "
        "FROM transactions t JOIN accounts a ON a.id=t.account_id ORDER BY t.posted DESC, t.id"))


def _changes(tx: dict, r: dict) -> bool:
    """Whether applying this rule would change anything you'd see (and is allowed to)."""
    if r.get("rename") and tx["payee"] != r["rename"]:
        return True
    if tx["category_source"] == "manual" or tx["is_split"]:
        return False
    if r.get("split"):
        return True
    if r.get("category") and tx["category"] != r["category"]:
        return True
    return bool(r.get("review")) and not tx["needs_review"]


def preview(conn, body: dict, limit: int = 8) -> dict:
    """What a rule (saved or not) would match among your transactions, and how many it would change."""
    try:
        r = clean(conn, body)
    except RuleError as e:
        return {"error": str(e), "matches": 0, "changes": 0, "examples": []}
    r["split"] = json.loads(r["split"]) if r["split"] else None
    hits = [t for t in _candidates(conn) if matches(r, t)]
    changing = [t for t in hits if _changes(t, r)]
    return {"matches": len(hits), "changes": len(changing),
            "examples": [{k: t[k] for k in ("posted", "amount", "payee", "description", "category", "account_name")} for t in hits[:limit]]}


def apply_rule(conn, rule_id: int) -> int:
    """Run one rule over past transactions: renames apply to every match; categories, splits and Review only to
    transactions you haven't categorized or split yourself. Returns how many changed."""
    r = next((x for x in load(conn) if x["id"] == rule_id), None)
    if not r:
        raise RuleError("Rule not found")
    n = 0
    for t in _candidates(conn):
        if not matches(r, t) or not _changes(t, r):
            continue
        locked = t["category_source"] == "manual" or t["is_split"]
        acts = {"rename": r["rename"]} if locked else {k: r[k] for k in ("category", "rename", "split", "review")}
        apply_actions(conn, t, acts)
        n += 1
    return n


def describe(r: dict, accounts: dict[str, str] | None = None) -> str:
    """"Merchant contains 'venmo' · over $1,000 · money out"."""
    bits = []
    if r["match"]:
        how = {"exact": "is", "starts": "starts with"}.get(r.get("match_mode") or "contains", "contains")
        bits.append(f"merchant {how} '{r['match']}'")
    lo, hi = r.get("amount_min"), r.get("amount_max")
    if lo is not None and hi is not None:
        bits.append(f"${lo:,.2f}–${hi:,.2f}")
    elif lo is not None:
        bits.append(f"${lo:,.2f} or more")
    elif hi is not None:
        bits.append(f"up to ${hi:,.2f}")
    if r.get("direction"):
        bits.append("money out" if r["direction"] == "out" else "money in")
    if r.get("account_id"):
        bits.append(f"in {(accounts or {}).get(r['account_id'], 'an account')}")
    return " · ".join(bits)


# ------------------------------------------------------------------------------------------------ categories

def rename_category(conn, old: str, new: str) -> None:
    conn.execute("UPDATE rules SET category=? WHERE category=?", (new, old))
    for r in conn.execute("SELECT id, split FROM rules WHERE split IS NOT NULL").fetchall():
        parts = json.loads(r["split"])
        if any(p["category"] == old for p in parts):
            for p in parts:
                if p["category"] == old:
                    p["category"] = new
            conn.execute("UPDATE rules SET split=? WHERE id=?", (json.dumps(parts), r["id"]))


def forget_category(conn, name: str) -> None:
    """A category is gone (without a replacement): rules stop setting it, and rules left with nothing to do go."""
    conn.execute("UPDATE rules SET category=NULL WHERE category=?", (name,))
    for r in conn.execute("SELECT id, split FROM rules WHERE split IS NOT NULL").fetchall():
        if any(p["category"] == name for p in json.loads(r["split"])):
            conn.execute("UPDATE rules SET split=NULL WHERE id=?", (r["id"],))
    conn.execute("DELETE FROM rules WHERE category IS NULL AND split IS NULL AND rename IS NULL AND COALESCE(review, 0)=0")
