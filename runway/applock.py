"""App lock: Face ID, Touch ID or the device's passcode before the web app shows anything on a device, checked by this
server (WebAuthn, with the device's own platform authenticator).

It sits on top of sign-in, not instead of it. A lock belongs to one signed-in browser (its auth_sessions row; an
iPhone's Home Screen app has a sign-in of its own) and ends with it: signing out, the session running out, or being
taken off the allow-list all end it, and signing in again starts without one. While a session's lock is on and not
unlocked, the server refuses that session's API calls (423) and the assistants' consent page, except the few calls the
lock screen needs (LOCKED_OK). So the lock holds for someone holding the unlocked phone who opens /api/... in its
browser, not only in the web app's own screens.

Turning it on, the web app makes a passkey on the device (navigator.credentials.create, user verification required)
and sends its public key with the authenticator's answer to a challenge from here. Unlocking, it asks the authenticator
to sign a new challenge (single use, for this session only, CHALLENGE_TTL seconds), with user verification, and this
checks the signature with that public key: the origin, the relying party, user presence and verification, and the
signature counter. Nothing secret is kept (the key's private half never leaves the device).

An unlock lasts while the app is in use: each API call made while it's on screen moves the unlock's end to `idle`
seconds (the device's choice, TIMEOUTS) plus GRACE from now. The web app checks in every minute while on screen, and
marks the calls it makes from the background (X-Runway-Hidden: 1), which don't count, so the server locks at most GRACE
after the web app would. However much it's used, an unlock ends MAX_UNLOCKED after it was made.

One row per device (app_locks: its id, the passkey's id and public key, created and last used) is what anything kept
for that device later attaches to (#357's encrypted cache: a key share the server holds for the device, referring to
app_locks.id with ON DELETE CASCADE). A device ends by its row being removed, never marked: with its sign-in (the
foreign key: signing out, the session running out or being deleted, someone taken off OIDC_ALLOWED_EMAILS, whose
sessions oidc.session_user deletes, or, with OIDC_ALLOWED_GROUPS, the session's fixed end, as oidc.access_lapsed
judges), when it's turned off on the device, and when it's turned on again (a new passkey, a new row).

What it doesn't stop: someone who already has the session cookie elsewhere, while the device is unlocked (they could
keep it unlocked); a script running in Runway's own pages; data the app already has in memory."""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import secrets
import threading
import urllib.parse

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from sqlalchemy import delete, insert, select, update

from . import oidc
from .storage.models import AppLock

TIMEOUTS = (0, 60, 300, 900)   # Immediately, 1, 5 or 15 minutes away
DEFAULT_IDLE = 60
GRACE = 120                    # on top of `idle`: two of the web app's check-ins
MAX_UNLOCKED = 12 * 3600
SLIDE_EVERY = 15               # an unlock's end is written at most this often
CHALLENGE_TTL = 120
MAX_CHALLENGES = 1000          # challenges handed out and not yet used, kept at once
ES256, RS256 = -7, -257
UP, UV, AT = 0x01, 0x04, 0x40  # authenticator data flags: user present, user verified, attested credential data

# What a locked session may still call: the lock screen's own calls. Anything else is refused with 423.
LOCKED_OK = frozenset({("GET", "/api/lock"), ("POST", "/api/lock/challenge"), ("POST", "/api/lock/unlock"),
                       ("POST", "/api/lock/engage")})

NO_SIGN_IN = "App lock needs Runway’s sign-in (OIDC): without it there’s no session on this device for it to lock."
NOT_CHECKED = "Face ID, Touch ID or the passcode didn’t check out. Try again."
EXPIRED = "That took too long. Try again."
OTHER_ADDRESS = "App lock only works at Runway’s own address ({origin}). Open Runway there and try again."
NO_VERIFICATION = "The device didn’t confirm it was you (Face ID, Touch ID or the passcode). Try again."


class LockError(Exception):
    """A request the lock refuses, with what to tell the person."""


_challenges: dict[tuple[str, str], tuple[bytes, float]] = {}
_challenges_lock = threading.Lock()
_B64URL = re.compile(r"[A-Za-z0-9_-]*")


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64d(v, limit: int = 4096) -> bytes:
    """base64url as WebAuthn sends it (no padding); anything else is refused."""
    if not isinstance(v, str) or len(v) > limit or not _B64URL.fullmatch(v):
        raise LockError(NOT_CHECKED)
    try:
        return base64.urlsafe_b64decode(v + "=" * (-len(v) % 4))
    except (binascii.Error, ValueError):
        raise LockError(NOT_CHECKED) from None


# ------------------------------------------------------------------------------------------------ the relying party

def relying_party() -> tuple[str, str] | None:
    """(origin, RP ID) WebAuthn is checked against: RUNWAY_PUBLIC_URL's, which sign-in requires. None without sign-in."""
    if not oidc.enabled():
        return None
    parts = urllib.parse.urlsplit(oidc.config()["public_url"])
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    host = parts.hostname.lower()
    default = {"http": 80, "https": 443}[parts.scheme]
    netloc = f"[{host}]" if ":" in host else host
    if parts.port and parts.port != default:
        netloc += f":{parts.port}"
    return f"{parts.scheme}://{netloc}", host


