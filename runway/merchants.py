"""Merchant logos, from Plaid, and from Logo.dev for merchants Plaid has no logo for.

Plaid tells Runway who a transaction's merchant is (an entity id, a name, a website) and often has a logo for it.
Runway downloads each logo once from Plaid, keeps it in the database and serves it itself, so the app never asks
anyone else for anything (and the page's content policy stays "images from Runway only"). Transactions from
SimpleFIN show the logo too when their merchant has the same name as one Plaid knows.

When Plaid has no logo, Runway asks Logo.dev for one by the merchant's website: the one Plaid gave, or for big names
(brands.MERCHANT_PATTERNS) the one Runway knows. Failing that (most SimpleFIN transactions: no website), it asks by
the merchant's name, for spending only (not transfers, income or "Interest"), and keeps no logo when Logo.dev knows no
such brand. That needs a Logo.dev publishable key (Settings). Runway, never the browser, downloads each (during a sync,
and for the past year's merchants right after you add the key), keeps it as merchant "site:<website>" or
"brand:<name>", and checks it again every month so a brand's new logo shows up by itself. Logo.dev only ever learns
merchants' websites and names, never what you bought or paid.
"""
from __future__ import annotations

import base64
import difflib
import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, delete, func, insert, or_, select, update

from . import brands, db
from . import settings_keys as sk
from .models import Category, Merchant, MerchantLogo, Transaction


MAX_LOGO = 256 * 1024
TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}   # never SVG: it can carry scripts
RETRY_DAYS = 30        # a logo that couldn't be fetched is tried again after this long
PER_SYNC = 60          # logos fetched per sync at most
THEME = "dark"         # Logo.dev's version for dark backgrounds: the app is dark and shows logos with nothing behind them
REFRESH_DAYS = 30      # a Logo.dev logo is fetched again after this long, in case it changed
SITE = "site:"         # merchants.id prefix for logos from Logo.dev, by website
BRAND = "brand:"       # ... and by the merchant's name, when no website is known
LOGO_DEV = "https://img.logo.dev/"
SEARCH = "https://api.logo.dev/search"
_SITE_RX = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$")


def _plaid_host(url: str | None) -> bool:
    """Only logos Plaid hosts: https, on plaid.com."""
    try:
        u = urllib.parse.urlsplit(url or "")
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    return u.scheme == "https" and (host == "plaid.com" or host.endswith(".plaid.com"))


def site(website: str | None) -> str | None:
    """A merchant's website as Logo.dev looks it up: "https://www.Target.com/x" -> "target.com"."""
    text = (website or "").strip().lower()
    if not text:
        return None
    try:
        host = urllib.parse.urlsplit(text if "//" in text else "https://" + text).hostname or ""
    except ValueError:
        return None
    host = host.removeprefix("www.")
    return host if len(host) <= 253 and _SITE_RX.match(host) else None


def _logo_dev_url(url: str | None) -> bool:
    try:
        u = urllib.parse.urlsplit(url or "")
    except ValueError:
        return False
    return u.scheme == "https" and (u.hostname or "").lower() == "img.logo.dev"


def configured(conn) -> bool:
    return bool(db.get_setting(conn, sk.LOGODEV_TOKEN))


def searchable(conn) -> bool:
    """Brand Search can be used: there's a secret key (and a publishable one, to fetch what it finds)."""
    return configured(conn) and bool(db.get_setting(conn, sk.LOGODEV_SECRET))


def key(name: str | None) -> str:
    return " ".join((name or "").lower().split())


def logo_path(k: str) -> str:
    """Where Runway serves the Logo.dev logo it fetched for a name (key(name))."""
    return f"/api/merchants/{urllib.parse.quote(BRAND + k, safe='')}/logo"


