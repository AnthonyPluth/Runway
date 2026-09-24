"""Home value estimates from RentCast (an automated valuation, similar in spirit to a Zestimate).

The free Developer plan allows 50 lookups a month; Runway counts its own lookups and stops at that limit so it
never runs up overage charges. Homes set to update from RentCast are refreshed about once a month.
"""
from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta

from . import db, networth

MONTHLY_LIMIT = 50
REFRESH_DAYS = 30


class RentCastError(Exception):
    pass


def _ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    try:
        import certifi  # type: ignore

        ctx.load_verify_locations(certifi.where())
    except Exception:
        pass
    return ctx


def configured(conn) -> bool:
    return bool(db.get_setting(conn, "rentcast_api_key"))


def used_this_month(conn, today: date | None = None) -> int:
    today = today or date.today()
    return int(db.get_setting(conn, f"rentcast_calls:{today:%Y-%m}") or 0)


def value_estimate(conn, address: str, today: date | None = None) -> dict:
    today = today or date.today()
    key = db.get_setting(conn, "rentcast_api_key")
    if not key:
        raise RentCastError("Add a RentCast API key in Settings first.")
    if not (address or "").strip():
        raise RentCastError("Add the property's full address (street, city, state, zip) first.")
    if used_this_month(conn, today) >= MONTHLY_LIMIT:
        raise RentCastError(f"Already used this month's {MONTHLY_LIMIT} free RentCast lookups; try again next month.")
    base = os.environ.get("RUNWAY_RENTCAST_URL", "https://api.rentcast.io/v1").rstrip("/")
    url = f"{base}/avm/value?{urllib.parse.urlencode({'address': address.strip(), 'compCount': 5})}"
    req = urllib.request.Request(url, headers={"X-Api-Key": key, "Accept": "application/json", "User-Agent": "Runway/0.1"})
    db.set_setting(conn, f"rentcast_calls:{today:%Y-%m}", str(used_this_month(conn, today) + 1))
    conn.commit()
    try:
        with urllib.request.urlopen(req, timeout=30, context=_ctx()) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode()).get("message")
        except Exception:
            detail = None
        if e.code in (401, 403):
            raise RentCastError("RentCast didn't accept the API key. Check it in Settings.") from e
        if e.code == 404:
            raise RentCastError("RentCast couldn't find that address. Check the spelling and include city, state and zip.") from e
        raise RentCastError(f"RentCast error ({detail or f'HTTP {e.code}'})") from e
    except urllib.error.URLError as e:
        raise RentCastError(f"Couldn't reach RentCast: {e.reason}") from e
    price = data.get("price")
    if price is None:
        raise RentCastError("RentCast didn't return a value for that address.")
    return {"value": float(price), "low": data.get("priceRangeLow"), "high": data.get("priceRangeHigh")}


def refresh_asset(conn, asset_id: int, today: date | None = None) -> dict:
    today = today or date.today()
    a = conn.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
    if not a:
        raise RentCastError("Not found")
    est = value_estimate(conn, a["address"], today)
    networth.set_value(conn, asset_id, est["value"], "rentcast", today, est["low"], est["high"])
    conn.execute("UPDATE assets SET last_lookup=? WHERE id=?", (today.isoformat(), asset_id))
    return est


def refresh_due(conn, today: date | None = None) -> int:
    """Monthly refresh for homes that update from RentCast. Returns how many were refreshed."""
    today = today or date.today()
    if not configured(conn):
        return 0
    n = 0
    cutoff = (today - timedelta(days=REFRESH_DAYS)).isoformat()
    for a in conn.execute("SELECT id FROM assets WHERE kind='home' AND auto_update=1 AND address IS NOT NULL "
                          "AND (last_lookup IS NULL OR last_lookup<=?)", (cutoff,)).fetchall():
        try:
            refresh_asset(conn, a["id"], today)
            n += 1
        except RentCastError:
            break
    return n
