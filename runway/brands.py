"""Which institution an account belongs to, for showing its logo (runway/static/banks/<slug>.svg), and which big
merchant a transaction is from, for showing theirs (runway/static/merchants/<slug>.svg) when Plaid has none.

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


# (pattern, logo) for merchants: checked in order against the lowercased payee, then the bank's description, so the
# more specific names come first ("uber eats" before "uber"). Only big, unambiguous names: a wrong logo is worse than
# a letter.
MERCHANT_PATTERNS = [
    (r"prime ?video", "amazon-prime-video"),
    (r"\baudible\b", "audible"),
    (r"amazon web services|\baws\b", "amazon-web-services"),
    (r"amazon|\bamzn\b", "amazon"),
    (r"\btarget\b", "target"),
    (r"wal-?mart|\bwm supercenter\b", "walmart"),
    (r"\bcostco\b", "costco"),
    (r"\betsy\b", "etsy"),
    (r"\bebay\b", "ebay"),
    (r"\bnike\b", "nike"),
    (r"\bold navy\b", "old-navy"),
    (r"\bkohl'?s\b", "kohls"),
    (r"lululemon", "lululemon"),
    (r"american eagle|\bae outfitters\b", "american-eagle"),
    (r"aliexpress", "aliexpress"),
    (r"\btemu\b", "temu"),
    (r"newegg", "newegg"),
    (r"micro ?center", "micro-center"),
    (r"\bikea\b", "ikea"),
    (r"\bmacy'?s\b", "macys"),
    (r"\bh ?& ?m\b|\bhm\.com\b", "h-and-m"),
    (r"\bzara\b", "zara"),
    (r"uniqlo", "uniqlo"),
    (r"\badidas\b", "adidas"),
    (r"\blidl\b", "lidl"),
    (r"instacart", "instacart"),
    (r"uber ?eats", "uber-eats"),
    (r"\buber\b", "uber"),
    (r"\blyft\b", "lyft"),
    (r"doordash", "doordash"),
    (r"starbucks", "starbucks"),
    (r"mcdonald'?s", "mcdonalds"),
    (r"burger king", "burger-king"),
    (r"\bkfc\b", "kfc"),
    (r"taco bell", "taco-bell"),
    (r"youtube", "youtube"),
    (r"google ?fi\b", "google-fi"),
    (r"^google\b|\bgoogle \*", "google"),
    (r"apple\.com|\bapple (store|music|tv|one|arcade|services)\b|^apple\b", "apple"),
    (r"netflix", "netflix"),
    (r"spotify", "spotify"),
    (r"\bhulu\b", "hulu"),
    (r"disney ?(plus|\+)", "disney-plus"),
    (r"hbo ?max|\bmax\.com\b", "hbo-max"),
    (r"paramount", "paramount-plus"),
    (r"\bpeacock\b", "peacock"),
    (r"steampowered|steam games|^steam\b", "steam"),
    (r"playstation|sony interactive", "playstation"),
    (r"\bxbox\b", "xbox"),
    (r"nintendo", "nintendo"),
    (r"microsoft|\bmsft\b", "microsoft"),
    (r"chatgpt", "chatgpt"),
    (r"openai", "openai"),
    (r"claude\.ai|^claude\b", "claude"),
    (r"anthropic", "anthropic"),
    (r"github", "github"),
    (r"1password", "1password"),
    (r"dropbox", "dropbox"),
    (r"\bnotion\b", "notion"),
    (r"patreon", "patreon"),
    (r"\badobe\b", "adobe"),
    (r"duolingo", "duolingo"),
    (r"humble ?bundle", "humble-bundle"),
    (r"kickstarter", "kickstarter"),
    (r"\bstrava\b", "strava"),
    (r"\bpeloton\b", "peloton"),
    (r"new york times|nytimes", "the-new-york-times"),
    (r"fandango", "fandango"),
    (r"^ring\b|\bring\.com\b", "ring"),
    (r"delta air|\bdelta\.com\b", "delta-air-lines"),
    (r"american airlines|\baa\.com\b", "american-airlines"),
    (r"united airlines|\bunited\.com\b", "united-airlines"),
    (r"southwest", "southwest-airlines"),
    (r"jetblue", "jetblue-airways"),
    (r"alaska air", "alaska-airlines"),
    (r"spirit airlines|\bspirit air\b", "spirit-airlines"),
    (r"airbnb", "airbnb"),
    (r"marriott", "marriott"),
    (r"\bhilton\b", "hilton"),
    (r"expedia", "expedia"),
    (r"booking\.com", "booking-com"),
    (r"\btesla\b", "tesla"),
    (r"^shell\b|\bshell (oil|service)", "shell"),
    (r"t-?mobile", "t-mobile"),
    (r"verizon", "verizon"),
    (r"\bat ?& ?t\b", "at-t"),
    (r"xfinity|comcast", "xfinity"),
    (r"mint mobile", "mint-mobile"),
    (r"progressive", "progressive"),
    (r"paypal", "paypal"),
    (r"\bvenmo\b", "venmo"),
    (r"\bzelle\b", "zelle"),
    (r"cash ?app", "cash-app"),
    (r"coinbase", "coinbase"),
    (r"\busps\b", "usps"),
    (r"^ups\b|\bups store\b", "ups"),
    (r"fedex", "fedex"),
    (r"\bdhl\b", "dhl"),
]
_merchants = [(re.compile(p), slug) for p, slug in MERCHANT_PATTERNS]


def merchant(*names: str | None) -> str | None:
    """The bundled logo for the first name (payee, then description) that names a big merchant."""
    for name in names:
        text = " ".join((name or "").lower().split())
        if not text:
            continue
        for rx, slug in _merchants:
            if rx.search(text):
                return slug
    return None
