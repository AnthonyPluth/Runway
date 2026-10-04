"""The pieces every part of the server shares: the error a handler raises, reading what a request sends (ids, whole
numbers, texts), the signed-in person for this request, request references, and which host names Runway answers to."""
from __future__ import annotations

import ipaddress
import os
import re
import secrets
import threading
import urllib.parse
from datetime import date

from dateutil.relativedelta import relativedelta

from .. import validate


class ApiError(Exception):
    """What a handler answers when it can't do what was asked: a message for the person, and the status (400 unless
    said otherwise)."""
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ------------------------------------------------------------------------------------------ reading a request

_ID = re.compile(r"[0-9]{1,18}")   # a row's number: digits only (no sign, spaces or other scripts' digits), fits 64 bits
_query = validate.Validator(ApiError, drop="", not_number="The {label} must be a whole number")


def row_id(value, missing: str = "Not found", status: int = 404) -> int:
    """A row's number from the address (/api/rules/12) or a request: anything else is `missing` (404, as a number
    that isn't there would be)."""
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ApiError(missing, status)
    return int(value)


def clamped_int(v, label: str, default: int, low: int, high: int) -> int:
    """A whole number someone sent, kept between low and high (a bigger or smaller one is moved in, as these always
    were); `default` (kept in too) when there's none, and a 400 for one that isn't a whole number."""
    n = _query.number(v, label)
    if isinstance(v, bool) or (n is not None and n != int(n)):
        raise ApiError(_query.not_number.format(label=label))
    return max(low, min(default if n is None else int(n), high))


def query_int(q, key: str, default: int, low: int, high: int, label: str | None = None) -> int:
    """A whole number from the query string (?limit=50): clamped_int."""
    return clamped_int((q.get(key) or [""])[0], label or key, default, low, high)


def text(v, field: str) -> str:
    """A text field as sent ("" when it's left out), so a handler's own check of it ("Enter a name") answers; anything
    else (a number, a list, an object, true) is refused, rather than taken as empty (which can mean "clear it")."""
    if v is None:
        return ""
    if not isinstance(v, str):
        raise ApiError(f'Send "{field}" as text')
    return v


MONTH_YEARS = 10   # how far from today a month in a request may be


def _month_range(q):
    today = date.today()
    try:
        y, m = (int(x) for x in (q.get("month", [f"{today:%Y-%m}"])[0]).split("-"))
        start = date(y, m, 1)
    except ValueError:
        raise ApiError("Month must look like 2026-09") from None
    if abs(y - today.year) > MONTH_YEARS:   # a budget's rollover is added up month by month from where it started
        raise ApiError(f"Month must be within {MONTH_YEARS} years of today")
    return start, start + relativedelta(months=1)


def request_ref() -> str:
    return secrets.token_hex(4)


_current = threading.local()
EXTRA_HOSTS = {h.strip().lower() for h in (os.environ.get("RUNWAY_ALLOWED_HOSTS") or "").split(",") if h.strip()}
if os.environ.get("RUNWAY_PUBLIC_URL"):   # the address you open Runway at is always allowed
    EXTRA_HOSTS.add((urllib.parse.urlsplit(os.environ["RUNWAY_PUBLIC_URL"]).hostname or "").lower())
# Names that can't be pointed at an outside website: this machine, mDNS (.local), home-router names, Tailscale.
SAFE_SUFFIXES = (".local", ".lan", ".home.arpa", ".internal", ".ts.net")


def host_allowed(host_header: str) -> bool:
    host = host_header.strip().lower()
    if host.startswith("["):                      # [::1]:8765
        host = host[1:host.find("]")] if "]" in host else host
    elif host.count(":") == 1:
        host = host.split(":")[0]
    if not host:
        return False
    if "*" in EXTRA_HOSTS or host in EXTRA_HOSTS or host == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(host)
        # An address typed directly (not a name) can't be used for DNS rebinding.
        return ip.is_loopback or ip.is_private or ip in ipaddress.ip_network("100.64.0.0/10")
    except ValueError:
        pass
    return host.endswith(SAFE_SUFFIXES) or "." not in host   # bare names like "nas" or "homeserver"
