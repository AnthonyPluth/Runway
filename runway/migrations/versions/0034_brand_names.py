"""Brands' own names for transactions synced before them: a big merchant's payee made from the bank's text becomes the
brand's name ("Amzn Mktp Us" -> "Amazon", "Wm Supercenter" -> "Walmart": brands.merchant_name), as a sync now does.
The bank's text stays in the description, and the transaction's details can put it back (Use the bank's name).

A name you gave a transaction stays: nothing records a rename, so only a payee that's the bank's own text is renamed
(every word of it in the description, payees.from_bank), never one a rule renames to, nor a merchant Plaid named.
What was made from the bank's name follows it, as in 0032: a logo you chose for it is kept for the brand's name, and so
is a recurring suggestion you dismissed. A recurring item matching it keeps its text (written out when the item matched
by its name), and is called by the brand's name if it was called by the bank's. It also matches the brand's name only
when the bank's name isn't in those transactions' own text, where it goes on matching them: otherwise "amazon" would
take in every Amazon order. Rules made from the bank's name match the brand's on their own (rules._brand_matches).

Revision ID: 0034
Revises: 0033
"""
import json
import re

import sqlalchemy as sa
from alembic import op

revision = '0034'
down_revision = '0033'
branch_labels = None
depends_on = None


# runway/brands.py's MERCHANT_PATTERNS (pattern, name) and merchant_name as they were when this migration was written,
# frozen here so 0034 always does the same thing (brands named later apply to new syncs, not to this one-time pass).
_PATTERNS = [
    (r"prime ?video", "Prime Video"),
    (r"\baudible\b", "Audible"),
    (r"\b(amazon|amzn) prime\b", "Amazon Prime"),
    (r"\bamazon fresh\b", "Amazon Fresh"),
    (r"\b(amazon|amzn) music\b", "Amazon Music"),
    (r"amazon web services|\baws\.amazon", "Amazon Web Services"),
    (r"amazon|\bamzn\b", "Amazon"),
    (r"\btarget\b", "Target"),
    (r"wal-?mart|\bwm supercenter\b", "Walmart"),
    (r"\bcostco\b", "Costco"),
    (r"\betsy\b", "Etsy"),
    (r"\bebay\b", "eBay"),
    (r"\bnike\b", "Nike"),
    (r"\bold navy\b", "Old Navy"),
    (r"\bkohl'?s\b", "Kohl’s"),
    (r"lululemon", "Lululemon"),
    (r"american eagle|\bae outfitters\b", None),
    (r"aliexpress", "AliExpress"),
    (r"\btemu\b", "Temu"),
    (r"newegg", "Newegg"),
    (r"micro ?center", "Micro Center"),
    (r"\bikea\b", "IKEA"),
    (r"\bmacy'?s\b", "Macy’s"),
    (r"\bh ?& ?m\b|\bhm\.com\b", None),
    (r"\bzara\b", None),
    (r"uniqlo", "Uniqlo"),
    (r"\badidas\b", "Adidas"),
    (r"\blidl\b", "Lidl"),
    (r"instacart", None),
    (r"uber ?eats", "Uber Eats"),
    (r"\buber ?one\b", "Uber One"),
    (r"\buber ?pass\b", None),
    (r"\buber\b", "Uber"),
    (r"\blyft\b", "Lyft"),
    (r"doordash", None),
    (r"starbucks", "Starbucks"),
    (r"mcdonald'?s", "McDonald’s"),
    (r"burger king", "Burger King"),
    (r"\bkfc\b", "KFC"),
    (r"taco bell", "Taco Bell"),
    (r"youtube", None),
    (r"google ?fi\b", "Google Fi"),
    (r"^google\b|\bgoogle \*", None),
    (r"^apple\.com\b|^apple (store|services)\b", "Apple"),
    (r"apple\.com|\bapple (store|music|tv|one|arcade|services)\b|^apple\b", None),
    (r"netflix", "Netflix"),
    (r"spotify", "Spotify"),
    (r"\bhulu\b", "Hulu"),
    (r"disney ?(plus|\+)", "Disney+"),
    (r"hbo ?max|\bmax\.com\b", None),
    (r"paramount", None),
    (r"\bpeacock\b", None),
    (r"steampowered|steam games|^steam\b", None),
    (r"playstation|sony interactive", "PlayStation"),
    (r"\bxbox\b", "Xbox"),
    (r"nintendo", "Nintendo"),
    (r"microsoft|\bmsft\b", "Microsoft"),
    (r"chatgpt", "ChatGPT"),
    (r"openai", "OpenAI"),
    (r"claude\.ai|^claude\b", None),
    (r"anthropic", "Anthropic"),
    (r"github", "GitHub"),
    (r"1password", "1Password"),
    (r"dropbox", "Dropbox"),
    (r"\bnotion\b", None),
    (r"patreon", None),
    (r"\badobe\b", "Adobe"),
    (r"duolingo", "Duolingo"),
    (r"humble ?bundle", "Humble Bundle"),
    (r"kickstarter", None),
    (r"\bstrava\b", "Strava"),
    (r"\bpeloton\b", "Peloton"),
    (r"new york times|nytimes", "New York Times"),
    (r"fandango", "Fandango"),
    (r"^ring\b|\bring\.com\b", None),
    (r"delta air( ?lines)?\b|\bdelta\.com\b", "Delta Air Lines"),
    (r"american airlines|\baa\.com\b", "American Airlines"),
    (r"united airlines|\bunited\.com\b", "United Airlines"),
    (r"southwest", None),
    (r"jetblue", "JetBlue"),
    (r"alaska air(lines)?\b", "Alaska Airlines"),
    (r"spirit airlines|\bspirit air\b", "Spirit Airlines"),
    (r"airbnb", "Airbnb"),
    (r"marriott", None),
    (r"\bhilton\b", None),
    (r"expedia", "Expedia"),
    (r"booking\.com", "Booking.com"),
    (r"\btesla\b", None),
    (r"^shell\b|\bshell (oil|service)", None),
    (r"t-?mobile", "T-Mobile"),
    (r"verizon", "Verizon"),
    (r"\bat ?& ?t\b", "AT&T"),
    (r"xfinity", "Xfinity"),
    (r"comcast", None),
    (r"mint mobile", "Mint Mobile"),
    (r"progressive", None),
    (r"paypal", None),
    (r"\bvenmo\b", None),
    (r"\bzelle\b", None),
    (r"cash ?app", None),
    (r"coinbase", None),
    (r"\busps\b", "USPS"),
    (r"^ups\b|\bups store\b", None),
    (r"fedex", "FedEx"),
    (r"\bdhl\b", None),
]
_compiled = [(re.compile(p), name) for p, name in _PATTERNS]
_NOT_A_PURCHASE = re.compile(
    r"\b(pay|paymnts?|payments?|pymts?|pmts?|autopay|e-?pay|syf|synchrony|card|cash|transfer|xfer|payroll|salary|"
    r"deposit|dir dep|refund|gas|fuel|pharmacy|rx|optical|liquor|car ?wash|tire|auto|travel|insurance|bank)\b")


