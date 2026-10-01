"""Churning: credit cards found on your accounts, as drafts to add, and an AI's suggestions for the rest.

Runway already knows a lot about each credit card account: whose it is, which bank, what it's called, what its annual
fee was and how far back its transactions go. `found` turns each one that isn't a churning card yet into a draft the
page pre-fills the add-card form with. Nothing is saved until you save the form. Everything here is generic: there is
no list of card products, benefits or bank-to-card mappings, only the issuers Churning already knows (ISSUERS) and
rules for cleaning a name up. A draft you don't want can be dismissed (and brought back).

`suggest` asks an AI model (the OpenRouter key from Settings, through categorize.chat) about a card from its bank and
name alone: family, currency, earning rates, annual fee, sign-up bonus, benefits. Card offers change often, so unless
it's switched off in Settings the request searches the web (OpenRouter's web search, billed per result) and the reply
lists the pages it came from, for you to check. It sends nothing else (no account, owner, balance or transaction), and
every part of its reply is checked against what Runway has: a currency or category that doesn't exist is dropped,
numbers are clamped, and a source has to be a web address.
"""
from __future__ import annotations

import json
import re
import time
from datetime import date
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import func, select

from . import categorize, churn_benefits, churning, db, monitoring
from . import settings_keys as sk
from .models import Account, ChurnCard, PlaidAccount, PlaidItem, Transaction

# ------------------------------------------------------------------------------------------------ the issuer

# Other ways a bank's name is written, for the issuers in churning.ISSUERS (their key and name always match too).
# Whole words only: "Citizens" is not Citi.
_ISSUER_WORDS: dict[str, tuple[str, ...]] = {
    "amex": ("american express", "americanexpress"),
    "citi": ("citibank", "citigroup"),
    "capital_one": ("capitalone",),
    "bank_of_america": ("bankofamerica", "bofa"),
    "barclays": ("barclay", "barclaycard"),
    "us_bank": ("u s bank", "usbank"),
    "wells_fargo": ("wellsfargo",),
}


def _words(text: str | None) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).split())


def _issuer_names(key: str) -> list[str]:
    """The ways an issuer's name appears, longest first."""
    names = {_words(key), _words(churning.ISSUERS[key]["name"]), *(_words(w) for w in _ISSUER_WORDS.get(key, ()))}
    return sorted((n for n in names if n), key=len, reverse=True)


def match_issuer(*texts: str | None) -> str:
    """The churning issuer a bank's name (the account's institution, the Plaid connection's) is, else 'other'."""
    for text in texts:
        w = f" {_words(text)} "
        if not w.strip():
            continue
        for key in churning.ISSUERS:
            if key != "other" and any(f" {n} " in w for n in _issuer_names(key)):
                return key
    return "other"


# ------------------------------------------------------------------------------------------------ the product

_MARKS = re.compile(r"[®™℠]")
# "(1234)", "-1234", "- 4417", "••1234", "x1234", "…1234" at the end: the card's last digits, however the bank writes them.
_MASK_PAREN = re.compile(r"\s*[(\[]\s*(?:[•*…·.]+\s*|x+(?=\d))?\d{2,}\s*[)\]]\s*$", re.I)
_MASK_TAIL = re.compile(r"[\s\-–—:]*(?:[•*…·.]+\s*|x+(?=\d))?(?<!\d)\d{3,6}\s*$", re.I)
# What isn't part of a card's name: its type and network.
# "World" is noise only as the network's tier (World Elite, World Mastercard, or last), not in a name (World of Hyatt).
_NOISE = re.compile(r"\b(?:credit\s*card|card\s*member|card|visa|master\s*card|mastercard|world\s+elite|world(?=\s+(?:master\s*card|mastercard)\b)"
                    r"|world\s*$|signature)\b", re.I)
_GENERIC = {"", "credit", "account", "credit account", "rewards", "cash", "personal", "business"}


def _strip_masks(name: str) -> str:
    prev = None
    while prev != name:
        prev = name
        name = _MASK_PAREN.sub("", name).strip()
        name = _MASK_TAIL.sub("", name).strip()
    return name


