"""Institution logos for accounts, and which big merchant a transaction is from."""
import base64
import os
import tempfile
import unittest

from runway import brands, db


class BrandTests(unittest.TestCase):
    def test_names(self):
        cases = {"Chase": "chase", "JPMorgan Chase Bank": "chase", "CSP (Sara)": "chase", "Capital One": "capital-one",
                 "Venture X (Anthony)": "capital-one", "Citi": "citibank", "American Express": "american-express",
                 "Amex Card": "american-express", "E*TRADE from Morgan Stanley": "e-trade", "Fidelity Investments": "fidelity",
                 "Wealthfront": None, "Mortgage 1588": None}   # which institution a name is; not logos
        for name, want in cases.items():
            self.assertEqual(brands.brand(name), want, name)

    def test_accounts_get_logo_dev_s_logo_by_institution_name(self):
        from unittest import mock

        from runway import merchants
        path = os.path.join(tempfile.mkdtemp(), "b.db")
        db.init(path)
        with db.session(path) as c:
            c.execute("INSERT INTO plaid_items(item_id, access_token, institution_name, products) VALUES ('i','t','Citibank','transactions')")
            c.execute("INSERT INTO plaid_accounts(plaid_account_id, item_id, name) VALUES ('p1','i','Double Cash')")
            c.execute("INSERT INTO accounts(id, name, kind, plaid_account_id) VALUES ('a1','My card','credit','p1')")
            c.execute("INSERT INTO accounts(id, name, org, kind) VALUES ('w','Roth','Wealthfront','investment')")
            c.execute("INSERT INTO accounts(id, name, org, kind) VALUES ('f','Brokerage','Fidelity Investments','investment')")
            c.execute("INSERT INTO plaid_items(item_id, access_token, institution_name, products) VALUES ('i2','t','Vestwell','investments')")
            c.execute("INSERT INTO accounts(id, name, org, kind) VALUES ('x','Odd','?','checking')")
            got = brands.account_brands(c)                                              # no Logo.dev key: letters, nothing asked
            self.assertEqual(got["a1"], {"src": None, "institution": "Citibank", "initial": "C"})
            self.assertEqual(got["w"], {"src": None, "institution": "Wealthfront", "initial": "W"})
            self.assertEqual(c.execute("SELECT COUNT(*) FROM merchants WHERE id LIKE 'brand:%'").fetchone()[0], 0)
            db.set_setting(c, "logodev_token", "pk_test")
            brands.account_brands(c)
            asked = {r["id"]: r["logo_url"] for r in c.execute("SELECT id, logo_url FROM merchants WHERE id LIKE 'brand:%'")}
            # every institution is noted for the next fetch (Fidelity too: nothing is bundled), connections included
            self.assertEqual(sorted(asked), ["brand:citibank", "brand:fidelity investments", "brand:vestwell", "brand:wealthfront"])
            self.assertTrue(asked["brand:wealthfront"].startswith("https://img.logo.dev/name/Wealthfront"))
            # once a logo has been fetched (a sync does it), the account uses it
            c.execute("UPDATE merchants SET logo=?, logo_type='image/png' WHERE id='brand:wealthfront'", (base64.b64encode(b"png").decode(),))
            got = brands.account_brands(c)
            self.assertEqual(got["w"]["src"], "/api/merchants/brand%3Awealthfront/logo")
            self.assertIsNone(got["f"]["src"])
            self.assertEqual(merchants.logo(c, "brand:wealthfront"), (b"png", "image/png"))
            with mock.patch.object(merchants, "configured", return_value=False):       # without the key nothing is used
                self.assertIsNone(brands.account_brands(c)["w"]["src"])


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
