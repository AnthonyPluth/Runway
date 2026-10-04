"""Which institution an account belongs to, for showing its logo (from Logo.dev, by the institution's name), which
institution a name is (PATTERNS, for telling whether two accounts are the same one), and which big merchant a transaction
is from (its website, for its logo from Logo.dev: runway/merchants.py, and the brand's own name for its payee:
merchant_name).

Accounts whose logo Runway doesn't have (no Logo.dev key, or not fetched yet), or that you chose no logo for, get a
letter badge instead.
"""
from __future__ import annotations

import functools
import re

from sqlalchemy import select

from .models import Account, PlaidAccount, PlaidItem, User

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


def _compact(name: str | None) -> str:
    """"E*TRADE from Morgan Stanley" and "E*Trade" → comparable keys ("etradefrommorganstanley", "etrade")."""
    return re.sub(r"[^a-z0-9]", "", re.sub(r"\b(financial|investments?|securities|bank|inc|llc)\b", "", (name or "").lower()))


def institution_match(ours: tuple[str | None, ...], theirs: tuple[str | None, ...]) -> bool | None:
    """Whether two accounts are at the same institution, each given as its names (the institution's first, then the
    account's own, as brand() takes them). By brand when both have a known one: True or False. Otherwise by the
    institution names, when one is in the other ("E*Trade" in "E*TRADE from Morgan Stanley"): True; else None, can't
    tell (a name that isn't in the other doesn't make it another institution)."""
    a, b = brand(*ours), brand(*theirs)
    if a and b:
        return a == b
    x, y = _compact(ours[0] if ours else None), _compact(theirs[0] if theirs else None)
    return True if len(x) >= 4 and len(y) >= 4 and (x in y or y in x) else None


def same_institution(a: str | None, b: str | None) -> bool:
    """Whether two institution names are the same institution (institution_match), "can't tell" counting as no."""
    return institution_match((a,), (b,)) is True


# Each institution's website, for its logo: Logo.dev has a bank's logo by website for sure, where a lookup by name
# ("Chase Bank Sam", "Citibank Online") may find nothing, or not clearly that bank.
SITES = {
    "chase": "chase.com", "capital-one": "capitalone.com", "citibank": "citi.com", "american-express": "americanexpress.com",
    "discover-card": "discover.com", "bank-of-america": "bankofamerica.com", "wells-fargo": "wellsfargo.com",
    "u-s-bank": "usbank.com", "m-t-bank": "mtb.com", "navy-federal-credit-union": "navyfederal.org", "usaa": "usaa.com",
    "fidelity": "fidelity.com", "vanguard": "vanguard.com", "charles-schwab": "schwab.com", "e-trade": "etrade.com",
    "interactive-brokers": "interactivebrokers.com", "robinhood": "robinhood.com", "sofi": "sofi.com",
    "paypal": "paypal.com", "apple": "apple.com", "amazon": "amazon.com", "target": "target.com",
}
NO_LOGO = "none"   # accounts.logo: you chose a letter instead of a logo


def institution(name: str | None, owners) -> str | None:
    """The institution's name without the owner's that a bank connection adds to it ("Chase Bank Sam" -> "Chase Bank")."""
    words = (name or "").split()
    drop = {o.lower() for o in owners if o}
    while len(words) > 1 and words[-1].lower() in drop:
        words.pop()
    return " ".join(words) or None


def connection_logos(conn) -> dict[str, str | None]:
    """{institution name: its logo's address, or None for a letter} for every bank connection (Plaid's), for Settings →
    Connections: by website for the banks Runway knows, else by name, like an account's."""
    from . import merchants   # imports this module too
    names = sorted({r["institution_name"] for r in conn.execute(
        select(PlaidItem.institution_name).where(PlaidItem.institution_name.is_not(None)))})
    out: dict[str, str | None] = dict.fromkeys(names)
    if not merchants.configured(conn):
        return out
    sites = {n: SITES[b] for n in names if (b := brand(n)) in SITES}
    by_name = {merchants.key(n): n for n in names if n not in sites and len(re.findall(r"[A-Za-z]", n)) >= 3}
    have_sites = merchants.site_logos(conn, sites.values())
    have_names = merchants.brand_logos(conn, list(by_name.items()))
    for n in names:
        s = sites.get(n)
        out[n] = merchants.site_path(s) if s in have_sites else merchants.logo_path(merchants.key(n)) if not s and merchants.key(n) in have_names else None
    return out


