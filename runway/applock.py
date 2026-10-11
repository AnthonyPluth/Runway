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
keep it unlocked); a script running in Runway's own pages; data the app already has in memory.

The cache key share (#357): the device's encrypted on-device cache is opened with a key the web app derives from its
passkey's PRF output and a random share (SHARE_BYTES) this server keeps for the device, encrypted (secretbox), in its
app_locks row (key_share). Neither half alone opens the cache, so the device's row ending (each way above) ends the
cache too, at the latest SHARE_WINDOW later (how long the device may keep its own copy of the share, offline). The share
is made the first time it's asked for and stays the same until the row ends: a new one would make the whole cache
unreadable. It's handed out (key_share) only to this session, unlocked, once per passkey check: an unlock or turning the
lock on lets the session have it once within SHARE_FRESH seconds, and nothing else does. At most SHARE_LIMIT asks per
SHARE_LIMIT_WINDOW per session. It's never logged or reported, doesn't travel in backups (app_locks doesn't), and is
refused to the assistants (mcp_access blocks /api/lock and below) and the browser extension (its key reaches /api/ext/
only)."""
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
from .storage import secretbox
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
SHARE_BYTES = 32
SHARE_WINDOW = 72 * 3600       # how long a device may go on using its own copy of the share without asking again
SHARE_FRESH = 60               # the share is handed out once, this soon after a passkey check (unlock or turning on)
SHARE_LIMIT = 30               # asks for the share per session...
SHARE_LIMIT_WINDOW = 3600      # ... in this many seconds

# What a locked session may still call: the lock screen's own calls. Anything else is refused with 423.
LOCKED_OK = frozenset({("GET", "/api/lock"), ("POST", "/api/lock/challenge"), ("POST", "/api/lock/unlock"),
                       ("POST", "/api/lock/engage")})

NO_SIGN_IN = "App lock needs Runway’s sign-in (OIDC): without it there’s no session on this device for it to lock."
NOT_CHECKED = "Face ID, Touch ID or the passcode didn’t check out. Try again."
EXPIRED = "That took too long. Try again."
OTHER_ADDRESS = "App lock only works at Runway’s own address ({origin}). Open Runway there and try again."
NO_VERIFICATION = "The device didn’t confirm it was you (Face ID, Touch ID or the passcode). Try again."
LOCKED = "Runway is locked on this device. Unlock it to carry on."
NOT_ON = "App lock isn’t on for this device."
NOT_FRESH = "Unlock Runway on this device again first."
TOO_MANY = "Runway was asked for this device’s cache key too often. Try again later."
LAPSED = "Your access to Runway has ended."


class LockError(Exception):
    """A request the lock refuses, with what to tell the person."""


class Locked(LockError):
    """The session locked after the server let this request through (Lock now in another tab, say, landing in between):
    it gets the 423 a locked session gets, and nothing it asked for is written."""

    def __init__(self) -> None:
        super().__init__(LOCKED)


class NotFresh(LockError):
    """The cache key share was asked for without a passkey check just before it (or a second time for one check)."""


class Lapsed(LockError):
    """The session's person can no longer sign in (oidc.access_lapsed), though the session hasn't ended yet."""


class TooMany(LockError):
    """The cache key share was asked for more than SHARE_LIMIT times in SHARE_LIMIT_WINDOW by this session."""


_challenges: dict[tuple[str, str], tuple[bytes, float]] = {}   # (session, purpose) -> (what it is, until when)
_challenges_lock = threading.Lock()
_asked: dict[str, list[float]] = {}   # session -> when it asked for the cache key share, within SHARE_LIMIT_WINDOW
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

def _keep(key: str, purpose: str, value: bytes, now: float, ttl: float) -> None:
    with _challenges_lock:
        for k in [k for k, (_c, until) in _challenges.items() if until < now]:
            del _challenges[k]
        while len(_challenges) >= MAX_CHALLENGES:   # bounded: the oldest go first
            del _challenges[min(_challenges, key=lambda k: _challenges[k][1])]
        _challenges[(key, purpose)] = (value, now + ttl)


def challenge(key: str, purpose: str, now: float) -> str:
    """A new challenge for this session and purpose ("register" or "unlock"), replacing any earlier one."""
    c = secrets.token_bytes(32)
    _keep(key, purpose, c, now, CHALLENGE_TTL)
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
    # Only an unlocked lock is replaced: a new passkey isn't the old one's signature, so a session that locked after
    # this request was let through stays locked (Locked) rather than coming back unlocked with a passkey of its own.
    conn.execute(delete(AppLock).where(AppLock.session == key, AppLock.unlocked_until > now))
    if _row(conn, key) is not None:
        raise Locked()
    device = "dev_" + secrets.token_urlsafe(16)
    conn.execute(insert(AppLock).values(id=device, session=key, **cols, idle=idle, **_unlocked(now, idle), created=now,
                                        last_used=now))
    _checked(key, device, now)


def unlock(conn, key: str, body: dict, now: float) -> None:
    rp = relying_party()
    r = _row(conn, key)
    if rp is None or r is None:
        raise LockError(NOT_ON)
    count = verify_assertion(body, dict(r), _take_challenge(key, "unlock", now), *rp)
    # The row whose passkey signed, only: one that replaced it in between (turned on again) isn't unlocked by the old one.
    done = conn.execute(update(AppLock).where(AppLock.session == key, AppLock.id == r["id"])
                        .values(sign_count=count, last_used=now, **_unlocked(now, r["idle"])))
    if done.rowcount != 1:
        raise LockError(NOT_CHECKED)
    _checked(key, r["id"], now)


def engage(conn, key: str) -> None:
    """Lock now (the web app locked itself: opened, or back after `idle`, or Lock now)."""
    conn.execute(update(AppLock).where(AppLock.session == key).values(unlocked_until=None))


def set_idle(conn, key: str, idle, now: float) -> None:
    """Change how long away locks it, and move the unlock's end to match. Only while unlocked: the server let this
    request through unlocked, but a Lock now landing in between must not be undone without a passkey's signature, so
    the update is conditional and a lock that locked meanwhile is Locked (423), with nothing changed."""
    idle = idle_seconds(idle)
    r = _row(conn, key)
    if r is None:
        raise LockError(NOT_ON)
    done = conn.execute(update(AppLock).where(AppLock.session == key, AppLock.unlocked_until > now)
                        .values(idle=idle, unlocked_until=min(now + idle + GRACE, (r["unlocked_at"] or now) + MAX_UNLOCKED)))
    if done.rowcount == 0:
        raise Locked()


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
            # Only an unlock still running is moved on: a Lock now landing since the read above stays locked, and this
            # call is refused like any other on a locked session (unless the lock was turned off meanwhile).
            done = conn.execute(update(AppLock).where(AppLock.session == key, AppLock.unlocked_until > now)
                                .values(unlocked_until=until))
            if done.rowcount == 0:
                return _row(conn, key) is not None
    return False


# ------------------------------------------------------------------------------------------------ the cache key share

def _checked(key: str, device: str, now: float) -> None:
    """This session's passkey was just checked for `device` (an unlock, or turning the lock on): it may have the cache
    key share once, within SHARE_FRESH seconds."""
    _keep(key, "share", device.encode(), now, SHARE_FRESH)


def _within_limit(key: str, now: float) -> bool:
    """Counts this ask; False once the session has asked SHARE_LIMIT times within SHARE_LIMIT_WINDOW."""
    since = now - SHARE_LIMIT_WINDOW
    with _challenges_lock:
        for k in [k for k, asked in _asked.items() if not asked or asked[-1] <= since]:
            del _asked[k]
        while len(_asked) >= MAX_CHALLENGES and key not in _asked:   # bounded: the longest quiet go first
            del _asked[min(_asked, key=lambda k: _asked[k][-1])]
        asked = [t for t in _asked.get(key, ()) if t > since]
        if len(asked) >= SHARE_LIMIT:
            _asked[key] = asked
            return False
        _asked[key] = [*asked, now]
        return True


def _readable(stored: str | None) -> bytes | None:
    """The share a row keeps, or None when it has none, or none this server can read (Runway's key changed and the
    old one is gone): then it's replaced, which leaves the device's cache unreadable, as a new device's would be."""
    if not secretbox.is_encrypted(stored):
        return None
    try:
        plain = secretbox.decrypt(stored) or ""
    except secretbox.SecretError:
        return None
    if not _B64URL.fullmatch(plain) or len(plain) != len(_b64e(bytes(SHARE_BYTES))):
        return None
    share = base64.urlsafe_b64decode(plain + "=" * (-len(plain) % 4))
    return share if len(share) == SHARE_BYTES else None


def key_share(conn, key: str, owner: dict | None, now: float) -> tuple[str, int]:
    """This device's cache key share (base64url) and until when the device may keep using it without asking again
    (whole seconds since the epoch, server time: now + SHARE_WINDOW). Only for this session, with its lock on and
    unlocked, and only once per passkey check (see _checked); made the first time. A lock that locked, or was turned off or on again, since the check gets nothing, and
    nothing is written for it: every write here is conditional on this row, still unlocked."""
    if relying_party() is None:
        raise LockError(NO_SIGN_IN)
    if not _within_limit(key, now):
        raise TooMany(TOO_MANY)
    with _challenges_lock:
        fresh = _challenges.pop((key, "share"), None)   # used up whatever follows: one share per check
    r = _row(conn, key)
    if r is None:
        raise LockError(NOT_ON)
    if not (r["unlocked_until"] and r["unlocked_until"] > now):
        raise Locked()
    if not fresh or fresh[1] < now or not hmac.compare_digest(fresh[0], r["id"].encode()):
        raise NotFresh(NOT_FRESH)
    if owner and oidc.access_lapsed(conn, owner.get("sub"), owner.get("email"), now):
        raise Lapsed(LAPSED)
    this = (AppLock.session == key, AppLock.id == r["id"], AppLock.unlocked_until > now)
    if _readable(r["key_share"]) is None:
        same = AppLock.key_share.is_(None) if r["key_share"] is None else AppLock.key_share == r["key_share"]
        conn.execute(update(AppLock).where(*this, same)
                     .values(key_share=secretbox.encrypt(_b64e(secrets.token_bytes(SHARE_BYTES)))))
    # Read back (a request alongside may have made it first: there's one share per device), from the row only while
    # it's still this one and unlocked.
    now_kept = conn.execute(select(AppLock.key_share).where(*this)).fetchone()
    if now_kept is None:
        if _row(conn, key) is None:
            raise LockError(NOT_ON)
        raise Locked()
    share = _readable(now_kept["key_share"])
    if share is None:   # (just written with this server's key: can't happen)
        raise RuntimeError("The cache key share just kept can't be read back")
    return _b64e(share), int(now) + SHARE_WINDOW


def drop_share(conn, key: str) -> None:
    """Forget this session's device's share (its person's access ended): its cache can't be opened once the device's
    own copy runs out."""
    conn.execute(update(AppLock).where(AppLock.session == key).values(key_share=None))
