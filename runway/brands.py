"""Which institution an account belongs to, for showing its logo (runway/static/banks/<slug>.svg).

Matched from the institution's name (SimpleFIN's org, or the Plaid connection's institution) and, failing that, the
account's own name ("Venture X" is Capital One). Accounts without a match get a letter badge instead.
"""
from __future__ import annotations

import re

# (pattern, logo): checked in order against the lowercased institution and account names.
PATTERNS = [
    (r"\bchase\b|sapphire|\bcs[pr]\b|freedom (flex|unlimited)|\bjpmorgan", "chase"),
    (r"capital ?one|venture ?x?\b|quicksilver|savor", "capital-one"),
    (r"\bciti(bank|group)?\b|double cash|custom cash|costco anywhere", "citibank"),
    (r"american express|\bamex\b|blue cash|platinum card|gold card|delta skymiles", "american-express"),
    (r"\bdiscover\b", "discover-card"),
    (r"bank of america|\bbofa\b|merrill", "bank-of-america"),
    (r"wells ?fargo", "wells-fargo"),
    (r"\bu\.? ?s\.? bank\b|\busbank\b", "u-s-bank"),
    (r"\bm ?& ?t bank\b|\bm&t\b", "m-t-bank"),
    (r"navy federal", "navy-federal-credit-union"),
    (r"\busaa\b", "usaa"),
    (r"fidelity", "fidelity"),
    (r"vanguard", "vanguard"),
    (r"schwab", "charles-schwab"),
    (r"e\*? ?trade", "e-trade"),
    (r"interactive brokers|\bibkr\b", "interactive-brokers"),
    (r"robinhood", "robinhood"),
    (r"\bsofi\b", "sofi"),
    (r"paypal", "paypal"),
    (r"apple (card|cash|savings)", "apple"),
    (r"amazon", "amazon"),
    (r"target (circle|redcard)|\btarget\b", "target"),
]
_compiled = [(re.compile(p), slug) for p, slug in PATTERNS]


def brand(*names: str | None) -> str | None:
    """The logo for the first name that matches (institution names first, then the account's own)."""
    for name in names:
        text = (name or "").lower()
        if not text:
            continue
        for rx, slug in _compiled:
            if rx.search(text):
                return slug
    return None


def account_brands(conn) -> dict[str, dict]:
    """{account_id: {"logo": slug or None, "initial": "C", "institution": "Chase"}} for every account."""
    out = {}
    rows = conn.execute(
        "SELECT a.id, a.name, a.display_name, a.org, i.institution_name FROM accounts a "
        "LEFT JOIN plaid_accounts p ON p.plaid_account_id=a.plaid_account_id "
        "LEFT JOIN plaid_items i ON i.item_id=p.item_id").fetchall()
    for r in rows:
        inst = r["institution_name"] or r["org"]
        logo = brand(r["institution_name"], r["org"], r["display_name"], r["name"])
        label = inst or r["display_name"] or r["name"] or "?"
        out[r["id"]] = {"logo": logo, "institution": inst, "initial": (re.sub(r"[^A-Za-z0-9]", "", label)[:1] or "?").upper()}
    return out
