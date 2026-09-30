"""Which institution an account belongs to, for showing its logo (from Logo.dev, by the institution's name), which
institution a name is (PATTERNS, for telling whether two accounts are the same one), and which big merchant a transaction
is from (its website, for its logo from Logo.dev: runway/merchants.py).

Accounts whose logo Runway doesn't have (no Logo.dev key, or not fetched yet) get a letter badge instead.
"""
from __future__ import annotations

import re

from sqlalchemy import select

from .models import Account, PlaidAccount, PlaidItem

# (pattern, institution): checked in order against the lowercased institution and account names. Not for logos: which
# institution a name is (plaidbank uses it to tell whether two accounts are the same one).
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
    """The institution for the first name that matches (institution names first, then the account's own)."""
    for name in names:
        text = (name or "").lower()
        if not text:
            continue
        for rx, slug in _compiled:
            if rx.search(text):
                return slug
    return None


def account_brands(conn) -> dict[str, dict]:
    """{account_id: {"src": image address or None, "initial": "C", "institution": "Chase"}} for every account.

    The logo is Logo.dev's, by the institution's name (Settings → Connections → Logo.dev), fetched by a sync and served by
    Runway like a merchant's: nothing is bundled, so a new bank needs nothing added to the app. `src` is None (a letter
    badge) without a Logo.dev key, or until the logo has been fetched. The institution's name is the connection's (Plaid)
    or the bank's own (SimpleFIN's org); the connected institutions' names are noted too, for Settings → Connections."""
    from . import merchants   # imports this module too
    out: dict[str, dict] = {}
    names: dict[str, str] = {}   # institution key -> name
    rows = conn.execute(
        select(Account.id, Account.name, Account.display_name, Account.org, PlaidItem.institution_name)
        .outerjoin(PlaidAccount, PlaidAccount.plaid_account_id == Account.plaid_account_id)
        .outerjoin(PlaidItem, PlaidItem.item_id == PlaidAccount.item_id)).fetchall()
    for r in rows:
        inst = r["institution_name"] or r["org"]
        label = inst or r["display_name"] or r["name"] or "?"
        out[r["id"]] = {"src": None, "institution": inst, "initial": (re.sub(r"[^A-Za-z0-9]", "", label)[:1] or "?").upper()}
        if inst and len(re.findall(r"[A-Za-z]", inst)) >= 3:
            names[merchants.key(inst)] = inst
    for r in conn.execute(select(PlaidItem.institution_name).where(PlaidItem.institution_name.is_not(None))):
        if len(re.findall(r"[A-Za-z]", r["institution_name"])) >= 3:
            names[merchants.key(r["institution_name"])] = r["institution_name"]
    if names and merchants.configured(conn):
        have = merchants.brand_logos(conn, list(names.items()))   # notes the ones never asked about, for the next fetch
        for r in rows:
            k = merchants.key(r["institution_name"] or r["org"])
            if k in have:
                out[r["id"]]["src"] = merchants.logo_path(k)
    return out