def merchant_name(payee: str | None) -> str | None:
    text = " ".join((payee or "").lower().split())
    if not text or _NOT_A_PURCHASE.search(text):
        return None
    for rx, name in _compiled:
        m = rx.search(text)
        if m:
            after = text[m.end():m.end() + 1]
            return name if name and m.start() == 0 and not after.isalnum() else None
    return None


# The payee a sync gives the bank's text without a brand's name (categorize.bank_payee: tidy_payee, then
# payees.shorten), frozen as they were when this was written. A payee is renamed only when it's exactly that, as a
# sync only gives the brand's name then (simplefin._store_transaction): a name a provider gave of its own ("Walmart
# Supercenter" for "WAL-MART SUPERCENTER #1234 DALLAS TX") stays, on old rows and new ones alike.
_PREFIX = re.compile(
    r"^(?:tst\s*\*|sq\s*\*|dd\s*\*|py\s*\*|sp\s*\*|pp\s*\*|paypal\s*\*|toast\s*\*|clover\s*\*|"
    r"pos\s+(?:purchase\s+)?|debit card purchase\s+|direct debit\s+|ach debit\s+|ach credit\s+|"
    r"checkcard\s+\d*\s*|purchase authorized on\s+\d+/\d+\s+)",
    re.I,
)
_KEEP_SECOND = {"eats", "prime", "music", "one", "pass", "plus"}
_BRAND_STAR = re.compile(r"^([A-Za-z][A-Za-z0-9&'.]{1,20})\s*\*\s*\S")
_TRAILING_NOISE = re.compile(
    r"(\s+-\s+\d.*$|\s+#+\s*\d+.*$|\s*##\d+.*$|\s+\d{3,}.*$|\s+\(cash\)$|\s+x{3,}\d*.*$|\s+-\s*$)", re.I
)


def _tidy(raw: str | None) -> str:
    if not raw:
        return ""
    s = re.sub(r"\s+", " ", raw).strip()
    for _ in range(2):
        s = _PREFIX.sub("", s).strip()
    m = _BRAND_STAR.match(s)
    if m:
        nxt = s[m.end() - 1 :].split(" ")[0].lower()
        s = f"{m.group(1)} {nxt}" if nxt in _KEEP_SECOND else m.group(1)
    s = re.sub(r"\s*\*\s*[A-Za-z0-9]{5,}$", "", s)
    s = s.replace("*", " ")
    prev = None
    while prev != s:
        prev = s
        s = _TRAILING_NOISE.sub("", s).strip(" -")
    s = re.sub(r"\s+", " ", s).strip()
    if not s:
        s = re.sub(r"\s+", " ", raw).strip()
    return " ".join(w[:1].upper() + w[1:].lower() if w.isupper() or w.islower() else w for w in s.split(" "))