def note(conn, t: dict) -> str | None:
    """Remember the merchant of a Plaid transaction. Returns its id (for transactions.merchant_id), or None."""
    cp: dict[str, Any] = next((c for c in t.get("counterparties") or [] if (c.get("type") or "merchant") == "merchant"), {}) or {}
    entity = t.get("merchant_entity_id") or cp.get("entity_id")
    name = t.get("merchant_name") or cp.get("name")
    logo = t.get("logo_url") or cp.get("logo_url")
    website = t.get("website") or cp.get("website")
    if not name or not (entity or logo):
        return None
    mid = entity or f"name:{key(name)}"
    row = conn.execute(select(Merchant.logo_url).where(Merchant.id == mid)).fetchone()
    if row is None:
        conn.execute(insert(Merchant).values(id=mid, name=name, website=website, logo_url=logo))
    else:
        conn.execute(update(Merchant).where(Merchant.id == mid).values(
            name=name, website=func.coalesce(website, Merchant.website), logo_url=func.coalesce(logo, Merchant.logo_url)))
        if logo and logo != row["logo_url"]:   # a new logo: fetch it again
            conn.execute(update(Merchant).where(Merchant.id == mid).values(logo=None, logo_type=None, logo_checked=None))
    return mid


class _SameRules(urllib.request.HTTPRedirectHandler):
    """Logos come from plaid.com and img.logo.dev only; a redirect is followed only if it stays there."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not (_plaid_host(newurl) or _logo_dev_url(newurl)):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# Settings: why the last Logo.dev lookup by website, and by name, failed (not "no such brand"), for Settings.
LAST_ERROR = {"site": sk.LOGODEV_LAST_ERROR, "name": sk.LOGODEV_LAST_ERROR_NAME}
_why = ""   # why the last _download returned nothing


def _download(url: str, opener=None) -> tuple[bytes, str] | None:
    global _why
    _why = ""
    req = urllib.request.Request(url, headers={"User-Agent": "Runway", "Accept": "image/png,image/*"})
    try:
        if opener:
            resp = opener(req)
        else:
            resp = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl.create_default_context()),
                                               _SameRules()).open(req, timeout=8)
        with resp:
            ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            data = resp.read(MAX_LOGO + 1)
    except urllib.error.HTTPError as e:
        _why = "" if e.code == 404 else f"HTTP {e.code}" + (" (the key was refused)" if e.code in (401, 403) else "")
        return None
    except (urllib.error.URLError, OSError, ValueError) as e:
        _why = f"couldn't connect ({getattr(e, 'reason', e)})"
        return None
    if ctype not in TYPES or not data or len(data) > MAX_LOGO:
        _why = f"got {ctype or 'something'} instead of an image"
        return None
    return data, ctype


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def search(conn, name: str, opener=None) -> list[dict] | None:
    """Logo.dev's Brand Search for a merchant name: [{name, domain}], best first. None when the search failed (why is
    in _why): a refused key, no connection, or an answer that isn't a list of brands."""
    global _why
    _why = ""
    req = urllib.request.Request(f"{SEARCH}?{urllib.parse.urlencode({'q': name})}", headers={
        "Authorization": f"Bearer {db.get_setting(conn, sk.LOGODEV_SECRET)}", "Accept": "application/json", "User-Agent": "Runway"})
    try:
        resp = opener(req) if opener else urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ssl.create_default_context()), _NoRedirects()).open(req, timeout=8)
        with resp:
            data = json.loads(resp.read(512 * 1024))
    except urllib.error.HTTPError as e:
        _why = f"Brand Search: HTTP {e.code}" + (" (the secret key was refused, or your plan doesn't include it)" if e.code in (401, 402, 403) else "")
        return None
    except (urllib.error.URLError, OSError, ValueError) as e:
        _why = f"Brand Search: couldn't connect ({getattr(e, 'reason', e)})"
        return None
    items = data if isinstance(data, list) else (data.get("results") or data.get("data") or []) if isinstance(data, dict) else []
    out = []
    for it in items[:10]:
        if isinstance(it, dict) and site(it.get("domain")):
            out.append({"name": str(it.get("name") or ""), "domain": site(it.get("domain"))})
    return out


_FILLER = re.compile(r"\b(inc|llc|ltd|co|corp|corporation|company|the|store|stores|usa)\b")


def _norm(s: str | None) -> str:
    s = (s or "").lower().replace("&", " and ").replace("'", "").replace("’", "")
    return " ".join(_FILLER.sub(" ", re.sub(r"[^a-z0-9 ]+", " ", s)).split())


