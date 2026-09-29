"""Net worth: the summary, assets you add (homes, cars, ...), and Realie home values."""
from __future__ import annotations

from datetime import date

from ... import db, networth, realie
from ... import settings_keys as sk
from ..common import ApiError


def api_networth(conn, _q, _b):
    out = networth.summary(conn)
    out["assets_list"] = networth.assets(conn)
    for a in out["assets_list"]:   # homes are looked up once a week at most: when the next lookup is allowed
        nxt = realie.next_lookup(a.get("last_lookup"))
        a["next_lookup"] = nxt.isoformat() if nxt and nxt > date.today() else None
    out["loan_accounts"] = db.rows(conn.execute(
        "SELECT id, COALESCE(display_name, name) AS name, kind FROM accounts WHERE kind='loan' AND hidden=0 ORDER BY name"))
    out["realie"] = {"configured": realie.configured(conn), "used": realie.used_this_month(conn), "limit": realie.monthly_limit()}
    return out


def api_asset_add(conn, _q, body):
    try:
        return {"id": networth.save_asset(conn, body)}
    except ValueError as e:
        raise ApiError(str(e)) from e


def api_asset_update(conn, _q, body, asset_id):
    try:
        networth.save_asset(conn, body, int(asset_id))
    except ValueError as e:
        raise ApiError(str(e)) from e
    return {"ok": True}


def api_asset_remove(conn, _q, _b, asset_id):
    networth.remove_asset(conn, int(asset_id))
    return {"ok": True}


def api_asset_refresh(conn, _q, _b, asset_id):
    try:
        est = realie.refresh_asset(conn, int(asset_id))
    except realie.RealieError as e:
        raise ApiError(str(e), 502) from e
    return {"ok": True, **est, "used": realie.used_this_month(conn)}


def api_realie_settings(conn, _q, body):
    key = (body.get("api_key") or "").strip()
    if body.get("clear"):
        db.set_setting(conn, sk.REALIE_API_KEY, None)
    elif key:
        db.set_setting(conn, sk.REALIE_API_KEY, key)
    return {"ok": True, "configured": realie.configured(conn)}
