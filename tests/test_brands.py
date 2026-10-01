"""Institution logos for accounts, and which big merchant a transaction is from."""
import base64
import os
import tempfile
import unittest
from unittest import mock

from runway import brands, db


class BrandTests(unittest.TestCase):
    def test_names(self):
        cases = {"Chase": "chase", "JPMorgan Chase Bank": "chase", "CSP (Sara)": "chase", "Capital One": "capital-one",
                 "Venture X (Anthony)": "capital-one", "Citi": "citibank", "American Express": "american-express",
                 "Amex Card": "american-express", "E*TRADE from Morgan Stanley": "e-trade", "Fidelity Investments": "fidelity",
                 "Wealthfront": None, "Mortgage 1588": None}   # which institution a name is; not logos
        for name, want in cases.items():
            self.assertEqual(brands.brand(name), want, name)

    def test_accounts_get_logo_dev_s_logo_by_institution(self):
        from unittest import mock

        from runway import merchants
        path = os.path.join(tempfile.mkdtemp(), "b.db")
        db.init(path)
        png = base64.b64encode(b"png").decode()
        with db.session(path) as c:
            c.execute("INSERT INTO plaid_items(item_id, access_token, institution_name, products) VALUES ('i','t','Citibank Online','transactions')")
            c.execute("INSERT INTO plaid_accounts(plaid_account_id, item_id, name) VALUES ('p1','i','Double Cash')")
            c.execute("INSERT INTO accounts(id, name, kind, plaid_account_id) VALUES ('a1','My card','credit','p1')")
            c.execute("INSERT INTO accounts(id, name, org, kind, owner) VALUES ('w','Roth','Wealthfront Sara','investment','Sara')")
            c.execute("INSERT INTO accounts(id, name, org, kind) VALUES ('f','Brokerage','Fidelity Investments','investment')")
            c.execute("INSERT INTO accounts(id, name, org, kind, owner) VALUES ('csr','CSR','Chase Bank Anthony','credit','Anthony')")
            c.execute("INSERT INTO accounts(id, name, kind) VALUES ('vx','Venture X','credit')")   # no institution: its name says
            c.execute("INSERT INTO plaid_items(item_id, access_token, institution_name, products) VALUES ('i2','t','Vestwell','investments')")
            c.execute("INSERT INTO accounts(id, name, org, kind) VALUES ('x','Odd','?','checking')")
            got = brands.account_brands(c)                                              # no Logo.dev key: letters, nothing asked
            self.assertEqual(got["a1"], {"src": None, "auto": None, "institution": "Citibank Online", "initial": "C"})
            self.assertEqual(got["w"]["institution"], "Wealthfront")                   # without the owner's name
            self.assertEqual(got["csr"]["institution"], "Chase Bank")
            self.assertEqual(c.execute("SELECT COUNT(*) FROM merchants").fetchone()[0], 0)
            db.set_setting(c, "logodev_token", "pk_test")
            brands.account_brands(c)
            asked = {r["id"]: r["logo_url"] for r in c.execute("SELECT id, logo_url FROM merchants")}
            # banks Runway knows by website; the rest by name, for the next fetch
            self.assertEqual(sorted(asked), ["brand:wealthfront", "site:capitalone.com", "site:chase.com",
                                             "site:citi.com", "site:fidelity.com"])
            self.assertTrue(asked["brand:wealthfront"].startswith("https://img.logo.dev/name/Wealthfront"))
            # once a logo has been fetched (a sync does it), the account uses it
            c.execute("UPDATE merchants SET logo=?, logo_type='image/png' WHERE id IN ('brand:wealthfront', 'site:chase.com')", (png,))
            got = brands.account_brands(c)
            self.assertEqual(got["w"]["src"], "/api/merchants/brand%3Awealthfront/logo")
            self.assertEqual(got["csr"]["src"], "/api/merchants/site%3Achase.com/logo")
            self.assertIsNone(got["f"]["src"])
            self.assertEqual(merchants.logo(c, "brand:wealthfront"), (b"png", "image/png"))
            # a logo you chose replaces the institution's (which stays as `auto`); "none" is the letter
            c.execute("UPDATE accounts SET logo='chase.com' WHERE id='f'")
            c.execute("UPDATE accounts SET logo='none' WHERE id='csr'")
            got = brands.account_brands(c)
            self.assertEqual(got["f"]["src"], "/api/merchants/site%3Achase.com/logo")
            self.assertIsNone(got["csr"]["src"])
            self.assertEqual(got["csr"]["auto"], "/api/merchants/site%3Achase.com/logo")
            with mock.patch.object(merchants, "configured", return_value=False):       # without the key nothing is used
                self.assertIsNone(brands.account_brands(c)["w"]["src"])

    def test_connections_logos(self):
        path = os.path.join(tempfile.mkdtemp(), "n.db")
        db.init(path)
        with db.session(path) as c:
            c.execute("INSERT INTO plaid_items(item_id, access_token, institution_name, products) VALUES ('i','t','Citibank Online','transactions')")
            c.execute("INSERT INTO plaid_items(item_id, access_token, institution_name, products) VALUES ('i2','t','Vestwell','investments')")
            self.assertEqual(brands.connection_logos(c), {"Citibank Online": None, "Vestwell": None})   # no key
            db.set_setting(c, "logodev_token", "pk_test")
            brands.connection_logos(c)
            self.assertEqual(sorted(r["id"] for r in c.execute("SELECT id FROM merchants")), ["brand:vestwell", "site:citi.com"])
            c.execute("UPDATE merchants SET logo='cG5n', logo_type='image/png'")
            self.assertEqual(brands.connection_logos(c), {"Citibank Online": "/api/merchants/site%3Aciti.com/logo",
                                                          "Vestwell": "/api/merchants/brand%3Avestwell/logo"})

    def test_institution_drops_owners_names(self):
        self.assertEqual(brands.institution("Citibank Anthony", {"Anthony", "Sara"}), "Citibank")
        self.assertEqual(brands.institution("Chase Bank sara", {"Sara"}), "Chase Bank")
        self.assertEqual(brands.institution("Sara", {"Sara"}), "Sara")             # never down to nothing
        self.assertEqual(brands.institution("Saratoga Bank", {"Sara"}), "Saratoga Bank")
        self.assertIsNone(brands.institution(None, {"Sara"}))

    def test_choosing_an_account_s_logo(self):
        from runway.server.api.accounts import api_account_logo, api_account_logo_options
        from runway.server.common import ApiError
        from runway import merchants
        path = os.path.join(tempfile.mkdtemp(), "c.db")
        db.init(path)
        with db.session(path) as c:
            c.execute("INSERT INTO accounts(id, name, org, kind) VALUES ('a','Card','Truist','credit')")
            with self.assertRaises(ApiError):                                           # no key: can't fetch a website's
                api_account_logo(c, {}, {"website": "truist.com"}, "a")
            db.set_setting(c, "logodev_token", "pk_test")
            with self.assertRaises(ApiError) as e:
                api_account_logo(c, {}, {"website": "not a site"}, "a")
            self.assertIn("website", str(e.exception))
            with mock.patch.object(merchants, "_download", return_value=None), self.assertRaises(ApiError):
                api_account_logo(c, {}, {"website": "truist.com"}, "a")              # Logo.dev has none: nothing changes
            self.assertIsNone(c.execute("SELECT logo FROM accounts WHERE id='a'").fetchone()[0])
            with mock.patch.object(merchants, "_download", return_value=(b"png", "image/png")):
                api_account_logo(c, {}, {"website": "https://www.Truist.com/"}, "a")
            self.assertEqual(c.execute("SELECT logo FROM accounts WHERE id='a'").fetchone()[0], "truist.com")
            self.assertEqual(brands.account_brands(c)["a"]["src"], "/api/merchants/site%3Atruist.com/logo")
            self.assertEqual(api_account_logo_options(c, {}, {}, "a")["choice"], {"website": "truist.com", "hidden": False})
            api_account_logo(c, {}, {"hidden": True}, "a")
            self.assertEqual(api_account_logo_options(c, {}, {}, "a")["choice"], {"website": None, "hidden": True})
            self.assertIsNone(brands.account_brands(c)["a"]["src"])
            api_account_logo(c, {}, {}, "a")                                            # back to the institution's
            self.assertIsNone(api_account_logo_options(c, {}, {}, "a")["choice"])
            with self.assertRaises(ApiError):
                api_account_logo(c, {}, {}, "nope")
            with self.assertRaises(ApiError):
                api_account_logo_options(c, {}, {}, "nope")


