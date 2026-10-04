"""Carta: your equity (option grants, RSUs, shares) and each company's latest fair market value, through Carta's
Portfolio API.

Access: Carta's API is OAuth 2.0. An app made in Carta's developer portal is a Playground app (Carta's test
environment, dummy data, its own sign-in at login.playground.carta.team); reading your real account needs Carta to
grant the app production access, with production credentials. With a client id and secret from Carta's developer portal, you
connect once from Settings (you sign in at Carta and approve read access to your portfolio), and Runway keeps the
token (encrypted) and refreshes it. Carta's mock environment serves sample data with any token, to try it out.

Carta's responses are read loosely (by field names, wherever they sit in the reply), and what Carta sent is kept
with each company and grant, so a field this doesn't pick up yet can be added later.
"""
from __future__ import annotations

import base64
import json
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from typing import Any

from sqlalchemy import insert, select, update

from . import db, equity, monitoring, tls, validate
from . import settings_keys as sk
from .models import EquityCompany, EquityGrant

ENVS: dict[str, dict[str, Any]] = {
    "production": {"api": "https://api.carta.com", "authorize": "https://login.app.carta.com/o/authorize/",
                   "token": "https://login.app.carta.com/o/access_token/"},
    # Apps made in Carta's developer portal start here: Carta's Playground, with its own sign-in and dummy data. They
    # work against production only once Carta grants the app production access.
    "playground": {"api": "https://api.playground.carta.team", "authorize": "https://login.playground.carta.team/o/authorize/",
                   "token": "https://login.playground.carta.team/o/access_token/"},
    "mock": {"api": "https://mock-api.carta.com", "authorize": None, "token": None},
}
SCOPES = "read_portfolio_info read_portfolio_securities"
VERSION = "v1alpha1"
MAX_RAW = 50_000
MAX_PAGES = 50


class CartaError(Exception):
    pass


def settings(conn) -> dict:
    return {"env": db.get_setting(conn, sk.CARTA_ENV) or "production", "client_id": db.get_setting(conn, sk.CARTA_CLIENT_ID),
            "has_secret": bool(db.get_setting(conn, sk.CARTA_CLIENT_SECRET)),
            "connected": bool(db.get_setting(conn, sk.CARTA_ACCESS_TOKEN)) or (db.get_setting(conn, sk.CARTA_ENV) == "mock"
                                                                               and bool(db.get_setting(conn, sk.CARTA_MOCK_ON))),
            "last_sync": db.get_setting(conn, sk.CARTA_LAST_SYNC), "last_error": db.get_setting(conn, sk.CARTA_LAST_ERROR),
            # through the browser extension
            "web_last": db.get_setting(conn, sk.CARTA_WEB_LAST), "web_error": db.get_setting(conn, sk.CARTA_WEB_LAST_ERROR),
            "web_capture": bool(db.get_setting(conn, sk.CARTA_WEB_CAPTURE) not in (None, "[]"))}


def save_settings(conn, body: dict) -> None:
    env = body.get("env") or db.get_setting(conn, sk.CARTA_ENV) or "production"
    if env not in ENVS:
        raise CartaError("Environment is production, playground or mock")
    if env != (db.get_setting(conn, sk.CARTA_ENV) or "production"):
        disconnect(conn)
    db.set_setting(conn, sk.CARTA_ENV, env)
    if "client_id" in body:
        db.set_setting(conn, sk.CARTA_CLIENT_ID, (body.get("client_id") or "").strip() or None)
    if body.get("client_secret"):
        db.set_setting(conn, sk.CARTA_CLIENT_SECRET, body["client_secret"].strip())


def disconnect(conn) -> None:
    for k in (sk.CARTA_ACCESS_TOKEN, sk.CARTA_REFRESH_TOKEN, sk.CARTA_TOKEN_EXPIRES, sk.CARTA_MOCK_ON, sk.CARTA_OAUTH_STATE):
        db.set_setting(conn, k, None)


# ------------------------------------------------------------------------------------------------ OAuth

