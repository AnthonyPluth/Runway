"""What the server sends, header by header, for the endpoints with answers of their own (downloads, uploads, syncs, a
merchant's logo, the live-prices stream), the OAuth consent page, the web app's files and a plain JSON answer; and who
may reach them (sign-in, the X-Runway header, same-site checks).

Pinned exactly, in order, so moving these endpoints around the server can't quietly change a security header, a cache
policy or a download's name. Only what differs from one request to the next is left out: the Date, a page's script
nonce, a backup's size and today's date in its name."""
import base64
import gzip
import http.client
import io
import json
import os
import re
import tempfile
import threading
import time
import unittest
import urllib.parse
import zipfile
from datetime import date
from unittest import mock

from sqlalchemy import delete, insert

from runway.storage import backup, db
from runway.server import mcp_access, mcp_oauth
from runway import oidc, server
from runway.providers import prices
from runway.storage import settings_keys as sk
from runway.storage.models import Merchant
from runway.server import common, mcp_http, sync
from runway.server.api import retail
from tests.shared import forget_oauth, freeze_today, own_database
from tests.test_web_app import built_app, serving

PNG = b"\x89PNG\r\n\x1a\n" + bytes(16)
PERMISSIONS = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; "
       "connect-src 'self' https://production.plaid.com https://sandbox.plaid.com; frame-src https://cdn.plaid.com; "
       "worker-src 'self'; manifest-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
PAGE_CSP = CSP.replace("script-src 'self'", "script-src 'nonce-N' 'strict-dynamic' 'self' https://cdn.plaid.com")


def security(csp: str = CSP, resource: str = "same-origin") -> list[tuple[str, str]]:
    """The headers every answer carries, after its own."""
    return [("X-Content-Type-Options", "nosniff"), ("Content-Security-Policy", csp), ("X-Frame-Options", "DENY"),
            ("Referrer-Policy", "no-referrer"), ("Cross-Origin-Opener-Policy", "same-origin-allow-popups"),
            ("Cross-Origin-Resource-Policy", resource), ("Permissions-Policy", PERMISSIONS)]


def json_answer(length: int | str, *extra: tuple[str, str]) -> list[tuple[str, str]]:
    return [("Server", "Runway "), ("Content-Type", "application/json"), *extra, ("Content-Length", str(length)),
            ("Cache-Control", "no-store"), *security()]


