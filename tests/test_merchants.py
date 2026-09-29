"""Merchant logos: noted from Plaid, downloaded from Plaid (or the icon CDN for big names), served by Runway."""
import io
import os
import sys
import tempfile
import unittest
from datetime import date
from unittest import mock
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runway import db, merchants, server  # noqa: E402
from runway import settings_keys as sk  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40


class FakeResponse(io.BytesIO):
    def __init__(self, data, ctype):
        super().__init__(data)
        self.headers = {"Content-Type": ctype}


class MerchantTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "m.db")
        db.init(self.path)
        self.c = db.connect(self.path)
        self.asked = []

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def opener(self, replies):
        def open_(req):
            self.asked.append(req.full_url)
            if req.full_url not in replies:
                raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
            data, ctype = replies[req.full_url]
            return FakeResponse(data, ctype)
        return open_

    def test_note_uses_the_counterparty_too(self):
        mid = merchants.note(self.c, {"merchant_name": None, "counterparties": [
            {"type": "payment_app", "name": "Venmo"},
            {"type": "merchant", "name": "Blue Bottle", "entity_id": "ent-bb", "logo_url": "https://plaid.com/bb.png"}]})
        self.assertEqual(mid, "ent-bb")
        self.assertIsNone(merchants.note(self.c, {"merchant_name": "Nobody"}))   # nothing to show for it
        self.assertEqual(merchants.note(self.c, {"merchant_name": "Corner Shop", "logo_url": "https://plaid.com/c.png"}),
                         "name:corner shop")

    def test_logos_come_only_from_plaid_as_images(self):
        good, other, svg = "https://plaid-merchant-logos.plaid.com/a.png", "https://evil.example.com/b.png", "https://plaid.com/c.svg"
        for i, url in enumerate((good, other, svg, "http://plaid.com/d.png")):
            merchants.note(self.c, {"merchant_name": f"M{i}", "merchant_entity_id": f"e{i}", "logo_url": url})
        got = merchants.fetch_logos(self.c, opener=self.opener({good: (PNG, "image/png"), svg: (b"<svg/>", "image/svg+xml")}))
        self.assertEqual(got, 1)
        self.assertEqual(self.asked, [good, svg])          # never the other host, never plain http
        self.assertEqual(merchants.logo(self.c, "e0"), (PNG, "image/png"))
        self.assertIsNone(merchants.logo(self.c, "e2"))    # SVG refused
        self.assertEqual(merchants.fetch_logos(self.c, opener=self.opener({})), 0)   # the misses wait a month

    def test_transactions_find_their_logo_by_id_or_name(self):
        merchants.note(self.c, {"merchant_name": "Blue Bottle", "merchant_entity_id": "ent-bb", "logo_url": "https://plaid.com/bb.png"})
        merchants.fetch_logos(self.c, opener=self.opener({"https://plaid.com/bb.png": (PNG, "image/png")}))
        txs = [{"id": "a", "merchant_id": "ent-bb", "payee": "BB"},       # from Plaid
               {"id": "b", "merchant_id": None, "payee": "blue  bottle"},  # from SimpleFIN, same name
               {"id": "c", "merchant_id": None, "payee": "Starbucks"},
               {"id": "d", "merchant_id": None, "payee": "Blue Bottle Coffee 12"},   # starts with the name
               {"id": "e", "merchant_id": None, "payee": "Blue Bottles Inc"}]        # not as whole words
        self.assertEqual(merchants.for_transactions(self.c, txs), {"a": "ent-bb", "b": "ent-bb", "d": "ent-bb"})


    def logo_dev(self, site, token="pk_test123456"):
        return f"https://img.logo.dev/{site}?token={token}&size=64&format=png&fallback=404"

    def test_plaid_logo_first_then_logo_dev_by_website(self):
        merchants.note(self.c, {"merchant_name": "Target", "merchant_entity_id": "ent-t", "logo_url": "https://plaid.com/t.png"})
        merchants.note(self.c, {"merchant_name": "Joe's Coffee", "merchant_entity_id": "ent-j", "website": "www.JoesCoffee.com"})
        merchants.fetch_logos(self.c, opener=self.opener({"https://plaid.com/t.png": (PNG, "image/png")}))
        self.c.execute("INSERT INTO accounts(id, name, kind) VALUES ('a', 'Card', 'credit')")
        self.c.executemany("INSERT INTO transactions(id, account_id, posted, amount, description, payee, merchant_id) "
                           "VALUES (?, 'a', '2026-09-20', -5, ?, ?, ?)", [
                               ("t1", "TARGET 0001", "Target", "ent-t"),              # Plaid's logo
                               ("t2", "WAL-MART #12", "Walmart", None),              # a big name: walmart.com
                               ("t3", "SQ *JOES COFFEE", "Joe's Coffee", "ent-j"),   # Plaid's website, no logo
                               ("t4", "CORNER SHOP", "Corner Shop", None)])           # neither: its initial
        self.c.commit()

        def logos():
            return {t["id"]: t["logo"] for t in server.api_transactions(self.c, {}, None)["items"]}
        want = {"t1": "/api/merchants/ent-t/logo", "t2": None, "t3": None, "t4": None}
        self.assertEqual(logos(), want)                                                 # no Logo.dev key
        self.assertEqual(merchants.fetch_logos(self.c, opener=self.opener({})), 0)
        db.set_setting(self.c, sk.LOGODEV_TOKEN, "pk_test123456")
        self.assertEqual(logos(), want)                                                 # noted, not fetched yet
        got = merchants.fetch_logos(self.c, opener=self.opener({self.logo_dev("walmart.com"): (PNG, "image/png"),
                                                                 self.logo_dev("joescoffee.com"): (PNG, "image/png")}))
        self.assertEqual(got, 2)
        self.assertEqual(logos(), {**want, "t2": "/api/merchants/site%3Awalmart.com/logo",
                                   "t3": "/api/merchants/site%3Ajoescoffee.com/logo"})
        self.assertEqual(merchants.logo(self.c, "site:walmart.com"), (PNG, "image/png"))

    def test_a_sync_notes_the_websites_it_has_seen(self):
        self.c.execute("INSERT INTO accounts(id, name, kind) VALUES ('a', 'Card', 'credit')")
        today = date.today().isoformat()
        self.c.executemany("INSERT INTO transactions(id, account_id, posted, amount, description, payee) VALUES (?, 'a', ?, -5, ?, ?)",
                           [("t1", today, "STARBUCKS 123", "Starbucks"), ("t2", today, "AMZN MKTP", "Amazon"),
                            ("t3", "2020-01-01", "TARGET", "Target"), ("t4", today, "CORNER SHOP", "Corner Shop")])
        merchants.note_sites(self.c)
        self.assertIsNone(self.c.execute("SELECT 1 FROM merchants").fetchone())        # not without a key
        db.set_setting(self.c, sk.LOGODEV_TOKEN, "pk_test123456")
        merchants.note_sites(self.c)
        self.assertEqual(sorted(r[0] for r in self.c.execute("SELECT id FROM merchants")),
                         ["brand:corner shop", "site:amazon.com", "site:starbucks.com"])   # no website known: by name

    def by_name(self, name, token="pk_test123456"):
        return f"https://img.logo.dev/name/{urllib.parse.quote(name, safe='')}?token={token}&size=64&format=png&fallback=404"

    def test_logo_dev_by_name_when_no_website_is_known(self):
        self.c.execute("INSERT INTO accounts(id, name, kind) VALUES ('a', 'Card', 'credit')")
        self.c.executemany("INSERT INTO transactions(id, account_id, posted, amount, description, payee, category) "
                           "VALUES (?, 'a', '2026-09-20', ?, ?, ?, ?)", [
                               ("t1", -8, "BLUE BOTTLE #4", "Blue Bottle Coffee", "Coffee"),   # by name
                               ("t2", -5, "CORNER SHOP", "Corner Shop", None),                  # asked, Logo.dev knows none
                               ("t3", -500, "TO SAVINGS", "Ally Bank", "Transfer"),             # a transfer: never asked
                               ("t4", 2.1, "INTEREST", "Interest Paid", None),                  # money in: never asked
                               ("t5", -3, "MONTHLY FEE", "Monthly Service Fee", "Fees")])       # not a merchant
        db.set_setting(self.c, sk.LOGODEV_TOKEN, "pk_test123456")

        def logos():
            return {t["id"]: t["logo"] for t in server.api_transactions(self.c, {}, None)["items"]}
        self.assertEqual(set(logos().values()), {None})                                        # noted, not fetched yet
        self.assertEqual(sorted(r[0] for r in self.c.execute("SELECT id FROM merchants")), ["brand:blue bottle coffee", "brand:corner shop"])
        got = merchants.fetch_logos(self.c, opener=self.opener({self.by_name("Blue Bottle Coffee"): (PNG, "image/png")}))
        self.assertEqual(got, 1)
        self.assertIn(self.by_name("Corner Shop"), self.asked)                                  # asked, 404: no logo
        self.assertEqual(logos(), {"t1": "/api/merchants/brand%3Ablue%20bottle%20coffee/logo", "t2": None, "t3": None,
                                   "t4": None, "t5": None})
        self.assertEqual(merchants.fetch_logos(self.c, opener=self.opener({})), 0)            # a miss isn't asked again soon
        self.assertEqual(len([u for u in self.asked if "Corner" in u]), 1)

    def test_adding_a_key_fetches_the_past_year_at_once(self):
        self.c.execute("INSERT INTO accounts(id, name, kind) VALUES ('a', 'Card', 'credit')")
        today = date.today().isoformat()
        self.c.executemany("INSERT INTO transactions(id, account_id, posted, amount, description, payee) VALUES (?, 'a', ?, -5, ?, ?)",
                           [(f"t{i}", today, f"SHOP {i}", f"Shop Number {chr(65 + i)}") for i in range(7)])
        db.set_setting(self.c, sk.LOGODEV_TOKEN, "pk_test123456")
        with mock.patch.object(merchants, "PER_SYNC", 3):                                     # more than one round's worth
            got = merchants.backfill(self.c, opener=self.opener({self.by_name(f"Shop Number {chr(65 + i)}"): (PNG, "image/png")
                                                                 for i in range(7)}))
        self.assertEqual(got, 7)
        self.assertFalse(merchants._todo(self.c, 1))

    def test_logo_dev_logos_are_checked_again_monthly(self):
        db.set_setting(self.c, sk.LOGODEV_TOKEN, "pk_test123456")
        url = self.logo_dev("target.com")
        merchants.site_logos(self.c, ["target.com"])
        self.assertEqual(merchants.fetch_logos(self.c, opener=self.opener({url: (PNG, "image/png")})), 1)
        self.assertEqual(merchants.fetch_logos(self.c, opener=self.opener({url: (PNG + b"new", "image/png")})), 0)
        self.c.execute("UPDATE merchants SET logo_checked='2026-01-01T00:00:00'")
        self.assertEqual(merchants.fetch_logos(self.c, opener=self.opener({})), 0)          # a miss keeps the old one
        self.assertEqual(merchants.logo(self.c, "site:target.com"), (PNG, "image/png"))
        self.c.execute("UPDATE merchants SET logo_checked='2026-01-01T00:00:00'")
        self.assertEqual(merchants.fetch_logos(self.c, opener=self.opener({url: (PNG + b"new", "image/png")})), 1)
        self.assertEqual(merchants.logo(self.c, "site:target.com"), (PNG + b"new", "image/png"))

    def test_the_key_setting(self):
        started = mock.patch.object(server, "start_logo_backfill").start()
        self.addCleanup(mock.patch.stopall)
        with self.assertRaises(server.ApiError):
            server.api_logodev_settings(self.c, {}, {"token": "sk_secret_abcdefgh"})
        self.assertFalse(merchants.configured(self.c))
        merchants.site_logos(self.c, ["target.com"])
        self.c.execute("UPDATE merchants SET logo_checked='2099-01-01T00:00:00'")
        self.assertEqual(server.api_logodev_settings(self.c, {}, {"token": " pk_abcdefgh123 "})["configured"], True)
        self.assertEqual(db.get_setting(self.c, sk.LOGODEV_TOKEN), "pk_abcdefgh123")
        self.assertIsNone(self.c.execute("SELECT logo_checked FROM merchants").fetchone()[0])   # tried again
        started.assert_called_once()                                                            # ...straight away
        self.assertEqual(server.api_logodev_settings(self.c, {}, {"clear": True})["configured"], False)

    def test_websites(self):
        cases = {"https://www.Target.com/stores": "target.com", "joescoffee.com": "joescoffee.com", "fi.google.com": "fi.google.com",
                 "localhost": None, "http://127.0.0.1/x": "127.0.0.1", "": None, None: None, "not a site": None,
                 "evil.com?x=1": "evil.com", "a.com/../..": "a.com"}
        for web, want in cases.items():
            self.assertEqual(merchants.site(web), want, web)

    def test_only_images_from_logo_dev(self):
        db.set_setting(self.c, sk.LOGODEV_TOKEN, "pk_test123456")
        self.c.execute("INSERT INTO merchants(id, logo_url) VALUES ('site:bad site', 'https://img.logo.dev/bad site')")
        merchants.site_logos(self.c, ["svg.com"])
        got = merchants.fetch_logos(self.c, opener=self.opener({self.logo_dev("svg.com"): (b"<svg/>", "image/svg+xml")}))
        self.assertEqual(got, 0)
        self.assertEqual(self.asked, [self.logo_dev("svg.com")])        # never a website that isn't one

    def test_status_says_why_logo_dev_failed(self):
        db.set_setting(self.c, sk.LOGODEV_TOKEN, "pk_test123456")
        merchants.site_logos(self.c, ["target.com", "walmart.com"])
        def refused(req):
            self.asked.append(req.full_url)
            raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)
        merchants.fetch_logos(self.c, opener=refused)
        st = merchants.status(self.c)
        self.assertIn("401", st["last_error"])
        self.assertEqual(len(self.asked), 1)
        # refused isn't "doesn't know": both are still waiting, and only one was asked before the round stopped
        self.assertEqual((st["logodev"], st["unknown"], st["waiting"]), (0, 0, 2))
        self.c.execute("UPDATE merchants SET logo_checked='2020-01-01' WHERE id='site:walmart.com'")
        merchants.retry_unknown(self.c)   # Fetch them now: looked up again
        self.assertEqual(merchants.status(self.c)["waiting"], 2)
        merchants.fetch_logos(self.c, opener=self.opener({self.logo_dev("target.com"): (b"png", "image/png")}))
        st = merchants.status(self.c)   # a 404 is "no such brand", not an error; a success clears the last one
        self.assertEqual((st["logodev"], st["unknown"]), (1, 1))
        self.assertIsNone(st["last_error"])

    def test_best_match_is_clearly_the_merchant_or_nothing(self):
        m = merchants.best_match
        c = lambda *pairs: [{"name": n, "domain": d} for n, d in pairs]
        self.assertEqual(m("Mackenthun's Fine Fo", c(("Fine Foods Co", "finefoods.com"), ("Mackenthun's Fine Foods", "mackenthuns.com")))["domain"], "mackenthuns.com")
        self.assertEqual(m("Kwik Trip 1173", c(("Kwik Trip", "kwiktrip.com")))["domain"], "kwiktrip.com")
        self.assertEqual(m("Target", c(("Target Corporation", "target.com")))["domain"], "target.com")
        self.assertIsNone(m("Corner Coffee", c(("Corner Bakery Cafe", "cornerbakerycafe.com"))))
        self.assertIsNone(m("Joe's Diner", c(("Joe & The Juice", "joejuice.com"))))
        self.assertIsNone(m("AB", c(("AB InBev", "ab-inbev.com"))))

    def test_brand_search_picks_a_clear_match(self):
        db.set_setting(self.c, sk.LOGODEV_TOKEN, "pk_test123456")
        db.set_setting(self.c, sk.LOGODEV_SECRET, "sk_test123456")
        merchants.brand_logos(self.c, [("kwik trip 1173", "Kwik Trip 1173"), ("corner coffee", "Corner Coffee")])
        searched = []
        def open_(req):
            url = req.full_url
            if url.startswith(merchants.SEARCH):
                searched.append((url, req.get_header("Authorization")))
                q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["q"][0]
                body = b'[{"name": "Kwik Trip", "domain": "kwiktrip.com"}]' if "Kwik" in q else b'[{"name": "Corner Bakery Cafe", "domain": "cornerbakerycafe.com"}]'
                return FakeResponse(body, "application/json")
            if url.startswith(self.logo_dev("kwiktrip.com")):
                return FakeResponse(PNG, "image/png")
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        self.c.execute("UPDATE merchants SET logo='old', logo_type='image/png' WHERE id='brand:corner coffee'")   # a wrong one from before
        self.assertEqual(merchants.fetch_logos(self.c, opener=open_), 1)
        self.assertEqual({a for _, a in searched}, {"Bearer sk_test123456"})
        rows = {r["id"]: r for r in self.c.execute("SELECT id, website, logo FROM merchants")}
        self.assertEqual(rows["brand:kwik trip 1173"]["website"], "kwiktrip.com")
        self.assertIsNone(rows["brand:corner coffee"]["logo"])   # no clear match: better no logo than someone else's

    def test_you_choose_a_merchants_logo(self):
        db.set_setting(self.c, sk.LOGODEV_TOKEN, "pk_test123456")
        txs = [{"id": "t1", "payee": "Mackenthun's Fine Fo"}, {"id": "t2", "payee": "mackenthun's  fine fo"}, {"id": "t3", "payee": "Other"}]
        merchants.choose(self.c, "Mackenthun's Fine Fo", "https://www.Mackenthuns.com/", opener=self.opener({self.logo_dev("mackenthuns.com"): (PNG, "image/png")}))
        self.assertEqual(merchants.chosen_for(self.c, txs), {"t1": "site:mackenthuns.com", "t2": "site:mackenthuns.com"})
        self.assertIsNotNone(merchants.logo(self.c, "site:mackenthuns.com"))
        merchants.choose(self.c, "Mackenthun's Fine Fo", hidden=True)
        self.assertEqual(merchants.chosen_for(self.c, txs), {"t1": None, "t2": None})
        merchants.choose(self.c, "Mackenthun's Fine Fo")   # back to Runway's pick
        self.assertEqual(merchants.chosen_for(self.c, txs), {})
        with self.assertRaises(ValueError):
            merchants.choose(self.c, "Other", "nowhere-logo.com", opener=self.opener({}))   # Logo.dev has none

    def test_redirects_only_to_the_same_sources(self):
        rules = merchants._SameRules()
        req = urllib.request.Request(self.logo_dev("target.com"))
        self.assertIsNone(rules.redirect_request(req, None, 302, "Found", {}, "https://evil.example.com/x.png"))
        self.assertIsNotNone(rules.redirect_request(req, None, 302, "Found", {}, "https://img.logo.dev/walmart.com?token=x"))

if __name__ == "__main__":
    unittest.main()