def clean_product(name: str | None, issuer: str = "other", display_name: str | None = None) -> str:
    """A card's name as a bank's account name gives it, cleaned of what isn't the product: the masked digits, ®™℠,
    the network (Visa, Mastercard, World Elite, Signature), "Card", and the issuer's own name. An account named only
    "CREDIT CARD" gives your own name for it (display_name) if you set one, else nothing."""
    s = _MARKS.sub("", name or "")
    s = _strip_masks(" ".join(s.split()))
    if issuer in churning.ISSUERS and issuer != "other":
        names = "|".join(re.escape(n).replace(r"\ ", r"\s+") for n in _issuer_names(issuer))
        s = re.sub(rf"\b(?:by|from)\s+(?:{names})\b", " ", s, flags=re.I)   # "... Card by Citi", "... from American Express"
        # The issuer's name goes, except where the product is named for it: "Discover it" keeps its "Discover".
        s = re.sub(rf"\b(?:{names})\b(?!\s+it\b)", " ", s, flags=re.I)
    s = _NOISE.sub(" ", s)
    s = re.sub(r"[/|,]+", " ", s)
    s = " ".join(s.split()).strip(" -–—:.&")
    if s.lower() in _GENERIC:
        s = ""
    if not s and (display_name or "").strip():
        s = " ".join((display_name or "").split())
    return s[:80]


# ------------------------------------------------------------------------------------------------ from the transactions

_FEE = re.compile(r"annual\s+(?:membership\s+)?fee", re.I)


def annual_fee_from(conn, account_id: str) -> float | None:
    """The latest annual fee charged on a card: from its transactions named like "Annual Fee" or "Annual Membership
    Fee" (charges, not refunds). The month it posted isn't kept: the fee follows the card's opening month."""
    t = Transaction
    text = func.lower(func.coalesce(t.payee, "") + " " + func.coalesce(t.description, ""))
    rows = conn.execute(select(t.posted, t.amount, t.payee, t.description)
                        .where(t.account_id == account_id, t.amount < 0, text.like("%annual%fee%") | text.like("%annual%membership%"))
                        .order_by(t.posted.desc())).fetchall()
    for r in rows:
        if _FEE.search(f"{r['payee'] or ''} {r['description'] or ''}") and 0 < abs(r["amount"]) <= 10000:
            return round(abs(r["amount"]), 2)
    return None


def earliest_posted(conn, account_id: str) -> str | None:
    return conn.execute(select(func.min(Transaction.posted)).where(Transaction.account_id == account_id)).scalar()


# ------------------------------------------------------------------------------------------------ the drafts

def dismissed_ids(conn) -> set[str]:
    try:
        ids = json.loads(db.get_setting(conn, sk.CHURN_FOUND_DISMISSED) or "[]")
    except ValueError:
        return set()
    return {i for i in ids if isinstance(i, str)} if isinstance(ids, list) else set()


def _candidates(conn) -> list[dict]:
    """Credit card accounts that aren't hidden and aren't a churning card yet, with their Plaid institution if any."""
    linked = select(ChurnCard.account_id).where(ChurnCard.account_id.is_not(None))
    q = (select(Account.id, Account.name, Account.display_name, Account.org, Account.owner,
                PlaidItem.institution_name.label("institution"))
         .select_from(Account.__table__.outerjoin(PlaidAccount.__table__, PlaidAccount.plaid_account_id == Account.plaid_account_id)
                      .outerjoin(PlaidItem.__table__, PlaidItem.item_id == PlaidAccount.item_id))
         .where(Account.kind == "credit", func.coalesce(Account.hidden, 0) == 0, Account.id.not_in(linked))
         .order_by(db.account_label_expr(), Account.id))
    return db.rows(conn.execute(q))


