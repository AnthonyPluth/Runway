"""Institution logos for accounts, and which big merchant a transaction is from."""
import base64
import os
import tempfile
import unittest
from unittest import mock

from sqlalchemy import func, insert, select, update

from runway import brands, db
from runway.models import Account, Merchant, PlaidAccount, PlaidItem


class BrandTests(unittest.TestCase):
    def test_names(self):
        cases = {"Chase": "chase", "JPMorgan Chase Bank": "chase", "CSP (Sam)": "chase", "Capital One": "capital-one",
                 "Venture X (Alex)": "capital-one", "Citi": "citibank", "American Express": "american-express",
                 "Amex Card": "american-express", "E*TRADE from Morgan Stanley": "e-trade", "Fidelity Investments": "fidelity",
                 "Wealthfront": None, "Mortgage 4100": None}   # which institution a name is; not logos
        for name, want in cases.items():
            self.assertEqual(brands.brand(name), want, name)

    def test_accounts_get_logo_dev_s_logo_by_institution(self):
        from unittest import mock

        from runway import merchants
        path = os.path.join(tempfile.mkdtemp(), "b.db")
        db.init(path)
        png = base64.b64encode(b"png").decode()
        with db.session(path) as c:
            c.execute(insert(PlaidItem).values(item_id="i", access_token="t", institution_name="Citibank Online",
                                               products="transactions"))
            c.execute(insert(PlaidAccount).values(plaid_account_id="p1", item_id="i", name="Double Cash"))
            c.execute(insert(Account).values(id="a1", name="My card", kind="credit", plaid_account_id="p1"))
            c.execute(insert(Account).values(id="w", name="Roth", org="Wealthfront Sam", kind="investment",
                                             owner="Sam"))
            c.execute(insert(Account).values(id="f", name="Brokerage", org="Fidelity Investments", kind="investment"))
            c.execute(insert(Account).values(id="csr", name="CSR", org="Chase Bank Alex", kind="credit",
                                             owner="Alex"))
            c.execute(insert(Account).values(id="vx", name="Venture X", kind="credit"))   # no institution: its name says
            c.execute(insert(PlaidItem).values(item_id="i2", access_token="t", institution_name="Vestwell",
                                               products="investments"))
            c.execute(insert(Account).values(id="x", name="Odd", org="?", kind="checking"))
            got = brands.account_brands(c)                                              # no Logo.dev key: letters, nothing asked
            self.assertEqual(got["a1"], {"src": None, "auto": None, "institution": "Citibank Online", "initial": "C"})
            self.assertEqual(got["w"]["institution"], "Wealthfront")                   # without the owner's name
            self.assertEqual(got["csr"]["institution"], "Chase Bank")
            self.assertEqual(c.execute(select(func.count()).select_from(Merchant)).fetchone()[0], 0)
            db.set_setting(c, "logodev_token", "pk_test")
            brands.account_brands(c)
            asked = {r["id"]: r["logo_url"] for r in c.execute(select(Merchant.id, Merchant.logo_url))}
            # banks Runway knows by website; the rest by name, for the next fetch
            self.assertEqual(sorted(asked), ["brand:wealthfront", "site:capitalone.com", "site:chase.com",
                                             "site:citi.com", "site:fidelity.com"])
            self.assertTrue(asked["brand:wealthfront"].startswith("https://img.logo.dev/name/Wealthfront"))
            # once a logo has been fetched (a sync does it), the account uses it
            c.execute(update(Merchant)
                      .where(Merchant.id.in_(["brand:wealthfront", "site:chase.com"]))
                      .values(logo=png, logo_type="image/png"))
            got = brands.account_brands(c)
            self.assertEqual(got["w"]["src"], "/api/merchants/brand%3Awealthfront/logo")
            self.assertEqual(got["csr"]["src"], "/api/merchants/site%3Achase.com/logo")
            self.assertIsNone(got["f"]["src"])
            self.assertEqual(merchants.logo(c, "brand:wealthfront"), (b"png", "image/png"))
            # a logo you chose replaces the institution's (which stays as `auto`); "none" is the letter
            c.execute(update(Account).where(Account.id == "f").values(logo="chase.com"))
            c.execute(update(Account).where(Account.id == "csr").values(logo="none"))
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
            c.execute(insert(PlaidItem).values(item_id="i", access_token="t", institution_name="Citibank Online",
                                               products="transactions"))
            c.execute(insert(PlaidItem).values(item_id="i2", access_token="t", institution_name="Vestwell",
                                               products="investments"))
            self.assertEqual(brands.connection_logos(c), {"Citibank Online": None, "Vestwell": None})   # no key
            db.set_setting(c, "logodev_token", "pk_test")
            brands.connection_logos(c)
            self.assertEqual(sorted(r["id"] for r in c.execute(select(Merchant.id))), ["brand:vestwell", "site:citi.com"])
            c.execute(update(Merchant).values(logo="cG5n", logo_type="image/png"))
            self.assertEqual(brands.connection_logos(c), {"Citibank Online": "/api/merchants/site%3Aciti.com/logo",
                                                          "Vestwell": "/api/merchants/brand%3Avestwell/logo"})

    def test_institution_drops_owners_names(self):
        self.assertEqual(brands.institution("Citibank Alex", {"Alex", "Sam"}), "Citibank")
        self.assertEqual(brands.institution("Chase Bank sam", {"Sam"}), "Chase Bank")
        self.assertEqual(brands.institution("Sam", {"Sam"}), "Sam")             # never down to nothing
        self.assertEqual(brands.institution("Samford Bank", {"Sam"}), "Samford Bank")
        self.assertIsNone(brands.institution(None, {"Sam"}))

    def test_choosing_an_account_s_logo(self):
        from runway.server.api.accounts import api_account_logo, api_account_logo_options
        from runway.server.common import ApiError
        from runway import merchants
        path = os.path.join(tempfile.mkdtemp(), "c.db")
        db.init(path)
        with db.session(path) as c:
            c.execute(insert(Account).values(id="a", name="Card", org="Northwind", kind="credit"))
            with self.assertRaises(ApiError):                                           # no key: can't fetch a website's
                api_account_logo(c, {}, {"website": "northwind-bank.com"}, "a")
            db.set_setting(c, "logodev_token", "pk_test")
            with self.assertRaises(ApiError) as e:
                api_account_logo(c, {}, {"website": "not a site"}, "a")
            self.assertIn("website", str(e.exception))
            with mock.patch.object(merchants, "_download", return_value=None), self.assertRaises(ApiError):
                api_account_logo(c, {}, {"website": "northwind-bank.com"}, "a")              # Logo.dev has none: nothing changes
            self.assertIsNone(c.execute(select(Account.logo).where(Account.id == "a")).fetchone()[0])
            with mock.patch.object(merchants, "_download", return_value=(b"png", "image/png")):
                api_account_logo(c, {}, {"website": "https://www.Northwind-Bank.com/"}, "a")
            self.assertEqual(c.execute(select(Account.logo).where(Account.id == "a")).fetchone()[0], "northwind-bank.com")
            self.assertEqual(brands.account_brands(c)["a"]["src"], "/api/merchants/site%3Anorthwind-bank.com/logo")
            self.assertEqual(api_account_logo_options(c, {}, {}, "a")["choice"], {"website": "northwind-bank.com", "hidden": False})
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
        for _p, site, _name in brands.MERCHANT_PATTERNS:
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
            c.execute(insert(Account), [{"id": i, "name": n, "display_name": d, "owner": o, "kind": "credit"} for i, n, d, o in [
                ("a", "Citi AAdvantage 4400", "AAdvantage", "Sam"),     # -> AAdvantage (Sam)
                ("b", "CSP", "CSP (Sam)", "Sam"),                       # already says so
                ("c", "Blue Cash", None, None),                           # no owner
                ("d", "Checking", None, "Joint")]])
            got = {r[0]: r[1] for r in c.execute(select(Account.id, db.account_label_expr()))}
            py = {r["id"]: db.account_label(r) for r in c.execute(select(Account)).fetchall()}
        want = {"a": "AAdvantage (Sam)", "b": "CSP (Sam)", "c": "Blue Cash", "d": "Checking (Joint)"}
        self.assertEqual(got, want)
        self.assertEqual(py, want)
