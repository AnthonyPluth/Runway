"""Categorization: payee cleanup, built-in heuristics, user rules, optional AI (via OpenRouter), and a review queue."""
from __future__ import annotations

import json
import os
import re
import ssl
import time
import urllib.error
import urllib.request

from sqlalchemy import Integer, case, delete, func, insert, or_, select, type_coerce, update

from . import categories as catmod
from . import settings_keys as sk
from . import db, monitoring, rules as rulesmod, splits
from .models import Account, AiLog, Category, Rule, Transaction

REVIEW_THRESHOLD = 0.85
DEFAULT_MODEL = "anthropic/claude-haiku-4.5"  # any OpenRouter model id works
OPENROUTER_URL = os.environ.get("RUNWAY_OPENROUTER_URL", "https://openrouter.ai/api/v1/chat/completions")

# Processor prefixes that hide the real merchant name.
_PREFIX = re.compile(
    r"^(?:tst\s*\*|sq\s*\*|dd\s*\*|py\s*\*|sp\s*\*|pp\s*\*|paypal\s*\*|toast\s*\*|clover\s*\*|"
    r"pos\s+(?:purchase\s+)?|debit card purchase\s+|direct debit\s+|ach debit\s+|ach credit\s+|"
    r"checkcard\s+\d*\s*|purchase authorized on\s+\d+/\d+\s+)",
    re.I,
)
_KEEP_SECOND = {"eats", "prime", "music", "one", "pass", "plus"}  # "UBER *EATS" differs from "UBER *TRIP"
_BRAND_STAR = re.compile(r"^([A-Za-z][A-Za-z0-9&'.]{1,20})\s*\*\s*\S")   # "LYFT   *SCHD AIR" -> "LYFT"
_TRAILING_NOISE = re.compile(
    r"(\s+-\s+\d.*$|\s+#+\s*\d+.*$|\s*##\d+.*$|\s+\d{3,}.*$|\s+\(cash\)$|\s+x{3,}\d*.*$|\s+-\s*$)", re.I
)


def clean_payee(raw: str | None) -> str:
    if not raw:
        return ""
    s = re.sub(r"\s+", " ", raw).strip()
    for _ in range(2):
        s = _PREFIX.sub("", s).strip()
    m = _BRAND_STAR.match(s)
    if m:
        nxt = s[m.end() - 1 :].split(" ")[0].lower()
        s = f"{m.group(1)} {nxt}" if nxt in _KEEP_SECOND else m.group(1)
    s = re.sub(r"\s*\*\s*[A-Za-z0-9]{5,}$", "", s)  # trailing order codes: "AMZN Mktp US*2K3AB1"
    s = s.replace("*", " ")
    prev = None
    while prev != s:
        prev = s
        s = _TRAILING_NOISE.sub("", s).strip(" -")
    s = re.sub(r"\s+", " ", s).strip()
    if not s:
        s = re.sub(r"\s+", " ", raw).strip()
    return " ".join(w[:1].upper() + w[1:].lower() if w.isupper() or w.islower() else w for w in s.split(" "))


# ---------------------------------------------------------------------------------------------------------
# Built-in heuristics that are safe without asking anyone.

_CARD_PAYMENT_OUT = re.compile(r"crcardpmt|card ?pmt|credit ?card|cardmember|payment to .*card|amex|chase credit|citi autopay|"
                               r"discover e-payment|applecard|capital one\b.*\b(?:pmt|payment)", re.I)
# "autopay" and "epay" are just as often a utility, insurer or loan: only a card payment when something says card.
_AUTOPAY = re.compile(r"autopay|e-?pay", re.I)
_CARD_WORDS = re.compile(r"card|visa|mastercard|amex|american express|discover|citi|chase|barclay|synchrony|capital one", re.I)
_NOT_CARD = re.compile(r"auto fin|auto loan|mortgage|\bloan\b|lease|insurance", re.I)
_CARD_PAYMENT_IN = re.compile(r"payment|autopay|thank you|pymt|pmt", re.I)
_SWEEP = re.compile(r"core account|money market|spaxx|fdrxx|sweep", re.I)


