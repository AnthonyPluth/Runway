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

from . import brands, db
from . import settings_keys as sk


MAX_LOGO = 256 * 1024
TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}   # never SVG: it can carry scripts
RETRY_DAYS = 30        # a logo that couldn't be fetched is tried again after this long
PER_SYNC = 60          # logos fetched per sync at most
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


def note(conn, t: dict) -> str | None:
    """Remember the merchant of a Plaid transaction. Returns its id (for transactions.merchant_id), or None."""
    cp = next((c for c in t.get("counterparties") or [] if (c.get("type") or "merchant") == "merchant"), {}) or {}
    entity = t.get("merchant_entity_id") or cp.get("entity_id")
    name = t.get("merchant_name") or cp.get("name")
    logo = t.get("logo_url") or cp.get("logo_url")
    website = t.get("website") or cp.get("website")
    if not name or not (entity or logo):
        return None
    mid = entity or f"name:{key(name)}"
    row = conn.execute("SELECT logo_url FROM merchants WHERE id=?", (mid,)).fetchone()
    if row is None:
        conn.execute("INSERT INTO merchants(id, name, website, logo_url) VALUES (?,?,?,?)", (mid, name, website, logo))
    else:
        conn.execute("UPDATE merchants SET name=?, website=COALESCE(?, website), logo_url=COALESCE(?, logo_url) WHERE id=?",
                     (name, website, logo, mid))
        if logo and logo != row["logo_url"]:   # a new logo: fetch it again
            conn.execute("UPDATE merchants SET logo=NULL, logo_type=NULL, logo_checked=NULL WHERE id=?", (mid,))
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


def _todo(conn, limit: int) -> list:
    """Logos to fetch: Plaid's that Runway doesn't have yet (or tried a month ago), and Logo.dev's (by website or name)
    that it doesn't have or last checked a month ago, when there's a Logo.dev key. Never tried ones first."""
    retry_before = (datetime.now() - timedelta(days=RETRY_DAYS)).isoformat(timespec="seconds")
    refresh_before = (datetime.now() - timedelta(days=REFRESH_DAYS)).isoformat(timespec="seconds")
    return conn.execute("SELECT id, name, logo_url FROM merchants WHERE logo_url IS NOT NULL AND ("
                        "(id NOT LIKE ? AND id NOT LIKE ? AND logo IS NULL AND (logo_checked IS NULL OR logo_checked < ?)) OR "
                        "(? AND (id LIKE ? OR id LIKE ?) AND (logo_checked IS NULL OR logo_checked < ?))) "
                        "ORDER BY logo_checked IS NOT NULL, id LIMIT ?",
                        (SITE + "%", BRAND + "%", retry_before, configured(conn), SITE + "%", BRAND + "%", refresh_before,
                         limit)).fetchall()


def fetch_logos(conn, limit: int = PER_SYNC, opener=None) -> int:
    """Download logos Runway doesn't have yet (from Plaid), and Logo.dev logos it doesn't have or last checked a month
    ago (when there's a Logo.dev key). Returns how many it got."""
    global _why
    now = datetime.now()
    token = db.get_setting(conn, sk.LOGODEV_TOKEN)
    todo = _todo(conn, limit)
    got = 0
    refused: set[str] = set()   # kinds of Logo.dev lookup that failed this round: the rest of that kind wait for next time
    for m in todo:
        kind = "site" if m["id"].startswith(SITE) else "name" if m["id"].startswith(BRAND) else None
        if kind in refused:
            continue
        _why = ""   # (a merchant skipped below isn't a failed download)
        wrong = False
        params = urllib.parse.urlencode({"token": token, "size": 64, "format": "png", "fallback": 404})
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
                    conn.execute("UPDATE merchants SET website=? WHERE id=?", (pick["domain"], m["id"]))
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
            conn.execute("UPDATE merchants SET logo=?, logo_type=?, logo_checked=? WHERE id=?",
                         (base64.b64encode(data).decode(), ctype, now.isoformat(timespec="seconds"), m["id"]))
            got += 1
        elif kind == "name" and wrong:   # the logo a plain name lookup found may be someone else's: drop it
            conn.execute("UPDATE merchants SET logo=NULL, logo_type=NULL, logo_checked=? WHERE id=?", (now.isoformat(timespec="seconds"), m["id"]))
        else:
            # (a Logo.dev logo that couldn't be fetched again keeps the one Runway has)
            conn.execute("UPDATE merchants SET logo_checked=? WHERE id=?", (now.isoformat(timespec="seconds"), m["id"]))
    return got