def _draft(conn, a: dict) -> dict:
    issuer = match_issuer(a["org"], a["institution"])
    shown = " ".join(f"{a['name'] or ''} {a['display_name'] or ''}".split())
    fee = annual_fee_from(conn, a["id"])
    owner = (a["owner"] or "").strip()
    return {
        "account_id": a["id"], "account_name": a["display_name"] or a["name"], "org": a["org"] or a["institution"] or "",
        "owner": "" if owner.lower() == "joint" else owner,
        "issuer": issuer, "product": clean_product(a["name"], issuer, a["display_name"]),
        "business": 1 if re.search(r"\bbusiness\b", shown, re.I) else 0,
        "annual_fee": fee,
        # The banks don't say when a card was opened: its first transaction here is the best guess, and it can only
        # be later than the truth ("on or before").
        "opened_on": earliest_posted(conn, a["id"]), "opened_on_estimate": True,
    }


def found(conn) -> dict:
    """{"drafts": [...], "dismissed": [{account_id, name}]}: credit card accounts to add, and the ones you dismissed."""
    dismissed = dismissed_ids(conn)
    rows = _candidates(conn)
    return {"drafts": [_draft(conn, a) for a in rows if a["id"] not in dismissed],
            "dismissed": [{"account_id": a["id"], "name": a["display_name"] or a["name"]} for a in rows if a["id"] in dismissed]}


def dismiss(conn, account_id: str, undo: bool = False) -> None:
    if not undo and not conn.execute(select(Account.id).where(Account.id == account_id, Account.kind == "credit")).fetchone():
        raise churning.ChurnError("Pick one of your credit card accounts")
    ids = dismissed_ids(conn)
    ids = ids - {account_id} if undo else ids | {account_id}
    db.set_setting(conn, sk.CHURN_FOUND_DISMISSED, json.dumps(sorted(ids)))


# ------------------------------------------------------------------------------------------------ the AI's suggestions

MAX_RATE = 20.0               # points per dollar
MAX_FEE = 10000.0
MAX_CREDIT = 5000.0           # dollars a period
MAX_BENEFITS = 15
MAX_BONUS = 1_000_000.0       # points (or dollars, for cash back)
MAX_BONUS_SPEND = 100_000.0
MAX_SOURCES = 8


def build_prompt(issuer: str, product: str, currencies: dict[str, str], categories: list[str], web: bool = True,
                 today: date | None = None) -> str:
    """What the model is asked: the bank's name and the card's name, and what Runway can take back (its currencies,
    categories and kinds of benefit). Nothing about you or your accounts."""
    bank = churning.ISSUERS[issuer]["name"] if issuer in churning.ISSUERS and issuer != "other" else "(unknown bank)"
    find = ([
        "Search the web for this card's current terms. Prefer the bank's own page for the card; use other sites (such as "
        "news or review sites) only for what the bank's page doesn't say. Ignore what is about a different card (another "
        "version, the business or personal sibling) and offers that have ended.",
    ] if web else [
        "Answer from what you know. Card offers change often: leave out anything you can't be sure is still current.",
    ])
    return "\n".join([
        f"Today is {(today or date.today()).isoformat()}. Find the current public terms of this US credit card.",
        f"Bank: {bank}",
        f"Card: {json.dumps(product, ensure_ascii=False)}",
        "",
        *find,
        "",
        "Reply with only one JSON object, with these keys. Leave a key out when your sources don't say it: never guess, and "
        "never fill something in from a similar card.",
        '- "family": the cards whose sign-up bonuses the bank counts as one, e.g. "Sapphire" for Sapphire Preferred and Reserve.',
        '- "currency": what it earns, one of these keys: ' + "; ".join(f"{k} = {n}" for k, n in currencies.items()),
        '- "base_rate": points (or percent cash back) per dollar on everything else.',
        '- "rates": [{"category": <one of the categories below>, "multiplier": <points per dollar>, '
        '"portal_only": <true if earned only when booked through the bank\'s travel portal>}]',
        "  Map each of the card's bonus categories to the closest of these, and leave out one that matches none: "
        + ", ".join(categories),
        '- "annual_fee": the regular annual fee in dollars (not a first-year waiver; 0 if it has none).',
        '- "bonus": the current public sign-up bonus for new cardmembers: {"amount": <points or miles, or dollars for a '
        'cash back card>, "spend": <dollars to spend>, "months": <months after opening to spend it in>}.',
        '- "portal_name": the bank\'s travel portal, if rates are earned through it.',
        '- "benefits": [{"name": <short name>, "kind": "credit" | "access" | "status" | "other", '
        '"amount": <for a credit: dollars each period, e.g. 10 for "$10 a month">, '
        '"period": "monthly" | "quarterly" | "semiannual" | "annual" | "every_4_years" | "one_time", '
        '"basis": "calendar" (resets on Jan 1 or the 1st of the month) | "anniversary" (resets on the cardmember year), '
        '"guests": <for lounge access: guests who get in free>}]',
        "  Credits (travel, dining, airline fee, Global Entry...), lounge access (each network its own benefit), elite "
        "status, free night certificates, anniversary points. Not insurance or purchase protections.",
        *(['- "sources": [the addresses (URLs) of the pages you took this from]'] if web else []),
    ])