class MerchantLogoTests(unittest.TestCase):
    def test_every_website_is_one(self):
        from runway import merchants
        for _p, site in brands.MERCHANT_PATTERNS:
            self.assertEqual(merchants.site(site), site)

    def test_names(self):
        cases = [(("Target", "TARGET T-1234 MINNEAPOLIS MN"), "target.com"), (("AMZN Mktp US*2K4", None), "amazon.com"),
                 (("Amazon Prime Video", None), "primevideo.com"), (("Uber Eats", None), "ubereats.com"),
                 (("Uber", "UBER *TRIP"), "uber.com"), (("Walmart Supercenter", None), "walmart.com"),
                 (("WAL-MART #1234", None), "walmart.com"), (("Starbucks Store 99", None), "starbucks.com"),
                 (("Joe's Coffee", "SQ *JOES COFFEE"), None), (("Payroll", "ACME CORP DIRECT DEP"), None),
                 (("POS Purchase", "COSTCO WHSE #0001"), "costco.com"),  # the description names it when the payee doesn't
                 (("Delta Dental", None), None), (("Targeted Ads LLC", None), None), (("Ringling Bros", None), None)]
        for (payee, desc), want in cases:
            self.assertEqual(brands.merchant(payee, desc), want, payee)


if __name__ == "__main__":
    unittest.main()


class LabelTests(unittest.TestCase):
    def test_owner_in_account_names(self):
        path = os.path.join(tempfile.mkdtemp(), "l.db")
        db.init(path)
        with db.session(path) as c:
            c.executemany("INSERT INTO accounts(id, name, display_name, owner, kind) VALUES (?,?,?,?,'credit')", [
                ("a", "Citi AAdvantage 8312", "AAdvantage", "Sara"),     # -> AAdvantage (Sara)
                ("b", "CSP", "CSP (Sara)", "Sara"),                       # already says so
                ("c", "Blue Cash", None, None),                           # no owner
                ("d", "Checking", None, "Joint")])
            got = {r[0]: r[1] for r in c.execute("SELECT a.id, " + db.label_sql("a") + " FROM accounts a")}
            py = {r["id"]: db.account_label(r) for r in c.execute("SELECT * FROM accounts").fetchall()}
        want = {"a": "AAdvantage (Sara)", "b": "CSP (Sara)", "c": "Blue Cash", "d": "Checking (Joint)"}
        self.assertEqual(got, want)
        self.assertEqual(py, want)