def site_logos(conn, sites) -> set[str]:
    """The websites (of sites) Runway has a Logo.dev logo for. Ones it has never seen are noted, for a sync to fetch."""
    sites = sorted({s for s in sites if s})
    if not sites:
        return set()
    ids = [SITE + s for s in sites]
    ph = ",".join("?" * len(ids))
    rows = {r["id"]: r["logo"] is not None for r in conn.execute(f"SELECT id, logo FROM merchants WHERE id IN ({ph})", ids)}
    for s in sites:
        if SITE + s not in rows:
            conn.execute("INSERT INTO merchants(id, logo_url) VALUES (?, ?)", (SITE + s, LOGO_DEV + s))
    return {s for s in sites if rows.get(SITE + s)}


def sites_for(conn, txs: list[dict]) -> dict[str, str]:
    """{transaction id: the merchant's website}: the one Plaid gave for its merchant, else a big name's."""
    mids = sorted({t["merchant_id"] for t in txs if t.get("merchant_id")})
    websites = {}
    if mids:
        ph = ",".join("?" * len(mids))
        websites = {r["id"]: site(r["website"]) for r in conn.execute(f"SELECT id, website FROM merchants WHERE id IN ({ph})", mids)}
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
    skip = {r["name"] for r in conn.execute("SELECT name FROM categories WHERE is_transfer=1 OR is_income=1")}
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
        ph = ",".join("?" * len(chunk))
        have.update({r["id"]: r["logo"] is not None for r in conn.execute(f"SELECT id, logo FROM merchants WHERE id IN ({ph})", chunk)})
    for k, name in names.items():
        if BRAND + k not in have:
            conn.execute("INSERT INTO merchants(id, name, logo_url) VALUES (?,?,?)",
                         (BRAND + k, name, LOGO_DEV + "name/" + urllib.parse.quote(name, safe="")))
    return {k for k in names if have.get(BRAND + k)}


def logo_dev_logos(conn, txs: list[dict]) -> dict[str, str]:
    """{transaction id: merchant id} for transactions without a Plaid logo: Logo.dev's logo by the merchant's website,
    else by its name. Ones not fetched yet are noted (and get their logo once a sync has fetched it)."""
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
    """Note the last year's merchants that have no Plaid logo (by website, else by name), so a sync fetches their logos
    from Logo.dev before anyone looks."""
    if not configured(conn):
        return
    since = (datetime.now() - timedelta(days=days)).date().isoformat()
    rows = db.rows(conn.execute("SELECT DISTINCT merchant_id, payee, description, category, amount < 0 AS spend "
                                "FROM transactions WHERE posted>=?", (since,)))
    plaid = {r["id"] for r in conn.execute("SELECT id FROM merchants WHERE logo IS NOT NULL AND id NOT LIKE ? AND id NOT LIKE ?",
                                           (SITE + "%", BRAND + "%"))}
    txs = [{**r, "id": str(i), "amount": -1 if r["spend"] else 1} for i, r in enumerate(rows) if r["merchant_id"] not in plaid]
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
    r = conn.execute("SELECT logo, logo_type FROM merchants WHERE id=? AND logo IS NOT NULL", (mid,)).fetchone()
    return (base64.b64decode(r["logo"]), r["logo_type"]) if r else None


def for_transactions(conn, txs: list[dict]) -> dict[str, str]:
    """{transaction id: merchant id with a logo}: by the merchant Plaid named, else by the merchant name ("Starbucks
    Store 99" is Starbucks: the longest known name it starts with, as whole words)."""
    have = {r["id"]: key(r["name"]) for r in conn.execute("SELECT id, name FROM merchants WHERE logo IS NOT NULL AND id NOT LIKE ? "
                                                          "AND id NOT LIKE ?", (SITE + "%", BRAND + "%"))}
    if not have:
        return {}
    by_name: dict[str, str] = {}
    for mid, name in sorted(have.items()):
        by_name.setdefault(name, mid)
    out = {}
    for t in txs:
        mid = t.get("merchant_id")
        if mid in have:
            out[t["id"]] = mid
        else:
            payee = key(t.get("payee"))
            name = payee if payee in by_name else max(
                (n for n in by_name if len(n) >= 4 and payee.startswith(n + " ")), key=len, default=None)
            if name:
                out[t["id"]] = by_name[name]
    return out


