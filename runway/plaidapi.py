"""Talking to Plaid: its address, credentials, and the one request helper everything else uses.

Both plaid.py (investments, linking) and plaidbank.py (banks and cards) build on this, so neither needs the other
to reach Plaid; plaid.py re-exports these names, which is where the rest of Runway gets them."""
from __future__ import annotations

import http.client
import json
import os
import ssl
import urllib.error
import urllib.request

from . import db, secretbox
from . import settings_keys as sk

HOSTS = {"sandbox": "https://sandbox.plaid.com", "production": "https://production.plaid.com"}


class PlaidError(Exception):
    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code


def _ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    try:
        import certifi  # type: ignore

        ctx.load_verify_locations(certifi.where())
    except (ImportError, OSError):   # certifi is optional; without it (or its bundle) the system certs still apply
        pass
    return ctx


def configured(conn) -> bool:
    return bool(db.get_setting(conn, sk.PLAID_CLIENT_ID) and db.get_setting(conn, sk.PLAID_SECRET))


def base_url(conn) -> str:
    override = os.environ.get("RUNWAY_PLAID_URL")
    if override:
        return override.rstrip("/")
    return HOSTS.get(db.get_setting(conn, sk.PLAID_ENV, "production") or "production", HOSTS["production"])


def call(conn, path: str, body: dict) -> dict:
    client_id, secret = db.get_setting(conn, sk.PLAID_CLIENT_ID), db.get_setting(conn, sk.PLAID_SECRET)
    if not client_id or not secret:
        raise PlaidError("Add your Plaid client ID and secret in Settings first.")
    if body.get("access_token"):   # stored encrypted (runway/secretbox.py); decrypted only to send to Plaid
        try:
            body = {**body, "access_token": secretbox.decrypt(body["access_token"])}
        except secretbox.SecretError as e:
            raise PlaidError(str(e), "RUNWAY_SECRET_KEY") from e
    payload = json.dumps({"client_id": client_id, "secret": secret, **body}).encode()
    req = urllib.request.Request(
        base_url(conn) + path, data=payload, method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "Runway/0.1", "Plaid-Version": "2020-09-14"},
    )
    try:
        with urllib.request.urlopen(req, timeout=90, context=_ctx()) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode())
        except Exception:
            err = {}
        code = err.get("error_code")
        msg = err.get("display_message") or err.get("error_message") or f"HTTP {e.code}"
        raise PlaidError(f"Plaid: {msg}" + (f" ({code})" if code else ""), code) from e
    except urllib.error.URLError as e:
        raise PlaidError(f"Couldn't reach Plaid: {e.reason}") from e
    except (OSError, http.client.HTTPException) as e:   # a timeout or a dropped connection while reading the reply
        raise PlaidError(f"Couldn't reach Plaid: {str(e) or type(e).__name__}") from e
    except ValueError as e:   # not JSON: a proxy's error page, or a reply cut short
        raise PlaidError("Plaid sent back a reply Runway couldn't read.") from e