def heuristic_category(tx: dict, account_kind: str) -> str | None:
    desc = f"{tx.get('description') or ''} {tx.get('payee') or ''}"
    amt = tx.get("amount") or 0
    if abs(amt) < 0.005:
        return "Ignore"
    if _SWEEP.search(desc):
        return "Ignore"
    if account_kind == "credit" and amt > 0 and _CARD_PAYMENT_IN.search(desc):
        return "Credit Card Payment"
    if account_kind in ("checking", "savings") and amt < 0 and not _NOT_CARD.search(desc) and (
            _CARD_PAYMENT_OUT.search(desc) or (_AUTOPAY.search(desc) and _CARD_WORDS.search(desc))):
        return "Credit Card Payment"
    return None


# ---------------------------------------------------------------------------------------------------------
# Rules (runway/rules.py)

def rule_key(tx: dict) -> str:
    return (tx.get("payee") or tx.get("description") or "").strip().lower()


# ---------------------------------------------------------------------------------------------------------
# AI categorization (OpenRouter)

def _category_names(conn) -> list[str]:
    return conn.execute(select(Category.name).order_by(Category.name)).scalars()


def _subcategory_hints(conn) -> list[str]:
    return [" > ".join(c["path"]) for c in catmod.all_categories(conn) if c["depth"] > 0]


def build_prompt(categories: list[str], examples: list[dict], txs: list[dict], subcategories: list[str] | None = None,
                 allow_new: bool = False) -> str:
    lines = [
        "You categorize personal bank and credit card transactions.",
        "Pick exactly one category for each transaction from this list:",
        ", ".join(categories),
    ]
    if subcategories:
        lines += ["Some are subcategories (parent > sub, possibly several levels). Prefer the most specific one that fits:", "; ".join(subcategories)]
    lines += [
        "",
        "Guidance:",
        "- 'Credit Card Payment' and 'Transfer' are money moving between the person's own accounts.",
        "- Positive amounts are money in. Refunds from merchants are 'Refunds'; paychecks and interest are 'Income'.",
        "- If you are not sure, still give your best guess but lower the confidence.",
        "- confidence is 0 to 1: how sure you are that a careful person would pick the same category.",
    ]
    if allow_new:
        lines += [
            "- If none of the categories is a good fit, you may propose a new one instead: set \"category\" to null and add",
            "  \"new_category\": \"<short name, Title Case>\" and optionally \"parent\": \"<an existing category it belongs under>\".",
            "  Only do this when nothing fits; never propose a near-duplicate of an existing category. Reuse the same new name for",
            "  similar merchants. confidence is then how sure you are the new category is the right home.",
        ]
    if examples:
        lines += ["", "How this person has categorized similar merchants before:"]
        lines += [f"- {e['payee']} -> {e['category']}" for e in examples]
    lines += [
        "",
        "Transactions (JSON):",
        json.dumps(txs, ensure_ascii=False),
        "",
        'Reply with only a JSON array, one object per transaction: [{"i": <i>, "category": "<category>", "confidence": <0-1>}]'
        + (' (or {"i": <i>, "category": null, "new_category": "<name>", "parent": "<existing or null>", "confidence": <0-1>})' if allow_new else ""),
    ]
    return "\n".join(lines)


