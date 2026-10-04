"""The extension's key and what it can call: tokens, their details, and the extension's API."""
import hashlib
import hmac
import json
import os
import threading
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from unittest import mock

from sqlalchemy import select

from runway import db, oidc, retail
from runway.models import RetailOrder
from tests.shared import ServerCase
from tests.retail_support import ORDER, Base


class TokenTests(Base):
    def test_the_key_is_the_persons_and_ends(self):
        token = retail.new_token(self.c, {"sub": "u1", "email": "a@example.com", "name": "A"})
        oidc.remember_user(self.c, "u1", "a@example.com", "A")
        self.assertIsNone(retail.token_check(self.c, f"Bearer {token}"))
        self.assertEqual(retail.token_check(self.c, "Bearer rwx_other"), "unknown")
        self.assertEqual(retail.token_check(self.c, None), "unknown")
        status = retail.status(self.c)
        self.assertIsNotNone(status["token_used"])                                  # noted on a working call
        self.assertEqual(status["token_expires"][:10], (date.today() + timedelta(days=retail.TOKEN_DAYS)).isoformat())
        self.assertIsNone(status["token_problem"])
        # taken off the sign-in list, the key they made stops working (like their sessions)
        with mock.patch.dict(os.environ, {"OIDC_ISSUER": "https://id.example.com", "OIDC_ALLOWED_EMAILS": "b@example.com"}):
            self.assertEqual(retail.token_check(self.c, f"Bearer {token}"), "owner_gone")
            self.assertEqual(retail.status(self.c)["token_problem"], "owner_gone")
        with mock.patch.dict(os.environ, {"OIDC_ISSUER": "https://id.example.com", "OIDC_ALLOWED_EMAILS": "a@example.com"}):
            self.assertIsNone(retail.token_check(self.c, f"Bearer {token}"))
        # and it expires
        db.set_setting(self.c, "retail_token_created", (datetime.now() - timedelta(days=retail.TOKEN_DAYS)).isoformat(timespec="seconds"))
        self.assertEqual(retail.token_check(self.c, f"Bearer {token}"), "expired")
        for reason in ("expired", "owner_gone", "unknown"):
            self.assertIn("Settings", retail.REFUSALS[reason])
        retail.remove_token(self.c)
        self.assertEqual(retail.token_check(self.c, f"Bearer {token}"), "unknown")
        self.assertEqual(retail.status(self.c)["token_expires"], None)

    def test_a_key_made_without_sign_in_only_expires(self):
        token = retail.new_token(self.c, {"name": None, "email": None, "local": True})
        self.assertIsNone(db.get_setting(self.c, "retail_token_owner"))
        with mock.patch.dict(os.environ, {"OIDC_ISSUER": "https://id.example.com", "OIDC_ALLOWED_EMAILS": "b@example.com"}):
            self.assertIsNone(retail.token_check(self.c, f"Bearer {token}"))