def authorize_url(conn, redirect_uri: str) -> str:
    """Where to send you to approve Runway at Carta. The mock environment needs no approval."""
    env = settings(conn)["env"]
    if env == "mock":
        db.set_setting(conn, sk.CARTA_MOCK_ON, "1")
        return redirect_uri + "?mock=1"
    client_id = db.get_setting(conn, sk.CARTA_CLIENT_ID)
    if not client_id or not db.get_setting(conn, sk.CARTA_CLIENT_SECRET):
        raise CartaError("Enter the client id and secret from Carta's developer portal first.")
    state = secrets.token_urlsafe(24)
    db.set_setting(conn, sk.CARTA_OAUTH_STATE, f"{state} {int(time.time())}")
    db.set_setting(conn, sk.CARTA_REDIRECT_URI, redirect_uri)   # the token request must repeat it exactly
    return ENVS[env]["authorize"] + "?" + urllib.parse.urlencode(
        {"response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri, "scope": SCOPES, "state": state})


def _post_form(url: str, data: dict, client_id: str, secret: str, opener=None) -> dict:
    body = urllib.parse.urlencode(data).encode()
    auth = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json", "Authorization": f"Basic {auth}"})
    try:
        resp = opener(req) if opener else tls.urlopen(req, timeout=30)
        with resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:200]
        raise CartaError(f"Carta refused the sign-in ({e.code}): {detail}") from e
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise CartaError(f"Couldn't reach Carta: {e}") from e


def _store_token(conn, tok: dict) -> None:
    if not tok.get("access_token"):
        raise CartaError("Carta didn't send an access token")
    db.set_setting(conn, sk.CARTA_ACCESS_TOKEN, tok["access_token"])
    if tok.get("refresh_token"):
        db.set_setting(conn, sk.CARTA_REFRESH_TOKEN, tok["refresh_token"])
    db.set_setting(conn, sk.CARTA_TOKEN_EXPIRES, str(int(time.time()) + int(tok.get("expires_in") or 3600)))


def finish_authorize(conn, code: str, state: str, opener=None) -> None:
    saved = (db.get_setting(conn, sk.CARTA_OAUTH_STATE) or "").split(" ")
    redirect_uri = db.get_setting(conn, sk.CARTA_REDIRECT_URI) or ""
    db.set_setting(conn, sk.CARTA_OAUTH_STATE, None)
    if len(saved) != 2 or not secrets.compare_digest(saved[0], state or "") or time.time() - int(saved[1]) > 900:
        raise CartaError("That Carta sign-in expired or didn't start here. Connect again from Settings.")
    env = ENVS[settings(conn)["env"]]
    tok = _post_form(env["token"], {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri},
                     db.get_setting(conn, sk.CARTA_CLIENT_ID) or "", db.get_setting(conn, sk.CARTA_CLIENT_SECRET) or "", opener)
    _store_token(conn, tok)


def _token(conn, opener=None) -> str:
    env = settings(conn)["env"]
    if env == "mock":
        return "mock-token"   # the mock API takes any token
    tok = db.get_setting(conn, sk.CARTA_ACCESS_TOKEN)
    if not tok:
        raise CartaError("Connect Carta in Settings first.")
    if int(db.get_setting(conn, sk.CARTA_TOKEN_EXPIRES) or 0) - 60 < time.time() and db.get_setting(conn, sk.CARTA_REFRESH_TOKEN):
        _store_token(conn, _post_form(ENVS[env]["token"], {"grant_type": "refresh_token",
                                                            "refresh_token": db.get_setting(conn, sk.CARTA_REFRESH_TOKEN)},
                                      db.get_setting(conn, sk.CARTA_CLIENT_ID) or "", db.get_setting(conn, sk.CARTA_CLIENT_SECRET) or "",
                                      opener))
        conn.commit()   # Carta may have rotated the refresh token: keep the new one even if the sync then fails
        tok = db.get_setting(conn, sk.CARTA_ACCESS_TOKEN) or tok   # _store_token refused a reply without one
    return tok


def _get(conn, path: str, params: dict | None = None, opener=None):
    """GET from the Portfolio API. Returns the JSON, or None for a 404 (an endpoint this account doesn't have)."""
    base = ENVS[settings(conn)["env"]]["api"]
    url = f"{base}/{VERSION}/{path}" + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {_token(conn, opener)}", "Accept": "application/json"})
    try:
        resp = opener(req) if opener else tls.urlopen(req, timeout=30)
        with resp:
            return json.loads(resp.read().decode() or "null")
    except urllib.error.HTTPError as e:
        if e.code in (404, 405):
            return None
        if e.code in (401, 403):
            raise CartaError("Carta refused access. Connect again from Settings (or ask Carta to enable your app's portfolio scopes).") from e
        raise CartaError(f"Carta answered {e.code} for {path}") from e
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise CartaError(f"Couldn't reach Carta: {e}") from e


def _list(conn, path: str, opener=None) -> list[dict]:
    """Every item of a list endpoint, following page tokens."""
    out, token = [], None
    for _ in range(MAX_PAGES):
        data = _get(conn, path, {"pageSize": 100, **({"pageToken": token} if token else {})}, opener)
        if data is None:
            break
        out += _items(data)
        token = data.get("nextPageToken") if isinstance(data, dict) else None
        if not token:
            break
    return out


def _items(data) -> list[dict]:
    """The list of objects in a reply, whatever it's called."""
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
                return v
    return []


# ------------------------------------------------------------------------------------------------ reading

def _snake(k: str) -> str:
    return re.sub(r"(?<!^)([A-Z])", r"_\1", k).lower()


def _pick(d: dict, *keys):
    """The first of these keys with a value (camelCase or snake_case), in the object or one level down."""
    keys = tuple(dict.fromkeys([*keys, *(_snake(k) for k in keys)]))
    for k in keys:
        if isinstance(d, dict) and d.get(k) not in (None, "", [], {}):
            return d[k]
    for v in (d or {}).values():
        if isinstance(v, dict):
            for k in keys:
                if v.get(k) not in (None, "", [], {}):
                    return v[k]
    return None


def _num(v) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return validate.parse_external(v)
    if isinstance(v, dict):
        for k in ("amount", "value", "quantity", "decimal"):
            if k in v:
                return _num(v[k])
        return None
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", str(v))
    return validate.parse_external(m.group(0), drop=",") if m else None


def _day(v) -> str | None:
    if not v:
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 1e8:   # a timestamp (Carta's web app: *_ts_ms)
        return date.fromtimestamp(v / 1000 if v > 1e11 else v).isoformat() if 0 < v < 1e14 else None
    if isinstance(v, dict):
        v = _pick(v, "value", "date")
    m = re.match(r"(\d{4}-\d{2}-\d{2})", str(v))
    return m.group(1) if m else None


def _schedule(text) -> tuple[int | None, int | None, int]:
    """(months, cliff months, every) from Carta's vesting schedule name, e.g. "1/48 monthly, 1 year cliff"."""
    s = str(text or "").lower()
    if not s:
        return None, None, 1
    total = cliff = None
    m = re.search(r"(\d+)\s*(year|month)s?\s*cliff", s) or re.search(r"cliff\D{0,10}(\d+)\s*(year|month)", s)
    if m:
        cliff = int(m.group(1)) * (12 if m.group(2) == "year" else 1)
        s = s[:m.start()] + s[m.end():]   # the rest of the name is the length
    m = re.search(r"1\s*/\s*(\d+)", s)
    if m:
        total = int(m.group(1))
    elif (m := re.search(r"(\d+)\s*years?", s)):
        total = int(m.group(1)) * 12
    elif (m := re.search(r"(\d+)\s*months?", s)):
        total = int(m.group(1))
    every = 3 if "quarter" in s else 12 if "annual" in s or "yearly" in s else 1
    # A name that reads as an impossible length is no schedule (equity.MAX_MONTHS: longer runs off the calendar).
    total = total if total is None or total <= equity.MAX_MONTHS else None
    cliff = cliff if cliff is None or cliff <= equity.MAX_MONTHS else None
    return total, cliff, every


def _grant(item: dict, kind_hint: str) -> dict | None:
    gid = _pick(item, "id", "securityId", "optionGrantId", "certificateId")
    qty = _num(_pick(item, "quantity", "issuedQuantity", "quantityIssued", "numberOfShares", "shares", "optionsGranted"))
    if not gid or not qty:
        return None
    kind = kind_hint
    t = str(_pick(item, "optionType", "subType", "type", "securityType", "stockOptionType") or "").lower()
    if kind_hint == "option":
        kind = "iso" if "iso" in t or "incentive" in t else "nso"
    total, cliff, every = _schedule(_pick(item, "vestingSchedule", "vestingScheduleName", "vestingPlan", "vesting"))
    return {
        "id": f"carta:{gid}", "kind": kind,
        "label": _pick(item, "label", "securityLabel", "name", "certificateLabel"),
        "quantity": qty,
        "strike": _num(_pick(item, "exercisePrice", "strikePrice", "exercisePricePerShare")) if kind in ("iso", "nso") else None,
        "granted_on": _day(_pick(item, "issueDate", "grantDate", "issuedDate", "boardApprovalDate", "issueTsMs", "grantTsMs")),
        "vest_start": _day(_pick(item, "vestingStartDate", "vestingStart", "vestingCommencementDate")),
        "vest_months": total, "cliff_months": cliff, "vest_every": every,
        "exercised": _num(_pick(item, "exercisedQuantity", "quantityExercised", "exercised")) or 0.0,
        "vested_reported": _num(_pick(item, "vestedQuantity", "quantityVested", "vested", "cumulativeVestedShares")),
        "expires_on": _day(_pick(item, "lastExercisableDate", "grantExpirationDate", "expirationDate", "expirationTsMs")),
    }


def _latest_price(conn, pid: str, iid: str, issuer: dict, opener=None) -> tuple[float | None, str | None]:
    """The company's latest fair market value per share: on the issuer, or from its FMV history."""
    price = _num(_pick(issuer, "latestFairMarketValue", "fairMarketValue", "fmv", "sharePrice", "pricePerShare"))
    when = _day(_pick(issuer, "fairMarketValueDate", "fmvDate", "effectiveDate"))
    if price:
        return price, when
    for path in (f"portfolios/{pid}/issuers/{iid}/fairMarketValues", f"portfolios/{pid}/issuers/{iid}/fmvs"):
        items = _list(conn, path, opener)
        if items:
            best = max(items, key=lambda x: _day(_pick(x, "effectiveDate", "date", "valuationDate")) or "")
            return _num(_pick(best, "value", "fairMarketValue", "pricePerShare", "price")), _day(_pick(best, "effectiveDate", "date", "valuationDate"))
    return None, None


def sync(conn, opener=None, today: date | None = None) -> dict:
    """Read every portfolio's companies and securities, and save them (replacing what Carta sent last time)."""
    today = today or date.today()
    out = {"companies": 0, "grants": 0}
    try:
        portfolios = _list(conn, "portfolios", opener)
        for p in portfolios:
            pid = _pick(p, "id", "portfolioId")
            if not pid:
                continue
            for issuer in _list(conn, f"portfolios/{pid}/issuers", opener):
                iid = _pick(issuer, "id", "issuerId")
                if not iid:
                    continue
                cid = f"carta:{iid}"
                name = _pick(issuer, "legalName", "name", "doingBusinessAsName", "displayName") or "Company"
                price, when = _latest_price(conn, pid, iid, issuer, opener)
                raw = json.dumps(issuer, separators=(",", ":"))[:MAX_RAW]
                as_of = when or (today.isoformat() if price else None)
                if conn.execute(select(EquityCompany.id).where(EquityCompany.id == cid)).fetchone():
                    # COALESCE(new, old), decided here: a value Carta didn't send leaves the stored one
                    conn.execute(update(EquityCompany).where(EquityCompany.id == cid).values(
                        name=name, share_price=EquityCompany.share_price if price is None else price,
                        price_as_of=EquityCompany.price_as_of if as_of is None else as_of, raw=raw, source="carta"))
                else:
                    conn.execute(insert(EquityCompany).values(id=cid, name=name, share_price=price, price_as_of=as_of,
                                                              source="carta", raw=raw))
                out["companies"] += 1
                for path, hint in (("optionGrants", "option"), ("rsuAwards", "rsu"), ("rsus", "rsu"), ("certificates", "shares")):
                    for item in _list(conn, f"portfolios/{pid}/issuers/{iid}/{path}", opener):
                        g = _grant(item, hint)
                        if g:
                            _save_grant(conn, cid, g, item, today)
                            out["grants"] += 1
        db.set_setting(conn, sk.CARTA_LAST_SYNC, today.isoformat())
        db.set_setting(conn, sk.CARTA_LAST_ERROR, None)
    except CartaError as e:
        conn.rollback()   # none of a half-read sync, but the error is kept (the caller's session rolls back too)
        db.set_setting(conn, sk.CARTA_LAST_ERROR, monitoring.public_text(str(e))[:300])
        conn.commit()
        raise
    return out


def _save_grant(conn, cid: str, g: dict, item: dict, today: date) -> None:
    raw = json.dumps(item, separators=(",", ":"))[:MAX_RAW]
    old = conn.execute(select(EquityGrant.vest_months, EquityGrant.cliff_months, EquityGrant.vest_every)
                       .where(EquityGrant.id == g["id"])).fetchone()
    today_iso = today.isoformat()
    if old:
        # A schedule you filled in yourself stays when Carta doesn't say.
        for k in ("vest_months", "cliff_months"):
            if g[k] is None:
                g[k] = old[k]
        if g["vest_every"] == 1 and old["vest_every"]:
            g["vest_every"] = old["vest_every"]
        # g's keys are the grant columns _grant() (or carta_web) fills in, never a request's
        sets = {k: v for k, v in g.items() if k != "id"}
        conn.execute(update(EquityGrant).where(EquityGrant.id == g["id"]).values(
            **sets, company_id=cid, source="carta", raw=raw, vested_reported_on=today_iso))
    else:
        conn.execute(insert(EquityGrant).values(**g, company_id=cid, source="carta", raw=raw, vested_reported_on=today_iso))
