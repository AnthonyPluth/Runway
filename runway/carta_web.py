"""Carta through the browser extension: your equity as carta.com shows it to you, with the sign-in in your browser.

Carta's API needs Carta to approve each app, so instead the extension (extension/) opens Carta in a background tab,
notes the data requests Carta's own pages make, fetches the same addresses again (reads only, same site), and sends
the replies here. Carta's web app isn't documented, so everything is read loosely: any object that looks like a
security (an id and a quantity, plus an exercise price, an option type, a vesting schedule or a certificate) is a
grant, and the nearest company around it is its company. What the extension read is kept (Settings -> Connections ->
Carta -> download) so the reading can be improved when Carta's pages differ from what's expected.
"""
from __future__ import annotations

import json
import re
import urllib.parse
from datetime import date

from . import carta, db

START_URL = "https://app.carta.com/"
CARTA_HOSTS = re.compile(r"(^|\.)carta\.com$")
# Addresses in Carta's replies worth reading too (holdings, grants, valuations), and ones never to touch.
FOLLOW = re.compile(r"portfolio|holding|securit|grant|option|certificate|vest|fmv|fair.?market|valuation|equity|issuer|compan|rsu", re.I)
NEVER = re.compile(r"logout|log-out|signout|sign-out|delete|remove|cancel|accept|exercise|sign|consent|download|export|upload|\.pdf", re.I)
MAX_CAPTURE = 6_000_000     # characters of replies kept per import
MAX_FOLLOW = 60

_COMPANY_NAME = ("issuerName", "companyName", "legalName", "corporationName", "issuerLegalName", "organizationName")
_COMPANY_ID = ("issuerId", "companyId", "corporationId", "organizationId")
_PRICE = ("fairMarketValue", "latestFairMarketValue", "fmv", "fmvPerShare", "currentFmv", "sharePrice", "pricePerShare",
          "currentSharePrice", "preferredPrice")
_SECURITY_SIGNS = ("exercisePrice", "strikePrice", "optionType", "vestingSchedule", "vestingStartDate", "certificateLabel",
                   "securityType", "grantDate", "quantityVested", "vestedQuantity", "exercisedQuantity")


class CartaWebError(ValueError):
    pass


def carta_url(url: str) -> bool:
    u = urllib.parse.urlsplit(url or "")
    return u.scheme == "https" and bool(CARTA_HOSTS.search((u.hostname or "").lower()))


def start(conn) -> dict:
    db.set_setting(conn, "carta_web_capture", json.dumps([]))
    return {"start_url": START_URL, "max_follow": MAX_FOLLOW}


def _capture(conn) -> list:
    try:
        return json.loads(db.get_setting(conn, "carta_web_capture") or "[]")
    except ValueError:
        return []


def _links(data, found: set) -> None:
    """Carta addresses mentioned in a reply that look like they hold equity data."""
    if isinstance(data, dict):
        for v in data.values():
            _links(v, found)
    elif isinstance(data, list):
        for v in data[:500]:
            _links(v, found)
    elif isinstance(data, str) and len(data) < 400 and ("carta.com/" in data or data.startswith("/api/")):
        url = data if data.startswith("http") else urllib.parse.urljoin(START_URL, data)
        if carta_url(url) and FOLLOW.search(url) and not NEVER.search(url):
            found.add(url.split("#")[0])


def ingest(conn, url: str, data) -> dict:
    """One reply the extension read from Carta. Returns more Carta addresses worth reading."""
    if not carta_url(url):
        raise CartaWebError("Only replies from carta.com are read")
    cap = _capture(conn)
    size = sum(len(json.dumps(c["data"])) for c in cap)
    blob = json.dumps(data)
    if size + len(blob) <= MAX_CAPTURE and not any(c["url"] == url for c in cap):
        cap.append({"url": url, "data": data})
        db.set_setting(conn, "carta_web_capture", json.dumps(cap))
    found: set = set()
    _links(data, found)
    return {"follow": sorted(found - {c["url"] for c in cap})[:20]}


# ------------------------------------------------------------------------------------------------ reading