# ------------------------------------------------------------------------------------------------ challenges

def challenge(key: str, purpose: str, now: float) -> str:
    """A new challenge for this session and purpose ("register" or "unlock"), replacing any earlier one."""
    c = secrets.token_bytes(32)
    with _challenges_lock:
        for k in [k for k, (_c, until) in _challenges.items() if until < now]:
            del _challenges[k]
        while len(_challenges) >= MAX_CHALLENGES:   # bounded: the oldest go first
            del _challenges[min(_challenges, key=lambda k: _challenges[k][1])]
        _challenges[(key, purpose)] = (c, now + CHALLENGE_TTL)
    return _b64e(c)


def _take_challenge(key: str, purpose: str, now: float) -> bytes:
    """This session's challenge for `purpose`, used up (each is good once)."""
    with _challenges_lock:
        found = _challenges.pop((key, purpose), None)
    if not found or found[1] < now:
        raise LockError(EXPIRED)
    return found[0]


# ------------------------------------------------------------------------------------------------ WebAuthn checks

def _check_client_data(raw: bytes, kind: str, expected: bytes, origin: str) -> None:
    """clientDataJSON: what was asked (`kind`), with this challenge, from Runway's own address, not in a frame."""
    try:
        cd = json.loads(raw.decode())
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise LockError(NOT_CHECKED) from None
    if not isinstance(cd, dict) or cd.get("type") != kind:
        raise LockError(NOT_CHECKED)
    if not hmac.compare_digest(_b64d(cd.get("challenge"), 256), expected):
        raise LockError(NOT_CHECKED)
    if cd.get("origin") != origin:
        raise LockError(OTHER_ADDRESS.format(origin=origin))
    if cd.get("crossOrigin") is True:
        raise LockError(NOT_CHECKED)


def _check_auth_data(raw: bytes, rp_id: str) -> tuple[int, int]:
    """Authenticator data: for this relying party, with the person present and verified. Returns (flags, counter)."""
    if len(raw) < 37 or not hmac.compare_digest(raw[:32], hashlib.sha256(rp_id.encode()).digest()):
        raise LockError(NOT_CHECKED)
    flags = raw[32]
    if not (flags & UP and flags & UV):
        raise LockError(NO_VERIFICATION)
    return flags, int.from_bytes(raw[33:37], "big")


def _public_key(spki: bytes, alg: int) -> ec.EllipticCurvePublicKey | rsa.RSAPublicKey:
    """The passkey's public key, of a kind and strength this checks: P-256 for ES256, RSA of 2048 bits or more for RS256."""
    try:
        key = serialization.load_der_public_key(spki)
    except (ValueError, UnsupportedAlgorithm):
        raise LockError(NOT_CHECKED) from None
    if alg == ES256 and isinstance(key, ec.EllipticCurvePublicKey) and isinstance(key.curve, ec.SECP256R1):
        return key
    if alg == RS256 and isinstance(key, rsa.RSAPublicKey) and key.key_size >= 2048:
        return key
    raise LockError("This device’s passkey uses a kind of key Runway doesn’t check. App lock can’t be used on it.")