def account_brands(conn) -> dict[str, dict]:
    """{account_id: {"src": image address or None, "initial": "C", "institution": "Chase", "auto": address or None}}
    for every account.

    The logo is the one you chose for the account (a website's, or none: Settings → Accounts), else its institution's,
    from Logo.dev: by website for the banks Runway knows (SITES), else by the institution's name. It's fetched by a sync
    (or when you choose it) and served by Runway like a merchant's. `src` is None (a letter badge) without a Logo.dev
    key, or until the logo has been fetched; `auto` is the institution's, which a choice replaces. The institution's
    name is the connection's (Plaid) or the bank's own (SimpleFIN's org, without the owner's name)."""
    from . import merchants   # imports this module too
    out: dict[str, dict] = {}
    names: dict[str, str] = {}   # institution key -> name
    sites: dict[str, str] = {}   # account id -> its institution's website
    rows = conn.execute(
        select(Account.id, Account.name, Account.display_name, Account.org, Account.logo, PlaidItem.institution_name)
        .outerjoin(PlaidAccount, PlaidAccount.plaid_account_id == Account.plaid_account_id)
        .outerjoin(PlaidItem, PlaidItem.item_id == PlaidAccount.item_id)).fetchall()
    owners = {r["owner"] for r in conn.execute(select(Account.owner).distinct().where(Account.owner.is_not(None)))}
    owners |= {r["first_name"] for r in conn.execute(select(User.first_name).where(User.first_name.is_not(None)))}
    insts: dict[str, str | None] = {}
    for r in rows:
        inst = insts[r["id"]] = institution(r["institution_name"] or r["org"], owners)
        label = inst or r["display_name"] or r["name"] or "?"
        out[r["id"]] = {"src": None, "auto": None, "institution": inst,
                        "initial": (re.sub(r"[^A-Za-z0-9]", "", label)[:1] or "?").upper()}
        # the account's own name only says which bank it is when the bank doesn't ("Venture X" is Capital One)
        s = SITES.get((brand(inst) if inst else brand(r["display_name"], r["name"])) or "")
        if s:
            sites[r["id"]] = s
        elif inst and len(re.findall(r"[A-Za-z]", inst)) >= 3:
            names[merchants.key(inst)] = inst
    if not merchants.configured(conn):
        return out
    chosen = {r["id"]: r["logo"] for r in rows if r["logo"] and r["logo"] != NO_LOGO}
    have_sites = merchants.site_logos(conn, [*sites.values(), *chosen.values()])   # notes the ones never asked about
    have_names = merchants.brand_logos(conn, list(names.items()))                  # ... for the next fetch
    for r in rows:
        o = out[r["id"]]
        s, k = sites.get(r["id"]), merchants.key(insts[r["id"]])
        o["auto"] = merchants.site_path(s) if s in have_sites else merchants.logo_path(k) if not s and k in have_names else None
        pick = chosen.get(r["id"])
        o["src"] = None if r["logo"] == NO_LOGO else (merchants.site_path(pick) if pick in have_sites else None) if pick else o["auto"]
    return out


