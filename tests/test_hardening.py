"""Fixes from the September 2026 review of the server: sign-in, slow clients, restore, outbound fetches, logos, and
smaller hardening (Carta mock, sync on visit, notifications, secret keys)."""
import base64
import contextlib
import gzip
import json
import os
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import date
from unittest import mock


from cryptography.fernet import Fernet

from runway import backup, db, notify, oidc, secretbox, server, simplefin

ENV = ("OIDC_ALLOWED_EMAILS", "OIDC_ALLOWED_GROUPS", "OIDC_ALLOW_ANY_USER", "OIDC_TRUST_UNVERIFIED_EMAIL")


class SignInTests(unittest.TestCase):
    def setUp(self):
        self.saved = {k: os.environ.get(k) for k in (*ENV, "RUNWAY_DATA")}
        for k in ENV:
            os.environ.pop(k, None)
        os.environ["OIDC_ALLOWED_EMAILS"] = "me@example.com"
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["RUNWAY_DATA"] = self.tmp.name
        db.init()

    def tearDown(self):
        with db.session() as c:
            c.execute("DELETE FROM auth_sessions")
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def test_email_must_be_verified(self):
        ok = {"sub": "u1", "email": "me@example.com"}
        self.assertEqual(oidc.authorize({**ok, "email_verified": True})["email"], "me@example.com")
        self.assertEqual(oidc.authorize({**ok, "email_verified": "true"})["email"], "me@example.com")
        for claim in ({}, {"email_verified": False}, {"email_verified": "false"}, {"email_verified": None}):
            with self.assertRaisesRegex(oidc.OIDCError, "verified"):
                oidc.authorize({**ok, **claim})

    def test_unverified_email_can_be_trusted_on_purpose(self):
        os.environ["OIDC_TRUST_UNVERIFIED_EMAIL"] = "1"
        self.assertEqual(oidc.authorize({"sub": "u1", "email": "me@example.com"})["email"], "me@example.com")

    def test_groups_dont_need_a_verified_email(self):
        os.environ["OIDC_ALLOWED_GROUPS"] = "finance"
        self.assertEqual(oidc.authorize({"sub": "u1", "email": "me@example.com", "groups": ["finance"]})["sub"], "u1")
        with self.assertRaises(oidc.OIDCError):
            oidc.authorize({"sub": "u2", "email": "other@example.com", "groups": ["sales"], "email_verified": True})

    def test_removed_email_ends_sessions(self):
        with db.session() as c:
            c.execute("INSERT INTO auth_sessions(token_hash, sub, email, name, created, expires) VALUES (?,?,?,?,?,?)",
                      (oidc._hash("tok"), "u1", "me@example.com", "Me", time.time(), time.time() + 3600))
            self.assertEqual(oidc.session_user(c, "tok")["email"], "me@example.com")
        os.environ["OIDC_ALLOWED_EMAILS"] = "someone@example.com"
        with db.session() as c:
            self.assertIsNone(oidc.session_user(c, "tok"))
            self.assertIsNone(c.execute("SELECT 1 FROM auth_sessions").fetchone())   # and it's gone for good
        os.environ["OIDC_ALLOWED_EMAILS"] = "me@example.com"
        with db.session() as c:
            self.assertIsNone(oidc.session_user(c, "tok"))


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.saved = os.environ.get("RUNWAY_DATA")
        os.environ["RUNWAY_DATA"] = cls.tmp.name
        db.init()
        cls.httpd = server.Server(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown(); cls.httpd.server_close()
        with db.session() as c:   # on Postgres the tests share one database: leave it as found
            c.execute("DELETE FROM merchants WHERE id LIKE 'test:%'")
            db.set_setting(c, "carta_env", None)
        cls.tmp.cleanup()
        if cls.saved is None:
            os.environ.pop("RUNWAY_DATA", None)
        else:
            os.environ["RUNWAY_DATA"] = cls.saved

    def open(self, path, method="GET", body=None, headers=None):
        r = urllib.request.Request(self.base + path, method=method, data=body, headers=headers or {})
        opener = urllib.request.build_opener(NoRedirect)
        try:
            resp = opener.open(r, timeout=10)
        except urllib.error.HTTPError as e:
            resp = e
        return resp.status if hasattr(resp, "status") else resp.code, resp.headers, resp.read()

    def test_trickled_headers_are_hung_up_on(self):
        with mock.patch.object(server, "HEADER_DEADLINE", 1):
            s = socket.create_connection(("127.0.0.1", self.httpd.server_port), timeout=10)
            s.setblocking(False)
            started, closed = time.monotonic(), False
            try:
                s.sendall(b"GET /healthz HTTP/1.1\r\n")
                while time.monotonic() - started < 5 and not closed:
                    s.send(b"X")   # a header byte every 0.1s: each read is quick, the headers never finish
                    time.sleep(0.1)
                    with contextlib.suppress(BlockingIOError):
                        closed = s.recv(1024) == b""
            except OSError:
                closed = True
            elapsed = time.monotonic() - started
            s.close()
        self.assertTrue(closed)
        self.assertLess(elapsed, 3.5)
        self.assertEqual(self.open("/healthz")[0], 200)   # everyone else is served as usual

    def test_restore_waits_for_a_running_sync(self):
        with db.session() as c:
            raw = backup.dump(c)
        headers = {"X-Runway": "1", "Content-Type": "application/octet-stream"}
        with server._sync_lock:
            code, _, body = self.open("/api/restore", "POST", raw, headers)
        self.assertEqual(code, 409)
        self.assertIn("sync is running", json.loads(body)["error"])
        for lock in (server._inv_lock, server._retail_categorize_lock):
            with lock:
                self.assertEqual(self.open("/api/restore", "POST", raw, headers)[0], 409)
        self.assertFalse(server._sync_lock.locked())   # released after refusing
        # Background work other tests started (an order import's categorizing) may still be finishing: wait for it,
        # as someone restoring would, rather than race it.
        for lock in (server._sync_lock, server._inv_lock, server._retail_categorize_lock):
            self.assertTrue(lock.acquire(timeout=30))
            lock.release()
        self.assertEqual(self.open("/api/restore", "POST", raw, headers)[0], 200)

    def test_only_image_logos_are_served(self):
        png = b"\x89PNG\r\n\x1a\n" + bytes(16)
        with db.session() as c:
            c.execute("INSERT INTO merchants(id, name, logo, logo_type) VALUES ('test:png', 'P', ?, 'image/png')",
                      (base64.b64encode(png).decode(),))
            c.execute("INSERT INTO merchants(id, name, logo, logo_type) VALUES ('test:html', 'H', ?, 'text/html')",
                      (base64.b64encode(b"<script>alert(1)</script>").decode(),))
        code, h, body = self.open("/api/merchants/test:png/logo")
        self.assertEqual((code, body, h["Content-Type"]), (200, png, "image/png"))
        self.assertTrue(any("sandbox" in v for v in h.get_all("Content-Security-Policy")))
        self.assertEqual(self.open("/api/merchants/test:html/logo")[0], 404)

    def test_carta_mock_callback_needs_mock_mode_and_same_site(self):
        with mock.patch.object(server.carta, "sync") as sync:
            with db.session() as c:
                db.set_setting(c, "carta_env", "production")
            self.assertEqual(self.open("/carta/callback?mock=1")[0], 400)
            with db.session() as c:
                db.set_setting(c, "carta_env", "mock")
            self.assertEqual(self.open("/carta/callback?mock=1", headers={"Sec-Fetch-Site": "cross-site"})[0], 400)
            sync.assert_not_called()
            self.assertEqual(self.open("/carta/callback?mock=1", headers={"Sec-Fetch-Site": "same-origin"})[0], 302)
            sync.assert_called_once()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class SyncOnVisitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = os.environ.get("RUNWAY_DATA")
        os.environ["RUNWAY_DATA"] = self.tmp.name
        db.init()
        with db.session() as c:
            db.set_setting(c, "simplefin_access_url", "https://u:p@bridge.example/simplefin")
            db.set_setting(c, "last_auto_sync_attempt", None)
            db.set_setting(c, "last_sync_ok", None)

    def tearDown(self):
        with db.session() as c:
            db.set_setting(c, "simplefin_access_url", None)
            db.set_setting(c, "last_auto_sync_attempt", None)
        self.tmp.cleanup()
        if self.saved is None:
            os.environ.pop("RUNWAY_DATA", None)
        else:
            os.environ["RUNWAY_DATA"] = self.saved

    def test_a_skipped_sync_isnt_an_attempt(self):
        with server._sync_lock:
            self.assertEqual(server.sync_on_visit(), {"started": False})
        with db.session() as c:
            self.assertIsNone(db.get_setting(c, "last_auto_sync_attempt"))   # the next visit still syncs
        with mock.patch.object(server, "_sync_everything"):
            self.assertEqual(server.sync_on_visit(), {"started": True})
        with db.session() as c:
            self.assertIsNotNone(db.get_setting(c, "last_auto_sync_attempt"))


class OutboundTests(unittest.TestCase):
    def test_push_hosts(self):
        for ok in ("https://fcm.googleapis.com/fcm/send/abc", "https://updates.push.services.mozilla.com/wpush/v2/x",
                   "https://web.push.apple.com/QG", "https://wns2-par02p.notify.windows.com/w/?token=x"):
            self.assertTrue(notify.push_host_allowed(ok), ok)
        for bad in ("https://169.254.169.254/latest", "https://evil.example/x", "https://fcm.googleapis.com.evil.example/",
                    "https://localhost/x"):
            self.assertFalse(notify.push_host_allowed(bad), bad)
        with mock.patch.dict(os.environ, {"RUNWAY_PUSH_HOSTS": "ntfy.example.org"}):
            self.assertTrue(notify.push_host_allowed("https://ntfy.example.org/up123"))

    def test_simplefin_addresses_must_be_public(self):
        for url in ("https://127.0.0.1/claim", "https://10.0.0.5/simplefin", "https://[::1]/x", "https://169.254.169.254/",
                    "http://bridge.simplefin.org/claim"):
            with self.assertRaises(simplefin.SimpleFinError, msg=url):
                simplefin.check_address(url)
        token = base64.b64encode(b"https://192.168.1.10/claim/abc").decode()
        with mock.patch.object(simplefin.urllib.request, "urlopen") as urlopen:
            with self.assertRaises(simplefin.SimpleFinError):
                simplefin.claim_setup_token(token)
            urlopen.assert_not_called()
        with mock.patch.object(socket, "getaddrinfo",
                               return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 443))]):
            simplefin.check_address("https://beta-bridge.simplefin.org/simplefin")   # a public address is fine


