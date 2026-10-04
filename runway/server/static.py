"""The files Runway serves: the web app (frontend/, built into static/app/), with a fresh script nonce on its page, and
Runway's own (icons, fonts, the service worker, the sign-in pages' look), with their ETags, gzip'd copies and how long
a browser may keep each. Who may fetch which is the handler's to decide (handler.PUBLIC_FILES); these only send them."""
from __future__ import annotations

import gzip
import hashlib
import mimetypes
import os
import secrets
import threading
from typing import TYPE_CHECKING

from .. import monitoring

if TYPE_CHECKING:
    from .handler import Handler

mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("application/manifest+json", ".webmanifest")

STATIC = os.path.realpath(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static"))
APP_DIR = os.path.join(STATIC, "app")       # the web app, built from frontend/
APP_INDEX = os.path.join(APP_DIR, "index.html")
_static_files: dict[str, dict] = {}
_static_lock = threading.Lock()


def serve(h: Handler, path: str) -> None:
    """A GET for anything that isn't the API, sign-in or OAuth: a file, or the web app's page."""
    if path == "/next" or path.startswith("/next/"):   # where the web app lived while it was being rebuilt
        return h._redirect("/")                         # (the browser keeps the #page on the way)
    # The web app's built files (frontend/, built into static/app/), then Runway's own (icons, fonts, the service
    # worker). Anything else is a route of the app itself (/plaid/oauth, ...), so it gets the app's page.
    rel = path.lstrip("/")
    full = None
    for base in (APP_DIR, STATIC):
        cand = os.path.realpath(os.path.join(base, rel))
        if rel and cand.startswith(base + os.sep) and os.path.isfile(cand):
            full = cand
            break
    if full is None or full == APP_INDEX:
        full = APP_INDEX
        if not os.path.isfile(full):
            return h._page(404, "The web app isn't built", "Run npm run build in frontend/ (the Docker image does this for you).")
    ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
    gz_ok = "gzip" in (h.headers.get("Accept-Encoding") or "")
    if full == APP_INDEX:
        # A fresh nonce per page, so only this page's own <script> tags may run (see content_security_policy).
        nonce = secrets.token_urlsafe(16)
        with open(full, "rb") as f:
            data = f.read().replace(b"<script ", f'<script nonce="{nonce}" '.encode())
        if meta := monitoring.trace_meta():   # the page-load trace in the browser continues this one
            data = data.replace(b"</head>", meta.encode() + b"</head>", 1)
        # The browser's JS profiler only runs on a page that asks for it.
        extra = {"Document-Policy": "js-profiling"} if monitoring.browser_profiling() else None
        return send_file(h, data, ctype, "no-store", None, gz_ok, nonce, extra=extra)
    entry = _static_entry(full)
    if h.headers.get("If-None-Match") == entry["etag"]:
        h.send_response(304)
        h.send_header("ETag", entry["etag"])
        h.send_header("Cache-Control", "no-cache")
        h._security_headers()
        h.end_headers()
        return None
    # "no-cache" = keep a copy but check it's current each time (a cheap 304), so updates show up at once. The app's
    # built files have their content's hash in their name, so they never change and can be kept for good.
    cache = "public, max-age=31536000, immutable" if full.startswith(APP_DIR + os.sep + "assets" + os.sep) else "no-cache"
    return send_file(h, entry["data"], ctype, cache, entry["etag"], gz_ok, None, entry.get("gz"))


def send_file(h: Handler, data: bytes, ctype: str, cache: str, etag: str | None, gz_ok: bool, nonce: str | None,
              gz: bytes | None = None, extra: dict[str, str] | None = None) -> None:
    if gz_ok and _compressible(ctype) and len(data) > 1024:
        data, encoded = gz or gzip.compress(data, 6), True
    else:
        encoded = False
    h.send_response(200)
    h.send_header("Content-Type", ctype)
    h.send_header("Content-Length", str(len(data)))
    h.send_header("Cache-Control", cache)
    h.send_header("Vary", "Accept-Encoding")
    if etag:
        h.send_header("ETag", etag)
    if encoded:
        h.send_header("Content-Encoding", "gzip")
    for k, v in (extra or {}).items():
        h.send_header(k, v)
    h._security_headers(nonce)
    h.end_headers()
    if h.command != "HEAD":
        h.wfile.write(data)


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
