"""The web app (frontend/, built into runway/static/app/) is Runway's page at /: signed in, under the content security
policy, with a fresh script nonce on its page; its routes and the old /next/ address lead to it."""
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

from runway import db, server

PAGE = b'<!doctype html><head><script type="module" crossorigin src="/assets/index-abc.js"></script></head><div id="app"></div>'


def built_app(static: str) -> None:
    """A stand-in for `npm run build`'s output in `static`/app."""
    os.makedirs(os.path.join(static, "app", "assets"), exist_ok=True)
    with open(os.path.join(static, "app", "index.html"), "wb") as f:
        f.write(PAGE)
    with open(os.path.join(static, "app", "assets", "index-abc.js"), "wb") as f:
        f.write(b"console.log(1)")


def serving(static: str):
    """Serve from `static` (Runway's own files) and `static`/app (the built app)."""
    app = os.path.join(static, "app")
    return mock.patch.multiple(server.handler, STATIC=static, APP_DIR=app, APP_INDEX=os.path.join(app, "index.html"))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class WebAppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["RUNWAY_DATA"] = cls.tmp.name
        db.init()
        cls.static = os.path.join(cls.tmp.name, "static")
        built_app(cls.static)
        with open(os.path.join(cls.static, "sw.js"), "wb") as f:
            f.write(b"// service worker")
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
        with serving(static or self.static):
            try:
                with urllib.request.build_opener(NoRedirect).open(self.base + path, timeout=10) as r:
                    return r.status, r.headers, r.read()
            except urllib.error.HTTPError as e:
                return e.code, e.headers, e.read()

    def test_page_gets_a_nonce_and_the_policy(self):
        for path in ("/", "/plaid/oauth", "/anything"):   # the app's own routes get its page
            with self.subTest(path=path):
                status, headers, body = self.get(path)
                self.assertEqual(status, 200)
                csp = headers["Content-Security-Policy"]
                nonce = csp.split("'nonce-")[1].split("'")[0]
                self.assertIn(f'<script nonce="{nonce}" type="module"'.encode(), body)
                self.assertIn("object-src 'none'", csp)
                self.assertEqual(headers["Cache-Control"], "no-store")

    def test_old_address_leads_to_the_app(self):
        for path in ("/next", "/next/", "/next/assets/index-abc.js"):
            with self.subTest(path=path):
                status, headers, _ = self.get(path)
                self.assertEqual((status, headers["Location"]), (302, "/"))

    def test_built_files_are_kept_for_good(self):
        status, headers, body = self.get("/assets/index-abc.js")
        self.assertEqual((status, body), (200, b"console.log(1)"))
        self.assertIn("immutable", headers["Cache-Control"])
        status, headers, body = self.get("/sw.js")   # Runway's own files are checked each time
        self.assertEqual((status, body), (200, b"// service worker"))
        self.assertNotIn("immutable", headers.get("Cache-Control") or "")

    def test_never_outside_static(self):
        status, _, body = self.get("/../../server.py")
        self.assertNotIn(b"def serve", body)
        self.assertEqual(status, 200)   # just the app's page

    def test_not_built(self):
        with tempfile.TemporaryDirectory() as empty:
            status, _, body = self.get("/", static=empty)
        self.assertEqual(status, 404)
        self.assertIn(b"npm run build", body)


if __name__ == "__main__":
    unittest.main()
