"""Carta through the browser extension: your equity as carta.com shows it to you, with the sign-in in your browser.

Carta's API needs Carta to approve each app, so instead the extension (extension/) opens Carta in a background tab,
notes the data requests Carta's own pages make, fetches the same addresses again (reads only, same site), and sends
the replies here. Carta's web app isn't documented, so everything is read loosely: any object that looks like a
security (an id and a quantity, plus an exercise price, an option type, a vesting schedule or a certificate) is a
grant, and the nearest company around it is its company. What the extension read is kept (Settings -> Browser extension ->
Carta -> download) so the reading can be improved when Carta's pages differ from what's expected.
"""
from __future__ import annotations

import json
import re
import urllib.parse
from datetime import date

from sqlalchemy import delete, insert, select, update

from . import carta, db
from . import settings_keys as sk
from .models import EquityCompany, EquityGrant

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
                   "securityType", "grantDate", "quantityVested", "vestedQuantity", "exercisedQuantity", "cumulativeVestedShares")


class CartaWebError(ValueError):
    pass


def carta_url(url: str) -> bool:
    u = urllib.parse.urlsplit(url or "")
    return u.scheme == "https" and bool(CARTA_HOSTS.search((u.hostname or "").lower()))


def start(conn) -> dict:
    db.set_setting(conn, sk.CARTA_WEB_CAPTURE, json.dumps([]))
    return {"start_url": START_URL, "max_follow": MAX_FOLLOW}


def _capture(conn) -> list:
    try:
        return json.loads(db.get_setting(conn, sk.CARTA_WEB_CAPTURE) or "[]")
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
    elif isinstance(data, str) and len(data) < 400 and data.startswith(("http://", "https://", "/api/")):
        url = data if data.startswith("http") else urllib.parse.urljoin(START_URL, data)
        if carta_url(url) and FOLLOW.search(url) and not NEVER.search(url):
            found.add(url.split("#")[0])


# Where carta.com's portfolio pages read your companies, each company's securities and its fair market value. Pages
# don't always leave a note of every data request they make (the browser keeps only so many), so these are read too.
_PORTFOLIO_PAGE = re.compile(r"/investors/(?:individual|fund)/(\d+)/")
_COMPANY_LIST = re.compile(r"/api/investors/portfolio/fund/(\d+)/list/")
_BY_COMPANY = (re.compile(r"/corporation/(\d+)/"), re.compile(r"/issuers/(?:v\d+/)?(\d+)/"))
_FMV = re.compile(r"/issuers/(?:v\d+/)?(\d+)/fair-market-value/")


def _known(url: str, data, found: set) -> None:
    pk = None
    if isinstance(data, dict) and str(data.get("corporation_pk") or "").isdigit():
        pk = str(data["corporation_pk"])
    elif (m := _PORTFOLIO_PAGE.search(url)):
        pk = m.group(1)
    if pk:
        found.add(urllib.parse.urljoin(START_URL, f"/api/investors/portfolio/fund/{pk}/list/"))
    if (m := _COMPANY_LIST.search(url)) and isinstance(data, dict):
        pk = m.group(1)
        companies = (data.get("results") or {}).get("companies") if isinstance(data.get("results"), dict) else None
        for c in companies or []:
            cid = str((c or {}).get("corporation_id") or "")
            if cid.isdigit():
                found.add(urllib.parse.urljoin(START_URL, f"/api/investors/holdings/portfolio/{pk}/corporation/{cid}/securities/"))
                found.add(urllib.parse.urljoin(START_URL, f"/api/issuers/v1/{cid}/fair-market-value/portfolio/{pk}/"))


def ingest(conn, url: str, data) -> dict:
    """One reply the extension read from Carta. Returns more Carta addresses worth reading."""
    if not carta_url(url):
        raise CartaWebError("Only replies from carta.com are read")
    cap = _capture(conn)
    size = sum(len(json.dumps(c["data"])) for c in cap)
    blob = json.dumps(data)
    if size + len(blob) <= MAX_CAPTURE and not any(c["url"] == url for c in cap):
        cap.append({"url": url, "data": data})
        db.set_setting(conn, sk.CARTA_WEB_CAPTURE, json.dumps(cap))
    found: set = set()
    _links(data, found)
    _known(url, data, found)
    return {"follow": sorted(found - {c["url"] for c in cap})[:20]}


# ------------------------------------------------------------------------------------------------ reading

def _company_of(d: dict) -> dict | None:
    flat = {k: d.get(k) for k in d if not isinstance(d.get(k), (dict, list))}
    name = carta._pick(flat, *_COMPANY_NAME)
    if not name and carta._pick(flat, *_COMPANY_ID) and isinstance(d.get("name"), str):
        name = d["name"]   # e.g. Carta's list of your companies: {"name": ..., "corporation_id": ...}
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


_GONE = ("CANCELED", "CANCELLED", "EXPIRED", "TERMINATED", "FORFEITED", "REPURCHASED", "TRANSFERRED", "CONVERTED")