class BackupSizeTests(unittest.TestCase):
    def test_a_gzip_bomb_is_refused(self):
        bomb = gzip.compress(b"0" * (2 * 1024 * 1024))
        with mock.patch.object(backup, "MAX_UNPACKED", 1024 * 1024):
            with self.assertRaisesRegex(ValueError, "too large"):
                backup.load(bomb)
        with self.assertRaisesRegex(ValueError, "isn't a Runway backup"):
            backup.load(gzip.compress(b"{not json"))
        with self.assertRaisesRegex(ValueError, "isn't a Runway backup"):
            backup.load(b"\x1f\x8b" + b"garbage")


class NotifyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "n.db")
        db.init(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_alert_is_remembered_even_if_the_run_fails_after_sending(self):
        alert = {"key": "test:1", "title": "Hello", "body": "b"}
        with db.session(self.path) as c:
            c.execute("INSERT INTO push_subscriptions(endpoint, p256dh, auth, device, created) VALUES ('https://fcm.googleapis.com/x','k','a','d',0)")
        with mock.patch.object(notify, "alerts", return_value=[alert]), \
                mock.patch.object(notify, "send_all", side_effect=RuntimeError("after sending")) as send:
            with self.assertRaises(RuntimeError):
                with db.session(self.path) as c:
                    notify.run(c, date(2026, 9, 28))
            send.assert_called_once()
        with db.session(self.path) as c:   # rolled back, but the alert was saved first: it won't go out twice
            self.assertIsNotNone(c.execute("SELECT 1 FROM notify_log WHERE key='test:1'").fetchone())


class SecretKeyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "s.db")
        self.env = mock.patch.dict(os.environ, {"RUNWAY_SECRET_KEY": "correct horse battery staple, but longer than 32"})
        self.env.start()
        secretbox._cache.clear()
        db.init(self.path)
        self.c = db.connect(self.path)

    def tearDown(self):
        self.c.close()
        self.env.stop()
        secretbox._cache.clear()
        self.tmp.cleanup()

    def test_passphrase_is_stretched_and_old_keys_still_open(self):
        key = os.environ["RUNWAY_SECRET_KEY"]
        self.assertNotEqual(secretbox._from_passphrase(key), secretbox._from_passphrase_v1(key))
        legacy = secretbox.PREFIX + Fernet(secretbox._from_passphrase_v1(key)).encrypt(b"sk-old").decode()
        self.c.execute("INSERT INTO settings(key, value) VALUES ('openrouter_api_key', ?)", (legacy,))
        self.assertEqual(db.get_setting(self.c, "openrouter_api_key"), "sk-old")   # saved by an earlier version
        self.assertEqual(secretbox.encrypt_stored(self.c), 1)                    # moved to the stretched key
        stored = self.c.execute("SELECT value FROM settings WHERE key='openrouter_api_key'").fetchone()[0]
        Fernet(secretbox._from_passphrase(key)).decrypt(stored[len(secretbox.PREFIX):].encode())
        self.assertEqual(db.get_setting(self.c, "openrouter_api_key"), "sk-old")


if __name__ == "__main__":
    unittest.main()