# (pattern, website, name) for big merchants, whose logo Logo.dev has by website (runway/merchants.py): checked in
# order against the lowercased payee, then the bank's description, so the more specific names come first ("uber eats"
# before "uber"). Only big, unambiguous names: a wrong logo is worse than a letter. Merchants Plaid names come with
# their own website and don't need to be here.
#
# The name, when there is one, is what a sync calls the merchant instead of the bank's text ("AMZN Mktp US*2K3" ->
# "Amazon": merchant_name). Stricter than the logo: a wrong name is worse than a long one, so only for a payee that
# starts with the brand (merchant_name), and none for a word that's also other businesses' ("Peacock", "Hilton",
# "Southwest"), a marketplace whose charges name the store ("DoorDash", "Instacart"), a payment app, or a brand
# whose products are worth telling apart and that the bank doesn't say ("Google", "YouTube", "Max"). A sub-brand
# with no name of its own here stops at its own entry ("Uber Pass" stays as it is, not "Uber").
MERCHANT_PATTERNS: list[tuple[str, str, str | None]] = [
    (r"prime ?video", "primevideo.com", "Prime Video"),
    (r"\baudible\b", "audible.com", "Audible"),
    (r"\b(amazon|amzn) prime\b", "amazon.com", "Amazon Prime"),
    (r"\bamazon fresh\b", "amazon.com", "Amazon Fresh"),
    (r"\b(amazon|amzn) music\b", "amazon.com", "Amazon Music"),
    (r"amazon web services|\baws\.amazon", "amazon.com", "Amazon Web Services"),
    (r"amazon|\bamzn\b", "amazon.com", "Amazon"),
    (r"\btarget\b", "target.com", "Target"),
    (r"wal-?mart|\bwm supercenter\b", "walmart.com", "Walmart"),
    (r"\bcostco\b", "costco.com", "Costco"),
    (r"\betsy\b", "etsy.com", "Etsy"),
    (r"\bebay\b", "ebay.com", "eBay"),
    (r"\bnike\b", "nike.com", "Nike"),
    (r"\bold navy\b", "oldnavy.com", "Old Navy"),
    (r"\bkohl'?s\b", "kohls.com", "Kohl’s"),
    (r"lululemon", "lululemon.com", "Lululemon"),
    (r"american eagle|\bae outfitters\b", "ae.com", None),
    (r"aliexpress", "aliexpress.com", "AliExpress"),
    (r"\btemu\b", "temu.com", "Temu"),
    (r"newegg", "newegg.com", "Newegg"),
    (r"micro ?center", "microcenter.com", "Micro Center"),
    (r"\bikea\b", "ikea.com", "IKEA"),
    (r"\bmacy'?s\b", "macys.com", "Macy’s"),
    (r"\bh ?& ?m\b|\bhm\.com\b", "hm.com", None),
    (r"\bzara\b", "zara.com", None),
    (r"uniqlo", "uniqlo.com", "Uniqlo"),
    (r"\badidas\b", "adidas.com", "Adidas"),
    (r"\blidl\b", "lidl.com", "Lidl"),
    (r"instacart", "instacart.com", None),
    (r"uber ?eats", "ubereats.com", "Uber Eats"),
    (r"\buber ?one\b", "uber.com", "Uber One"),
    (r"\buber ?pass\b", "uber.com", None),
    (r"\buber\b", "uber.com", "Uber"),
    (r"\blyft\b", "lyft.com", "Lyft"),
    (r"doordash", "doordash.com", None),
    (r"starbucks", "starbucks.com", "Starbucks"),
    (r"mcdonald'?s", "mcdonalds.com", "McDonald’s"),
    (r"burger king", "bk.com", "Burger King"),
    (r"\bkfc\b", "kfc.com", "KFC"),
    (r"taco bell", "tacobell.com", "Taco Bell"),
    (r"youtube", "youtube.com", None),
    (r"google ?fi\b", "fi.google.com", "Google Fi"),
    (r"^google\b|\bgoogle \*", "google.com", None),
    (r"^apple\.com\b|^apple (store|services)\b", "apple.com", "Apple"),
    (r"apple\.com|\bapple (store|music|tv|one|arcade|services)\b|^apple\b", "apple.com", None),
    (r"netflix", "netflix.com", "Netflix"),
    (r"spotify", "spotify.com", "Spotify"),
    (r"\bhulu\b", "hulu.com", "Hulu"),
    (r"disney ?(plus|\+)", "disneyplus.com", "Disney+"),
    (r"hbo ?max|\bmax\.com\b", "max.com", None),
    (r"paramount", "paramountplus.com", None),
    (r"\bpeacock\b", "peacocktv.com", None),
    (r"steampowered|steam games|^steam\b", "steampowered.com", None),
    (r"playstation|sony interactive", "playstation.com", "PlayStation"),
    (r"\bxbox\b", "xbox.com", "Xbox"),
    (r"nintendo", "nintendo.com", "Nintendo"),
    (r"microsoft|\bmsft\b", "microsoft.com", "Microsoft"),
    (r"chatgpt", "chatgpt.com", "ChatGPT"),
    (r"openai", "openai.com", "OpenAI"),
    (r"claude\.ai|^claude\b", "claude.ai", None),
    (r"anthropic", "anthropic.com", "Anthropic"),
    (r"github", "github.com", "GitHub"),
    (r"1password", "1password.com", "1Password"),
    (r"dropbox", "dropbox.com", "Dropbox"),
    (r"\bnotion\b", "notion.so", None),
    (r"patreon", "patreon.com", None),
    (r"\badobe\b", "adobe.com", "Adobe"),
    (r"duolingo", "duolingo.com", "Duolingo"),
    (r"humble ?bundle", "humblebundle.com", "Humble Bundle"),
    (r"kickstarter", "kickstarter.com", None),
    (r"\bstrava\b", "strava.com", "Strava"),
    (r"\bpeloton\b", "onepeloton.com", "Peloton"),
    (r"new york times|nytimes", "nytimes.com", "New York Times"),
    (r"fandango", "fandango.com", "Fandango"),
    (r"^ring\b|\bring\.com\b", "ring.com", None),
    (r"delta air( ?lines)?\b|\bdelta\.com\b", "delta.com", "Delta Air Lines"),
    (r"american airlines|\baa\.com\b", "aa.com", "American Airlines"),
    (r"united airlines|\bunited\.com\b", "united.com", "United Airlines"),
    (r"southwest", "southwest.com", None),
    (r"jetblue", "jetblue.com", "JetBlue"),
    (r"alaska air(lines)?\b", "alaskaair.com", "Alaska Airlines"),
    (r"spirit airlines|\bspirit air\b", "spirit.com", "Spirit Airlines"),
    (r"airbnb", "airbnb.com", "Airbnb"),
    (r"marriott", "marriott.com", None),
    (r"\bhilton\b", "hilton.com", None),
    (r"expedia", "expedia.com", "Expedia"),
    (r"booking\.com", "booking.com", "Booking.com"),
    (r"\btesla\b", "tesla.com", None),
    (r"^shell\b|\bshell (oil|service)", "shell.com", None),
    (r"t-?mobile", "t-mobile.com", "T-Mobile"),
    (r"verizon", "verizon.com", "Verizon"),
    (r"\bat ?& ?t\b", "att.com", "AT&T"),
    (r"xfinity", "xfinity.com", "Xfinity"),
    (r"comcast", "xfinity.com", None),
    (r"mint mobile", "mintmobile.com", "Mint Mobile"),
    (r"progressive", "progressive.com", None),
    (r"paypal", "paypal.com", None),
    (r"\bvenmo\b", "venmo.com", None),
    (r"\bzelle\b", "zellepay.com", None),
    (r"cash ?app", "cash.app", None),
    (r"coinbase", "coinbase.com", None),
    (r"\busps\b", "usps.com", "USPS"),
    (r"^ups\b|\bups store\b", "ups.com", None),
    (r"fedex", "fedex.com", "FedEx"),
    (r"\bdhl\b", "dhl.com", None),
]
_merchants = [(re.compile(p), site, name) for p, site, name in MERCHANT_PATTERNS]
BRAND_NAMES = {name.lower() for _p, _site, name in MERCHANT_PATTERNS if name}   # the brands' own names, lowercased
# A payee with one of these words is a payment to, money from, or a part of the business that's worth telling apart
# ("Amazon Corp Syf Paymnt" is a store card's bill, "Costco Gas" isn't groceries, "Apple Cash" isn't a purchase): it
# keeps the bank's text.
_NOT_A_PURCHASE = re.compile(
    r"\b(pay|paymnts?|payments?|pymts?|pmts?|autopay|e-?pay|syf|synchrony|card|cash|transfer|xfer|payroll|salary|"
    r"deposit|dir dep|refund|gas|fuel|pharmacy|rx|optical|liquor|car ?wash|tire|auto|travel|insurance|bank)\b")