class TokenDetailTests(Base):
    """The extension's key exactly as it's made, kept and checked, so a change to any of it is on purpose."""

    def test_made_and_kept(self):
        token = retail.new_token(self.c, {"sub": "u1", "email": "a@example.com", "name": "A"})
        self.assertRegex(token, r"^rwx_[A-Za-z0-9_-]{43}$")
        self.assertEqual(db.get_setting(self.c, "retail_token_hash"), hashlib.sha256(token.encode()).hexdigest())
        self.assertEqual(db.get_setting(self.c, "retail_token_owner"), '{"sub": "u1", "email": "a@example.com"}')
        made = db.get_setting(self.c, "retail_token_created")
        self.assertRegex(made, r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d$")   # local time, to the second, no zone
        self.assertLess(abs((datetime.fromisoformat(made) - datetime.now()).total_seconds()), 5)
        self.assertIsNone(db.get_setting(self.c, "retail_token_used"))
        self.assertEqual(retail.token_expires(self.c), (datetime.fromisoformat(made) + timedelta(days=90)).isoformat())
        self.assertEqual(retail.TOKEN_DAYS, 90)
        # a new key replaces the old one, and starts unused
        self.assertIsNone(retail.token_check(self.c, f"Bearer {token}"))
        again = retail.new_token(self.c, {"sub": "u2"})
        self.assertEqual(db.get_setting(self.c, "retail_token_owner"), '{"sub": "u2", "email": null}')
        self.assertIsNone(db.get_setting(self.c, "retail_token_used"))
        self.assertEqual(retail.token_check(self.c, f"Bearer {token}"), "unknown")
        self.assertIsNone(retail.token_check(self.c, f"Bearer {again}"))
        retail.new_token(self.c, None)
        self.assertIsNone(db.get_setting(self.c, "retail_token_owner"))

    def test_the_header(self):
        self.assertEqual(retail.token_check(self.c, "Bearer rwx_x"), "unknown")   # no key at all
        token = retail.new_token(self.c)
        with mock.patch("hmac.compare_digest", wraps=hmac.compare_digest) as compare:
            self.assertIsNone(retail.token_check(self.c, f"Bearer {token}"))
        compare.assert_called_once_with(hashlib.sha256(token.encode()).hexdigest(),
                                        db.get_setting(self.c, "retail_token_hash"))   # in constant time
        self.assertIsNone(retail.token_check(self.c, f"  Bearer   {token}  "))
        self.assertIsNone(retail.token_check(self.c, f"Bearer\t{token}"))
        for refused in (f"bearer {token}", f"Basic {token}", f"Bearer {token} x", f"Bearer{token}", token, "",
                        f"Bearer {token[:-1]}", f"Bearer {token}x", f"Bearer {hashlib.sha256(token.encode()).hexdigest()}"):
            self.assertEqual(retail.token_check(self.c, refused), "unknown", refused)

    def test_last_use_is_noted_once_a_minute(self):
        token = retail.new_token(self.c)
        recent = (datetime.now() - timedelta(seconds=30)).isoformat(timespec="seconds")
        db.set_setting(self.c, "retail_token_used", recent)
        retail.token_check(self.c, f"Bearer {token}")
        self.assertEqual(db.get_setting(self.c, "retail_token_used"), recent)
        db.set_setting(self.c, "retail_token_used", (datetime.now() - timedelta(seconds=61)).isoformat(timespec="seconds"))
        retail.token_check(self.c, f"Bearer {token}")
        used = db.get_setting(self.c, "retail_token_used")
        self.assertRegex(used, r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d$")
        self.assertLess(abs((datetime.fromisoformat(used) - datetime.now()).total_seconds()), 5)
        db.set_setting(self.c, "retail_token_used", "not a date")   # unreadable: noted again
        retail.token_check(self.c, f"Bearer {token}")
        self.assertNotEqual(db.get_setting(self.c, "retail_token_used"), "not a date")
        retail.token_check(self.c, "Bearer rwx_other")   # a refused call isn't a use
        db.set_setting(self.c, "retail_token_used", None)
        retail.token_check(self.c, "Bearer rwx_other")
        self.assertIsNone(db.get_setting(self.c, "retail_token_used"))

    def test_when_it_ends(self):
        retail.new_token(self.c, {"sub": "u1", "email": "a@example.com"})
        db.set_setting(self.c, "retail_token_created", "2026-01-01T10:00:00")
        self.assertEqual(retail.token_expires(self.c), "2026-04-01T10:00:00")
        self.assertIsNone(retail.token_problem(self.c, datetime(2026, 4, 1, 9, 59, 59)))
        self.assertEqual(retail.token_problem(self.c, datetime(2026, 4, 1, 10, 0, 0)), "expired")
        with mock.patch.object(oidc, "access_lapsed", return_value=True) as lapsed:
            self.assertEqual(retail.token_problem(self.c, datetime(2026, 4, 2)), "expired")   # expired first
            self.assertEqual(retail.token_problem(self.c, datetime(2026, 1, 2)), "owner_gone")
        lapsed.assert_called_once_with(self.c, "u1", "a@example.com")
        for owner in ("not json", "[]", '"u1"', "null"):   # an owner that can't be read: the key only expires
            db.set_setting(self.c, "retail_token_owner", owner)
            with mock.patch.object(oidc, "access_lapsed", return_value=True) as lapsed:
                self.assertIsNone(retail.token_problem(self.c, datetime(2026, 1, 2)), owner)
            lapsed.assert_not_called()
        db.set_setting(self.c, "retail_token_created", "garbled")   # a date it can't read: no expiry
        self.assertIsNone(retail.token_expires(self.c))
        self.assertIsNone(retail.token_problem(self.c, datetime(2099, 1, 1)))

    def test_removed(self):
        retail.new_token(self.c, {"sub": "u1"})
        retail.token_check(self.c, "Bearer x")
        db.set_setting(self.c, "retail_token_used", "2026-01-01T10:00:00")
        retail.remove_token(self.c)
        for key in ("retail_token_hash", "retail_token_created", "retail_token_owner", "retail_token_used"):
            self.assertIsNone(db.get_setting(self.c, key), key)
        st = retail.status(self.c)
        self.assertEqual((st["token"], st["token_created"], st["token_used"], st["token_expires"], st["token_problem"]),
                         (False, None, None, None, None))
        db.set_setting(self.c, "retail_token_created", "2020-01-01T00:00:00")   # without a key there's no problem to show
        self.assertIsNone(retail.status(self.c)["token_problem"])


class ExtensionApiTests(ServerCase):
    """The extension's calls carry its key instead of a sign-in; everything else still needs the app's header."""

    app_header = False      # the extension's calls carry its key; the tests add the app's header where a call needs it

    def test_key(self):
        self.assertEqual(self.req("POST", "/api/retail/token")[0], 403)              # the app's own calls need its header
        code, r = self.req("POST", "/api/retail/token", headers={"X-Runway": "1"})
        self.assertEqual(code, 200)
        key = r["token"]
        ext = {"Authorization": f"Bearer {key}", "Origin": "chrome-extension://abcdef"}
        self.assertEqual(self.req("POST", "/api/ext/ping", {}, {"Origin": "chrome-extension://abcdef"})[0], 401)
        self.assertEqual(self.req("POST", "/api/ext/ping", {}, {"Authorization": "Bearer nope"})[0], 401)
        self.assertEqual(self.req("POST", "/api/ext/ping", {}, ext), (200, {"ok": True}))
        code, r = self.req("POST", "/api/ext/start", {"retailer": "target"}, ext)
        self.assertEqual(code, 200)
        self.assertIn("store", r["detail_urls"])
        self.assertEqual(set(r["order_pages"]), {"store", "online"})   # where to find an order's items otherwise
        self.assertEqual(self.req("POST", "/api/ext/start", {"retailer": "walmart"}, ext)[0], 400)
        code, r = self.req("POST", "/api/ext/target/history", {"purchase_type": "STORE", "data": {"orders": []}}, ext)
        self.assertEqual((code, r["more"]), (200, False))
        self.assertEqual(self.req("POST", "/api/ext/finish", {"retailer": "target"}, ext)[0], 200)
        # What's still unmatched now, for the extension's popup (matches you make in Runway count straight away).
        self.assertEqual(self.req("POST", "/api/ext/status", {}, ext), (200, {"unmatched": {"amazon": 0, "target": 0, "costco": 0}}))
        # The key only opens the extension's calls.
        self.assertEqual(self.req("POST", "/api/retail/token", headers=ext)[0], 403)
        self.req("POST", "/api/retail/token/remove", headers={"X-Runway": "1"})
        self.assertEqual(self.req("POST", "/api/ext/ping", {}, ext)[0], 401)
        with urllib.request.urlopen(self.base + "/api/retail/extension.zip") as resp:   # to install it from Settings
            import io
            import re
            import zipfile
            z = zipfile.ZipFile(io.BytesIO(resp.read()))
        names = z.namelist()
        self.assertIn("runway-orders/manifest.json", names)
        # Every script the extension loads ships too: background.js is split into several files, which Chrome's worker
        # imports and Firefox loads from the manifest.
        background = json.loads(z.read("runway-orders/manifest.json"))["background"]
        imports = re.search(r"importScripts\(([^)]*)\)", z.read("runway-orders/background.js").decode())
        scripts = {background["service_worker"], *background["scripts"], *re.findall(r'"([^"]+\.js)"', imports.group(1))}
        self.assertGreater(len(scripts), 9)
        for script in scripts:
            self.assertIn(f"runway-orders/{script}", names)
        code, st = self.req("GET", "/api/retail")
        self.assertEqual((code, st["token"], st["stores"]["target"]["orders"]), (200, False, 0))

    def test_store_pages_that_stop_an_import(self):
        _, r = self.req("POST", "/api/retail/token", headers={"X-Runway": "1"})
        ext = {"Authorization": f"Bearer {r['token']}"}
        robot = '<html><title>Robot Check</title><form action="/errors/validateCaptcha"></form></html>'
        code, r = self.req("POST", "/api/ext/amazon/order", {"order_number": ORDER, "html": robot}, ext)
        self.assertEqual((code, r["code"]), (400, "robot"))    # the extension stops, and the order keeps its tries
        code, r = self.req("POST", "/api/ext/amazon/order", {"order_number": ORDER, "html": "<html></html>", "final": False}, ext)
        self.assertEqual((code, r), (200, {"read": False}))
        with db.session() as conn:
            self.assertEqual(conn.execute(select(RetailOrder.attempts)
                                          .where(RetailOrder.id == retail.order_key("amazon", ORDER))).fetchone()[0] or 0, 0)
        # Stopped early: the last import's date stays put. Categorizing carries on after the answer.
        with mock.patch.object(retail, "categorize_and_apply") as later:
            code, r = self.req("POST", "/api/ext/finish", {"retailer": "amazon", "complete": False}, ext)
            self.assertEqual(code, 200)
            for _ in range(50):
                if later.called:
                    break
                threading.Event().wait(0.05)
            self.assertEqual(later.call_args[0][1], "amazon")
        with db.session() as conn:
            self.assertIsNone(db.get_setting(conn, "retail_last_amazon"))
        self.req("POST", "/api/retail/token/remove", headers={"X-Runway": "1"})