def _object(text: str) -> dict | None:
    """The JSON object in a model's reply, past any reasoning (<think>) and code fences."""
    text = re.sub(r"<think>.*?</think>", " ", text or "", flags=re.S | re.I)
    decoder = json.JSONDecoder()
    best = None
    for m in re.finditer(r"\{", text):
        try:
            val, _ = decoder.raw_decode(text[m.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(val, dict):
            best = val if best is None or len(str(val)) > len(str(best)) else best
    return best


def _number(v: Any, high: float) -> float | None:
    if isinstance(v, bool):
        return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return None if n != n else round(max(0.0, min(high, n)), 2)


def _list(v: Any) -> list:
    return v if isinstance(v, list) else []


def _short(v: Any, limit: int) -> str | None:
    s = " ".join(str(v).split())[:limit] if isinstance(v, (str, int, float)) and not isinstance(v, bool) else ""
    return s or None


def _sources(*lists: Any) -> list[str]:
    """Web addresses to show as where a suggestion came from: http(s) only, with a host and no user name or password in
    them, each once, at most MAX_SOURCES."""
    out: list[str] = []
    for v in lists:
        for u in _list(v):
            if not isinstance(u, str) or len(u.strip()) > 500:
                continue
            u = u.strip()
            try:
                parts = urlsplit(u)
            except ValueError:
                continue
            if parts.scheme.lower() not in ("http", "https") or not parts.hostname or "@" in parts.netloc or any(c.isspace() for c in u):
                continue
            if u not in out:
                out.append(u)
            if len(out) >= MAX_SOURCES:
                return out
    return out


def _bonus(v: Any) -> dict | None:
    """The sign-up bonus: {amount, spend, months}, or None without an amount."""
    if not isinstance(v, dict):
        return None
    months = _number(v.get("months"), 24)
    bonus = {"amount": _number(v.get("amount"), MAX_BONUS), "spend": _number(v.get("spend"), MAX_BONUS_SPEND),
             "months": int(months) if months else None}
    return bonus if bonus["amount"] else None


def validate_suggestion(reply: dict, currencies: dict[str, str], categories: list[str], cited: list[str] | None = None) -> dict:
    """What the model said, kept only where it fits Runway: a currency Runway has (by key or name), categories it has
    (case aside), a benefit's kind, period and basis from its lists; numbers clamped; sources that are web addresses
    (the model's own list, then the pages OpenRouter says it cited). The rest is dropped."""
    out: dict[str, Any] = {"family": _short(reply.get("family"), 60)}
    cur = str(reply.get("currency") or "").strip().lower()
    by_name = {n.lower(): k for k, n in currencies.items()}
    out["currency"] = cur if cur in currencies else by_name.get(cur)
    out["base_rate"] = _number(reply.get("base_rate"), MAX_RATE)
    names = {c.lower(): c for c in categories}
    rates, seen = [], set()
    for r in _list(reply.get("rates")):
        if not isinstance(r, dict):
            continue
        cat = names.get(str(r.get("category") or "").strip().lower())
        mult = _number(r.get("multiplier"), MAX_RATE)
        portal = 1 if r.get("portal_only") is True else 0
        if cat and mult is not None and (cat, portal) not in seen:
            seen.add((cat, portal))
            rates.append({"category": cat, "multiplier": mult, "portal_only": portal})
    out["rates"] = rates
    out["annual_fee"] = _number(reply.get("annual_fee"), MAX_FEE)
    out["bonus"] = _bonus(reply.get("bonus"))
    out["portal_name"] = _short(reply.get("portal_name"), 60)
    benefits, names_seen = [], set()
    for b in _list(reply.get("benefits")):
        name = _short(b.get("name"), 80) if isinstance(b, dict) else None
        if not name or name.lower() in names_seen:
            continue
        names_seen.add(name.lower())
        kind = str(b.get("kind") or "").strip().lower()
        kind = kind if kind in churn_benefits.KINDS else "other"
        period = str(b.get("period") or "").strip().lower()
        period = period if period in churn_benefits.PERIODS else "annual"
        basis = str(b.get("basis") or "").strip().lower()
        amount = _number(b.get("amount"), MAX_CREDIT) if kind == "credit" else None
        guests = _number(b.get("guests"), 20) if kind == "access" else None
        benefits.append({"name": name, "kind": kind, "amount": amount or None, "period": period,
                         "basis": basis if basis in churn_benefits.BASES else None,
                         "guests": int(guests) if guests is not None else None})
        if len(benefits) >= MAX_BENEFITS:
            break
    out["benefits"] = benefits
    out["sources"] = _sources(reply.get("sources"), cited or [])
    return out


def web_search_on(conn) -> bool:
    """Whether "Fill in the rest with AI" searches the web (Settings; on unless switched off)."""
    return (db.get_setting(conn, sk.CHURN_AI_WEB, "1") or "1") == "1"


def _ask(ask, api_key: str, model: str, prompt: str, web: bool) -> tuple[str, list[str]]:
    """The model's reply and the pages it cites. With web search: OpenRouter's web search tool, or its web plugin for a
    model that can't call tools."""
    if not web:
        return ask(api_key, model, prompt)
    try:
        return ask(api_key, model, prompt, web="tool")
    except categorize.ToolsUnsupported:
        return ask(api_key, model, prompt, web="plugin")


def suggest(conn, issuer: str, product: str, caller=None) -> dict:
    """Ask the model about this card, searching the web first unless that's switched off in Settings. Sends the bank's
    name and the card's name only. Raises churning.ChurnError for a bad request or no key, RuntimeError if the request
    fails or the reply is unusable."""
    if issuer not in churning.ISSUERS:
        raise churning.ChurnError("Pick the bank")
    product = _MARKS.sub("", _strip_masks(" ".join(str(product or "").split())))[:80].strip()
    if not product:
        raise churning.ChurnError("Enter the card's name first")
    api_key = db.get_setting(conn, sk.OPENROUTER_API_KEY)
    if not api_key:
        raise churning.ChurnError("Add an OpenRouter API key in Settings first.")
    model = categorize.card_ai_model(conn)
    web = web_search_on(conn)
    currencies = {k: v["name"] for k, v in churning.values(conn).items()}
    categories = [c["name"] for c in churning.spending_categories(conn)]
    prompt = build_prompt(issuer, product, currencies, categories, web)
    began = time.time()
    try:
        with monitoring.ai_agent("Card suggestions", "churning"):
            reply, cited = _ask(caller or categorize.chat, api_key, model, prompt, web)
    except Exception as e:   # network, timeout or API error (its text quotes OpenRouter's answer: kept scrubbed)
        said = monitoring.public_text(str(e)) or type(e).__name__
        if web:
            raise RuntimeError(f"The AI’s web search failed after {time.time() - began:.0f}s ({model}): {said}"[:240]
                               + ". To ask without searching, turn off web search for card suggestions in Settings → Connections → AI categorization.") from e
        raise RuntimeError(f"The AI request failed after {time.time() - began:.0f}s: {said}"[:300]) from e
    parsed = _object(reply)
    if parsed is None:
        raise RuntimeError(f"The model ({model}) didn't answer in the expected format. Try {categorize.DEFAULT_CARD_MODEL} in Settings.")
    return {**validate_suggestion(parsed, currencies, categories, cited), "web": web}