def extract_json_array(text: str) -> list | None:
    """Find the answer array in a model reply, skipping any visible reasoning (<think>...</think>), code fences
    and stray brackets in the prose around it. Returns None if there isn't one."""
    text = re.sub(r"<think>.*?</think>", " ", text or "", flags=re.S | re.I)
    decoder = json.JSONDecoder()
    best = None
    for m in re.finditer(r"\[", text):
        try:
            val, _ = decoder.raw_decode(text[m.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(val, list) and val and all(isinstance(x, dict) for x in val) and any("i" in x for x in val):
            best = val   # keep the last good one: models sometimes restate a draft before the final answer
    return best


def parse_ai_reply(text: str, categories: list[str], allow_new: bool = False) -> dict[int, tuple]:
    """{i: (category, confidence)}; with allow_new, {i: (category, confidence, proposal)} where proposal is
    {"name", "parent"} for a new category the model suggests (checked against what already exists)."""
    items = extract_json_array(text)
    if items is None:
        return {}
    lookup = {c.lower(): c for c in categories}
    out: dict[int, tuple] = {}
    for it in items:
        try:
            i = int(it["i"])
        except (KeyError, TypeError, ValueError):
            continue
        cat = lookup.get(str(it.get("category") or "").strip().lower())
        try:
            conf = max(0.0, min(1.0, float(it.get("confidence", 0))))
        except (TypeError, ValueError):
            conf = 0.0
        if not allow_new:
            out[i] = (cat, conf if cat else 0.0)
            continue
        proposal = None
        new = " ".join(str(it.get("new_category") or "").split())[:40]
        if not cat and new:
            if new.lower() in lookup:            # "new" but it already exists: just use it
                cat = lookup[new.lower()]
            else:
                parent = lookup.get(str(it.get("parent") or "").strip().lower())
                proposal = {"name": new, "parent": parent}
        out[i] = (cat, conf if (cat or proposal) else 0.0, proposal)
    return out


def call_llm(api_key: str, model: str, prompt: str) -> str:
    """Ask a model through OpenRouter's chat completions API."""
    body = json.dumps({
        "model": model,
        "max_tokens": 4096,
        "temperature": 0,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(
        OPENROUTER_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost:8765",
            "X-Title": "Runway",
        },
    )
    ctx = ssl.create_default_context()
    try:
        import certifi

        ctx.load_verify_locations(certifi.where())
    except (ImportError, OSError):   # certifi is optional; without it (or its bundle) the system certs still apply
        pass
    # A chat span in Sentry's Agent Tracing: the model, timings and tokens (the prompt only if you ask; monitoring.py).
    with monitoring.ai_call(model, prompt, max_tokens=4096, temperature=0) as span:
        try:
            with urllib.request.urlopen(req, timeout=120, context=ctx) as resp:
                data = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            raise RuntimeError(f"OpenRouter HTTP {e.code}: {detail}") from e
        if data.get("error"):
            raise RuntimeError(f"OpenRouter: {data['error']}")
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        if isinstance(content, list):  # some providers return content parts
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        monitoring.ai_result(span, data, model, content)
    return content


# ---------------------------------------------------------------------------------------------------------
# Pipeline

def categorize(conn, tx_ids: list[str] | None = None, use_ai: bool = True, caller=call_llm) -> dict:
    """Categorize the given transactions (or every uncategorized one). Returns counts by outcome."""
    todo = _to_categorize(conn, tx_ids)
    counts = {"auto": 0, "rule": 0, "history": 0, "ai": 0, "review": 0}
    rules = rulesmod.load(conn)
    history = _merchant_history(conn)
    leftover: list[dict] = []
    review_after: list[str] = []
    for tx in todo:
        source = _categorize_locally(conn, tx, rules, history, review_after)
        if source:
            counts[source] += 1
        else:
            leftover.append(tx)

    api_key = db.get_setting(conn, sk.OPENROUTER_API_KEY)
    auto_ai = (db.get_setting(conn, sk.AUTO_AI_ON_SYNC, "1") or "1") == "1"
    if leftover and use_ai and auto_ai and api_key:
        _categorize_with_ai(conn, leftover, caller, counts)
    else:
        for t in leftover:
            conn.execute(update(Transaction).where(Transaction.id == t["id"]).values(needs_review=1))
            counts["review"] += 1
    for tid in review_after:   # a rule said to look at these, whatever category they got
        conn.execute(update(Transaction).where(Transaction.id == tid).values(needs_review=1))
    return counts


def _to_categorize(conn, tx_ids: list[str] | None) -> list[dict]:
    """The given transactions (or every uncategorized one), leaving out split ones, with their account's kind."""
    t = Transaction
    q = select(t, Account.kind).join(Account, Account.id == t.account_id).where(func.coalesce(t.is_split, 0) == 0)
    if tx_ids is None:
        return db.rows(conn.execute(q.where(t.category.is_(None))))
    todo = []
    for i in range(0, len(tx_ids), 500):
        todo += db.rows(conn.execute(q.where(t.id.in_(tx_ids[i : i + 500]))))
    return todo


def _merchant_history(conn) -> dict[tuple, str]:
    """What each merchant was settled as most recently (rules, your own picks, or confident AI answers), by
    lowercased merchant and whether money came in."""
    t = Transaction
    # type_coerce: the comparison's value as the database gives it (0/1 on SQLite), as the SQL text did
    q = (select(func.lower(t.payee).label("k"), type_coerce(t.amount > 0, Integer).label("sign"), t.category)
         .where(t.needs_review == 0, t.category.is_not(None), t.category_source.in_(["manual", "rule", "ai", "history"]),
                t.payee != "")
         .order_by(t.posted, t.id))
    return {(r["k"], r["sign"]): r["category"] for r in conn.execute(q)}


def _categorize_locally(conn, tx: dict, rules: list[dict], history: dict[tuple, str], review_after: list[str]) -> str | None:
    """Categorize one transaction without the model: your rules, then the built-in guesses, then what the merchant
    was last time. Returns how it was settled ("rule", "auto" or "history"), or None if it's still open. Adds its id
    to review_after when a rule says to look at it."""
    acts = rulesmod.actions_for(tx, rules)
    if acts["rename"]:   # before the history lookup, which goes by merchant
        rulesmod.apply_actions(conn, tx, {"rename": acts["rename"]})
    if acts["review"]:
        review_after.append(tx["id"])
    # Your rules come before the built-in guesses: a rule saying an "autopay" is Utilities wins.
    if acts["split"]:
        if rulesmod.apply_actions(conn, tx, {"split": acts["split"]}):
            return "rule"
        if abs(tx["amount"] or 0) >= 0.005:
            review_after.append(tx["id"])   # the rule couldn't split it: categorize it as usual, and ask
    cat, source = acts["category"], "rule"
    if not cat:
        cat, source = heuristic_category(tx, tx["kind"]), "auto"
    if not cat and tx.get("payee"):
        cat, source = history.get((tx["payee"].lower(), int(tx["amount"] > 0))), "history"
    if not cat:
        return None
    conn.execute(update(Transaction).where(Transaction.id == tx["id"]).values(
        category=cat, category_source=source, confidence=1, needs_review=0))
    return source


def _categorize_with_ai(conn, leftover: list[dict], caller, counts: dict[str, int]) -> None:
    """Ask the model about what's left, one question per merchant, and save its answers (counted in counts)."""
    groups = group_by_merchant(leftover)
    conn.commit()  # don't hold the database while waiting on the model
    try:
        answers = ask_model(conn, groups, caller)
    except RuntimeError:  # error is recorded; these wait in Review
        answers = [(None, 0.0)] * len(groups)
    # Categories that take money out of spending (transfers, card payments, Ignore): the model only ever
    # suggests them. Its input includes text the other side of a payment writes (an ACH or Zelle memo), which
    # could talk it into hiding a charge, so these always wait for you in Review.
    hides = set(conn.execute(select(Category.name).where(Category.is_transfer == 1)).scalars())
    spends_as_income = set(conn.execute(select(Category.name).where(Category.is_income == 1)).scalars())
    not_manual = func.coalesce(Transaction.category_source, "") != "manual"
    for group, (cat, conf) in zip(groups, answers, strict=True):
        for t in group:
            if cat is None:
                conn.execute(update(Transaction).where(Transaction.id == t["id"], not_manual).values(needs_review=1))
                counts["review"] += 1
                continue
            review = 1 if (conf < REVIEW_THRESHOLD or cat in hides
                           or (cat in spends_as_income and t["amount"] < 0)) else 0
            conn.execute(
                # Don't overwrite a choice you made while the model was thinking.
                update(Transaction).where(Transaction.id == t["id"], not_manual)
                .values(category=cat, category_source="ai", confidence=conf, needs_review=review))
            counts["review" if review else "ai"] += 1
    conn.commit()


def group_by_merchant(txs: list[dict]) -> list[list[dict]]:
    """One group per merchant and direction of money (a refund from a shop isn't the same as buying there)."""
    groups: dict[tuple, list[dict]] = {}
    for t in txs:
        groups.setdefault(((t["payee"] or t["description"] or "").lower(), t["amount"] > 0), []).append(t)
    return list(groups.values())


def create_proposed(conn, new: dict, is_income: bool = False) -> tuple[str, bool]:
    """Accept a category the AI proposed ({"name", "parent"}): create it unless one of that name exists by now.
    Returns its name and whether it was created. A parent that's gone, or too deep, makes it a top-level one."""
    name = " ".join(str(new.get("name") or "").split())
    if not name:
        raise ValueError("The suggested category has no name")
    existing = conn.execute(select(Category.name).where(func.lower(Category.name) == func.lower(name))).fetchone()
    if existing:
        return existing["name"], False
    parent = new.get("parent") or None
    if parent and not conn.execute(select(Category.name).where(Category.name == parent)).fetchone():
        parent = None
    try:
        catmod.add(conn, name, parent, is_income=is_income and not parent)
    except catmod.CategoryError as e:
        if parent and "levels deep" in str(e):
            catmod.add(conn, name, None)
        else:
            raise ValueError(str(e)) from e
    return name, True


def _log(conn, purpose, model, merchants, answered, new_cats, ok, seconds, message, reply):
    conn.execute(insert(AiLog).values(purpose=purpose, model=model, merchants=merchants, answered=answered, new_cats=new_cats,
                                      ok=1 if ok else 0, seconds=round(seconds, 1), message=message, reply=(reply or "")[:1500]))
    newest = select(AiLog.id).order_by(AiLog.id.desc()).limit(200).correlate(None)   # its own FROM ai_log, not the DELETE's
    conn.execute(delete(AiLog).where(AiLog.id.not_in(newest)))


def ask_model(conn, groups: list[list[dict]], caller=call_llm, allow_new: bool = False, purpose: str = "sync") -> list[tuple]:
    """Ask the model for one category per group. Doesn't write anything except the last error message.
    Call with no write transaction open: requests can take a while."""
    api_key = db.get_setting(conn, sk.OPENROUTER_API_KEY)
    empty = (None, 0.0, None) if allow_new else (None, 0.0)
    if not api_key or not groups:
        return [empty] * len(groups)
    model = db.get_setting(conn, sk.LLM_MODEL, DEFAULT_MODEL) or DEFAULT_MODEL
    categories = _category_names(conn)
    # The latest choice for each merchant (newest first), as examples for the model.
    examples, seen = [], set()
    t = Transaction
    for r in conn.execute(select(t.payee, t.category).where(t.category_source.in_(["manual", "rule"]), t.payee != "")
                          .order_by(t.posted.desc()).limit(5000)):
        if r["payee"].lower() not in seen:
            seen.add(r["payee"].lower())
            examples.append({"payee": r["payee"], "category": r["category"]})
        if len(examples) >= 60:
            break
    out: list[tuple[str | None, float]] = []
    # One agent run in Sentry's Agent Tracing (monitoring.py): its batches are the turns of one conversation.
    with monitoring.ai_agent("Transaction categorizer", purpose):
        for start in range(0, len(groups), 40):
            batch = groups[start : start + 40]
            payload = [
                {"i": i, "date": g[-1]["posted"], "amount": round(g[-1]["amount"], 2), "account_type": g[-1]["kind"],
                 "payee": g[-1]["payee"], "description": g[-1]["description"]}
                for i, g in enumerate(batch)
            ]
            began, reply = time.time(), None
            try:
                reply = caller(api_key, model, build_prompt(categories, examples, payload, _subcategory_hints(conn), allow_new))
                if extract_json_array(reply) is None:
                    snippet = " ".join((reply or "(empty reply)").split())[:160]
                    raise ValueError(f"the model ({model}) didn't answer in the expected format. It said: \"{snippet}\". "
                                     f"Free or small models often do this; try {DEFAULT_MODEL} in Settings.")
                results = parse_ai_reply(reply, categories, allow_new)
                db.set_setting(conn, sk.LAST_LLM_ERROR, None)
                answered = sum(1 for r in results.values() if r[0] or (len(r) > 2 and r[2]))
                new_cats = sum(1 for r in results.values() if len(r) > 2 and r[2])
                _log(conn, purpose, model, len(batch), answered, new_cats, True, time.time() - began,
                     f"Suggested a category for {answered} of {len(batch)} merchants" + (f", including {new_cats} new categor{'y' if new_cats == 1 else 'ies'}" if new_cats else ""),
                     reply)
            except Exception as e:  # network or API error
                db.set_setting(conn, sk.LAST_LLM_ERROR, str(e)[:300])
                _log(conn, purpose, model, len(batch), 0, 0, False, time.time() - began, str(e)[:500], reply)
                conn.commit()
                raise RuntimeError(f"The AI request failed: {e}") from e
            conn.commit()
            out += [results.get(i, empty) for i in range(len(batch))]
    return out


def suggest_for_review(conn, caller=call_llm, limit_groups: int = 120) -> list[dict]:
    """Suggestions for everything waiting in Review, one per merchant. Nothing is applied."""
    t = Transaction
    txs = db.rows(conn.execute(
        select(t, Account.kind, db.account_label_expr().label("account_name"))
        .join(Account, Account.id == t.account_id)
        .where(or_(t.needs_review == 1, t.category.is_(None)), func.coalesce(t.category_source, "") != "manual",
               Account.kind != "investment")
        .order_by(t.posted.desc())))
    groups = group_by_merchant(txs)
    groups.sort(key=lambda g: -len(g))
    groups = groups[:limit_groups]
    conn.commit()
    answers = ask_model(conn, groups, caller, allow_new=True, purpose="review")
    out = []
    for g, (cat, conf, proposal) in zip(groups, answers, strict=True):
        out.append({
            "merchant": g[0]["payee"] or g[0]["description"],
            "direction": "in" if g[0]["amount"] > 0 else "out",
            "count": len(g),
            "total": round(sum(t["amount"] for t in g), 2),
            "examples": [f"{t['posted']} · {t['description']} · {t['account_name']}" for t in g[:3]],
            "tx_ids": [t["id"] for t in g],
            "category": cat,
            "new_category": proposal,
            "confidence": round(conf, 2),
        })
    return out


def apply_to_group(conn, tx_ids: list[str], category: str, remember: bool) -> int:
    """Apply a category you confirmed to a merchant's transactions. Returns how many were updated."""
    if not tx_ids:
        return 0
    if not _known_category(conn, category):
        raise ValueError(f"Unknown category: {category}")
    set_category(conn, tx_ids[0], category, remember=remember)  # also saves the rule when remember=True
    for tid in tx_ids[1:]:
        conn.execute(update(Transaction).where(Transaction.id == tid).values(
            category=category, category_source="manual", confidence=1, needs_review=0))
    return len(tx_ids)


def _known_category(conn, name: str) -> bool:
    return conn.execute(select(Category.name).where(Category.name == name)).fetchone() is not None


def set_category(conn, tx_id: str, category: str, remember: bool = False) -> int:
    """Manually set a category. With remember=True, add a rule and apply it to matching unreviewed items.
    Returns how many other transactions the new rule updated."""
    if not _known_category(conn, category):
        raise ValueError(f"Unknown category: {category}")
    t = Transaction
    tx = conn.execute(select(t).where(t.id == tx_id)).fetchone()
    if not tx:
        raise ValueError("Transaction not found")
    conn.execute(update(t).where(t.id == tx_id).values(category=category, category_source="manual", confidence=1, needs_review=0))
    if tx["is_split"]:   # one category for the whole thing means it isn't split any more
        splits.clear(conn, tx_id)
    if not remember:
        return 0
    key = rule_key(dict(tx))
    if len(key) < 3:
        return 0
    rulesmod.remember(conn, key, category)
    cur = conn.execute(
        update(t).where(t.id != tx_id, func.coalesce(t.category_source, "") != "manual", func.coalesce(t.is_split, 0) == 0,
                        or_(t.needs_review == 1, t.category.is_(None)),
                        or_(db.instr(func.lower(t.payee), key) > 0, db.instr(func.lower(t.description), key) > 0))
        .values(category=category, category_source="rule", confidence=1, needs_review=0))
    return cur.rowcount


def rule_offer(conn, tx_id: str, category: str) -> dict | None:
    """After you pick a category: whether to offer "always use it for this merchant" (no, if the merchant's name is too
    short to make a rule from, or its plain rule already gives this category)."""
    tx = conn.execute(select(Transaction.payee, Transaction.description).where(Transaction.id == tx_id)).fetchone()
    if not tx:
        return None
    key = rule_key(dict(tx))
    if len(key) < 3:
        return None
    have = conn.execute(select(Rule.category).where(Rule.match == key, *rulesmod.plain())).fetchone()
    if have and have["category"] == category:
        return None
    return {"merchant": tx["payee"] or tx["description"], "match": key, "replaces": have["category"] if have else None}


def accept_suggestion(conn, tx_id: str) -> None:
    conn.execute(update(Transaction).where(Transaction.id == tx_id, Transaction.category.is_not(None))
                 .values(needs_review=0, category_source="manual", confidence=1))


MAX_BULK = 2000


def bulk_update(conn, tx_ids: list[str], category: str | None = None, payee: str | None = None,
                reviewed: bool = False) -> int:
    """Change many transactions at once: a category (as if you picked it for each, so a split one goes back
    together), a merchant name, and/or marking them reviewed. Returns how many transactions there were."""
    ids = list(dict.fromkeys(str(i) for i in tx_ids or []))
    if not ids:
        raise ValueError("Select some transactions first")
    if len(ids) > MAX_BULK:
        raise ValueError(f"Change at most {MAX_BULK:,} transactions at once")
    if category and not _known_category(conn, category):
        raise ValueError(f"Unknown category: {category}")
    payee = " ".join((payee or "").split())[:80] or None
    if not (category or payee or reviewed):
        raise ValueError("Choose what to change")
    found = 0
    t = Transaction
    for i in range(0, len(ids), 500):
        chunk = t.id.in_(ids[i:i + 500])
        found += conn.execute(select(func.count()).select_from(t).where(chunk)).scalar()
        if category:
            for tid in conn.execute(select(t.id).where(chunk, t.is_split == 1)).scalars():
                splits.clear(conn, tid)
            conn.execute(update(t).where(chunk).values(category=category, category_source="manual", confidence=1, needs_review=0))
        if payee:
            conn.execute(update(t).where(chunk).values(payee=payee))
        if reviewed:
            conn.execute(update(t).where(chunk).values(
                needs_review=0, category_source=case((t.category.is_(None), t.category_source), else_="manual")))
    return found
