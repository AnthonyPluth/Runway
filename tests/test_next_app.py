"""The new web app (frontend/, built into runway/static/next/) is served at /next/ like the classic one: signed in,
under the same content security policy, with a fresh script nonce on its page."""
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runway import db, server  # noqa: E402

PAGE = b'<!doctype html><head><script type="module" crossorigin src="/next/assets/index-abc.js"></script></head><div id="app"></div>'


class NextAppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["RUNWAY_DATA"] = cls.tmp.name
        db.init()
        cls.static = os.path.join(cls.tmp.name, "static")
        os.makedirs(os.path.join(cls.static, "next", "assets"))
        with open(os.path.join(cls.static, "index.html"), "wb") as f:
            f.write(b"<!doctype html><script src=/app.js></script>")
        with open(os.path.join(cls.static, "next", "index.html"), "wb") as f:
            f.write(PAGE)
        with open(os.path.join(cls.static, "next", "assets", "index-abc.js"), "wb") as f:
            f.write(b"console.log(1)")
        cls.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()
        os.environ.pop("RUNWAY_DATA", None)

    def get(self, path, static=None):
        static = static or self.static
        with mock.patch.multiple(server, STATIC=static, INDEX=os.path.join(static, "index.html"),
                                 NEXT_INDEX=os.path.join(static, "next", "index.html")):
            try:
                with urllib.request.urlopen(self.base + path, timeout=10) as r:
                    return r.status, r.headers, r.read()
            except urllib.error.HTTPError as e:
                return e.code, e.headers, e.read()

    def test_page_gets_a_nonce_and_the_same_policy(self):
        for path in ("/next/", "/next", "/next/anything"):
            with self.subTest(path=path):
                status, headers, body = self.get(path)
                self.assertEqual(status, 200)
                csp = headers["Content-Security-Policy"]
                nonce = csp.split("'nonce-")[1].split("'")[0]
                self.assertIn(f'<script nonce="{nonce}" type="module"'.encode(), body)
                self.assertIn("object-src 'none'", csp)
                self.assertEqual(headers["Cache-Control"], "no-store")

    def test_built_files_are_kept_for_good(self):
        status, headers, body = self.get("/next/assets/index-abc.js")
        self.assertEqual((status, body), (200, b"console.log(1)"))
        self.assertIn("immutable", headers["Cache-Control"])
        status, headers, _ = self.get("/app.js")   # the classic app's files are still checked each time
        self.assertNotIn("immutable", headers.get("Cache-Control") or "")

    def test_not_built(self):
        with tempfile.TemporaryDirectory() as empty:
            status, _, body = self.get("/next/", static=empty)
        self.assertEqual(status, 404)
        self.assertIn(b"npm run build", body)


if __name__ == "__main__":
    unittest.main()
