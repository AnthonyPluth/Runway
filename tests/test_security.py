"""Hardening for running Runway on the internet: headers, errors, limits, redirects, secrets at rest, sync status."""
import gzip
import json
import os
import re
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runway import backup, db, oidc, secretbox, server, simplefin  # noqa: E402


class SafeNextTests(unittest.TestCase):
    def test_only_paths_on_this_site(self):
        for good in ("/", "/#budget", "/plaid/oauth?oauth_state_id=abc"):
            self.assertEqual(oidc.safe_next(good), good)
        for bad in ("//evil.com", "/\\evil.com", "/\\\\evil.com", "https://evil.com", "evil.com", "/\tx", "/a\r\nSet-Cookie: x", "", None):
            self.assertEqual(oidc.safe_next(bad), "/", bad)


class PublicUrlTests(unittest.TestCase):
    def setUp(self):
        self.saved = dict(os.environ)
        os.environ.update({"OIDC_ISSUER": "https://auth.example.com", "OIDC_CLIENT_ID": "runway", "OIDC_ALLOWED_EMAILS": "me@example.com"})

    def tearDown(self):
        os.environ.clear(); os.environ.update(self.saved)

    def problems(self, url, **env):
        os.environ["RUNWAY_PUBLIC_URL"] = url
        os.environ.update(env)
        return oidc.check_config()

    def test_https_required_on_the_internet(self):
        self.assertTrue(any("https://" in p for p in self.problems("http://runway.example.com")))
        self.assertEqual(self.problems("https://runway.example.com"), [])
        for home in ("http://192.168.1.20:8765", "http://nas:8765", "http://runway.local", "http://localhost:8765", "http://box.tail12.ts.net"):
            self.assertEqual(self.problems(home), [], home)
        self.assertEqual(self.problems("http://runway.example.com", RUNWAY_ALLOW_INSECURE_HTTP="1"), [])


class SecretsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "s.db")
        db.init(self.path)
        self.c = db.connect(self.path)

    def tearDown(self):
        self.c.close(); self.tmp.cleanup()

    def raw(self, key):
        return self.c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()[0]

    def test_secret_settings_are_encrypted(self):
        db.set_setting(self.c, "openrouter_api_key", "sk-or-123")
        db.set_setting(self.c, "llm_model", "some/model")                  # not a secret: stored as is
        self.assertTrue(self.raw("openrouter_api_key").startswith("enc:v1:"))
        self.assertNotIn("sk-or-123", self.raw("openrouter_api_key"))
        self.assertEqual(db.get_setting(self.c, "openrouter_api_key"), "sk-or-123")
        self.assertEqual(self.raw("llm_model"), "some/model")

    def test_plaintext_from_older_versions_is_encrypted_at_start(self):
        self.c.execute("INSERT INTO settings(key, value) VALUES ('plaid_secret', 'plain-secret')")
        self.c.execute("INSERT INTO plaid_items(item_id, access_token) VALUES ('i1', 'access-plain')")
        self.assertEqual(secretbox.encrypt_stored(self.c), 2)
        self.assertTrue(self.raw("plaid_secret").startswith("enc:v1:"))
        tok = self.c.execute("SELECT access_token FROM plaid_items").fetchone()[0]
        self.assertTrue(tok.startswith("enc:v1:"))
        self.assertEqual(secretbox.decrypt(tok), "access-plain")
        self.assertEqual(secretbox.encrypt_stored(self.c), 0)              # nothing left to do

    def test_backups_carry_secrets_decrypted_and_restore_encrypted(self):
        db.set_setting(self.c, "rentcast_api_key", "rc-key")
        self.c.execute("INSERT INTO plaid_items(item_id, access_token) VALUES ('i1', ?)", (secretbox.encrypt("access-1"),))
        data = backup.load(backup.dump(self.c))
        rows = data["tables"]["settings"]["rows"]
        self.assertIn(["rentcast_api_key", "rc-key"], [r[:2] for r in rows])
        self.assertIn("access-1", data["tables"]["plaid_items"]["rows"][0])
        other = os.path.join(self.tmp.name, "o.db")
        db.init(other)
        with db.session(other) as c2:
            backup.restore(c2, data)
        with db.session(other) as c2:
            self.assertTrue(c2.execute("SELECT value FROM settings WHERE key='rentcast_api_key'").fetchone()[0].startswith("enc:v1:"))
            self.assertEqual(db.get_setting(c2, "rentcast_api_key"), "rc-key")

    def test_key_rotation_and_a_wrong_key(self):
        db.set_setting(self.c, "openrouter_api_key", "sk-1")
        old = os.environ["RUNWAY_SECRET_KEY"]
        new = "a-brand-new-key-abcdefghijklmnopqrstuvwxyz"
        with mock.patch.dict(os.environ, {"RUNWAY_SECRET_KEY": new, "RUNWAY_SECRET_KEY_OLD": old}):
            self.assertEqual(db.get_setting(self.c, "openrouter_api_key"), "sk-1")   # read with the old key
            secretbox.encrypt_stored(self.c)                                          # and moved to the new one
        with mock.patch.dict(os.environ, {"RUNWAY_SECRET_KEY": new}):
            self.assertEqual(db.get_setting(self.c, "openrouter_api_key"), "sk-1")
        # the original key alone can't read it any more: treated as not entered, never a crash
        self.assertIsNone(db.get_setting(self.c, "openrouter_api_key"))

    def test_short_keys_are_refused(self):
        with mock.patch.dict(os.environ, {"RUNWAY_SECRET_KEY": "short"}):
            self.assertTrue(secretbox.check_config())


