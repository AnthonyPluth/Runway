"""The browser extension's key: how the extension's calls to /api/ext/ prove they're yours, without a sign-in.

Security-sensitive (see SECURITY.md): only a SHA-256 hash of the key is kept, it's compared in constant time, and it
works until a new key replaces it, it's removed under Settings → Connections, or the person who made it can no longer
sign in (oidc.access_lapsed). It doesn't expire with age: keys used to end 90 days after they were made, and one already
past that when Runway was updated was removed then (migration 0048), so a key that had stopped working stays stopped."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from datetime import datetime, timedelta

from ...storage import db
from ... import oidc
from ...storage import settings_keys as sk

TOUCH_EVERY = 60    # seconds between notes of when the key was last used

def new_token(conn, owner: dict | None = None) -> str:
    """A new key for the browser extension (replacing any earlier one). Only a hash of it is kept, with who made it
    (`owner`: the signed-in person, {sub, email}): the key ends when they can no longer sign in (oidc.access_lapsed),
    as their sessions and the assistants they approved do."""
    token = "rwx_" + secrets.token_urlsafe(32)
    db.set_setting(conn, sk.RETAIL_TOKEN_HASH, hashlib.sha256(token.encode()).hexdigest())
    db.set_setting(conn, sk.RETAIL_TOKEN_CREATED, datetime.now().isoformat(timespec="seconds"))
    db.set_setting(conn, sk.RETAIL_TOKEN_OWNER,
                   json.dumps({"sub": owner["sub"], "email": owner.get("email")}) if owner and owner.get("sub") else None)
    db.set_setting(conn, sk.RETAIL_TOKEN_USED, None)
    return token


def remove_token(conn) -> None:
    for key in (sk.RETAIL_TOKEN_HASH, sk.RETAIL_TOKEN_CREATED, sk.RETAIL_TOKEN_OWNER, sk.RETAIL_TOKEN_USED):
        db.set_setting(conn, key, None)


def _when(text: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(text) if text else None
    except ValueError:
        return None


def token_owner(conn) -> dict | None:
    """Who made the key ({sub, email}), or None: made without sign-in, or before owners were kept."""
    try:
        owner = json.loads(db.get_setting(conn, sk.RETAIL_TOKEN_OWNER) or "null")
    except ValueError:
        return None
    return owner if isinstance(owner, dict) and owner.get("sub") else None


def token_problem(conn) -> str | None:
    """Why the key no longer works, or None: "owner_gone" (the person who made it can no longer sign in). A key made
    without sign-in, or before owners were kept, has no owner: it works until it's replaced or removed."""
    owner = token_owner(conn)
    if owner and oidc.access_lapsed(conn, owner.get("sub"), owner.get("email")):
        return "owner_gone"
    return None


def token_check(conn, authorization: str | None) -> str | None:
    """Why a call's key is refused, or None if it's the key and it still works: "unknown" (no key, or not this one),
    else token_problem's reason. A working key's last use is noted (at most every TOUCH_EVERY seconds)."""
    want = db.get_setting(conn, sk.RETAIL_TOKEN_HASH)
    m = re.match(r"Bearer\s+(\S+)$", (authorization or "").strip())
    if not want or not m or not hmac.compare_digest(hashlib.sha256(m.group(1).encode()).hexdigest(), want):
        return "unknown"
    problem = token_problem(conn)
    if problem:
        return problem
    now = datetime.now()
    used = _when(db.get_setting(conn, sk.RETAIL_TOKEN_USED))
    if not used or now - used > timedelta(seconds=TOUCH_EVERY):
        db.set_setting(conn, sk.RETAIL_TOKEN_USED, now.isoformat(timespec="seconds"))
    return None


REFUSALS = {
    "owner_gone": "The person who made this key can no longer sign in to Runway. Make a new one under Settings → Connections.",
    "unknown": "Runway doesn't know this key. Make a new one under Settings → Connections.",
}
