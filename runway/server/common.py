"""The pieces every part of the server shares: the error a handler raises, the signed-in person for this request,
request references, and which host names Runway answers to."""
from __future__ import annotations

import ipaddress
import os
import secrets
import threading
import urllib.parse
from datetime import date

from dateutil.relativedelta import relativedelta


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


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
