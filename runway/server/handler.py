"""The HTTP server: routing, sign-in, security headers, request limits, static files, streaming, and serve()."""
from __future__ import annotations

import contextlib
import gzip
import hashlib
import html
from http.cookies import SimpleCookie
import json
import mimetypes
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("application/manifest+json", ".webmanifest")
import os
import secrets
import socket
import sys
import threading
import time
import urllib.parse
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import sqlalchemy.exc

from .. import backup, carta, categories, db, merchants, monitoring, oidc, plaid, prices, recurring, retail, secretbox, sfinvest
from .. import settings_keys as sk
from . import sync
from .common import ApiError, _current, host_allowed, request_ref
from .sync import _inv_lock, _sync_lock, background_sync, run_investment_sync, run_sync, sync_on_visit
from .api.investments import live_tickers
from .api.retail import EXT_ROUTES, MAX_EXT_BODY, _retail_categorize_lock, extension_zip
from .routes import ROUTES, _match

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
# Files anyone may fetch: the sign-in pages' look, and what a phone needs to install Runway (it fetches the manifest
# without cookies). None of them hold any data.
PUBLIC_FILES = {"/page.css", "/logo.svg", "/logo-180.png", "/fonts/Geist-Variable.woff2", "/manifest.webmanifest", "/sw.js",
                "/icon-192.png", "/icon-512.png", "/icon-maskable-512.png"}


MAX_JSON_BODY = 1024 * 1024          # API requests are small; anything bigger is refused before it's read
MAX_RESTORE_BODY = 200 * 1024 * 1024
REQUEST_TIMEOUT = 60                 # seconds a client may stall while sending or receiving (slow-client protection)
HEADER_DEADLINE = 30                 # seconds to send the request line and headers in all, however it's trickled in
MIN_BODY_RATE = 16 * 1024            # bytes a second a request body must average, on top of REQUEST_TIMEOUT
MAX_CONCURRENT_REQUESTS = 64

# Plaid Link (Settings → Connections) loads its script and iframe from Plaid; nothing else comes from elsewhere.
PLAID_ORIGINS = "https://cdn.plaid.com"
PLAID_API = "https://production.plaid.com https://sandbox.plaid.com"


