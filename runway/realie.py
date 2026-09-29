"""Home value estimates from Realie (an automated valuation, similar in spirit to a Zestimate).

The free plan allows 25 lookups a month, so Runway looks each home up at most once a week (automatically for homes
set to update from Realie, or when you ask), counts its own lookups, and stops at the monthly limit so it never runs
up charges (set RUNWAY_REALIE_MONTHLY_LIMIT for a paid plan).

Realie looks a property up by its street line and two-letter state; when that matches homes in several towns, the
city and zip from the address pick the right one. Its reply comes in one of two shapes, chosen per Realie account
(the newer nested one is the default for accounts made after August 2026), so both are read.
"""
from __future__ import annotations

import json
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta

from . import db, networth
from . import settings_keys as sk

REFRESH_DAYS = 7   # at most one lookup per home per week, whether automatic or asked for


def monthly_limit() -> int:
    try:
        return max(0, int(os.environ.get("RUNWAY_REALIE_MONTHLY_LIMIT") or 25))
    except ValueError:
        return 25


class RealieError(Exception):
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
    return bool(db.get_setting(conn, sk.REALIE_API_KEY))


def used_this_month(conn, today: date | None = None) -> int:
    today = today or date.today()
    return int(db.get_setting(conn, sk.realie_calls(today)) or 0)


_STATE_ZIP = re.compile(r"\b([A-Za-z]{2})\.?(?:\s+(\d{5})(?:-\d{4})?)?\s*$")


def split_address(address: str) -> dict:
    """"1 Main St, Springfield, IL 62701" → street, city, state, zip. The state is required; city and zip help
    pick the right home when the street is in several towns."""
    parts = [p.strip() for p in (address or "").split(",") if p.strip()]
    if len(parts) < 2:
        raise RealieError("Add the property's full address (street, city, state, zip) first.")
    m = _STATE_ZIP.search(parts[-1])
    if not m:
        raise RealieError("Add the state to the property's address (street, city, state, zip), e.g. \"1 Main St, Springfield, IL 62701\".")
    rest = parts[-1][:m.start()].strip()          # "Springfield IL 62701" written without a comma before the state
    city = rest or (parts[-2] if len(parts) >= 3 else "")
    return {"street": parts[0], "city": city, "state": m.group(1).upper(), "zip": m.group(2) or ""}


def _model_value(r: dict):
    return r.get("modelValue") or ((r.get("realieValuation") or {}).get("ml") or {}).get("value")


def _city(r: dict) -> str:
    return str(r.get("city") or (r.get("propertyLocation") or {}).get("city") or "").strip()


def _zip(r: dict) -> str:
    return str(r.get("zipCode") or (r.get("propertyLocation") or {}).get("zipCode") or "").strip()[:5]


def _matches(r: dict, want: dict) -> bool:
    """The record isn't in another town: its city and zip don't contradict the address (either may be missing)."""
    city, zip_ = _city(r), _zip(r)
    return not ((city and want["city"] and city.lower() != want["city"].lower()) or (zip_ and want["zip"] and zip_ != want["zip"]))


def value_estimate(conn, address: str, today: date | None = None) -> dict:
    today = today or date.today()
    key = db.get_setting(conn, sk.REALIE_API_KEY)
    if not key:
        raise RealieError("Add a Realie API key in Settings first.")
    if not (address or "").strip():
        raise RealieError("Add the property's full address (street, city, state, zip) first.")
    want = split_address(address)
    limit = monthly_limit()
    if used_this_month(conn, today) >= limit:
        raise RealieError(f"Already used this month's {limit} free Realie lookups; try again next month.")
    base = os.environ.get("RUNWAY_REALIE_URL", "https://app.realie.ai").rstrip("/")
    url = f"{base}/api/public/property/address/?{urllib.parse.urlencode({'address': want['street'], 'state': want['state']})}"
    req = urllib.request.Request(url, headers={"Authorization": key, "Accept": "application/json", "User-Agent": "Runway/0.1"})
    db.set_setting(conn, sk.realie_calls(today), str(used_this_month(conn, today) + 1))
    conn.commit()
    try:
        with urllib.request.urlopen(req, timeout=30, context=_ctx()) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode())
            detail = body.get("message") or body.get("error")
        except Exception:
            detail = None
        if e.code in (401, 403):
            raise RealieError("Realie didn't accept the API key. Check it in Settings.") from e
        if e.code == 404:
            raise RealieError("Realie couldn't find that address. Check the spelling and include city, state and zip.") from e
        if e.code == 429:
            raise RealieError("Realie says this key's lookups are used up for now; try again later.") from e
        raise RealieError(f"Realie error ({detail or f'HTTP {e.code}'})") from e
    except urllib.error.URLError as e:
        raise RealieError(f"Couldn't reach Realie: {e.reason}") from e
    records = data.get("property") if isinstance(data, dict) else None
    records = [r for r in (records if isinstance(records, list) else [records]) if isinstance(r, dict) and r]
    if not records:
        raise RealieError("Realie couldn't find that address. Check the spelling and include city, state and zip.")
    record = next((r for r in records if _matches(r, want)), None)
    if record is None:
        raise RealieError(f"Realie found that street, but not in {want['city'] or want['zip']}. Check the city and zip.")
    value = _model_value(record)
    try:
        value = float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        value = 0.0
    if value <= 0:   # Realie sends 0 when it has no estimate (its estimates don't cover every state yet)
        raise RealieError("Realie doesn't have a value estimate for that home yet.")
    return {"value": value, "low": None, "high": None}


def refresh_asset(conn, asset_id: int, today: date | None = None) -> dict:
    today = today or date.today()
    a = conn.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
    if not a:
        raise RealieError("Not found")
    nxt = next_lookup(a["last_lookup"])
    if nxt and nxt > today:
        raise RealieError(f"Already looked up from Realie on {date.fromisoformat(a['last_lookup'][:10]):%b %-d}; "
                          f"Runway checks each home once a week, so the next lookup is {nxt:%b %-d}.")
    est = value_estimate(conn, a["address"], today)
    networth.set_value(conn, asset_id, est["value"], "realie", today, est["low"], est["high"])
    conn.execute("UPDATE assets SET last_lookup=? WHERE id=?", (today.isoformat(), asset_id))
    return est


def next_lookup(last_lookup: str | None) -> date | None:
    """The first day a home can be looked up again, or None if it never has been."""
    return date.fromisoformat(last_lookup[:10]) + timedelta(days=REFRESH_DAYS) if last_lookup else None


def refresh_due(conn, today: date | None = None) -> int:
    """Weekly refresh for homes that update from Realie. Returns how many were refreshed."""
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
        except RealieError:
            break
    return n