def _check_signature(key: ec.EllipticCurvePublicKey | rsa.RSAPublicKey, signature: bytes, signed: bytes) -> None:
    try:
        if isinstance(key, ec.EllipticCurvePublicKey):
            key.verify(signature, signed, ec.ECDSA(hashes.SHA256()))
        else:
            key.verify(signature, signed, padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature:
        raise LockError(NOT_CHECKED) from None


def verify_registration(body: dict, expected: bytes, origin: str, rp_id: str) -> dict:
    """The web app's new passkey (navigator.credentials.create): its answer to `expected`, from this origin, for this RP,
    with the person verified, and a public key this can check. Returns the columns to keep.

    The attestation is "none" (Runway doesn't care which make of authenticator it is), so the public key comes from the
    browser's getPublicKey() rather than the CBOR in the attestation; the credential id is checked against the one in
    the authenticator data. A key that isn't the authenticator's would only fail every unlock after."""
    cred_id = _b64d(body.get("credential_id"), 1400)
    client_data = _b64d(body.get("client_data"))
    auth_data = _b64d(body.get("authenticator_data"), 8192)
    spki = _b64d(body.get("public_key"))
    alg = body.get("alg")
    if not cred_id or not isinstance(alg, int) or isinstance(alg, bool):
        raise LockError(NOT_CHECKED)
    _check_client_data(client_data, "webauthn.create", expected, origin)
    flags, count = _check_auth_data(auth_data, rp_id)
    if not flags & AT or len(auth_data) < 55:
        raise LockError(NOT_CHECKED)
    n = int.from_bytes(auth_data[53:55], "big")   # after the AAGUID: the credential id's length, then the id
    if not hmac.compare_digest(auth_data[55:55 + n], cred_id) or n != len(cred_id):
        raise LockError(NOT_CHECKED)
    _public_key(spki, alg)
    return {"credential_id": _b64e(cred_id), "public_key": _b64e(spki), "alg": alg, "sign_count": count}


def verify_assertion(body: dict, lock: dict, expected: bytes, origin: str, rp_id: str) -> int:
    """An unlock (navigator.credentials.get): signed by this lock's passkey, over `expected`, from this origin, for this
    RP, with the person verified, and a counter that moved on (when the authenticator keeps one: Apple's passkeys
    always send 0). Returns the new counter."""
    if not hmac.compare_digest(_b64d(body.get("credential_id"), 1400), _b64d(lock["credential_id"], 1400)):
        raise LockError(NOT_CHECKED)
    client_data = _b64d(body.get("client_data"))
    auth_data = _b64d(body.get("authenticator_data"), 8192)
    signature = _b64d(body.get("signature"))
    _check_client_data(client_data, "webauthn.get", expected, origin)
    _flags, count = _check_auth_data(auth_data, rp_id)
    key = _public_key(_b64d(lock["public_key"]), lock["alg"])
    _check_signature(key, signature, auth_data + hashlib.sha256(client_data).digest())
    if (count or lock["sign_count"]) and count <= lock["sign_count"]:
        raise LockError("This passkey’s counter went backwards, which a copied passkey would do. Sign out and turn app "
                        "lock on again.")
    return count


# ------------------------------------------------------------------------------------------------ a session's lock

def _row(conn, key: str):
    return conn.execute(select(AppLock).where(AppLock.session == key)).fetchone()


def status(conn, key: str | None, now: float) -> dict:
    """Whether this session's lock is on, whether it's locked now, how long away locks it, its passkey's id and the
    device's."""
    r = _row(conn, key) if key else None
    if r is None:
        return {"available": relying_party() is not None and key is not None, "on": False, "locked": False,
                "idle": DEFAULT_IDLE, "credential_id": None, "device_id": None}
    return {"available": True, "on": True, "locked": not (r["unlocked_until"] and r["unlocked_until"] > now),
            "idle": r["idle"], "credential_id": r["credential_id"], "device_id": r["id"]}


def idle_seconds(v) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or v not in TIMEOUTS:
        raise LockError("Choose when to lock: immediately, or after 1, 5 or 15 minutes away.")
    return v


def _unlocked(now: float, idle: int) -> dict:
    return {"unlocked_at": now, "unlocked_until": now + idle + GRACE}


def turn_on(conn, key: str, body: dict, now: float) -> None:
    """Keep the passkey the web app just made (its answer to this session's "register" challenge), unlocked. A device
    that turns it on again gets a new row (a new device id): what was kept for the old passkey goes with the old row."""
    rp = relying_party()
    if rp is None:
        raise LockError(NO_SIGN_IN)
    idle = idle_seconds(body.get("idle", DEFAULT_IDLE))
    cols = verify_registration(body, _take_challenge(key, "register", now), *rp)
    turn_off(conn, key)
    conn.execute(insert(AppLock).values(id="dev_" + secrets.token_urlsafe(16), session=key, **cols, idle=idle,
                                        **_unlocked(now, idle), created=now, last_used=now))


def unlock(conn, key: str, body: dict, now: float) -> None:
    rp = relying_party()
    r = _row(conn, key)
    if rp is None or r is None:
        raise LockError("App lock isn’t on for this device.")
    count = verify_assertion(body, dict(r), _take_challenge(key, "unlock", now), *rp)
    conn.execute(update(AppLock).where(AppLock.session == key)
                 .values(sign_count=count, last_used=now, **_unlocked(now, r["idle"])))


def engage(conn, key: str) -> None:
    """Lock now (the web app locked itself: opened, or back after `idle`, or Lock now)."""
    conn.execute(update(AppLock).where(AppLock.session == key).values(unlocked_until=None))


def set_idle(conn, key: str, idle, now: float) -> None:
    idle = idle_seconds(idle)
    r = _row(conn, key)
    if r is None:
        raise LockError("App lock isn’t on for this device.")
    conn.execute(update(AppLock).where(AppLock.session == key)
                 .values(idle=idle, unlocked_until=min(now + idle + GRACE, (r["unlocked_at"] or now) + MAX_UNLOCKED)))


def turn_off(conn, key: str) -> None:
    conn.execute(delete(AppLock).where(AppLock.session == key))


def refuses(conn, key: str | None, method: str, path: str, hidden: bool, now: float) -> bool:
    """Whether this session is locked for this call (it then gets 423). A call made while the app is on screen (not
    `hidden`) keeps an unlock going (see the module's docstring)."""
    if not key or (method, path) in LOCKED_OK:
        return False
    r = conn.execute(select(AppLock.idle, AppLock.unlocked_at, AppLock.unlocked_until).where(AppLock.session == key)).fetchone()
    if r is None:
        return False
    if not r["unlocked_until"] or r["unlocked_until"] <= now:
        return True
    if not hidden:
        until = min(now + r["idle"] + GRACE, (r["unlocked_at"] or now) + MAX_UNLOCKED)
        if until - r["unlocked_until"] >= SLIDE_EVERY:
            conn.execute(update(AppLock).where(AppLock.session == key).values(unlocked_until=until))
    return False