_LEAD = re.compile(r"^(?:direct deposit|direct dep|dir dep)\s+", re.I)
_MARKER = re.compile(r"(?:c|debit|credit)?ach|ppd|ccd|baweb|cpurchase")
_TAIL_WORDS = {"cash", "tran", "trans", "transfer", "xfer", "pay", "pmt", "pymt", "payment", "debit", "credit", "web",
               "purchase", "deposit", "dep", "withdrawal", "wd", "bill", "online", "id"}
_PAY_WORDS = {"pay", "pmt", "pymt", "payment"}


def _plain(word: str) -> str:
    return re.sub(r"[^a-z0-9]", "", word.lower())


def _bank_tail(words: list[str]) -> bool:
    for i, w in enumerate(words):
        nxt = words[i + 1] if i + 1 < len(words) else ""
        if _MARKER.fullmatch(w) or (w == "web" and nxt in _PAY_WORDS) or (len(w) == 1 and w.isalpha() and nxt in _TAIL_WORDS):
            return True
    return False


def _noise(w: str) -> bool:
    return bool(_MARKER.fullmatch(w)) or w in _TAIL_WORDS or (len(w) == 1 and w.isalpha())


def _shorten(name: str) -> str:
    s = " ".join((name or "").split())
    words = _LEAD.sub("", s).split(" ")
    plain = [_plain(w) for w in words]
    cut = len(words)
    while cut > 1 and _noise(plain[cut - 1]):
        cut -= 1
    if cut == len(words) or not _bank_tail(plain[cut:]):
        return s
    return " ".join(words[:cut])


def from_bank(payee: str | None, description: str | None) -> bool:
    return bool(description) and _key(payee or "") == _key(_shorten(_tidy(description)))


def _key(s: str) -> str:
    return " ".join((s or "").lower().split())


def upgrade() -> None:
    bind = op.get_bind()
    renames = {_key(r) for (r,) in bind.execute(sa.text("SELECT rename FROM rules WHERE rename IS NOT NULL"))}
    names: dict[str, str] = {}         # the bank's payee (as a key) -> the brand's name
    in_text: dict[str, bool] = {}      # ... and whether it's in the own text of every transaction renamed from it
    for payee, desc in bind.execute(sa.text(
            "SELECT DISTINCT payee, description FROM transactions WHERE payee IS NOT NULL AND merchant_id IS NULL")).fetchall():
        new = merchant_name(payee)
        if not new or _key(new) == _key(payee) or _key(payee) in renames or not from_bank(payee, desc):
            continue
        bind.execute(sa.text("UPDATE transactions SET payee = :new WHERE payee = :old AND description = :desc AND merchant_id IS NULL"),
                     {"new": new, "old": payee, "desc": desc})
        names[_key(payee)] = new
        in_text[_key(payee)] = in_text.get(_key(payee), True) and _key(payee) in _key(desc)
    if not names:
        return
    for rid, name, match in bind.execute(sa.text('SELECT id, name, "match" FROM recurring')).fetchall():
        texts = [m for m in dict.fromkeys(_key(line) for line in (match or name or "").splitlines()) if m]
        more = [names[m].lower() for m in texts if m in names and not in_text[m] and names[m].lower() not in texts]
        renamed = names.get(_key(name)) if name else None
        if more or renamed:
            bind.execute(sa.text('UPDATE recurring SET "match" = :match, name = :name WHERE id = :id'),
                         {"match": "\n".join(dict.fromkeys(texts + more)) or None, "name": renamed or name, "id": rid})
    chosen = {k: (w, h) for k, w, h in bind.execute(sa.text("SELECT key, website, hidden FROM merchant_logos")).fetchall()}
    for old, new in names.items():
        if old in chosen and _key(new) not in chosen:
            bind.execute(sa.text("INSERT INTO merchant_logos(key, website, hidden) VALUES (:key, :website, :hidden)"),
                         {"key": _key(new), "website": chosen[old][0], "hidden": chosen[old][1]})
            chosen[_key(new)] = chosen[old]
    row = bind.execute(sa.text("SELECT value FROM settings WHERE key = 'recurring_suggestions_dismissed'")).fetchone()
    try:
        dismissed = json.loads(row[0] or "[]") if row else []
    except ValueError:
        return
    if not isinstance(dismissed, list):
        return
    more = []
    for k in dismissed:   # forecast.suggestion_key: account|payee|frequency
        parts = k.split("|") if isinstance(k, str) else []
        if len(parts) >= 3 and parts[-2] in names:
            more.append("|".join([*parts[:-2], names[parts[-2]].lower(), parts[-1]]))
    if more:
        bind.execute(sa.text("UPDATE settings SET value = :value WHERE key = 'recurring_suggestions_dismissed'"),
                     {"value": json.dumps(sorted({k for k in dismissed if isinstance(k, str)} | set(more)))})


def downgrade() -> None:
    pass   # the bank's names are still in each transaction's description