def status(conn) -> dict:
    """How many merchants have a logo, from Plaid and from Logo.dev; how many Logo.dev doesn't know or are still to be
    fetched; and why the last Logo.dev download failed, if it did. For Settings."""
    def count(where: str, *args) -> int:
        return conn.execute(f"SELECT COUNT(*) FROM merchants WHERE {where}", args).fetchone()[0]
    ld = "(id LIKE ? OR id LIKE ?)"
    return {
        "plaid": count(f"logo IS NOT NULL AND NOT {ld}", SITE + "%", BRAND + "%"),
        "logodev": count(f"logo IS NOT NULL AND {ld}", SITE + "%", BRAND + "%"),
        "unknown": count(f"logo IS NULL AND logo_checked IS NOT NULL AND {ld}", SITE + "%", BRAND + "%"),
        "waiting": len(_todo(conn, 100000)),
        "last_error": db.get_setting(conn, LAST_ERROR["site"]),
        "last_error_name": db.get_setting(conn, LAST_ERROR["name"]),
        "searchable": searchable(conn),
    }


def retry_unknown(conn) -> None:
    """Look up again the merchants Logo.dev had no logo for (their lookups may have failed, not found nothing)."""
    conn.execute("UPDATE merchants SET logo_checked=NULL WHERE (id LIKE ? OR id LIKE ?) AND logo IS NULL", (SITE + "%", BRAND + "%"))


# ---------------------------------------------------------------------------------------------- logos you choose

def choice(conn, name: str | None) -> dict | None:
    """The logo you chose for a merchant: {website, hidden}, or None (Runway picks)."""
    r = conn.execute("SELECT website, hidden FROM merchant_logos WHERE key=?", (key(name),)).fetchone()
    return {"website": r["website"], "hidden": bool(r["hidden"])} if r else None


def choose(conn, name: str | None, website: str | None = None, hidden: bool = False, opener=None) -> None:
    """Choose a merchant's logo, for every transaction from it: a website's logo (fetched from Logo.dev now), none
    (hidden), or (neither) Runway's own pick again."""
    k = key(name)
    if not k:
        raise ValueError("Which merchant?")
    if not website and not hidden:
        conn.execute("DELETE FROM merchant_logos WHERE key=?", (k,))
        return
    s = None
    if website:
        s = site(website)
        if not s:
            raise ValueError("That doesn't look like a website (e.g. target.com)")
        if not configured(conn):
            raise ValueError("Add a Logo.dev publishable key in Settings → Connections first")
        mid = SITE + s
        row = conn.execute("SELECT logo FROM merchants WHERE id=?", (mid,)).fetchone()
        if not row or not row["logo"]:
            params = urllib.parse.urlencode({"token": db.get_setting(conn, sk.LOGODEV_TOKEN), "size": 64, "format": "png", "fallback": 404})
            found = _download(f"{LOGO_DEV}{s}?{params}", opener)
            if not found:
                raise ValueError(f"Logo.dev has no logo for {s}" + (f" ({_why})" if _why else ""))
            data, ctype = found
            now = datetime.now().isoformat(timespec="seconds")
            conn.execute("INSERT INTO merchants(id, logo_url, logo, logo_type, logo_checked) VALUES (?,?,?,?,?) "
                         "ON CONFLICT(id) DO UPDATE SET logo=excluded.logo, logo_type=excluded.logo_type, logo_checked=excluded.logo_checked",
                         (mid, LOGO_DEV + s, base64.b64encode(data).decode(), ctype, now))
    conn.execute("INSERT INTO merchant_logos(key, website, hidden) VALUES (?,?,?) "
                 "ON CONFLICT(key) DO UPDATE SET website=excluded.website, hidden=excluded.hidden", (k, s, int(bool(hidden))))


def chosen_for(conn, txs: list[dict]) -> dict[str, str | None]:
    """{transaction id: merchant id of the logo you chose, or None for no logo}, for transactions whose merchant has
    a choice. Transactions not in it keep the logo Runway found."""
    keys = sorted({key(t.get("payee")) for t in txs if t.get("payee")})
    if not keys:
        return {}
    ph = ",".join("?" * len(keys))
    rows = {r["key"]: r for r in conn.execute(f"SELECT key, website, hidden FROM merchant_logos WHERE key IN ({ph})", keys)}
    out: dict[str, str | None] = {}
    for t in txs:
        r = rows.get(key(t.get("payee")))
        if r:
            out[t["id"]] = None if r["hidden"] or not r["website"] else SITE + r["website"]
    return out
