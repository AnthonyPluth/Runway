"""Sending Web Push notifications (RFC 8030) with VAPID (RFC 8292) and encrypted payloads (RFC 8291).

The protocol work (signing, encryption) is done by pywebpush; this module keeps the server's VAPID key and turns
the push service's answers into something Runway can act on. Works with every browser push service, including
Apple's (iOS 16.4+, for web apps added to the Home Screen).
"""
from __future__ import annotations

import base64
import json

import py_vapid
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pywebpush import WebPushException, webpush

from . import db


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64u(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def valid_public_key(p256dh: str) -> bool:
    """True if a subscription's key is a real P-256 point (what browsers send)."""
    try:
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), unb64u(p256dh))
        return True
    except (ValueError, TypeError):
        return False


def vapid_keys(conn) -> tuple[py_vapid.Vapid02, str]:
    """This server's VAPID key (made once and kept in the database, as 32 raw bytes in hex).
    Returns (key, public key as base64url for the browser's applicationServerKey)."""
    raw = db.get_setting(conn, "vapid_private_key")
    if not raw:
        key = ec.generate_private_key(ec.SECP256R1())
        raw = format(key.private_numbers().private_value, "064x")
        db.set_setting(conn, "vapid_private_key", raw)
    vapid = py_vapid.Vapid02.from_raw(b64u(bytes.fromhex(raw)).encode())
    public = vapid.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return vapid, b64u(public)


class Gone(Exception):
    """The subscription no longer exists (the app was removed or notifications were turned off)."""


def send(sub: dict, message: dict, vapid: py_vapid.Vapid02, subject: str, ttl: int = 86400,
         urgency: str = "normal", timeout: int = 15) -> int:
    """Send one notification. sub = {endpoint, p256dh, auth}. Raises Gone for dead subscriptions."""
    try:
        r = webpush({"endpoint": sub["endpoint"], "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}},
                    data=json.dumps(message, separators=(",", ":")), vapid_private_key=vapid,
                    vapid_claims={"sub": subject}, ttl=ttl, timeout=timeout, headers={"Urgency": urgency})
        return r.status_code
    except WebPushException as e:
        status = e.response.status_code if e.response is not None else None
        if status in (404, 410):
            raise Gone(sub["endpoint"]) from e
        # Only the status: the reply's text is the push service's (or whoever answered), not something to pass on.
        raise RuntimeError(f"push service said {status}" if status else "couldn't reach the push service") from e