class HttpTests(unittest.TestCase):
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
        cls.tmp.cleanup()
        if cls.saved is None:
            os.environ.pop("RUNWAY_DATA", None)
        else:
            os.environ["RUNWAY_DATA"] = cls.saved

    def open(self, path, method="GET", body=None, headers=None):
        r = urllib.request.Request(self.base + path, method=method, data=body, headers=headers or {})
        try:
            resp = urllib.request.urlopen(r, timeout=10)
        except urllib.error.HTTPError as e:
            resp = e
        return resp.status if hasattr(resp, "status") else resp.code, resp.headers, resp.read()

    def api(self, method, path, body=None, headers=None):
        h = {"X-Runway": "1", "Content-Type": "application/json", **(headers or {})}
        code, _, raw = self.open(path, method, json.dumps(body).encode() if body is not None else None, h)
        return code, json.loads(raw or b"{}")

    def test_security_headers_and_script_nonce(self):
        code, h, page = self.open("/")
        self.assertEqual(code, 200)
        csp = h["Content-Security-Policy"]
        nonce = re.search(r"'nonce-([^']+)'", csp).group(1)
        self.assertIn(f'<script nonce="{nonce}" src="/app.js">'.encode(), page)
        for d in ("frame-ancestors 'none'", "object-src 'none'", "base-uri 'none'"):
            self.assertIn(d, csp)
        self.assertEqual(h["X-Frame-Options"], "DENY")
        self.assertEqual(h["Referrer-Policy"], "no-referrer")
        self.assertEqual(h["Server"].strip(), "Runway")                           # no Python version
        self.assertNotEqual(nonce, re.search(r"'nonce-([^']+)'", self.open("/")[1]["Content-Security-Policy"]).group(1))
        self.assertIn("frame-ancestors 'none'", self.open("/api/state")[1]["Content-Security-Policy"])

    def test_errors_dont_show_internals(self):
        code, body = self.api("GET", "/api/transactions?limit=x")
        self.assertEqual(code, 400)
        self.assertNotIn("int()", body["error"])
        self.assertIn("reference", body["error"])
        with mock.patch.object(server.categories, "all_categories", side_effect=RuntimeError("secret detail")):
            code, body = self.api("GET", "/api/categories")
        self.assertEqual(code, 500)
        self.assertNotIn("secret detail", body["error"])

    def test_request_limits(self):
        code, _ = self.api("POST", "/api/settings", headers={"Content-Length": str(server.MAX_JSON_BODY + 1)})
        self.assertEqual(code, 413)
        code, _, _ = self.open("/api/settings", "POST", b"[1,2]", {"X-Runway": "1", "Content-Type": "application/json"})
        self.assertEqual(code, 400)

    def test_cross_site_requests_are_refused(self):
        self.assertEqual(self.api("POST", "/api/settings", {"horizon_days": 90}, {"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.api("POST", "/api/settings", {"horizon_days": 90}, {"Sec-Fetch-Site": "cross-site"})[0], 403)
        self.assertEqual(self.api("POST", "/api/settings", {"horizon_days": 90}, {"Origin": "null"})[0], 403)
        self.assertEqual(self.api("POST", "/api/settings", {"horizon_days": 90}, {"Origin": self.base})[0], 200)

    def test_static_files(self):
        code, h, body = self.open("/../server.py")
        self.assertNotIn(b"def serve", body)                                        # never outside static/
        code, h, body = self.open("/app.js", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(h["Content-Encoding"], "gzip")
        self.assertIn(b"use strict", gzip.decompress(body))
        self.assertEqual(self.open("/app.js", headers={"If-None-Match": h["ETag"]})[0], 304)
        code, _, body = self.open("/healthz", "HEAD")
        self.assertEqual((code, body), (200, b""))


class SyncStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = os.environ.get("RUNWAY_DATA")
        os.environ["RUNWAY_DATA"] = self.tmp.name
        db.init()
        with db.session() as c:
            db.set_setting(c, "simplefin_access_url", "https://u:p@bridge.example/simplefin")

    def tearDown(self):
        server.AUTO_SYNC = True
        self.tmp.cleanup()
        if self.saved is None:
            os.environ.pop("RUNWAY_DATA", None)
        else:
            os.environ["RUNWAY_DATA"] = self.saved

    def test_a_failed_sync_is_recorded(self):
        with mock.patch.object(simplefin, "sync", side_effect=simplefin.SimpleFinError("SimpleFIN is down")):
            with self.assertRaises(server.ApiError):
                server.run_sync()
        with db.session() as c:
            last = c.execute("SELECT ok, message FROM sync_log ORDER BY id DESC LIMIT 1").fetchone()
            self.assertEqual((last["ok"], last["message"]), (0, "SimpleFIN is down"))
            self.assertIsNone(db.get_setting(c, "last_sync_ok"))

    def test_no_sync_means_no_sync_on_visit(self):
        server.AUTO_SYNC = False
        with mock.patch.object(threading, "Thread") as t:
            self.assertEqual(server.sync_on_visit(), {"started": False})
            t.assert_not_called()


if __name__ == "__main__":
    unittest.main()
