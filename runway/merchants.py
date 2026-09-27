"""Merchant logos, from Plaid only.

Plaid tells Runway who a transaction's merchant is (an entity id, a name, a website) and often has a logo for it.
Runway downloads each logo once from Plaid, keeps it in the database and serves it itself, so the app never asks
anyone else for anything (and the page's content policy stays "images from Runway only"). Transactions from
SimpleFIN show the logo too when their merchant has the same name as one Plaid knows.
"""
from __future__ import annotations

import base64
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from . import db

MAX_LOGO = 256 * 1024
TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}   # never SVG: it can carry scripts
RETRY_DAYS = 30        # a logo that couldn't be fetched is tried again after this long
PER_SYNC = 60          # logos fetched per sync at most


def _plaid_host(url: str | None) -> bool:
    """Only logos Plaid hosts: https, on plaid.com."""
    try:
        u = urllib.parse.urlsplit(url or "")
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    return u.scheme == "https" and (host == "plaid.com" or host.endswith(".plaid.com"))


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


def _download(url: str, opener=None) -> tuple[bytes, str] | None:
    req = urllib.request.Request(url, headers={"User-Agent": "Runway", "Accept": "image/png,image/*"})
    try:
        if opener:
            resp = opener(req)
        else:
            resp = urllib.request.urlopen(req, timeout=8, context=ssl.create_default_context())
        with resp:
            ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            data = resp.read(MAX_LOGO + 1)
    except (urllib.error.URLError, OSError, ValueError):
        return None
    if ctype not in TYPES or not data or len(data) > MAX_LOGO:
        return None
    return data, ctype


def fetch_logos(conn, limit: int = PER_SYNC, opener=None) -> int:
    """Download logos Runway doesn't have yet (from Plaid only). Returns how many it got."""
    now = datetime.now()
    retry_before = (now - timedelta(days=RETRY_DAYS)).isoformat(timespec="seconds")
    todo = conn.execute("SELECT id, logo_url FROM merchants WHERE logo IS NULL AND logo_url IS NOT NULL "
                        "AND (logo_checked IS NULL OR logo_checked < ?) ORDER BY id LIMIT ?", (retry_before, limit)).fetchall()
    got = 0
    for m in todo:
        found = _download(m["logo_url"], opener) if _plaid_host(m["logo_url"]) else None
        if found:
            data, ctype = found
            conn.execute("UPDATE merchants SET logo=?, logo_type=?, logo_checked=? WHERE id=?",
                         (base64.b64encode(data).decode(), ctype, now.isoformat(timespec="seconds"), m["id"]))
            got += 1
        else:
            conn.execute("UPDATE merchants SET logo_checked=? WHERE id=?", (now.isoformat(timespec="seconds"), m["id"]))
    return got


def logo(conn, mid: str) -> tuple[bytes, str] | None:
    r = conn.execute("SELECT logo, logo_type FROM merchants WHERE id=? AND logo IS NOT NULL", (mid,)).fetchone()
    return (base64.b64decode(r["logo"]), r["logo_type"]) if r else None


def for_transactions(conn, txs: list[dict]) -> dict[str, str]:
    """{transaction id: merchant id with a logo}: by the merchant Plaid named, else by the merchant name ("Starbucks
    Store 99" is Starbucks: the longest known name it starts with, as whole words)."""
    have = {r["id"]: key(r["name"]) for r in conn.execute("SELECT id, name FROM merchants WHERE logo IS NOT NULL")}
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
