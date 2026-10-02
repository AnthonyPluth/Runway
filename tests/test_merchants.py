"""Merchant logos: noted from Plaid, downloaded from Plaid (or the icon CDN for big names), served by Runway."""
import io
import unittest
from datetime import date
from unittest import mock
import urllib.error
import urllib.parse
import urllib.request

from sqlalchemy import func, insert, select, update

from runway import db, merchants, server
from runway import settings_keys as sk
from runway.models import Account, Holding, InvAccount, Merchant, MerchantLogo, Security, Transaction
from tests.shared import DbCase

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40


class FakeResponse(io.BytesIO):
    def __init__(self, data, ctype):
        super().__init__(data)
        self.headers = {"Content-Type": ctype}


class MerchantTests(DbCase):
    def setUp(self):
        super().setUp()
        self.asked = []

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
        return f"https://img.logo.dev/{site}?token={token}&size=64&format=png&theme=dark&fallback=404"

    def test_logo_dev_first_then_plaid(self):
        merchants.note(self.c, {"merchant_name": "Target", "merchant_entity_id": "ent-t", "logo_url": "https://plaid.com/t.png"})
        merchants.note(self.c, {"merchant_name": "Joe's Coffee", "merchant_entity_id": "ent-j", "website": "www.JoesCoffee.com"})
        merchants.fetch_logos(self.c, opener=self.opener({"https://plaid.com/t.png": (PNG, "image/png")}))
        self.c.execute(insert(Account).values(id="a", name="Card", kind="credit"))
        self.c.execute(insert(Transaction), [
            {"id": i, "account_id": "a", "posted": "2026-09-20", "amount": -5, "description": d, "payee": p, "merchant_id": m}
            for i, d, p, m in [
                ("t1", "TARGET 0001", "Target", "ent-t"),              # Plaid's logo
                ("t2", "WAL-MART #12", "Walmart", None),              # a big name: walmart.com
                ("t3", "SQ *JOES COFFEE", "Joe's Coffee", "ent-j"),   # Plaid's website, no logo
                ("t4", "CORNER SHOP", "Corner Shop", None)]])          # neither: its initial
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
        # Once Logo.dev has Target too, its dark-background logo takes the place of Plaid's (opaque, dark on white).
        self.c.execute(update(Merchant).where(Merchant.id == "site:target.com").values(logo_checked=None))
        self.assertEqual(merchants.fetch_logos(self.c, opener=self.opener({self.logo_dev("target.com"): (PNG, "image/png")})), 1)
        self.assertEqual(logos()["t1"], "/api/merchants/site%3Atarget.com/logo")

    def test_a_sync_notes_the_websites_it_has_seen(self):
        self.c.execute(insert(Account).values(id="a", name="Card", kind="credit"))
        today = date.today().isoformat()
        self.c.execute(insert(Transaction), [{"id": "t1", "account_id": "a", "posted": today, "amount": -5,
                                              "description": "STARBUCKS 123", "payee": "Starbucks"},
                                             {"id": "t2", "account_id": "a", "posted": today, "amount": -5,
                                              "description": "AMZN MKTP", "payee": "Amazon"},
                                             {"id": "t3", "account_id": "a", "posted": "2020-01-01", "amount": -5,
                                              "description": "TARGET", "payee": "Target"},
                                             {"id": "t4", "account_id": "a", "posted": today, "amount": -5,
                                              "description": "CORNER SHOP", "payee": "Corner Shop"}])
        merchants.note_sites(self.c)
        self.assertIsNone(self.c.execute(select(Merchant.id)).fetchone())        # not without a key
        db.set_setting(self.c, sk.LOGODEV_TOKEN, "pk_test123456")
        merchants.note_sites(self.c)
        self.assertEqual(sorted(r[0] for r in self.c.execute(select(Merchant.id))),
                         ["brand:corner shop", "site:amazon.com", "site:starbucks.com"])   # no website known: by name

    def test_held_tickers_get_a_logo_by_ticker(self):
        self.c.execute(insert(InvAccount).values(id="ia", item_id="item", name="Brokerage"))
        self.c.execute(insert(Security), [{"id": "s1", "ticker": "vti", "name": "Vanguard Total Market", "is_cash": 0},
                                          {"id": "s2", "ticker": "CUR:USD", "name": "Cash", "is_cash": 1},
                                          {"id": "s3", "ticker": "BRK.B", "name": "Berkshire", "is_cash": None},
                                          {"id": "s4", "ticker": "NOTHELD", "name": "Not held", "is_cash": 0}])
        self.c.execute(insert(Holding), [{"account_id": "ia", "security_id": "s1", "quantity": 1},
                                         {"account_id": "ia", "security_id": "s2", "quantity": 1},
                                         {"account_id": "ia", "security_id": "s3", "quantity": 1}])
        merchants.note_tickers(self.c)
        merchants.note_sites(self.c)
        self.assertEqual(sorted(r[0] for r in self.c.execute(select(Merchant.id))), ["site:vanguard.com", "ticker:BRK.B", "ticker:VTI"])   # no cash, nothing unheld; Vanguard for the fund
        url = lambda t: f"https://img.logo.dev/ticker/{t}?token=pk_test123456&size=64&format=png&theme=dark&fallback=404"
        db.set_setting(self.c, sk.LOGODEV_TOKEN, "pk_test123456")
        self.assertEqual(merchants.fetch_logos(self.c, opener=self.opener({url("VTI"): (PNG, "image/png")})), 1)
        self.assertEqual(merchants.logo(self.c, "ticker:VTI"), (PNG, "image/png"))
        self.assertIsNone(merchants.logo(self.c, "ticker:BRK.B"))

    def test_holdings_get_their_logo_by_ticker_else_by_fund_family(self):
        from runway import portfolio
        db.set_setting(self.c, sk.LOGODEV_TOKEN, "pk_test123456")
        self.c.execute(insert(InvAccount).values(id="ia", item_id="item", name="Brokerage"))
        self.c.execute(insert(Security), [
            {"id": "s1", "ticker": "AAPL", "name": "Apple Inc", "is_cash": 0},
            {"id": "s2", "ticker": "VTSAX", "name": "Vanguard Total Stock Market Index Admiral", "is_cash": 0},   # a fund: no logo by ticker
            {"id": "s3", "ticker": "XYZ", "name": "XYZ Corp", "is_cash": 0},                                      # nothing known
            {"id": "s4", "ticker": "CUR:USD", "name": "Vanguard Cash", "is_cash": 1}])
        self.c.execute(insert(Holding), [{"account_id": "ia", "security_id": s, "quantity": 1, "value": 100} for s in ("s1", "s2", "s3", "s4")])
        self.c.execute(insert(Merchant).values(id="site:target.com", logo_url="https://img.logo.dev/target.com"))   # a merchant: not fetched here
        params = "token=pk_test123456&size=64&format=png&theme=dark&fallback=404"
        got = merchants.refresh_holding_logos(self.c, opener=self.opener({
            f"https://img.logo.dev/ticker/AAPL?{params}": (PNG, "image/png"), f"https://img.logo.dev/vanguard.com?{params}": (PNG, "image/png")}))
        self.assertEqual(got, 2)
        # only the held securities' logos were looked up (each ticker, and the fund family for a fund): not the
        # merchant's, nor one for cash
        self.assertEqual(sorted(self.asked), sorted(f"https://img.logo.dev/{k}?{params}"
                                                    for k in ("ticker/AAPL", "ticker/VTSAX", "ticker/XYZ", "vanguard.com")))
        by_ticker = {h["ticker"]: h["logo"] for h in portfolio.holdings(self.c)}
        self.assertEqual(by_ticker, {"AAPL": "/api/merchants/ticker%3AAAPL/logo", "VTSAX": "/api/merchants/site%3Avanguard.com/logo",
                                     "XYZ": None, "CUR:USD": None})
        self.assertEqual(merchants.logo(self.c, "ticker:AAPL"), (PNG, "image/png"))   # and the URL it gives serves the image

    def test_no_logos_are_fetched_for_holdings_without_a_key(self):
        self.c.execute(insert(InvAccount).values(id="ia", item_id="item", name="Brokerage"))
        self.c.execute(insert(Security).values(id="s1", ticker="AAPL", name="Apple Inc", is_cash=0))
        self.c.execute(insert(Holding).values(account_id="ia", security_id="s1", quantity=1, value=100))
        self.assertEqual(merchants.refresh_holding_logos(self.c, opener=self.opener({})), 0)
        self.assertEqual(self.asked, [])

    def by_name(self, name, token="pk_test123456"):
        return f"https://img.logo.dev/name/{urllib.parse.quote(name, safe='')}?token={token}&size=64&format=png&theme=dark&fallback=404"

    def test_logo_dev_by_name_when_no_website_is_known(self):
        self.c.execute(insert(Account).values(id="a", name="Card", kind="credit"))
        self.c.execute(insert(Transaction), [
            {"id": i, "account_id": "a", "posted": "2026-09-20", "amount": amount, "description": d, "payee": p, "category": cat}
            for i, amount, d, p, cat in [
                ("t1", -8, "BLUE BOTTLE #4", "Blue Bottle Coffee", "Coffee"),   # by name
                ("t2", -5, "CORNER SHOP", "Corner Shop", None),                  # asked, Logo.dev knows none
                ("t3", -500, "TO SAVINGS", "Ally Bank", "Transfer"),             # a transfer: never asked
                ("t4", 2.1, "INTEREST", "Interest Paid", None),                  # money in: never asked
                ("t5", -3, "MONTHLY FEE", "Monthly Service Fee", "Fees")]])      # not a merchant
        db.set_setting(self.c, sk.LOGODEV_TOKEN, "pk_test123456")

        def logos():
            return {t["id"]: t["logo"] for t in server.api_transactions(self.c, {}, None)["items"]}
        self.assertEqual(set(logos().values()), {None})                                        # noted, not fetched yet
        self.assertEqual(sorted(r[0] for r in self.c.execute(select(Merchant.id))), ["brand:blue bottle coffee", "brand:corner shop"])
        got = merchants.fetch_logos(self.c, opener=self.opener({self.by_name("Blue Bottle Coffee"): (PNG, "image/png")}))
        self.assertEqual(got, 1)
        self.assertIn(self.by_name("Corner Shop"), self.asked)                                  # asked, 404: no logo
        self.assertEqual(logos(), {"t1": "/api/merchants/brand%3Ablue%20bottle%20coffee/logo", "t2": None, "t3": None,
                                   "t4": None, "t5": None})
        self.assertEqual(merchants.fetch_logos(self.c, opener=self.opener({})), 0)            # a miss isn't asked again soon
        self.assertEqual(len([u for u in self.asked if "Corner" in u]), 1)

    def test_adding_a_key_fetches_the_past_year_at_once(self):
        self.c.execute(insert(Account).values(id="a", name="Card", kind="credit"))
        today = date.today().isoformat()
        self.c.execute(insert(Transaction), [{"id": f"t{i}", "account_id": "a", "posted": today, "amount": -5,
                                              "description": f"SHOP {i}", "payee": f"Shop Number {chr(65 + i)}"}
                                             for i in range(7)])
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
        self.c.execute(update(Merchant).values(logo_checked="2026-01-01T00:00:00"))
        self.assertEqual(merchants.fetch_logos(self.c, opener=self.opener({})), 0)          # a miss keeps the old one
        self.assertEqual(merchants.logo(self.c, "site:target.com"), (PNG, "image/png"))
        self.c.execute(update(Merchant).values(logo_checked="2026-01-01T00:00:00"))
        self.assertEqual(merchants.fetch_logos(self.c, opener=self.opener({url: (PNG + b"new", "image/png")})), 1)
        self.assertEqual(merchants.logo(self.c, "site:target.com"), (PNG + b"new", "image/png"))

    def test_logos_fetched_for_another_theme_are_fetched_again(self):
        db.set_setting(self.c, sk.LOGODEV_TOKEN, "pk_test123456")
        url = self.logo_dev("target.com")
        merchants.site_logos(self.c, ["target.com"])
        self.assertEqual(merchants.fetch_logos(self.c, opener=self.opener({url: (PNG, "image/png")})), 1)
        db.set_setting(self.c, sk.LOGODEV_THEME, None)   # as stored before Runway asked for dark-background logos
        self.assertEqual(merchants.fetch_logos(self.c, opener=self.opener({url: (PNG + b"dark", "image/png")})), 1)
        self.assertEqual(merchants.logo(self.c, "site:target.com"), (PNG + b"dark", "image/png"))
        self.assertEqual(merchants.fetch_logos(self.c, opener=self.opener({url: (PNG, "image/png")})), 0)   # once only

    def test_the_key_setting(self):
        started = mock.patch.object(server.api.merchants, "start_logo_backfill").start()
        self.addCleanup(mock.patch.stopall)
        with self.assertRaises(server.ApiError):
            server.api_logodev_settings(self.c, {}, {"token": "sk_secret_abcdefgh"})
        self.assertFalse(merchants.configured(self.c))
        merchants.site_logos(self.c, ["target.com"])
        self.c.execute(update(Merchant).values(logo_checked="2099-01-01T00:00:00"))
        self.assertEqual(server.api_logodev_settings(self.c, {}, {"token": " pk_abcdefgh123 "})["configured"], True)
        self.assertEqual(db.get_setting(self.c, sk.LOGODEV_TOKEN), "pk_abcdefgh123")
        self.assertIsNone(self.c.execute(select(Merchant.logo_checked)).fetchone()[0])   # tried again
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
        self.c.execute(insert(Merchant).values(id="site:bad site", logo_url="https://img.logo.dev/bad site"))
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
        self.c.execute(update(Merchant).where(Merchant.id == "site:walmart.com").values(logo_checked="2020-01-01"))
        merchants.retry_unknown(self.c)   # Fetch them now: looked up again
        self.assertEqual(merchants.status(self.c)["waiting"], 2)
        merchants.fetch_logos(self.c, opener=self.opener({self.logo_dev("target.com"): (b"png", "image/png")}))
        st = merchants.status(self.c)   # a 404 is "no such brand", not an error; a success clears the last one
        self.assertEqual((st["logodev"], st["unknown"]), (1, 1))
        self.assertIsNone(st["last_error"])

    def test_best_match_is_clearly_the_merchant_or_nothing(self):
        m = merchants.best_match
        c = lambda *pairs: [{"name": n, "domain": d} for n, d in pairs]
        self.assertEqual(m("Hillside's Fine Fo", c(("Fine Foods Co", "finefoods.com"), ("Hillside's Fine Foods", "hillsidefoods.com")))["domain"], "hillsidefoods.com")
        self.assertEqual(m("Kwik Trip 2050", c(("Kwik Trip", "kwiktrip.com")))["domain"], "kwiktrip.com")
        self.assertEqual(m("Target", c(("Target Corporation", "target.com")))["domain"], "target.com")
        self.assertIsNone(m("Corner Coffee", c(("Corner Bakery Cafe", "cornerbakerycafe.com"))))
        self.assertIsNone(m("Joe's Diner", c(("Joe & The Juice", "joejuice.com"))))
        self.assertIsNone(m("AB", c(("AB InBev", "ab-inbev.com"))))

    def test_brand_search_picks_a_clear_match(self):
        db.set_setting(self.c, sk.LOGODEV_TOKEN, "pk_test123456")
        db.set_setting(self.c, sk.LOGODEV_SECRET, "sk_test123456")
        merchants.brand_logos(self.c, [("kwik trip 2050", "Kwik Trip 2050"), ("corner coffee", "Corner Coffee")])
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
        self.c.execute(update(Merchant).where(Merchant.id == "brand:corner coffee")
                       .values(logo="old", logo_type="image/png"))   # a wrong one from before
        self.assertEqual(merchants.fetch_logos(self.c, opener=open_), 1)
        self.assertEqual({a for _, a in searched}, {"Bearer sk_test123456"})
        rows = {r["id"]: r for r in self.c.execute(select(Merchant.id, Merchant.website, Merchant.logo))}
        self.assertEqual(rows["brand:kwik trip 2050"]["website"], "kwiktrip.com")
        self.assertIsNone(rows["brand:corner coffee"]["logo"])   # no clear match: better no logo than someone else's

    def test_you_choose_a_merchants_logo(self):
        db.set_setting(self.c, sk.LOGODEV_TOKEN, "pk_test123456")
        txs = [{"id": "t1", "payee": "Hillside's Fine Fo"}, {"id": "t2", "payee": "hillside's  fine fo"}, {"id": "t3", "payee": "Other"}]
        merchants.choose(self.c, "Hillside's Fine Fo", "https://www.HillsideFoods.com/", opener=self.opener({self.logo_dev("hillsidefoods.com"): (PNG, "image/png")}))
        self.assertEqual(merchants.chosen_for(self.c, txs), {"t1": "site:hillsidefoods.com", "t2": "site:hillsidefoods.com"})
        self.assertIsNotNone(merchants.logo(self.c, "site:hillsidefoods.com"))
        merchants.choose(self.c, "Hillside's Fine Fo", hidden=True)
        self.assertEqual(merchants.chosen_for(self.c, txs), {"t1": None, "t2": None})
        merchants.choose(self.c, "Hillside's Fine Fo")   # back to Runway's pick
        self.assertEqual(merchants.chosen_for(self.c, txs), {})
        with self.assertRaises(ValueError):
            merchants.choose(self.c, "Other", "nowhere-logo.com", opener=self.opener({}))   # Logo.dev has none

    def test_redirects_only_to_the_same_sources(self):
        rules = merchants._SameRules()
        req = urllib.request.Request(self.logo_dev("target.com"))
        self.assertIsNone(rules.redirect_request(req, None, 302, "Found", {}, "https://evil.example.com/x.png"))
        self.assertIsNotNone(rules.redirect_request(req, None, 302, "Found", {}, "https://img.logo.dev/walmart.com?token=x"))

    def test_note_again_keeps_what_plaid_left_out(self):
        merchants.note(self.c, {"merchant_name": "Blue Bottle", "merchant_entity_id": "e", "website": "bb.com",
                                "logo_url": "https://plaid.com/a.png"})
        self.c.execute(update(Merchant).where(Merchant.id == "e")
                       .values(logo="x", logo_type="image/png", logo_checked="2026-01-01"))
        merchants.note(self.c, {"merchant_name": "Blue Bottle Coffee", "merchant_entity_id": "e"})   # no website or logo
        row = dict(self.c.execute(select(Merchant).where(Merchant.id == "e")).fetchone())
        self.assertEqual(row, {"id": "e", "name": "Blue Bottle Coffee", "website": "bb.com", "logo_url": "https://plaid.com/a.png",
                               "logo": "x", "logo_type": "image/png", "logo_checked": "2026-01-01"})
        merchants.note(self.c, {"merchant_name": "BB", "merchant_entity_id": "e", "logo_url": "https://plaid.com/b.png"})
        row = dict(self.c.execute(select(Merchant).where(Merchant.id == "e")).fetchone())
        self.assertEqual(row, {"id": "e", "name": "BB", "website": "bb.com", "logo_url": "https://plaid.com/b.png",
                               "logo": None, "logo_type": None, "logo_checked": None})   # a new logo: fetched again

    def test_todo_order_and_the_key(self):
        self.c.execute(insert(Merchant), [{"id": "p-old", "name": "P", "logo_url": "https://plaid.com/1.png",
                                           "logo": None, "logo_checked": "2020-01-01T00:00:00"},
                                          {"id": "p-new", "name": "P", "logo_url": "https://plaid.com/2.png",
                                           "logo": None, "logo_checked": None},
                                          {"id": "p-recent", "name": "P", "logo_url": "https://plaid.com/3.png",
                                           "logo": None, "logo_checked": "2099-01-01T00:00:00"},
                                          {"id": "p-have", "name": "P", "logo_url": "https://plaid.com/4.png",
                                           "logo": "x", "logo_checked": None},
                                          {"id": "p-nourl", "name": "P", "logo_url": None, "logo": None,
                                           "logo_checked": None},
                                          {"id": "site:a.com", "name": None, "logo_url": "https://img.logo.dev/a.com",
                                           "logo": "x", "logo_checked": "2020-01-01T00:00:00"},
                                          {"id": "site:b.com", "name": None, "logo_url": "https://img.logo.dev/b.com",
                                           "logo": None, "logo_checked": None},
                                          {"id": "brand:c", "name": "C", "logo_url": "https://img.logo.dev/name/C",
                                           "logo": None, "logo_checked": "2099-01-01T00:00:00"}])
        self.assertEqual([dict(r) for r in merchants._todo(self.c, 10)],
                         [{"id": "p-new", "name": "P", "logo_url": "https://plaid.com/2.png"},
                          {"id": "p-old", "name": "P", "logo_url": "https://plaid.com/1.png"}])
        db.set_setting(self.c, sk.LOGODEV_TOKEN, "pk_test123456")
        self.assertEqual([r["id"] for r in merchants._todo(self.c, 10)], ["p-new", "site:b.com", "p-old", "site:a.com"])
        self.assertEqual([r["id"] for r in merchants._todo(self.c, 2)], ["p-new", "site:b.com"])
        self.assertEqual(merchants.status(self.c), {"plaid": 1, "logodev": 1, "unknown": 1, "waiting": 4, "last_error": None,
                                                    "last_error_name": None, "searchable": False})

    def test_your_choice(self):
        db.set_setting(self.c, sk.LOGODEV_TOKEN, "pk_test123456")
        self.assertIsNone(merchants.choice(self.c, "Target"))
        merchants.site_logos(self.c, ["target.com"])   # noted already, no logo yet
        merchants.choose(self.c, " TARGET ", "target.com", opener=self.opener({self.logo_dev("target.com"): (PNG, "image/png")}))
        self.assertEqual(merchants.choice(self.c, "target"), {"website": "target.com", "hidden": False})
        row = self.c.execute(select(Merchant.logo_url, Merchant.logo_type, Merchant.logo_checked.is_not(None))
                             .where(Merchant.id == "site:target.com")).fetchone()
        self.assertEqual(tuple(row), ("https://img.logo.dev/target.com", "image/png", 1))
        merchants.choose(self.c, "Target", hidden=True)
        self.assertEqual(merchants.choice(self.c, "Target"), {"website": None, "hidden": True})
        self.assertEqual(self.c.execute(select(func.count()).select_from(MerchantLogo)).fetchone()[0], 1)
        with self.assertRaises(ValueError):
            merchants.choose(self.c, "  ", "target.com")

    def holdings_for_logos(self):
        self.c.execute(insert(InvAccount).values(id="ia", item_id="item", name="Brokerage"))
        self.c.execute(insert(Security), [
            {"id": "s1", "ticker": "AAPL", "name": "Apple Inc", "is_cash": 0},
            {"id": "s2", "ticker": None, "name": "Vanguard Made-Up Fund", "is_cash": 0},
            {"id": "s3", "ticker": "CUR:USD", "name": "Cash", "is_cash": 1}])
        self.c.execute(insert(Holding), [{"account_id": "ia", "security_id": s, "quantity": 1, "value": 100} for s in ("s1", "s2", "s3")])
        self.c.execute(insert(Merchant), [{"id": "ticker:AAPL", "logo_url": "u", "logo": "x"}, {"id": "site:vanguard.com", "logo_url": "u", "logo": "x"}])

    def test_you_choose_a_holdings_logo(self):
        from runway import portfolio
        db.set_setting(self.c, sk.LOGODEV_TOKEN, "pk_test123456")
        self.holdings_for_logos()
        logos = lambda: {h["name"]: h["logo"] for h in portfolio.holdings(self.c)}
        auto = {"Apple Inc": "/api/merchants/ticker%3AAAPL/logo", "Vanguard Made-Up Fund": "/api/merchants/site%3Avanguard.com/logo", "Cash": None}
        self.assertEqual(logos(), auto)
        self.assertIsNone(merchants.holding_choice(self.c, "t:AAPL"))
        merchants.choose_holding(self.c, "t:AAPL", "https://www.Apple-Example.com/",
                                 opener=self.opener({self.logo_dev("apple-example.com"): (PNG, "image/png")}))
        self.assertEqual(merchants.holding_choice(self.c, "t:AAPL"), {"website": "apple-example.com", "hidden": False})
        self.assertEqual(logos(), {**auto, "Apple Inc": "/api/merchants/site%3Aapple-example.com/logo"})
        self.assertEqual(merchants.logo(self.c, "site:apple-example.com"), (PNG, "image/png"))
        merchants.choose_holding(self.c, "t:AAPL", hidden=True)
        self.assertEqual(logos(), {**auto, "Apple Inc": None})
        # a fund without a ticker is chosen by its security id, and leaves the other holdings alone
        merchants.choose_holding(self.c, "s2", hidden=True)
        self.assertEqual(logos(), {**auto, "Apple Inc": None, "Vanguard Made-Up Fund": None})
        merchants.choose_holding(self.c, "t:AAPL")
        merchants.choose_holding(self.c, "s2")                      # back to Runway's own picks
        self.assertEqual(logos(), auto)
        self.assertEqual(self.c.execute(select(func.count()).select_from(MerchantLogo)).fetchone()[0], 0)

    def test_a_holdings_choice_never_meets_a_merchants(self):
        merchants.choose(self.c, "t:AAPL", hidden=True)               # a merchant that happens to be named like a group
        self.assertIsNone(merchants.holding_choice(self.c, "t:AAPL"))
        merchants.choose_holding(self.c, "t:AAPL", hidden=True)
        merchants.choose_holding(self.c, "t:AAPL")
        self.assertEqual(merchants.choice(self.c, "t:AAPL"), {"website": None, "hidden": True})   # still there
        self.assertEqual(merchants.holding_key("t:AAPL")[:7], "holding")
        self.assertEqual(merchants.key(merchants.holding_key("t:AAPL")), "holding t:aapl")   # a merchant's key can't carry the prefix

    def test_a_missing_website_logo_falls_back_to_runways_pick(self):
        from runway import portfolio
        self.holdings_for_logos()
        self.c.execute(insert(MerchantLogo).values(key=merchants.holding_key("t:AAPL"), website="gone-example.com", hidden=0))
        self.assertEqual({h["name"]: h["logo"] for h in portfolio.holdings(self.c)}["Apple Inc"], "/api/merchants/ticker%3AAAPL/logo")

    def test_choosing_a_holdings_logo_through_the_api(self):
        from runway.server.common import ApiError
        api = server.api.merchants
        with self.assertRaises(ApiError):
            api.api_holding_logo(self.c, {}, {"website": "apple-example.com"})                      # which holding?
        with self.assertRaises(ApiError):
            api.api_holding_logo_options(self.c, {"group": [""]}, None)
        with self.assertRaises(ApiError) as e:                                                      # no Logo.dev key
            api.api_holding_logo(self.c, {}, {"group": "t:AAPL", "website": "apple-example.com"})
        self.assertIn("Logo.dev", str(e.exception))
        db.set_setting(self.c, sk.LOGODEV_TOKEN, "pk_test123456")
        with self.assertRaises(ApiError) as e:
            api.api_holding_logo(self.c, {}, {"group": "t:AAPL", "website": "not a site"})
        self.assertIn("website", str(e.exception))
        self.assertIsNone(merchants.holding_choice(self.c, "t:AAPL"))                               # failures change nothing
        self.assertEqual(api.api_holding_logo(self.c, {}, {"group": "t:AAPL", "hidden": True}), {"ok": True})
        options = api.api_holding_logo_options(self.c, {"group": ["t:AAPL"], "name": ["Apple Inc"]}, None)
        self.assertEqual(options["choice"], {"website": None, "hidden": True})
        self.assertTrue(options["configured"])
        self.assertEqual(options["candidates"], [])                                                 # no secret key: no search
        found = [{"name": "Apple Example", "domain": "apple-example.com"}]
        db.set_setting(self.c, sk.LOGODEV_SECRET, "sk_test123456")
        with mock.patch.object(merchants, "search", return_value=found) as search:
            options = api.api_holding_logo_options(self.c, {"group": ["t:AAPL"], "name": ["Apple Inc"]}, None)
        search.assert_called_once_with(self.c, "Apple Inc")
        self.assertEqual(options["candidates"], found)
        with mock.patch.object(merchants, "search", return_value=None):
            self.assertEqual(api.api_holding_logo_options(self.c, {"group": ["t:AAPL"], "name": ["Apple Inc"]}, None)["error"], merchants._why)
        self.assertEqual(api.api_holding_logo(self.c, {}, {"group": "t:AAPL"}), {"ok": True})
        self.assertIsNone(merchants.holding_choice(self.c, "t:AAPL"))

    def test_the_holding_logo_routes_are_served(self):
        from runway.server import routes
        self.assertIn(("POST", "/api/investments/logo"), [(m, p) for m, p, _ in routes.ROUTES])
        self.assertIn(("GET", "/api/investments/logo-options"), [(m, p) for m, p, _ in routes.ROUTES])

    def test_the_secret_key_looks_up_names_again(self):
        started = mock.patch.object(server.api.merchants, "start_logo_backfill").start()
        self.addCleanup(mock.patch.stopall)
        self.c.execute(insert(Merchant), [{"id": "brand:x", "logo_url": "u", "logo": "x", "logo_checked": "2026-01-01"},
                                          {"id": "site:y.com", "logo_url": "u", "logo": None,
                                           "logo_checked": "2026-01-01"},
                                          {"id": "e", "logo_url": "u", "logo": None, "logo_checked": "2026-01-01"}])
        server.api_logodev_settings(self.c, {}, {"secret": "sk_abcdefgh123"})
        self.assertEqual({r[0]: r[1] for r in self.c.execute(select(Merchant.id, Merchant.logo_checked))},
                         {"brand:x": None, "site:y.com": "2026-01-01", "e": "2026-01-01"})
        server.api_logodev_settings(self.c, {}, {"token": "pk_abcdefgh123"})
        self.assertEqual({r[0]: r[1] for r in self.c.execute(select(Merchant.id, Merchant.logo_checked))},
                         {"brand:x": None, "site:y.com": None, "e": "2026-01-01"})
        self.assertEqual(started.call_count, 2)


if __name__ == "__main__":
    unittest.main()