def content_security_policy(nonce: str | None = None) -> str:
    """Only Runway's own scripts run (the page's <script> tags carry a per-response nonce; scripts they add, like Plaid
    Link, are trusted through 'strict-dynamic'). No framing, no plugins, no <base> tricks."""
    scripts = f"'nonce-{nonce}' 'strict-dynamic' 'self' {PLAID_ORIGINS}" if nonce else "'self'"
    return ("default-src 'self'; "
            f"script-src {scripts}; "
            "style-src 'self' 'unsafe-inline'; "     # inline style attributes (and Plaid Link) need this
            "img-src 'self' data:; font-src 'self'; "
            f"connect-src 'self' {PLAID_API}{' ' + monitoring.browser_origin() if monitoring.browser_origin() else ''}; "
            f"frame-src {PLAID_ORIGINS}; worker-src 'self'; manifest-src 'self'; "
            "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


class Handler(BaseHTTPRequestHandler):
    server_version = "Runway"
    sys_version = ""                  # don't advertise the Python version
    timeout = REQUEST_TIMEOUT

    def setup(self):
        super().setup()
        self._deadline(HEADER_DEADLINE)

    def finish(self):
        self._deadline(None)
        super().finish()

    def _deadline(self, seconds: float | None) -> None:
        """REQUEST_TIMEOUT is per read, so a client sending a byte every few seconds would never hit it. Server hangs up
        once this overall deadline passes (None: no deadline, while Runway itself is working)."""
        set_deadline = getattr(self.server, "set_deadline", None)
        if set_deadline:
            set_deadline(self.connection, seconds)

    def _read_body(self, n: int) -> bytes:
        self._deadline(REQUEST_TIMEOUT + n / MIN_BODY_RATE)
        try:
            return self.rfile.read(n)
        finally:
            self._deadline(None)

    def log_message(self, fmt, *args):
        pass   # the standard per-request line includes query strings (sign-in codes); log_request writes our own

    def log_request(self, code="-", size="-"):
        # One line per request, path only (no query string: /auth/callback carries sign-in codes).
        path = urllib.parse.urlsplit(getattr(self, "path", "") or "").path
        if path == "/healthz":
            return   # the container health check, every minute
        started = getattr(self, "_started", None)
        ms = f" {int((time.monotonic() - started) * 1000)}ms" if started else ""
        print(f"{self.client_address[0]} {getattr(self, 'command', '-')} {path} {code}{ms}", flush=True)

    def _security_headers(self, nonce: str | None = None) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", content_security_policy(nonce))
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin-allow-popups")
        # The browser extension reads the answers to its own calls (it has its key, and permission for this site).
        self.send_header("Cross-Origin-Resource-Policy",
                         "cross-origin" if getattr(self, "_ext_call", False) else "same-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()")
        if oidc.config()["secure_cookie"]:   # served over https: tell browsers never to use plain http
            self.send_header("Strict-Transport-Security", "max-age=31536000")

    def _send(self, status: int, body: bytes, ctype: str = "application/json", cache: str = "no-store",
              nonce: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self._security_headers(nonce)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _error(self, e: BaseException) -> None:
        """An unexpected failure: log the details, show only a reference to them."""
        ref = request_ref()
        print(f"[error {ref}] {self.command} {urllib.parse.urlsplit(self.path).path}", flush=True)
        monitoring.report(e, ref=ref)
        self._json(500, {"error": f"Something went wrong on Runway's side (reference {ref}; the details are in its log)."})

    def _body_length(self, limit: int) -> int | None:
        """The request's Content-Length, or None (after answering) if it's missing a number or too big."""
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n < 0 or n > limit:
            self.close_connection = True
            self._json(413 if n > limit else 400, {"error": "That request is too large." if n > limit else "Bad request."})
            return None
        return n

    def _json(self, status: int, obj) -> None:
        self._send(status, json.dumps(obj).encode())

    def _host_ok(self) -> bool:
        # Refuse requests addressed to hostnames we don't know (DNS-rebinding protection).
        return host_allowed(self.headers.get("Host") or "")

    def _cookie(self, name: str) -> str | None:
        c = SimpleCookie()
        try:
            c.load(self.headers.get("Cookie") or "")
        except Exception:
            return None
        return c[name].value if name in c else None

    def _user(self) -> dict | None:
        """The signed-in person, or None. Without OIDC configured everyone is 'local'."""
        if not oidc.enabled():
            return {"name": None, "email": None, "local": True}
        with db.session() as conn:
            return oidc.session_user(conn, self._cookie("runway_session"))

    def _redirect(self, location: str, cookies: list[str] | None = None) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        for ck in cookies or []:
            self.send_header("Set-Cookie", ck)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self._security_headers()
        self.end_headers()

    def _page(self, status: int, title: str, message: str, link: tuple[str, str] | None = None) -> None:
        body = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} · Runway</title><link rel="icon" href="/logo.svg"><link rel="stylesheet" href="/page.css"></head>
<body><main style="max-width:520px;margin:12vh auto"><div class="card" style="text-align:center">
<img src="/logo.svg" width="48" height="48" alt=""><h1 style="margin-top:12px">{html.escape(title)}</h1>
<p class="help" style="margin:0 auto 16px">{html.escape(message)}</p>
{f'<a class="btn primary" href="{html.escape(link[0])}">{html.escape(link[1])}</a>' if link else ''}</div></main></body></html>"""
        self._send(status, body.encode(), "text/html; charset=utf-8")

    def _cookie_header(self, name: str, value: str, max_age: int, path: str = "/") -> str:
        secure = "; Secure" if oidc.config()["secure_cookie"] else ""
        return f"{name}={value}; Path={path}; Max-Age={max_age}; HttpOnly; SameSite=Lax{secure}"

    def _auth_routes(self, url) -> bool:
        """/auth/* pages. Returns True if handled."""
        q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        if url.path == "/auth/login":
            if not oidc.enabled():
                self._redirect("/"); return True
            try:
                with db.session() as conn:
                    target, state = oidc.start_login(conn, q.get("next") or "/")
            except oidc.OIDCError as e:
                self._page(502, "Can't reach sign-in", str(e), ("/auth/login", "Try again")); return True
            self._redirect(target, [self._cookie_header("runway_login", state, oidc.LOGIN_TTL, "/auth")]); return True
        if url.path == "/auth/callback":
            try:
                with db.session() as conn:
                    token, nxt = oidc.finish_login(conn, q, self._cookie("runway_login"))
            except oidc.OIDCError as e:
                self._page(403, "Couldn't sign you in", str(e), ("/auth/login", "Try again")); return True
            days = oidc.config()["session_days"]
            self._redirect(nxt, [self._cookie_header("runway_session", token, days * 86400),
                                 self._cookie_header("runway_login", "", 0, "/auth")]); return True
        if url.path == "/auth/logout":
            # Signing out is a POST from the app (see _logout), so another site can't sign you out with a link or image.
            self._page(405, "Sign out from Runway", "Use the sign-out button at the bottom of Runway's sidebar.", ("/", "Open Runway")); return True
        if url.path == "/auth/signed-out":
            self._page(200, "Signed out", "You've signed out of Runway.", ("/auth/login", "Sign in again")); return True
        return False

    def send_response(self, code, message=None):
        self._responded = True
        super().send_response(code, message)

    def _dispatch(self, method: str) -> None:
        self._deadline(None)   # the headers are in
        self._started, self._responded = time.monotonic(), False
        self._ext_call = False
        try:
            self._route(method)
        except Exception as e:   # never show internals; never leave the browser hanging
            if not self._responded:
                self._error(e)
            else:
                monitoring.report()

    def _same_site(self) -> bool:
        """A state-changing request must come from Runway's own pages (defense in depth beside the X-Runway header)."""
        if (self.headers.get("Sec-Fetch-Site") or "").lower() == "cross-site":
            return False
        origin = self.headers.get("Origin")
        if origin:   # "null" (sandboxed frames, file: pages) is never Runway
            return origin != "null" and host_allowed(urllib.parse.urlsplit(origin).netloc)
        return True

    def _logout(self) -> None:
        """POST /auth/logout from the app: end the session and say where to go next (the provider's sign-out page)."""
        with db.session() as conn:
            target = oidc.logout(conn, self._cookie("runway_session")) if oidc.enabled() else "/"
        body = json.dumps({"redirect": target}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Set-Cookie", self._cookie_header("runway_session", "", 0))
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _route(self, method: str) -> None:
        url = urllib.parse.urlsplit(self.path)
        if url.path == "/healthz" and method == "GET":   # container health check: says nothing about your data
            return self._send(200, b"ok", "text/plain")
        if not self._host_ok():
            return self._send(403, b"Runway doesn't recognise this address. Add it to RUNWAY_ALLOWED_HOSTS.", "text/plain")
        if url.path.startswith("/api/ext/"):   # the browser extension: its own key instead of a sign-in
            return self._extension(method, url.path)
        if method != "GET" and not self._same_site():
            return self._json(403, {"error": "forbidden"})
        if url.path.startswith("/auth/") and method == "GET" and self._auth_routes(url):
            return
        if url.path == "/auth/logout" and method == "POST":
            if self.headers.get("X-Runway") != "1":
                return self._json(403, {"error": "forbidden"})
            return self._logout()
        # The look of the sign-in pages is public; everything else needs you signed in.
        if url.path not in PUBLIC_FILES:
            self.user = self._user()
            if not self.user:
                if url.path.startswith("/api/"):
                    return self._json(401, {"error": "You've been signed out.", "login": "/auth/login"})
                back = (url.path or "/") + ("?" + url.query if url.query else "")   # e.g. /plaid/oauth?oauth_state_id=…
                return self._redirect("/auth/login?next=" + urllib.parse.quote(back, safe=""))
        if url.path == "/carta/callback" and method == "GET":
            return self._carta_callback(url)
        if not url.path.startswith("/api/"):
            if method != "GET":
                return self._send(405, b"", "text/plain")
            return self._static(url.path)
        # State-changing calls must carry a custom header, which a foreign web page can't add without CORS approval.
        if method != "GET" and self.headers.get("X-Runway") != "1":
            return self._json(403, {"error": "forbidden"})
        if method == "GET" and url.path == "/api/backup":
            with db.session() as conn:
                data = backup.dump(conn)
            self.send_response(200)
            self.send_header("Content-Type", "application/gzip")
            self.send_header("Content-Disposition", f'attachment; filename="runway-backup-{date.today().isoformat()}.json.gz"')
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self._security_headers()
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)
            return
        if method == "GET" and url.path.startswith("/api/merchants/") and url.path.endswith("/logo"):
            mid = urllib.parse.unquote(url.path[len("/api/merchants/"):-len("/logo")])
            with db.session() as conn:
                found = merchants.logo(conn, mid)
            if not found:
                return self._send(404, b"", "text/plain")
            data, ctype = found
            if ctype not in merchants.TYPES:   # a backup can hold anything; only ever serve an image
                return self._send(404, b"", "text/plain")
            etag = '"' + hashlib.sha256(data).hexdigest()[:20] + '"'
            if self.headers.get("If-None-Match") == etag:
                self.send_response(304)
                self.send_header("ETag", etag)
                self._security_headers()
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "private, max-age=604800")
            self.send_header("ETag", etag)
            self._security_headers()
            self.send_header("Content-Security-Policy", "default-src 'none'; sandbox")   # opened directly, it's inert
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)
            return
        if method == "GET" and url.path == "/api/carta/capture":
            # What the extension last read from Carta, to see why something wasn't picked up.
            with db.session() as conn:
                data = (db.get_setting(conn, sk.CARTA_WEB_CAPTURE) or "[]").encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Disposition", 'attachment; filename="runway-carta-read.json"')
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self._security_headers()
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)
            return
        if method == "GET" and url.path == "/api/retail/extension.zip":
            data = extension_zip()
            if data is None:
                return self._json(404, {"error": "The extension isn't included with this copy of Runway."})
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", 'attachment; filename="runway-orders-extension.zip"')
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self._security_headers()
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)
            return
        if method == "POST" and url.path == "/api/restore":
            n = self._body_length(MAX_RESTORE_BODY)
            if n is None:
                return
            if not n:
                return self._json(400, {"error": "Choose a backup file (up to 200 MB)."})
            try:
                data = backup.load(self._read_body(n))
            except ValueError as e:
                return self._json(400, {"error": str(e)})
            # Nothing in the background may write while the data is replaced (a sync, or categorizing an order
            # import): its rows would be mixed into the restored ones.
            held = []
            for lock in (_sync_lock, _inv_lock, _retail_categorize_lock):
                if not lock.acquire(blocking=False):
                    for h in held:
                        h.release()
                    return self._json(409, {"error": "A sync is running. Restore once it has finished."})
                held.append(lock)
            failed = None   # the locks are let go before answering, so a sync can start as soon as you have the answer
            try:
                with db.session() as conn:
                    counts = backup.restore(conn, data)
                with db.session() as conn:
                    sfinvest.repair_stored(conn)
            except (ValueError, sqlalchemy.exc.OperationalError) as e:
                failed = e
            finally:
                for lock in held:
                    lock.release()
            if isinstance(failed, ValueError):
                return self._json(400, {"error": str(failed)})
            if failed is not None:
                if "locked" in str(failed):
                    return self._json(503, {"error": "Runway is busy saving something else. Try the restore again in a few seconds."})
                return self._error(failed)
            return self._json(200, {"ok": True, "created": data.get("created"), "source": data.get("source"),
                                    "transactions": counts.get("transactions", 0), "accounts": counts.get("accounts", 0)})
        body = {}
        if method in ("POST", "DELETE"):
            n = self._body_length(MAX_JSON_BODY)
            if n is None:
                return
            if n:
                try:
                    body = json.loads(self._read_body(n).decode() or "{}")
                except (json.JSONDecodeError, UnicodeDecodeError):
                    return self._json(400, {"error": "Bad JSON"})
                if not isinstance(body, dict):
                    return self._json(400, {"error": "Bad JSON"})
        q = urllib.parse.parse_qs(url.query)
        _current.user = getattr(self, "user", None)
        if method == "POST" and url.path == "/api/investments/sync":
            try:
                bank = None
                with db.session() as conn:
                    has_sf = bool(db.get_setting(conn, sk.SIMPLEFIN_ACCESS_URL))
                if has_sf:  # positions from SimpleFIN arrive with the regular bank sync
                    bank = run_sync()
                out = run_investment_sync()
                out["bank"] = bank
                return self._json(200, out)
            except ApiError as e:
                return self._json(e.status, {"error": str(e)})
        if method == "GET" and url.path == "/api/investments/stream":
            return self._quote_stream()
        if method == "POST" and url.path == "/api/sync/auto":
            return self._json(200, sync_on_visit())
        if method == "POST" and url.path == "/api/sync":
            try:
                return self._json(200, run_sync())
            except ApiError as e:
                return self._json(e.status, {"error": str(e)})
        for m, pattern, fn in ROUTES:
            if m != method:
                continue
            params = _match(pattern, url.path)
            if params is None:
                continue
            try:
                with db.session() as conn:
                    result = fn(conn, q, body, *params)
                return self._json(200, result)
            except ApiError as e:
                return self._json(e.status, {"error": str(e)})
            except sqlalchemy.exc.OperationalError as e:
                if "locked" in str(e):
                    return self._json(503, {"error": "Runway is busy saving a sync. Try again in a few seconds."})
                return self._error(e)
            except (ValueError, TypeError, KeyError) as e:   # almost always a value in the request Runway can't read
                ref = request_ref()
                print(f"[bad request {ref}] {method} {url.path}: {type(e).__name__}: {e}", flush=True)
                return self._json(400, {"error": f"Runway couldn't read one of the values sent (reference {ref})."})
        return self._json(404, {"error": "Not found"})

    def _quote_stream(self) -> None:
        """Live prices as Server-Sent Events: an update whenever a held stock moves, while the market is open. With it
        closed, one update and then the browser is told to come back in a few minutes."""
        with db.session() as conn:
            tickers = live_tickers(conn)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")   # a reverse proxy (nginx) would otherwise hold the events back
        self._security_headers()
        self.end_headers()
        self.close_connection = True
        if self.command == "HEAD":
            return
        market = "closed"
        try:
            self.wfile.write(b"retry: 5000\n\n")
            for update in prices.quote_stream(tickers):
                if update is None:
                    self.wfile.write(b": still here\n\n")
                else:
                    market = update["market"]
                    self.wfile.write(b"event: quotes\ndata: " + json.dumps(update).encode() + b"\n\n")
                self.wfile.flush()
            if market != "open":
                self.wfile.write(f"retry: {prices.CLOSED_RETRY * 1000}\n\n".encode())
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass   # the page was closed

    def _carta_callback(self, url) -> None:
        """Back from approving Runway at Carta: trade the code for a token, read your equity, and go to Net worth."""
        q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        if q.get("error"):
            return self._page(400, "Carta wasn't connected", q.get("error_description") or q["error"], ("/#setup/connections", "Back to Settings"))
        try:
            with db.session() as conn:
                if q.get("mock"):
                    # The mock environment has no sign-in to prove this came from Settings, so a link from another site
                    # mustn't be able to start a sync.
                    if (db.get_setting(conn, sk.CARTA_ENV) != "mock"
                            or (self.headers.get("Sec-Fetch-Site") or "").lower() == "cross-site"):
                        return self._page(400, "Carta wasn't connected", "Connect Carta from Settings.",
                                          ("/#setup/connections", "Back to Settings"))
                else:
                    carta.finish_authorize(conn, q.get("code", ""), q.get("state", ""))
            with db.session() as conn:
                carta.sync(conn)
        except carta.CartaError as e:
            return self._page(502, "Carta wasn't connected", str(e), ("/#setup/connections", "Back to Settings"))
        self._redirect("/#networth")

    def _extension(self, method: str, path: str) -> None:
        """A call from Runway's browser extension. It carries the key made under Settings → Connections (a bearer
        token, which a web page can't send on your behalf), so it needs no sign-in or same-site checks."""
        self._ext_call = True
        fn = EXT_ROUTES.get(path)
        if method != "POST" or not fn:
            return self._json(404, {"error": "Not found"})
        with db.session() as conn:
            ok = retail.check_token(conn, self.headers.get("Authorization"))
        if not ok:
            self.close_connection = True
            return self._json(401, {"error": "Runway doesn't know this key. Make a new one under Settings → Connections."})
        n = self._body_length(MAX_EXT_BODY)
        if n is None:
            return
        try:
            body = json.loads(self._read_body(n).decode() or "{}") if n else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self._json(400, {"error": "Bad JSON"})
        if not isinstance(body, dict):
            return self._json(400, {"error": "Bad JSON"})
        try:
            with db.session() as conn:
                result = fn(conn, body)
            return self._json(200, result)   # once it's saved, so the extension's next call sees it
        except retail.RetailError as e:
            return self._json(400, {"error": str(e), **({"code": e.code} if e.code else {})})
        except sqlalchemy.exc.OperationalError as e:
            if "locked" in str(e):
                return self._json(503, {"error": "Runway is busy saving a sync. Try again in a few seconds."})
            return self._error(e)

    def _static(self, path: str) -> None:
        if path == "/next" or path.startswith("/next/"):   # where the web app lived while it was being rebuilt
            return self._redirect("/")                      # (the browser keeps the #page on the way)
        # The web app's built files (frontend/, built into static/app/), then Runway's own (icons, fonts, the service
        # worker). Anything else is a route of the app itself (/plaid/oauth, ...), so it gets the app's page.
        rel = path.lstrip("/")
        full = None
        for base in (APP_DIR, STATIC):
            cand = os.path.realpath(os.path.join(base, rel))
            if rel and os.path.commonpath([cand, base]) == base and os.path.isfile(cand):
                full = cand
                break
        if full is None or full == APP_INDEX:
            full = APP_INDEX
            if not os.path.isfile(full):
                return self._page(404, "The web app isn't built", "Run npm run build in frontend/ (the Docker image does this for you).")
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        gz_ok = "gzip" in (self.headers.get("Accept-Encoding") or "")
        if full == APP_INDEX:
            # A fresh nonce per page, so only this page's own <script> tags may run (see content_security_policy).
            nonce = secrets.token_urlsafe(16)
            with open(full, "rb") as f:
                data = f.read().replace(b"<script ", f'<script nonce="{nonce}" '.encode())
            return self._send_file(data, ctype, "no-store", None, gz_ok, nonce)
        entry = _static_entry(full)
        if self.headers.get("If-None-Match") == entry["etag"]:
            self.send_response(304)
            self.send_header("ETag", entry["etag"])
            self.send_header("Cache-Control", "no-cache")
            self._security_headers()
            self.end_headers()
            return
        # "no-cache" = keep a copy but check it's current each time (a cheap 304), so updates show up at once. The
        # app's built files have their content's hash in their name, so they never change and can be kept for good.
        cache = "public, max-age=31536000, immutable" if full.startswith(APP_DIR + os.sep + "assets" + os.sep) else "no-cache"
        self._send_file(entry["data"], ctype, cache, entry["etag"], gz_ok, None, entry.get("gz"))

    def _send_file(self, data: bytes, ctype: str, cache: str, etag: str | None, gz_ok: bool, nonce: str | None,
                   gz: bytes | None = None) -> None:
        if gz_ok and _compressible(ctype) and len(data) > 1024:
            data, encoded = gz or gzip.compress(data, 6), True
        else:
            encoded = False
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        self.send_header("Vary", "Accept-Encoding")
        if etag:
            self.send_header("ETag", etag)
        if encoded:
            self.send_header("Content-Encoding", "gzip")
        self._security_headers(nonce)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def do_GET(self):
        self._dispatch("GET")

    def do_HEAD(self):
        self._dispatch("GET")   # same answer without the body (_send and _send_file skip it for HEAD)

    def do_POST(self):
        self._dispatch("POST")

    def do_DELETE(self):
        self._dispatch("DELETE")


STATIC = os.path.realpath(STATIC)
APP_DIR = os.path.join(STATIC, "app")       # the web app, built from frontend/
APP_INDEX = os.path.join(APP_DIR, "index.html")
_static_files: dict[str, dict] = {}
_static_lock = threading.Lock()


def _compressible(ctype: str) -> bool:
    return ctype.startswith("text/") or ctype in ("application/javascript", "application/json", "image/svg+xml",
                                                  "application/manifest+json")


def _static_entry(full: str) -> dict:
    """A static file's bytes, ETag and gzip'd copy, kept in memory until the file changes."""
    st = os.stat(full)
    key = (st.st_mtime_ns, st.st_size)
    with _static_lock:
        entry = _static_files.get(full)
        if entry and entry["key"] == key:
            return entry
    with open(full, "rb") as f:
        data = f.read()
    ctype = mimetypes.guess_type(full)[0] or ""
    entry = {"key": key, "data": data, "etag": '"' + hashlib.sha256(data).hexdigest()[:20] + '"',
             "gz": gzip.compress(data, 6) if _compressible(ctype) and len(data) > 1024 else None}
    with _static_lock:
        _static_files[full] = entry
    return entry


class Server(ThreadingHTTPServer):
    """The standard threaded server, with a cap on requests handled at once so a flood can't exhaust the machine."""
    daemon_threads = True
    request_queue_size = 128

    def __init__(self, *a, **k):
        self._slots = threading.BoundedSemaphore(MAX_CONCURRENT_REQUESTS)
        self._deadlines: dict = {}
        self._deadlines_lock = threading.Lock()
        self._closed = threading.Event()
        super().__init__(*a, **k)
        threading.Thread(target=self._hang_up_late, daemon=True).start()

    def set_deadline(self, sock, seconds: float | None) -> None:
        with self._deadlines_lock:
            if seconds is None:
                self._deadlines.pop(sock, None)
            else:
                self._deadlines[sock] = time.monotonic() + seconds

    def _hang_up_late(self) -> None:
        """Close connections past their deadline (see Handler._deadline), so trickling clients can't hold every slot."""
        while not self._closed.wait(0.5):
            now = time.monotonic()
            with self._deadlines_lock:
                late = [sock for sock, t in self._deadlines.items() if t < now]
                for sock in late:
                    del self._deadlines[sock]
            for sock in late:
                with contextlib.suppress(OSError):
                    sock.shutdown(socket.SHUT_RDWR)   # the handler's blocked read returns, and it finishes

    def server_close(self):
        self._closed.set()
        super().server_close()

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], OSError):
            return   # the client went away (or was hung up on): nothing worth a traceback
        monitoring.report()

    def process_request(self, request, client_address):
        if not self._slots.acquire(timeout=REQUEST_TIMEOUT):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


def serve(host: str = "127.0.0.1", port: int = 8765, auto_sync: bool = True) -> None:
    monitoring.init()   # error reports to Sentry, when SENTRY_DSN is set
    problems = secretbox.check_config()
    if problems:
        raise SystemExit("\n".join(problems))
    if db.using_postgres() and not os.environ.get("RUNWAY_SECRET_KEY"):
        print(f"Note: set RUNWAY_SECRET_KEY. Without it, the key that encrypts your saved bank access and API keys is "
              f"{secretbox.key_file_path()}, and losing that file means reconnecting them.", flush=True)
    if oidc.enabled():
        problems = oidc.check_config()
        if problems:
            raise SystemExit("Sign-in (OIDC) isn't set up correctly:\n  - " + "\n  - ".join(problems))
    elif host not in ("127.0.0.1", "localhost", "::1") and os.environ.get("RUNWAY_ALLOW_NO_AUTH") != "1":
        raise SystemExit("Runway is set to accept connections from other devices, so it needs sign-in.\n"
                         "Set OIDC_ISSUER, OIDC_CLIENT_ID, OIDC_CLIENT_SECRET, RUNWAY_PUBLIC_URL and OIDC_ALLOWED_EMAILS\n"
                         "(or RUNWAY_ALLOW_NO_AUTH=1 if a proxy in front of Runway already handles sign-in).")
    db.init()
    with db.session() as conn:
        recurring.auto_match(conn)  # pick up matches for items created before this version
        sfinvest.repair_stored(conn)  # fix investment positions saved by earlier versions
        categories.flatten(conn)      # subcategories are one level deep
        plaid.hide_all_duplicates(conn)  # an institution linked through both Plaid and SimpleFIN is counted once
        for r in conn.execute("SELECT item_id FROM plaid_items WHERE COALESCE(products, 'investments') LIKE '%investments%'").fetchall():
            plaid.update_investment_accounts(conn, r["item_id"])   # investment accounts from Plaid in your accounts
        oidc.backfill_users(conn)        # people who signed in before owners existed
    sync.AUTO_SYNC = auto_sync   # sync_on_visit reads it there
    if auto_sync:
        threading.Thread(target=background_sync, daemon=True).start()
    httpd = Server((host, port), Handler)
    where = f"http://localhost:{port}" if host in ("127.0.0.1", "localhost") else f"port {port} on all network addresses"
    print(f"Runway is running at {where}  (data: {db.describe()})"
          f"{'  · sign-in via ' + oidc.config()['issuer'] if oidc.enabled() else ''}", flush=True)
    with contextlib.suppress(KeyboardInterrupt):
        httpd.serve_forever()