def best_match(name: str, candidates: list[dict]) -> dict | None:
    """The candidate brand that is clearly this merchant, or None: better no logo than someone else's. A match is the
    same name (or website) give or take spaces, punctuation and "Inc", a name the bank cut short ("Mackenthun's Fine
    Fo" is "Mackenthun's Fine Foods"), a brand the merchant's name starts with ("Kwik Trip 1173"), or a close spelling."""
    n = _norm(name)
    squashed = n.replace(" ", "")
    if len(squashed) < 3:
        return None
    best, score = None, 0.0
    for c in candidates:
        cn = _norm(c.get("name"))
        dom = (c.get("domain") or "").split(".")[0].replace("-", "")
        s = difflib.SequenceMatcher(None, n, cn).ratio() if cn else 0.0
        if cn.replace(" ", "") == squashed or dom == squashed:
            s = 1.0
        elif (cn and len(n) >= 6 and cn.startswith(n)) or (cn and len(cn) >= 5 and n.startswith(cn + " ")):
            s = max(s, 0.9)
        if s > score:
            best, score = c, s
    return best if score >= 0.85 else None


def _logo_dev():
    """The merchants whose logo comes from Logo.dev (by website or name), not Plaid: a condition on merchants.id."""
    return or_(Merchant.id.like(SITE + "%"), Merchant.id.like(BRAND + "%"))


def _todo(conn, limit: int) -> list:
    """Logos to fetch: Plaid's that Runway doesn't have yet (or tried a month ago), and Logo.dev's (by website or name)
    that it doesn't have or last checked a month ago, when there's a Logo.dev key. Never tried ones first."""
    retry_before = (datetime.now() - timedelta(days=RETRY_DAYS)).isoformat(timespec="seconds")
    refresh_before = (datetime.now() - timedelta(days=REFRESH_DAYS)).isoformat(timespec="seconds")
    m = Merchant
    want: ColumnElement[bool] = (m.id.not_like(SITE + "%") & m.id.not_like(BRAND + "%") & m.logo.is_(None)
                                 & or_(m.logo_checked.is_(None), m.logo_checked < retry_before))
    if configured(conn):
        want = or_(want, _logo_dev() & or_(m.logo_checked.is_(None), m.logo_checked < refresh_before))
    return conn.execute(select(m.id, m.name, m.logo_url).where(m.logo_url.is_not(None), want)
                        .order_by(m.logo_checked.is_not(None), m.id).limit(limit)).fetchall()


def _params(token: str | None) -> str:
    return urllib.parse.urlencode({"token": token, "size": 64, "format": "png", "theme": THEME, "fallback": 404})


