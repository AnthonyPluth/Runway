"""Error reports to Sentry, only when you ask for them (SENTRY_DSN). Off, nothing leaves the machine.

Runway holds bank access and your transactions, so a report carries only what it takes to find the bug: the error,
its stack trace (without the values of variables), the request's method and path, and the release. Never request
bodies, cookies, headers or query strings; credentials in addresses (a SimpleFIN access URL has them) and Plaid tokens
are blanked wherever they turn up.

  SENTRY_DSN                   the server's reports (from your Sentry project's Client Keys)
  SENTRY_BROWSER_DSN           the web app's reports; defaults to SENTRY_DSN (RUNWAY_SENTRY_BROWSER=0 turns them off)
  SENTRY_ENVIRONMENT           e.g. production (the default) or staging
  SENTRY_TRACES_SAMPLE_RATE    share of requests to trace for performance, 0 (the default) to 1
"""
from __future__ import annotations

import os
import re
import traceback
import urllib.parse

_enabled = False

# Secrets that can appear in an error's text: user:password@ in an address, and Plaid's tokens.
_USERINFO = re.compile(r"(\b[a-z][a-z0-9+.-]*://)[^/\s@]+@", re.I)
_QUERY = re.compile(r"(\b[a-z][a-z0-9+.-]*://[^\s?#]*)\?[^\s#]*", re.I)
_PLAID_TOKEN = re.compile(r"\b(access|public|link|processor)-(sandbox|development|production)-[0-9a-f-]{8,}", re.I)


def scrub(text):
    if not isinstance(text, str):
        return text
    text = _USERINFO.sub(r"\1[Filtered]@", text)
    text = _QUERY.sub(r"\1?[Filtered]", text)
    return _PLAID_TOKEN.sub("[Filtered]", text)


def _path_only(url: str | None) -> str | None:
    """scheme://host/path, without who's signing in or what's asked."""
    if not url:
        return url
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parts.scheme, parts.hostname or "", parts.path, "", ""))


def _before_send(event, _hint):
    req = event.get("request")
    if req:
        event["request"] = {"method": req.get("method"), "url": _path_only(req.get("url"))}
    event.pop("user", None)
    event["message"] = scrub(event.get("message"))
    if isinstance(event.get("logentry"), dict):
        event["logentry"] = {k: scrub(v) if k in ("message", "formatted") else v for k, v in event["logentry"].items()
                             if k != "params"}
    for exc in (event.get("exception") or {}).get("values") or []:
        exc["value"] = scrub(exc.get("value"))
        for frame in (exc.get("stacktrace") or {}).get("frames") or []:
            frame.pop("vars", None)
    event.pop("extra", None)
    return event


def _before_breadcrumb(crumb, _hint):
    if crumb.get("category") == "query":   # a database query: its SQL helps, the values bound to it are your data
        crumb.pop("data", None)
    data = crumb.get("data")
    if isinstance(data, dict):
        if "url" in data:
            data["url"] = _path_only(data["url"])
        for k in ("http.query", "http.fragment"):
            data.pop(k, None)
    crumb["message"] = scrub(crumb.get("message"))
    return crumb


def init() -> bool:
    """Start reporting if SENTRY_DSN is set. Returns whether it's on."""
    global _enabled
    dsn = (os.environ.get("SENTRY_DSN") or "").strip()
    if not dsn:
        return False
    import sentry_sdk   # loaded only when reporting is on, so it costs nothing otherwise
    try:
        rate = min(1.0, max(0.0, float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE") or 0)))
    except ValueError:
        rate = 0.0
    sentry_sdk.init(
        dsn=dsn, release=os.environ.get("RUNWAY_VERSION") or "dev",
        environment=os.environ.get("SENTRY_ENVIRONMENT") or "production",
        server_name="runway", send_default_pii=False, include_local_variables=False, include_source_context=True,
        max_request_body_size="never", traces_sample_rate=rate,
        before_send=_before_send, before_breadcrumb=_before_breadcrumb,
    )
    _enabled = True
    print("Error reports go to Sentry (SENTRY_DSN is set).", flush=True)
    return True


def enabled() -> bool:
    return _enabled


def report(e: BaseException | None = None, **tags) -> None:
    """Log an error that was caught (the current one, or `e`), and send it to Sentry when that's on."""
    if e is None:
        traceback.print_exc()
    else:
        traceback.print_exception(type(e), e, e.__traceback__)
    if not _enabled:
        return
    import sentry_sdk   # loaded only when reporting is on (see init)
    with sentry_sdk.new_scope() as scope:
        for k, v in tags.items():
            scope.set_tag(k, v)
        sentry_sdk.capture_exception(e)


def browser_dsn() -> str | None:
    if os.environ.get("RUNWAY_SENTRY_BROWSER") == "0":
        return None
    return (os.environ.get("SENTRY_BROWSER_DSN") or os.environ.get("SENTRY_DSN") or "").strip() or None


def browser_config() -> dict | None:
    """What the web app needs to send its own errors (a DSN is meant to be public), or None when that's off."""
    dsn = browser_dsn()
    if not dsn or not browser_origin():   # the page may only send to an https address it's been told about
        return None
    return {"dsn": dsn, "environment": os.environ.get("SENTRY_ENVIRONMENT") or "production",
            "release": os.environ.get("RUNWAY_VERSION") or "dev"}


def browser_origin() -> str | None:
    """Where the web app's reports go, for the Content-Security-Policy's connect-src."""
    dsn = browser_dsn()
    if not dsn:
        return None
    parts = urllib.parse.urlsplit(dsn)
    if parts.scheme != "https" or not parts.hostname or not re.fullmatch(r"[a-z0-9.-]+", parts.hostname, re.I):
        return None
    return f"https://{parts.hostname}" + (f":{parts.port}" if parts.port else "")
