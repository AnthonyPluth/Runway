"""App lock (runway/applock.py): turning it on and off for this signed-in device, the challenges, unlocking and
locking. Each acts on the session that calls it and nothing else: there's no way to see or change another device's.
While the lock is on and locked, the server refuses everything else (server/handler.py, applock.LOCKED_OK)."""
from __future__ import annotations

import time

from ... import applock
from ...storage import db
from ..common import ApiError, _current
from ..contract import LockChallenge, LockChallengeAsk, LockIdle, LockKeyShare, LockRegister, LockStatus, LockUnlock


def _key() -> str:
    """This request's session (a hash of it), set by server/handler.py; none without sign-in (or from /mcp)."""
    key = getattr(_current, "session_key", None)
    if not key:
        raise ApiError(applock.NO_SIGN_IN)
    return key


def _refused(e: applock.LockError) -> ApiError:
    """What the web app is told: a session that locked while this request was on its way gets the 423 a locked
    session gets (server/handler.py), anything else the lock's own message."""
    if isinstance(e, applock.Locked):
        return ApiError(applock.LOCKED, 423, extra={"locked": True})
    return ApiError(str(e))


def _status(conn) -> LockStatus:
    s = applock.status(conn, getattr(_current, "session_key", None), time.time())
    return {"available": s["available"], "on": s["on"], "locked": s["locked"], "idle": s["idle"],
            "credential_id": s["credential_id"], "device_id": s["device_id"]}


def api_lock(conn, _q, _b) -> LockStatus:
    return _status(conn)


def api_lock_challenge(conn, _q, body: LockChallengeAsk) -> LockChallenge:
    key, purpose = _key(), body.get("purpose")
    rp = applock.relying_party()
    if rp is None:
        raise ApiError(applock.NO_SIGN_IN)
    if purpose not in ("register", "unlock"):
        raise ApiError('Send "purpose": "register" or "unlock"')
    st = applock.status(conn, key, time.time())
    if purpose == "unlock" and not st["on"]:
        raise ApiError(applock.NOT_ON)
    return {"challenge": applock.challenge(key, purpose, time.time()), "rp_id": rp[1],
            "credential_id": st["credential_id"] if purpose == "unlock" else None}


def api_lock_register(conn, _q, body: LockRegister) -> LockStatus:
    try:
        applock.turn_on(conn, _key(), dict(body), time.time())
    except applock.LockError as e:
        raise _refused(e) from None
    return _status(conn)


def api_lock_unlock(conn, _q, body: LockUnlock) -> LockStatus:
    try:
        applock.unlock(conn, _key(), dict(body), time.time())
    except applock.LockError as e:
        raise _refused(e) from None
    return _status(conn)


def api_lock_engage(conn, _q, _b) -> LockStatus:
    applock.engage(conn, _key())
    return _status(conn)


def api_lock_settings(conn, _q, body: LockIdle) -> LockStatus:
    try:
        applock.set_idle(conn, _key(), body.get("idle"), time.time())
    except applock.LockError as e:
        raise _refused(e) from None
    return _status(conn)


def api_lock_off(conn, _q, _b) -> LockStatus:
    applock.turn_off(conn, _key())
    return _status(conn)


def api_lock_key_share(conn, _q, _b) -> LockKeyShare:
    """The device's cache key share (applock.key_share): sent as no-store JSON like every API reply, and never logged
    (the request log and Sentry carry the route and status only). Refused with 403 without an unlock just before it (or
    a second time for one), 423 when locked meanwhile, 429 when asked too often, 400 when the lock isn't on."""
    key = _key()
    try:
        share, expires = applock.key_share(conn, key, getattr(_current, "user", None), time.time())
    except applock.Lapsed as e:
        with db.session() as other:   # (this request's own connection is rolled back with the refusal)
            applock.drop_share(other, key)
        raise ApiError(str(e), 403) from None
    except applock.NotFresh as e:
        raise ApiError(str(e), 403) from None
    except applock.TooMany as e:
        raise ApiError(str(e), 429) from None
    except applock.LockError as e:
        raise _refused(e) from None
    return {"share": share, "expires": expires}