def fetch_logos(conn, limit: int = PER_SYNC, opener=None) -> int:
    """Download logos Runway doesn't have yet (from Plaid), and Logo.dev logos it doesn't have or last checked a month
    ago (when there's a Logo.dev key). Returns how many it got."""
    global _why
    now = datetime.now()
    token = db.get_setting(conn, sk.LOGODEV_TOKEN)
    if db.get_setting(conn, sk.LOGODEV_THEME) != THEME:
        # Logos fetched for another theme (or before there was one) are fetched again, a batch per sync; one that
        # can't be keeps the logo Runway has.
        conn.execute(update(Merchant).where(_logo_dev()).values(logo_checked=None))
        db.set_setting(conn, sk.LOGODEV_THEME, THEME)
    todo = _todo(conn, limit)
    got = 0
    refused: set[str] = set()   # kinds of Logo.dev lookup that failed this round: the rest of that kind wait for next time
    for m in todo:
        kind = "site" if m["id"].startswith(SITE) else "name" if m["id"].startswith(BRAND) else None
        if kind in refused:
            continue
        _why = ""   # (a merchant skipped below isn't a failed download)
        wrong = False
        params = _params(token)
        if m["id"].startswith(SITE):
            s = m["id"][len(SITE):]
            found = _download(f"{LOGO_DEV}{s}?{params}", opener) if site(s) == s else None
        elif m["id"].startswith(BRAND):
            found = None
            if not m["name"]:
                pass
            elif searchable(conn):   # Brand Search, and only a clear match (better no logo than the wrong one)
                cands = search(conn, m["name"], opener)
                pick = best_match(m["name"], cands) if cands else None
                wrong = cands is not None and not pick   # Brand Search answered, and nothing is clearly this merchant
                if pick:
                    conn.execute(update(Merchant).where(Merchant.id == m["id"]).values(website=pick["domain"]))
                    found = _download(f"{LOGO_DEV}{pick['domain']}?{params}", opener)
            else:
                found = _download(f"{LOGO_DEV}name/{urllib.parse.quote(m['name'], safe='')}?{params}", opener)
        else:
            found = _download(m["logo_url"], opener) if _plaid_host(m["logo_url"]) else None
        if kind:   # remember how Logo.dev last answered, for Settings
            db.set_setting(conn, LAST_ERROR[kind], None if found or not _why else f"{now:%b %d %H:%M}: {_why}")
            if not found and _why:
                # A refused key or no connection isn't "no such brand": this one (and the rest of its kind) isn't
                # marked as looked up, so it's tried again next time rather than a month from now.
                refused.add(kind)
                continue
        if found:
            data, ctype = found
            conn.execute(update(Merchant).where(Merchant.id == m["id"]).values(
                logo=base64.b64encode(data).decode(), logo_type=ctype, logo_checked=now.isoformat(timespec="seconds")))
            got += 1
        elif kind == "name" and wrong:   # the logo a plain name lookup found may be someone else's: drop it
            conn.execute(update(Merchant).where(Merchant.id == m["id"]).values(
                logo=None, logo_type=None, logo_checked=now.isoformat(timespec="seconds")))
        else:
            # (a Logo.dev logo that couldn't be fetched again keeps the one Runway has)
            conn.execute(update(Merchant).where(Merchant.id == m["id"]).values(logo_checked=now.isoformat(timespec="seconds")))
    return got


def site_logos(conn, sites) -> set[str]:
    """The websites (of sites) Runway has a Logo.dev logo for. Ones it has never seen are noted, for a sync to fetch."""
    sites = sorted({s for s in sites if s})
    if not sites:
        return set()
    ids = [SITE + s for s in sites]
    rows = {r["id"]: r["logo"] is not None for r in conn.execute(select(Merchant.id, Merchant.logo).where(Merchant.id.in_(ids)))}
    for s in sites:
        if SITE + s not in rows:
            conn.execute(insert(Merchant).values(id=SITE + s, logo_url=LOGO_DEV + s))
    return {s for s in sites if rows.get(SITE + s)}


def sites_for(conn, txs: list[dict]) -> dict[str, str]:
    """{transaction id: the merchant's website}: the one Plaid gave for its merchant, else a big name's."""
    mids = sorted({t["merchant_id"] for t in txs if t.get("merchant_id")})
    websites = {}
    if mids:
        websites = {r["id"]: site(r["website"])
                    for r in conn.execute(select(Merchant.id, Merchant.website).where(Merchant.id.in_(mids)))}
    out = {}
    for t in txs:
        s = websites.get(t.get("merchant_id")) or brands.merchant(t.get("payee"), t.get("description"))
        if s:
            out[t["id"]] = s
    return out


# Payees that are money moving, not a merchant: never looked up by name (Logo.dev would find some brand for "Transfer").
_NOT_A_MERCHANT = re.compile(r"\b(transfer|xfer|payment|pmt|autopay|deposit|withdrawal|interest|dividend|fee|atm|check|"
                             r"cheque|payroll|salary|paycheck|refund|balance|ach|wire|zelle|cash)\b", re.I)


def names_for(conn, txs: list[dict]) -> dict[str, tuple[str, str]]:
    """{transaction id: (key, name)}: the merchant name to ask Logo.dev about, for spending whose merchant has no website
    Runway knows. Not for transfers or income (by category), money in that isn't categorized, or payees like "Interest"."""
    skip = {r["name"] for r in conn.execute(select(Category.name).where(or_(Category.is_transfer == 1, Category.is_income == 1)))}
    out = {}
    for t in txs:
        name = " ".join((t.get("payee") or "").split())
        if (t.get("category") in skip or (t.get("category") is None and (t.get("amount") or 0) > 0)
                or len(re.findall(r"[A-Za-z]", name)) < 3 or _NOT_A_MERCHANT.search(name)):
            continue
        out[t["id"]] = (key(name), name)
    return out