def merchant(*names: str | None) -> str | None:
    """The website of the big merchant the first name (payee, then description) names, if any."""
    for name in names:
        text = " ".join((name or "").lower().split())
        if not text:
            continue
        for rx, site, _name in _merchants:
            if rx.search(text):
                return site
    return None


# Fund families by name, for a fund's or ETF's logo when Logo.dev has none by its ticker (most mutual funds and many ETFs:
# it knows companies' tickers). "Vanguard Total Stock Market Index" is shown with Vanguard's logo. Checked in order.
FUND_FAMILIES = [
    (r"\bvanguard\b", "vanguard.com"), (r"\bishares\b|\bblackrock\b", "ishares.com"),
    (r"\bspdr\b|state street", "ssga.com"), (r"\bfidelity\b", "fidelity.com"), (r"\bschwab\b", "schwab.com"),
    (r"\binvesco\b", "invesco.com"), (r"\bt\.? ?rowe price\b", "troweprice.com"),
    (r"american funds|capital group", "capitalgroup.com"),
    (r"\bj\.? ?p\.? ?morgan\b", "jpmorgan.com"), (r"\bpimco\b", "pimco.com"),
    (r"\bdimensional\b|\bdfa\b", "dimensional.com"), (r"\bwisdomtree\b", "wisdomtree.com"),
    (r"\bvaneck\b", "vaneck.com"), (r"\bproshares\b", "proshares.com"), (r"\bark (innovation|invest|genomic|next|fintech)", "ark-funds.com"),
    (r"\bglobal x\b", "globalxetfs.com"), (r"\bfirst trust\b", "ftportfolios.com"), (r"\bgoldman sachs\b", "gsam.com"),
    (r"\bfranklin\b|\btempleton\b", "franklintempleton.com"), (r"\bnuveen\b|\btiaa\b", "nuveen.com"),
    (r"\bjanus\b", "janushenderson.com"), (r"\bdodge ?& ?cox\b", "dodgeandcox.com"), (r"\bmfs\b", "mfs.com"),
    (r"\bpgim\b|\bprudential\b", "pgim.com"), (r"\bmorgan stanley\b", "morganstanley.com"),
    (r"\bwellington\b", "wellington.com"), (r"\bnorthern (trust|funds?)\b", "northerntrust.com"),
]
_families = [(re.compile(p), site) for p, site in FUND_FAMILIES]


def fund_family(name: str | None) -> str | None:
    """The website of the fund family a security's name names ("iShares Core S&P 500 ETF" -> ishares.com)."""
    text = " ".join((name or "").lower().split())
    for rx, site in _families:
        if rx.search(text):
            return site
    return None


@functools.lru_cache(maxsize=4096)
def merchant_name(payee: str | None) -> str | None:
    """The brand's own name for a payee made from the bank's text ("Amzn Mktp Us" -> "Amazon", "Wm Supercenter" ->
    "Walmart"), or None to keep the payee as it is. Only when the payee starts with the brand, as a whole word, and the
    first entry it matches (the most specific) has a name: "Payment To Amazon", "Amazonia Cafe", "Costco Gas" and
    "Uber Pass" get none."""
    text = " ".join((payee or "").lower().split())
    if not text or _NOT_A_PURCHASE.search(text):
        return None
    for rx, _site, name in _merchants:
        m = rx.search(text)
        if m:
            after = text[m.end():m.end() + 1]
            return name if name and m.start() == 0 and not after.isalnum() else None
    return None
