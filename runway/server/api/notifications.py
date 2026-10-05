"""Push notifications: the devices subscribed, what they hear about, and a test message. Each person's own: with
sign-in, nobody sees or changes another person's devices or what they're told about (notify.devices)."""
from __future__ import annotations

from ...domain import notify
from ... import oidc
from ...providers import webpush
from ..common import ApiError, _current


def _me() -> str | None:
    return notify.person(getattr(_current, "user", None))


def _mine(conn, endpoint: str) -> dict | None:
    """One of the signed-in person's devices (or one that's nobody's), by its address."""
    return next((s for s in notify.devices(conn, _me()) if endpoint and s["endpoint"] == endpoint), None)


def api_push(conn, _q, _b):
    _priv, pub = webpush.vapid_keys(conn)
    me = _me()
    return {"public_key": pub, "prefs": notify.prefs(conn, me),
            "devices": [{"endpoint": s["endpoint"], "device": s["device"], "created": s["created"], "last_ok": s["last_ok"],
                         "last_error": s["last_error"], "unclaimed": oidc.enabled() and not s["user_sub"]}
                        for s in notify.devices(conn, me)],
            "recent": notify.recent(conn, me)}


def api_push_subscribe(conn, _q, body):
    sub = body.get("subscription") or {}
    if not isinstance(sub, dict) or not isinstance(sub.get("keys") or {}, dict):
        raise ApiError("That isn't a push subscription.")
    try:
        notify.subscribe(conn, sub, str(body.get("device") or ""), (getattr(_current, "user", None) or {}).get("sub"))
    except ValueError as e:
        raise ApiError(str(e)) from e
    return {"ok": True}


def api_push_unsubscribe(conn, _q, body):
    endpoint = str(body.get("endpoint") or "")
    if _mine(conn, endpoint):   # (someone else's device: as if it weren't there)
        notify.unsubscribe(conn, endpoint)
    return {"ok": True}


def api_push_prefs(conn, _q, body):
    try:
        return notify.save_prefs(conn, body, _me())
    except ValueError as e:
        raise ApiError(str(e)) from e


def api_push_test(conn, _q, body):
    endpoint = str(body.get("endpoint") or "")
    device = _mine(conn, endpoint) if endpoint else None
    if endpoint and not device:
        raise ApiError("Nothing was delivered. That device isn't one of yours.")
    if device and oidc.enabled() and not device["user_sub"]:
        raise ApiError("Nothing was delivered. That device was turned on before sign-in: turn notifications on again from it.")
    r = notify.send_all(conn, {"title": "Runway notifications are on", "body": "You'll hear about card payments, low balances and missed bills here.",
                               "url": "/#overview", "tag": "test"}, only=endpoint or None,
                        to=_me() if oidc.enabled() else notify.EVERYONE)
    if not r["sent"]:
        raise ApiError("Nothing was delivered. " + ("; ".join(r["failed"]) if r["failed"] else "No device is subscribed."))
    return r