def brand_logos(conn, names) -> set[str]:
    """The merchant names (of (key, name) pairs) Runway has a Logo.dev logo for, by key. Ones it has never asked about
    are noted, for a sync (or the fetch after you add a key) to look up."""
    names = dict(n for n in names if n[0])
    if not names:
        return set()
    ids = [BRAND + k for k in names]
    have: dict[str, bool] = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        have.update({r["id"]: r["logo"] is not None
                     for r in conn.execute(select(Merchant.id, Merchant.logo).where(Merchant.id.in_(chunk)))})
    for k, name in names.items():
        if BRAND + k not in have:
            conn.execute(insert(Merchant).values(id=BRAND + k, name=name,
                                                 logo_url=LOGO_DEV + "name/" + urllib.parse.quote(name, safe="")))
    return {k for k in names if have.get(BRAND + k)}


def logo_dev_logos(conn, txs: list[dict]) -> dict[str, str]:
    """{transaction id: merchant id}: Logo.dev's logo by the merchant's website, else by its name. Ones not fetched yet
    are noted (and get their logo once a sync has fetched it); until then a Plaid logo, if there is one, stands in."""
    if not configured(conn):
        return {}
    sites = sites_for(conn, txs)
    have = site_logos(conn, sites.values())
    out = {tid: SITE + s for tid, s in sites.items() if s in have}
    names = names_for(conn, [t for t in txs if t["id"] not in sites])
    have = brand_logos(conn, names.values())
    out.update({tid: BRAND + k for tid, (k, _) in names.items() if k in have})
    return out


def note_sites(conn, days: int = 400) -> None:
    """Note the last year's merchants (by website, else by name), so a sync fetches their logos from Logo.dev before
    anyone looks. Merchants with a Plaid logo too: Logo.dev's dark-background one is shown in its place."""
    if not configured(conn):
        return
    since = (datetime.now() - timedelta(days=days)).date().isoformat()
    t = Transaction
    rows = db.rows(conn.execute(select(t.merchant_id, t.payee, t.description, t.category, (t.amount < 0).label("spend"))
                                .distinct().where(t.posted >= since)))
    txs = [{**r, "id": str(i), "amount": -1 if r["spend"] else 1} for i, r in enumerate(rows)]
    sites = sites_for(conn, txs)
    site_logos(conn, sites.values())
    brand_logos(conn, names_for(conn, [t for t in txs if t["id"] not in sites]).values())


def backfill(conn, rounds: int = 40, opener=None) -> int:
    """Right after a Logo.dev key is added: note the past year's merchants and fetch their logos in rounds of PER_SYNC,
    saving after each so they show up as they arrive, until none are left (or `rounds` is used up; syncs carry on
    from there). Returns how many it got."""
    note_sites(conn)
    conn.commit()
    got = 0
    for _ in range(rounds):
        if not _todo(conn, 1):
            break
        got += fetch_logos(conn, opener=opener)
        conn.commit()
    return got


def logo(conn, mid: str) -> tuple[bytes, str] | None:
    r = conn.execute(select(Merchant.logo, Merchant.logo_type).where(Merchant.id == mid, Merchant.logo.is_not(None))).fetchone()
    return (base64.b64decode(r["logo"]), r["logo_type"]) if r else None


def for_transactions(conn, txs: list[dict]) -> dict[str, str]:
    """{transaction id: merchant id with a logo}: by the merchant Plaid named, else by the merchant name ("Starbucks
    Store 99" is Starbucks: the longest known name it starts with, as whole words)."""
    have = {r["id"]: key(r["name"]) for r in conn.execute(
        select(Merchant.id, Merchant.name).where(Merchant.logo.is_not(None), ~_logo_dev()))}
    if not have:
        return {}
    by_name: dict[str, str] = {}
    for mid, name in sorted(have.items()):
        by_name.setdefault(name, mid)
    out: dict[str, str] = {}
    for t in txs:
        mid = t.get("merchant_id")
        if mid is not None and mid in have:
            out[t["id"]] = mid
        else:
            payee = key(t.get("payee"))
            known = payee if payee in by_name else max(
                (n for n in by_name if len(n) >= 4 and payee.startswith(n + " ")), key=len, default=None)
            if known:
                out[t["id"]] = by_name[known]
    return out


