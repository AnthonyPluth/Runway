"""Push notifications: the devices subscribed, what they hear about, and a test message."""
from __future__ import annotations

from ... import db, notify, webpush
from ..common import ApiError, _current


def api_push(conn, _q, _b):
    _priv, pub = webpush.vapid_keys(conn)
    return {"public_key": pub, "prefs": notify.prefs(conn),
            "devices": [{"endpoint": s["endpoint"], "device": s["device"], "created": s["created"], "last_ok": s["last_ok"],
                         "last_error": s["last_error"]} for s in notify.subscriptions(conn)],
            "recent": db.rows(conn.execute("SELECT title, sent FROM notify_log ORDER BY sent DESC LIMIT 8"))}


def api_push_subscribe(conn, _q, body):
    try:
        notify.subscribe(conn, body.get("subscription") or {}, str(body.get("device") or ""), (getattr(_current, "user", None) or {}).get("sub"))
    except ValueError as e:
        raise ApiError(str(e)) from e
    return {"ok": True}


def api_push_unsubscribe(conn, _q, body):
    notify.unsubscribe(conn, str(body.get("endpoint") or ""))
    return {"ok": True}


def api_push_prefs(conn, _q, body):
    try:
        return notify.save_prefs(conn, body)
    except ValueError as e:
        raise ApiError(str(e)) from e


def api_push_test(conn, _q, body):
    r = notify.send_all(conn, {"title": "Runway notifications are on", "body": "You'll hear about card payments, low balances and missed bills here.",
                               "url": "/#overview", "tag": "test"}, only=body.get("endpoint") or None)
    if not r["sent"]:
        raise ApiError("Nothing was delivered. " + ("; ".join(r["failed"]) if r["failed"] else "No device is subscribed."))
    return r