class Pinned(unittest.TestCase):
    maxDiff = None
    @classmethod
    def setUpClass(cls):
        own_database(cls)
        for k in ("RUNWAY_PUBLIC_URL", "OIDC_ISSUER"):
            os.environ.pop(k, None)
        cls.static_dir = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.static_dir.cleanup)
        cls.static = cls.static_dir.name
        built_app(cls.static)
        with open(os.path.join(cls.static, "sw.js"), "wb") as f:
            f.write(b"// service worker " + b"x" * 2000)
        cls.httpd = server.Server(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.port = cls.httpd.server_port

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def send(self, method: str, path: str, body: bytes | None = None, headers: dict | None = None):
        """(status, the headers in order without Date, body). A page's nonce reads 'nonce-N'."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        try:
            with serving(self.static):
                conn.request(method, path, body=body, headers={"Host": f"127.0.0.1:{self.port}", **(headers or {})})
                resp = conn.getresponse()
                data = resp.read()
            heads = [(k, re.sub(r"'nonce-[^']+'", "'nonce-N'", v)) for k, v in resp.getheaders() if k != "Date"]
            return resp.status, heads, data
        finally:
            conn.close()

    def api(self, method: str, path: str, body: bytes | None = None, headers: dict | None = None):
        return self.send(method, path, body, {"X-Runway": "1", **(headers or {})})

    def assertJson(self, got, status: int, reply: dict, *extra: tuple[str, str]):
        code, heads, data = got
        self.assertEqual((code, json.loads(data)), (status, reply))
        self.assertEqual(heads, json_answer(len(data), *extra))


    def test_a_json_answer(self):
        code, heads, data = self.send("GET", "/api/accounts")
        self.assertEqual((code, json.loads(data)), (200, []))
        self.assertEqual(heads, json_answer(len(data)))
        self.assertJson(self.send("GET", "/api/nothing-here"), 404, {"error": "Not found"})
        self.assertJson(self.api("POST", "/api/nothing-here", b"{}"), 404, {"error": "Not found"})

    def test_a_backup_download(self):
        freeze_today(self)      # the file is named after today
        code, heads, data = self.send("GET", "/api/backup")
        self.assertEqual(code, 200)
        self.assertIn("accounts", json.loads(gzip.decompress(data))["tables"])
        self.assertEqual(heads, [("Server", "Runway "), ("Content-Type", "application/gzip"),
                                 ("Content-Disposition", f'attachment; filename="runway-backup-{date.today().isoformat()}.json.gz"'),
                                 ("Content-Length", str(len(data))), ("Cache-Control", "no-store"), *security()])
        self.assertTrue(self.last_backup())
        with db.session() as conn:
            db.set_setting(conn, sk.LAST_BACKUP, None)
        code, heads, data = self.send("HEAD", "/api/backup")
        self.assertEqual((code, data), (200, b""))
        self.assertIsNone(self.last_backup(wait=0.5))
        with db.session() as conn:
            self.assertIsNone(db.get_setting(conn, sk.LAST_BACKUP))

    @staticmethod
    def last_backup(wait: float = 5.0) -> str | None:
        """When the last backup was downloaded, once the server has noted it (just after sending it), or None after
        `wait` seconds."""
        end = time.monotonic() + wait
        while True:
            with db.session() as conn:
                when = db.get_setting(conn, sk.LAST_BACKUP)
            if when or time.monotonic() > end:
                return when
            time.sleep(0.05)

    def test_carta_capture_download(self):
        with db.session() as conn:
            db.set_setting(conn, sk.CARTA_WEB_CAPTURE, '[{"page": "x"}]')
        code, heads, data = self.send("GET", "/api/carta/capture")
        self.assertEqual((code, data), (200, b'[{"page": "x"}]'))
        self.assertEqual(heads, [("Server", "Runway "), ("Content-Type", "application/json"),
                                 ("Content-Disposition", 'attachment; filename="runway-carta-read.json"'),
                                 ("Content-Length", "15"), ("Cache-Control", "no-store"), *security()])

    def test_extension_download(self):
        with tempfile.TemporaryDirectory() as ext:
            with open(os.path.join(ext, "manifest.json"), "w") as f:
                f.write("{}")
            with mock.patch.object(retail, "EXTENSION_DIR", ext):
                code, heads, data = self.send("GET", "/api/retail/extension.zip")
                with zipfile.ZipFile(io.BytesIO(data)) as z:
                    self.assertEqual(z.namelist(), ["runway-orders/manifest.json"])
            self.assertEqual(code, 200)
            self.assertEqual(heads, [("Server", "Runway "), ("Content-Type", "application/zip"),
                                     ("Content-Disposition", 'attachment; filename="runway-orders-extension.zip"'),
                                     ("Content-Length", str(len(data))), ("Cache-Control", "no-store"), *security()])
            os.remove(os.path.join(ext, "manifest.json"))
            with mock.patch.object(retail, "EXTENSION_DIR", ext):
                self.assertJson(self.send("GET", "/api/retail/extension.zip"), 404,
                                {"error": "The extension isn't included with this copy of Runway."})

    def test_a_merchants_logo(self):
        with db.session() as c:
            c.execute(insert(Merchant).values(id="pin:png", name="P", logo=base64.b64encode(PNG).decode(), logo_type="image/png"))
            c.execute(insert(Merchant).values(id="pin:svg", name="S", logo=base64.b64encode(b"<svg/>").decode(),
                                              logo_type="image/svg+xml"))
        self.addCleanup(self.forget_merchants)
        code, heads, data = self.send("GET", "/api/merchants/pin%3Apng/logo")
        self.assertEqual((code, data), (200, PNG))
        etag = dict(heads)["ETag"]
        self.assertRegex(etag, r'^"[0-9a-f]{20}"$')
        self.assertEqual(heads, [("Server", "Runway "), ("Content-Type", "image/png"), ("Content-Length", str(len(PNG))),
                                 ("Cache-Control", "private, no-cache"), ("ETag", etag), *security(),
                                 ("Content-Security-Policy", "default-src 'none'; sandbox")])
        code, heads, data = self.send("GET", "/api/merchants/pin%3Apng/logo", headers={"If-None-Match": etag})
        self.assertEqual((code, data), (304, b""))
        self.assertEqual(heads, [("Server", "Runway "), ("ETag", etag), *security()])
        self.assertEqual(self.send("HEAD", "/api/merchants/pin%3Apng/logo")[2], b"")
        for path in ("/api/merchants/pin%3Asvg/logo", "/api/merchants/nope/logo"):
            code, heads, data = self.send("GET", path)
            self.assertEqual((code, data), (404, b""))
            self.assertEqual(heads, [("Server", "Runway "), ("Content-Type", "text/plain"), ("Content-Length", "0"),
                                     ("Cache-Control", "no-store"), *security()])

    def forget_merchants(self):
        with db.session() as c:
            c.execute(delete(Merchant).where(Merchant.id.in_(["pin:png", "pin:svg"])))

    def test_backup_inspect_and_restore_uploads(self):
        for path in ("/api/backup/inspect", "/api/restore"):
            with self.subTest(path=path):
                self.assertJson(self.api("POST", path), 400, {"error": "Choose a backup file (up to 200 MB)."})
                code, _h, data = self.api("POST", path, b"not a backup")
                self.assertEqual(code, 400)
                self.assertIn("error", json.loads(data))
                code, heads, data = self.api("POST", path, headers={"Content-Length": str(server.MAX_RESTORE_BODY + 1)})
                self.assertEqual((code, json.loads(data)), (413, {"error": "That request is too large."}))
                self.assertEqual(heads, json_answer(len(data)))
                code, heads, data = self.api("POST", path, headers={"Content-Length": "lots"})
                self.assertEqual((code, json.loads(data)), (400, {"error": "Bad request."}))
        with db.session() as conn:
            raw = backup.dump(conn)
        code, heads, data = self.api("POST", "/api/backup/inspect", raw)
        reply = json.loads(data)
        self.assertEqual((code, reply["database"]), (200, "postgres" if db.using_postgres() else "sqlite"))
        self.assertIn("current", reply)
        self.assertEqual(heads, json_answer(len(data)))
        with mock.patch.object(backup, "restore_all", side_effect=backup.Busy("A sync is running.")):
            self.assertJson(self.api("POST", "/api/restore", raw), 409, {"error": "A sync is running."})
        with mock.patch.object(backup, "restore_all", side_effect=OSError(28, "No space left on device")):
            self.assertJson(self.api("POST", "/api/restore", raw), 500, {
                "error": "Couldn’t save a copy of what’s here first (No space left on device), so nothing was restored."})
        done = {"counts": {"transactions": 3, "accounts": 1}, "safety_copy": "copy.json.gz", "unreadable_secrets": [],
                "warning": None}
        with mock.patch.object(backup, "restore_all", return_value=done):
            code, heads, data = self.api("POST", "/api/restore", raw)
        reply = json.loads(data)
        self.assertEqual((code, reply["ok"], reply["transactions"], reply["accounts"], reply["safety_copy"]),
                         (200, True, 3, 1, "copy.json.gz"))
        self.assertEqual(list(reply), ["ok", "created", "source", "transactions", "accounts", "safety_copy",
                                       "unreadable_secrets", "warning"])

    def test_syncs(self):
        self.assertJson(self.api("POST", "/api/sync"), 400, {"error": "Connect SimpleFIN or a Plaid bank in Settings first."})
        with mock.patch.object(sync, "AUTO_SYNC", False):
            self.assertJson(self.api("POST", "/api/sync/auto"), 200, {"started": False})
        self.assertJson(self.api("POST", "/api/investments/sync"), 200, {"items": 0, "errors": [], "prices": {}, "bank": None})
        with sync._inv_lock:
            self.assertJson(self.api("POST", "/api/investments/sync"), 409, {"error": "An investment sync is already running."})

    def test_live_prices_stream(self):
        def quotes(tickers, live=None, live_key=None):
            yield None
            yield {"market": "closed", "quotes": {}}
        with mock.patch.object(prices, "quote_stream", side_effect=lambda *a, **k: _Closing(quotes(*a, **k))):
            code, heads, data = self.send("GET", "/api/investments/stream")
        self.assertEqual(code, 200)
        self.assertEqual(heads, [("Server", "Runway "), ("Content-Type", "text/event-stream"), ("Cache-Control", "no-store"),
                                 ("X-Accel-Buffering", "no"), *security()])
        self.assertEqual(data, b'retry: 5000\n\n: still here\n\nevent: quotes\ndata: {"market": "closed", "quotes": {}}\n\n'
                               + f"retry: {prices.CLOSED_RETRY * 1000}\n\n".encode())
        with mock.patch.object(prices, "quote_stream") as stream:
            code, heads, data = self.send("HEAD", "/api/investments/stream")
        self.assertEqual((code, data), (200, b""))
        stream.assert_not_called()

    def test_changes_need_the_app_header_and_this_site(self):
        for method, path in (("POST", "/api/restore"), ("POST", "/api/backup/inspect"), ("POST", "/api/sync"),
                             ("POST", "/api/sync/auto"), ("POST", "/api/investments/sync"), ("POST", "/api/settings")):
            with self.subTest(path=path):
                code, heads, data = self.send(method, path, b"{}")
                self.assertEqual((code, json.loads(data)), (403, {"error": server.handler.NO_APP_HEADER}))
                self.assertEqual(heads, json_answer(len(data)))
                code, heads, data = self.api(method, path, b"{}", {"Origin": "https://evil.example"})
                self.assertEqual((code, json.loads(data)), (403, {"error": server.handler.NOT_SAME_SITE}))
                self.assertEqual(self.api(method, path, b"{}", {"Sec-Fetch-Site": "cross-site"})[0], 403)

    def test_json_bodies(self):
        code, heads, data = self.api("POST", "/api/settings", b"[1, 2]")
        self.assertEqual((code, json.loads(data)), (400, {"error": "Bad JSON"}))
        self.assertEqual(heads, json_answer(len(data)))
        self.assertJson(self.api("POST", "/api/settings", b"{nope"), 400, {"error": "Bad JSON"})
        self.assertJson(self.api("POST", "/api/settings", b"\xff\xfe"), 400, {"error": "Bad JSON"})
        self.assertJson(self.api("POST", "/api/settings", headers={"Content-Length": str(server.MAX_JSON_BODY + 1)}),
                        413, {"error": "That request is too large."})

    def test_json_nested_deeper_than_python_reads_is_a_400(self):
        deep = b"[" * 200_000 + b"]" * 200_000
        nested = b'{"a": ' + deep + b"}"
        self.assertJson(self.api("POST", "/api/settings", nested), 400, {"error": "Bad JSON"})
        with mock.patch.object(mcp_http, "authorized", return_value=mcp_access.Access(frozenset({"read"}), None, None)):
            code, _h, data = self.send("POST", "/mcp", deep, {"Content-Type": "application/json"})
        self.assertEqual((code, json.loads(data)["error"]["code"]), (400, -32700))
        with mock.patch("runway.domain.retail.token_check", return_value=None):
            code, _h, data = self.send("POST", "/api/ext/start", nested)
        self.assertEqual((code, json.loads(data)), (400, {"error": "Bad JSON"}))
        code, _h, data = self.send("POST", "/oauth/register", b"[" * 4000 + b"]" * 4000, {"Content-Type": "application/json"})
        self.assertEqual((code, json.loads(data)["error"]), (400, "invalid_client_metadata"))

    def test_the_extension_and_assistants_without_their_keys(self):
        code, heads, data = self.send("POST", "/api/ext/start", b"{}")
        self.assertEqual(code, 401)
        self.assertEqual(heads, [("Server", "Runway "), ("Content-Type", "application/json"), ("Content-Length", str(len(data))),
                                 ("Cache-Control", "no-store"), *security(resource="cross-origin")])
        code, heads, data = self.send("POST", "/mcp", b"{}", {"Content-Type": "application/json"})
        self.assertEqual(code, 401)
        challenge = f'Bearer realm="Runway", resource_metadata="http://127.0.0.1:{self.port}/.well-known/oauth-protected-resource/mcp"'
        self.assertEqual(heads, [("Server", "Runway "), ("Content-Type", "application/json"), ("WWW-Authenticate", challenge),
                                 ("Content-Length", str(len(data))), ("Cache-Control", "no-store"), *security()])
        self.assertEqual(self.send("GET", "/mcp")[:2], (405, [
            ("Server", "Runway "), ("Content-Type", "text/plain"), ("Allow", "POST"), ("Content-Length", "0"),
            ("Cache-Control", "no-store"), *security()]))


    def test_the_web_apps_page_and_files(self):
        code, heads, data = self.send("GET", "/")
        self.assertEqual(code, 200)
        self.assertEqual(heads, [("Server", "Runway "), ("Content-Type", "text/html"), ("Content-Length", str(len(data))),
                                 ("Cache-Control", "no-store"), ("Vary", "Accept-Encoding"), *security(PAGE_CSP)])
        code, heads, data = self.send("GET", "/assets/index-abc.js")
        etag = dict(heads)["ETag"]
        self.assertEqual(heads, [("Server", "Runway "), ("Content-Type", "text/javascript"), ("Content-Length", "14"),
                                 ("Cache-Control", "public, max-age=31536000, immutable"), ("Vary", "Accept-Encoding"),
                                 ("ETag", etag), *security()])
        code, heads, data = self.send("GET", "/sw.js", headers={"Accept-Encoding": "gzip"})
        sw_etag = dict(heads)["ETag"]
        self.assertEqual(heads, [("Server", "Runway "), ("Content-Type", "text/javascript"), ("Content-Length", str(len(data))),
                                 ("Cache-Control", "no-cache"), ("Vary", "Accept-Encoding"), ("ETag", sw_etag),
                                 ("Content-Encoding", "gzip"), *security()])
        code, heads, data = self.send("GET", "/sw.js", headers={"If-None-Match": sw_etag})
        self.assertEqual((code, data), (304, b""))
        self.assertEqual(heads, [("Server", "Runway "), ("ETag", sw_etag), ("Cache-Control", "no-cache"), *security()])
        code, heads, data = self.send("GET", "/next/budget")
        self.assertEqual((code, heads), (302, [("Server", "Runway "), ("Location", "/"), ("Content-Length", "0"),
                                               ("Cache-Control", "no-store"), *security()]))
        self.assertEqual(self.send("POST", "/", b"", {"X-Runway": "1"})[:2], (405, [
            ("Server", "Runway "), ("Content-Type", "text/plain"), ("Content-Length", "0"), ("Cache-Control", "no-store"),
            *security()]))

    def test_health_and_unknown_hosts(self):
        self.assertEqual(self.send("GET", "/healthz"), (200, [
            ("Server", "Runway "), ("Content-Type", "text/plain"), ("Content-Length", "2"), ("Cache-Control", "no-store"),
            *security()], b"ok"))
        code, heads, _ = self.send("GET", "/api/accounts", headers={"Host": "evil.example"})
        self.assertEqual(code, 403)
        self.assertEqual(heads[1], ("Content-Type", "text/plain"))


class _Closing:
    """A stand-in for prices.quote_stream's generator, which the server closes when it's done."""
    def __init__(self, gen):
        self.gen = gen

    def __iter__(self):
        return self.gen

    def close(self):
        self.gen.close()


USER = {"sub": "pin-sub", "email": "pin@example.com", "name": "Pin"}


class SignInTests(unittest.TestCase):
    """With sign-in on: who may reach each endpoint, and the consent page an assistant's connection is approved on."""

    @classmethod
    def setUpClass(cls):
        own_database(cls, OIDC_ISSUER="https://id.example", OIDC_CLIENT_ID="runway", RUNWAY_PUBLIC_URL="https://runway.example",
                     OIDC_ALLOWED_EMAILS="pin@example.com")
        session_user = mock.patch.object(oidc, "session_user", side_effect=lambda _c, token: USER if token == "good" else None)
        renew = mock.patch.object(oidc, "renew_session", return_value=None)
        hosts = mock.patch.object(common, "EXTRA_HOSTS", {"runway.example"})
        for p in (session_user, renew, hosts):
            p.start()
            cls.addClassCleanup(p.stop)
        cls.httpd = server.Server(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.port = cls.httpd.server_port

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    send = Pinned.send
    static = None

    def setUp(self):
        self.static_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.static_dir.cleanup)
        self.static = self.static_dir.name
        built_app(self.static)

    def test_signed_out(self):
        hsts = ("Strict-Transport-Security", "max-age=31536000")
        for method, path in (("GET", "/api/backup"), ("GET", "/api/merchants/x/logo"), ("GET", "/api/carta/capture"),
                             ("GET", "/api/retail/extension.zip"), ("POST", "/api/backup/inspect"), ("POST", "/api/restore"),
                             ("POST", "/api/investments/sync"), ("POST", "/api/sync/auto"), ("POST", "/api/sync"),
                             ("GET", "/api/investments/stream"), ("GET", "/api/accounts"), ("GET", "/api/nothing-here")):
            with self.subTest(path=path):
                code, heads, data = self.send(method, path, b"{}" if method == "POST" else None, {"X-Runway": "1"})
                self.assertEqual((code, json.loads(data)), (401, {"error": "You've been signed out.", "login": "/auth/login"}))
                self.assertEqual(heads, [*json_answer(len(data)), hsts])
        code, heads, _ = self.send("GET", "/budget?month=2026-09")
        self.assertEqual((code, heads), (302, [("Server", "Runway "), ("Location", "/auth/login?next=%2Fbudget%3Fmonth%3D2026-09"),
                                               ("Content-Length", "0"), ("Cache-Control", "no-store"), *security(), hsts]))
        code, heads, _ = self.send("GET", "/oauth/authorize?client_id=x")
        self.assertEqual((code, dict(heads)["Location"]), (302, "/auth/login?next=%2Foauth%2Fauthorize%3Fclient_id%3Dx"))
        self.assertEqual(self.send("GET", "/logo.svg")[0], 200)

    def test_signed_in(self):
        signed_in = {"Cookie": "runway_session=good", "X-Runway": "1"}
        self.assertEqual(self.send("GET", "/api/carta/capture", headers=signed_in)[0], 200)
        self.assertEqual(self.send("POST", "/api/sync/auto", b"{}", signed_in)[0], 200)
        code, heads, _ = self.send("GET", "/", headers=signed_in)
        self.assertEqual(code, 200)
        self.assertEqual(heads[-1], ("Strict-Transport-Security", "max-age=31536000"))

    def test_the_consent_page(self):
        with db.session() as conn:
            client = mcp_oauth.register(conn, {"client_name": "Pin <b>App</b>", "redirect_uris": ["https://app.example/cb"],
                                               "token_endpoint_auth_method": "none"})
        self.addCleanup(self.forget, client["client_id"])
        query = urllib.parse.urlencode({"response_type": "code", "client_id": client["client_id"],
                                        "redirect_uri": "https://app.example/cb", "code_challenge": "x" * 43,
                                        "code_challenge_method": "S256", "scope": "read", "state": "s1",
                                        "resource": "https://runway.example/mcp"})
        code, heads, data = self.send("GET", "/oauth/authorize?" + query, headers={"Cookie": "runway_session=good",
                                                                                  "Host": "runway.example"})
        self.assertEqual(code, 200, data)
        cookie = dict(heads)["Set-Cookie"]
        self.assertRegex(cookie, r"^runway_consent=[^;]+; Path=/oauth; Max-Age=\d+; HttpOnly; SameSite=Lax; Secure$")
        csp = ("default-src 'none'; style-src 'self' 'unsafe-inline'; img-src 'self'; font-src 'self'; "
               "form-action 'self' https://app.example; base-uri 'none'; frame-ancestors 'none'")
        self.assertEqual(heads, [("Server", "Runway "), ("Content-Type", "text/html; charset=utf-8"), ("Set-Cookie", cookie),
                                 ("Content-Length", str(len(data))), ("Cache-Control", "no-store"), *security(csp),
                                 ("Strict-Transport-Security", "max-age=31536000")])
        page = data.decode()
        self.assertIn("Pin &lt;b&gt;App&lt;/b&gt; wants to connect to Runway", page)
        self.assertIn("You're signed in as <b>pin@example.com</b>.", page)
        self.assertIn('<form method="post" action="/oauth/authorize">', page)
        token = re.search(r'name="consent" value="([^"]+)"', page).group(1)
        self.assertTrue(cookie.startswith(f"runway_consent={token};"))
        answer = urllib.parse.urlencode({"consent": token, "decision": "deny"}).encode()
        code, heads, _ = self.send("POST", "/oauth/authorize", answer, {
            "Cookie": f"runway_session=good; runway_consent={token}", "Host": "runway.example", "Origin": "null",
            "Sec-Fetch-Site": "same-origin", "Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(code, 302)
        self.assertEqual(heads, [("Server", "Runway "), ("Location", "https://app.example/cb?error=access_denied&error_description="
                                                       "The+person+using+Runway+said+no.&state=s1&iss=https%3A%2F%2Frunway.example"),
                                 ("Set-Cookie", "runway_consent=; Path=/oauth; Max-Age=0; HttpOnly; SameSite=Lax; Secure"),
                                 ("Content-Length", "0"), ("Cache-Control", "no-store"), *security(),
                                 ("Strict-Transport-Security", "max-age=31536000")])

    def forget(self, client_id):
        with db.session() as conn:
            forget_oauth(conn, [client_id])


if __name__ == "__main__":
    unittest.main()