def status(conn) -> dict:
    """How many merchants have a logo, from Plaid and from Logo.dev; how many Logo.dev doesn't know or are still to be
    fetched; and why the last Logo.dev download failed, if it did. For Settings."""
    def count(*where) -> int:
        return conn.execute(select(func.count()).select_from(Merchant).where(*where)).scalar()
    m = Merchant
    return {
        "plaid": count(m.logo.is_not(None), ~_logo_dev()),
        "logodev": count(m.logo.is_not(None), _logo_dev()),
        "unknown": count(m.logo.is_(None), m.logo_checked.is_not(None), _logo_dev()),
        "waiting": len(_todo(conn, 100000)),
        "last_error": db.get_setting(conn, LAST_ERROR["site"]),
        "last_error_name": db.get_setting(conn, LAST_ERROR["name"]),
        "searchable": searchable(conn),
    }


def retry_unknown(conn) -> None:
    """Look up again the merchants Logo.dev had no logo for (their lookups may have failed, not found nothing)."""
    conn.execute(update(Merchant).where(_logo_dev(), Merchant.logo.is_(None)).values(logo_checked=None))


# ---------------------------------------------------------------------------------------------- logos you choose

def choice(conn, name: str | None) -> dict | None:
    """The logo you chose for a merchant: {website, hidden}, or None (Runway picks)."""
    r = conn.execute(select(MerchantLogo.website, MerchantLogo.hidden).where(MerchantLogo.key == key(name))).fetchone()
    return {"website": r["website"], "hidden": bool(r["hidden"])} if r else None


def choose(conn, name: str | None, website: str | None = None, hidden: bool = False, opener=None) -> None:
    """Choose a merchant's logo, for every transaction from it: a website's logo (fetched from Logo.dev now), none
    (hidden), or (neither) Runway's own pick again."""
    k = key(name)
    if not k:
        raise ValueError("Which merchant?")
    if not website and not hidden:
        conn.execute(delete(MerchantLogo).where(MerchantLogo.key == k))
        return
    s = None
    if website:
        s = site(website)
        if not s:
            raise ValueError("That doesn't look like a website (e.g. target.com)")
        if not configured(conn):
            raise ValueError("Add a Logo.dev publishable key in Settings → Services first")
        mid = SITE + s
        row = conn.execute(select(Merchant.logo).where(Merchant.id == mid)).fetchone()
        if not row or not row["logo"]:
            params = _params(db.get_setting(conn, sk.LOGODEV_TOKEN))
            found = _download(f"{LOGO_DEV}{s}?{params}", opener)
            if not found:
                raise ValueError(f"Logo.dev has no logo for {s}" + (f" ({_why})" if _why else ""))
            data, ctype = found
            now = datetime.now().isoformat(timespec="seconds")
            db.upsert(conn, Merchant, {"id": mid, "logo_url": LOGO_DEV + s, "logo": base64.b64encode(data).decode(),
                                       "logo_type": ctype, "logo_checked": now}, key=["id"],
                      update=["logo", "logo_type", "logo_checked"])
    db.upsert(conn, MerchantLogo, {"key": k, "website": s, "hidden": int(bool(hidden))}, key=["key"])


def chosen_for(conn, txs: list[dict]) -> dict[str, str | None]:
    """{transaction id: merchant id of the logo you chose, or None for no logo}, for transactions whose merchant has
    a choice. Transactions not in it keep the logo Runway found."""
    keys = sorted({key(t.get("payee")) for t in txs if t.get("payee")})
    if not keys:
        return {}
    ml = MerchantLogo
    rows = {r["key"]: r for r in conn.execute(select(ml.key, ml.website, ml.hidden).where(ml.key.in_(keys)))}
    out: dict[str, str | None] = {}
    for t in txs:
        r = rows.get(key(t.get("payee")))
        if r:
            out[t["id"]] = None if r["hidden"] or not r["website"] else SITE + r["website"]
    return out