# (pattern, website) for big merchants, whose logo Logo.dev has by website (runway/merchants.py): checked in order
# against the lowercased payee, then the bank's description, so the more specific names come first ("uber eats" before
# "uber"). Only big, unambiguous names: a wrong logo is worse than a letter. Merchants Plaid names come with their
# own website and don't need to be here.
MERCHANT_PATTERNS = [
    (r"prime ?video", "primevideo.com"),
    (r"\baudible\b", "audible.com"),
    (r"amazon|\bamzn\b", "amazon.com"),
    (r"\btarget\b", "target.com"),
    (r"wal-?mart|\bwm supercenter\b", "walmart.com"),
    (r"\bcostco\b", "costco.com"),
    (r"\betsy\b", "etsy.com"),
    (r"\bebay\b", "ebay.com"),
    (r"\bnike\b", "nike.com"),
    (r"\bold navy\b", "oldnavy.com"),
    (r"\bkohl'?s\b", "kohls.com"),
    (r"lululemon", "lululemon.com"),
    (r"american eagle|\bae outfitters\b", "ae.com"),
    (r"aliexpress", "aliexpress.com"),
    (r"\btemu\b", "temu.com"),
    (r"newegg", "newegg.com"),
    (r"micro ?center", "microcenter.com"),
    (r"\bikea\b", "ikea.com"),
    (r"\bmacy'?s\b", "macys.com"),
    (r"\bh ?& ?m\b|\bhm\.com\b", "hm.com"),
    (r"\bzara\b", "zara.com"),
    (r"uniqlo", "uniqlo.com"),
    (r"\badidas\b", "adidas.com"),
    (r"\blidl\b", "lidl.com"),
    (r"instacart", "instacart.com"),
    (r"uber ?eats", "ubereats.com"),
    (r"\buber\b", "uber.com"),
    (r"\blyft\b", "lyft.com"),
    (r"doordash", "doordash.com"),
    (r"starbucks", "starbucks.com"),
    (r"mcdonald'?s", "mcdonalds.com"),
    (r"burger king", "bk.com"),
    (r"\bkfc\b", "kfc.com"),
    (r"taco bell", "tacobell.com"),
    (r"youtube", "youtube.com"),
    (r"google ?fi\b", "fi.google.com"),
    (r"^google\b|\bgoogle \*", "google.com"),
    (r"apple\.com|\bapple (store|music|tv|one|arcade|services)\b|^apple\b", "apple.com"),
    (r"netflix", "netflix.com"),
    (r"spotify", "spotify.com"),
    (r"\bhulu\b", "hulu.com"),
    (r"disney ?(plus|\+)", "disneyplus.com"),
    (r"hbo ?max|\bmax\.com\b", "max.com"),
    (r"paramount", "paramountplus.com"),
    (r"\bpeacock\b", "peacocktv.com"),
    (r"steampowered|steam games|^steam\b", "steampowered.com"),
    (r"playstation|sony interactive", "playstation.com"),
    (r"\bxbox\b", "xbox.com"),
    (r"nintendo", "nintendo.com"),
    (r"microsoft|\bmsft\b", "microsoft.com"),
    (r"chatgpt", "chatgpt.com"),
    (r"openai", "openai.com"),
    (r"claude\.ai|^claude\b", "claude.ai"),
    (r"anthropic", "anthropic.com"),
    (r"github", "github.com"),
    (r"1password", "1password.com"),
    (r"dropbox", "dropbox.com"),
    (r"\bnotion\b", "notion.so"),
    (r"patreon", "patreon.com"),
    (r"\badobe\b", "adobe.com"),
    (r"duolingo", "duolingo.com"),
    (r"humble ?bundle", "humblebundle.com"),
    (r"kickstarter", "kickstarter.com"),
    (r"\bstrava\b", "strava.com"),
    (r"\bpeloton\b", "onepeloton.com"),
    (r"new york times|nytimes", "nytimes.com"),
    (r"fandango", "fandango.com"),
    (r"^ring\b|\bring\.com\b", "ring.com"),
    (r"delta air|\bdelta\.com\b", "delta.com"),
    (r"american airlines|\baa\.com\b", "aa.com"),
    (r"united airlines|\bunited\.com\b", "united.com"),
    (r"southwest", "southwest.com"),
    (r"jetblue", "jetblue.com"),
    (r"alaska air", "alaskaair.com"),
    (r"spirit airlines|\bspirit air\b", "spirit.com"),
    (r"airbnb", "airbnb.com"),
    (r"marriott", "marriott.com"),
    (r"\bhilton\b", "hilton.com"),
    (r"expedia", "expedia.com"),
    (r"booking\.com", "booking.com"),
    (r"\btesla\b", "tesla.com"),
    (r"^shell\b|\bshell (oil|service)", "shell.com"),
    (r"t-?mobile", "t-mobile.com"),
    (r"verizon", "verizon.com"),
    (r"\bat ?& ?t\b", "att.com"),
    (r"xfinity|comcast", "xfinity.com"),
    (r"mint mobile", "mintmobile.com"),
    (r"progressive", "progressive.com"),
    (r"paypal", "paypal.com"),
    (r"\bvenmo\b", "venmo.com"),
    (r"\bzelle\b", "zellepay.com"),
    (r"cash ?app", "cash.app"),
    (r"coinbase", "coinbase.com"),
    (r"\busps\b", "usps.com"),
    (r"^ups\b|\bups store\b", "ups.com"),
    (r"fedex", "fedex.com"),
    (r"\bdhl\b", "dhl.com"),
]
_merchants = [(re.compile(p), site) for p, site in MERCHANT_PATTERNS]


def merchant(*names: str | None) -> str | None:
    """The website of the big merchant the first name (payee, then description) names, if any."""
    for name in names:
        text = " ".join((name or "").lower().split())
        if not text:
            continue
        for rx, site in _merchants:
            if rx.search(text):
                return site
    return None