def _gone(d: dict) -> bool:
    """A grant that's no longer yours (canceled, expired, forfeited), which Carta still lists."""
    if any(d.get(k) is True for k in ("is_canceled", "isCanceled", "is_expired", "isExpired", "is_terminated", "isTerminated")):
        return True
    return str(d.get("status") or "").upper() in _GONE


def _security(d: dict) -> dict | None:
    flat = {k: v for k, v in d.items()}
    if not any(carta._pick(flat, s) not in (None, "") for s in _SECURITY_SIGNS):
        return None
    kinds = " ".join(str(carta._pick(flat, k) or "") for k in ("optionType", "subType", "securityType", "type", "label", "kind")).lower()
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
    gone: set[str] = set()

    def company_key(c: dict) -> str:
        key = f"carta:{c['id']}" if c.get("id") else "carta:" + re.sub(r"[^a-z0-9]+", "-", c["name"].lower()).strip("-")
        have = companies.setdefault(key, {"id": key, "name": c["name"], "price": None, "price_date": None})
        if have["name"] is None:
            have["name"] = c["name"]
        if c.get("price") and (not have["price"] or (c.get("price_date") or "") >= (have["price_date"] or "")):
            have["price"], have["price_date"] = c["price"], c.get("price_date")
        return key

    def walk(obj, ctx: str | None, depth: int = 0):
        if depth > 30:
            return
        if isinstance(obj, dict):
            g = _security(obj)
            # A security's own name is not a company's; a company inside it (its issuer) is.
            c = _company_of({k: v for k, v in obj.items() if isinstance(v, dict)} if g else obj)
            here = company_key(c) if c else ctx
            if g:
                # A security that says which company issued it belongs to that company, even when the company's
                # name is in another reply (Carta's holdings replies give only the issuer's number).
                issuer = carta._pick({k: v for k, v in obj.items() if not isinstance(v, (dict, list))}, *_COMPANY_ID)
                owner = f"carta:{issuer}" if issuer else here
                if owner and _gone(obj):
                    gone.add(g["id"])
                elif owner:
                    grants[g["id"]] = (owner, g, obj)
            for v in obj.values():
                if isinstance(v, (dict, list)):
                    walk(v, here, depth + 1)
        elif isinstance(obj, list):
            for v in obj:
                walk(v, ctx, depth + 1)

    for c in capture:
        url, data = c.get("url") or "", c.get("data")
        by_url = next((f"carta:{m.group(1)}" for rx in _BY_COMPANY if (m := rx.search(url))), None)
        if (m := _FMV.search(url)) and isinstance(data, dict):
            price = carta._num(carta._pick(data, *_PRICE))
            if price:
                company_key({"id": m.group(1), "name": None, "price": price, "price_date": None})
        walk(data, by_url)
    for cid, _, _ in grants.values():   # a company known only by its number
        have = companies.setdefault(cid, {"id": cid, "name": None, "price": None, "price_date": None})
        if not have["name"]:
            have["name"], have["unnamed"] = f"Carta company {cid.split(':', 1)[1]}", True
    used = {cid for cid, _, _ in grants.values()}
    return {"companies": [c for k, c in companies.items() if k in used], "grants": list(grants.values()),
            "gone": sorted(gone - set(grants))}


def finish(conn) -> dict:
    """Save what was read: Carta's companies and grants replace the ones Carta sent before."""
    found = read(_capture(conn))
    if not found["grants"]:
        db.set_setting(conn, sk.CARTA_WEB_LAST_ERROR, "Runway didn't find any grants in what it read from Carta. Download what "
                                                      "it read (Settings -> Browser extension -> Carta) so the reading can be fixed.")
        return {"companies": 0, "grants": 0, "pages": len(_capture(conn))}
    today = date.today().isoformat()
    for c in found["companies"]:
        row = conn.execute(select(EquityCompany.id).where(EquityCompany.id == c["id"])).fetchone()
        when = c["price_date"] or (today if c["price"] else None)
        if row:   # a company Carta gave no name for this time keeps the one it has
            ec = EquityCompany   # the CASE and COALESCEs, decided here: what Carta didn't send leaves the stored value
            conn.execute(update(ec).where(ec.id == c["id"]).values(
                name=ec.name if c.get("unnamed") else c["name"],
                share_price=ec.share_price if c["price"] is None else c["price"],
                price_as_of=ec.price_as_of if when is None else when, source="carta"))
        else:
            conn.execute(insert(EquityCompany).values(id=c["id"], name=c["name"], share_price=c["price"], price_as_of=when,
                                                      source="carta"))
    for cid, g, raw in found["grants"]:
        carta._save_grant(conn, cid, g, raw)
    for gid in found["gone"]:   # canceled or expired since the last import
        conn.execute(delete(EquityGrant).where(EquityGrant.id == gid, EquityGrant.source == "carta"))
    db.set_setting(conn, sk.CARTA_WEB_LAST, today)
    db.set_setting(conn, sk.CARTA_WEB_LAST_ERROR, None)
    return {"companies": len(found["companies"]), "grants": len(found["grants"]), "pages": len(_capture(conn))}