def _company_of(d: dict) -> dict | None:
    name = carta._pick({k: d.get(k) for k in d if not isinstance(d.get(k), (dict, list))}, *_COMPANY_NAME)
    for key in ("issuer", "company", "corporation", "organization"):
        sub = d.get(key)
        if isinstance(sub, dict):
            inner = _company_of(sub) or ({"name": sub.get("name"), "id": sub.get("id"),
                                          "price": carta._num(carta._pick(sub, *_PRICE))} if sub.get("name") else None)
            if inner:
                return inner
    if not name:
        return None
    cid = carta._pick(d, *_COMPANY_ID)
    return {"name": str(name), "id": str(cid) if cid else None, "price": carta._num(carta._pick(d, *_PRICE)),
            "price_date": carta._day(carta._pick(d, "fairMarketValueDate", "fmvDate", "valuationDate", "effectiveDate"))}


def _security(d: dict) -> dict | None:
    flat = {k: v for k, v in d.items()}
    if not any(carta._pick(flat, s) not in (None, "") for s in _SECURITY_SIGNS):
        return None
    kinds = " ".join(str(carta._pick(flat, k) or "") for k in ("optionType", "securityType", "type", "label", "kind")).lower()
    if carta._pick(flat, "exercisePrice", "strikePrice", "optionType") is not None or "option" in kinds:
        hint = "option"
    elif "rsu" in kinds or "restricted stock unit" in kinds:
        hint = "rsu"
    elif "rsa" in kinds or "restricted stock award" in kinds:
        hint = "rsa"
    else:
        hint = "shares"
    g = carta._grant(flat, hint)
    if g:   # the web app numbers each kind of security on its own: keep an option and a certificate with the same id apart
        g["id"] = f"carta-web:{hint}:{g['id'].split(':', 1)[1]}"
    return g


def read(capture: list) -> dict:
    """Companies and grants in everything the extension read."""
    companies: dict[str, dict] = {}
    grants: dict[str, tuple[str, dict, dict]] = {}

    def company_key(c: dict) -> str:
        key = f"carta:{c['id']}" if c.get("id") else "carta:" + re.sub(r"[^a-z0-9]+", "-", c["name"].lower()).strip("-")
        have = companies.setdefault(key, {"id": key, "name": c["name"], "price": None, "price_date": None})
        if c.get("price") and (not have["price"] or (c.get("price_date") or "") >= (have["price_date"] or "")):
            have["price"], have["price_date"] = c["price"], c.get("price_date")
        return key

    def walk(obj, ctx: str | None, depth: int = 0):
        if depth > 30:
            return
        if isinstance(obj, dict):
            c = _company_of(obj)
            here = company_key(c) if c else ctx
            g = _security(obj)
            if g and here:
                grants[g["id"]] = (here, g, obj)
            for v in obj.values():
                if isinstance(v, (dict, list)):
                    walk(v, here, depth + 1)
        elif isinstance(obj, list):
            for v in obj:
                walk(v, ctx, depth + 1)

    for c in capture:
        walk(c.get("data"), None)
    used = {cid for cid, _, _ in grants.values()}
    return {"companies": [c for k, c in companies.items() if k in used], "grants": list(grants.values())}


def finish(conn) -> dict:
    """Save what was read: Carta's companies and grants replace the ones Carta sent before."""
    found = read(_capture(conn))
    if not found["grants"]:
        db.set_setting(conn, "carta_web_last_error", "Runway didn't find any grants in what it read from Carta. Download what "
                                                      "it read (Settings -> Connections -> Carta) so the reading can be fixed.")
        return {"companies": 0, "grants": 0, "pages": len(_capture(conn))}
    today = date.today().isoformat()
    for c in found["companies"]:
        row = conn.execute("SELECT 1 FROM equity_companies WHERE id=?", (c["id"],)).fetchone()
        when = c["price_date"] or (today if c["price"] else None)
        if row:
            conn.execute("UPDATE equity_companies SET name=?, share_price=COALESCE(?, share_price), price_as_of=COALESCE(?, price_as_of), "
                         "source='carta' WHERE id=?", (c["name"], c["price"], when, c["id"]))
        else:
            conn.execute("INSERT INTO equity_companies(id, name, share_price, price_as_of, source) VALUES (?,?,?,?,?)",
                         (c["id"], c["name"], c["price"], when, "carta"))
    for cid, g, raw in found["grants"]:
        carta._save_grant(conn, cid, g, raw)
    db.set_setting(conn, "carta_web_last", today)
    db.set_setting(conn, "carta_web_last_error", None)
    return {"companies": len(found["companies"]), "grants": len(found["grants"]), "pages": len(_capture(conn))}
