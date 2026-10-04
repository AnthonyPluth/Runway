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
        cases = {"Chase": "chase", "JPMorgan Chase Bank": "chase", "Chase Freedom (Sam)": "chase", "Capital One": "capital-one",
                 "Citi": "citibank", "American Express": "american-express",
                 "Amex Card": "american-express", "E*TRADE from Morgan Stanley": "e-trade", "Fidelity Investments": "fidelity",
                 "Wealthfront": None, "Mortgage 4100": None,
                 # a card's own name isn't its bank's: that's the bank's to say
                 "CSP (Sam)": None, "Venture X (Alex)": None, "Double Cash": None, "Costco Anywhere Visa": None,
                 "Blue Cash Preferred": None, "Apple Card": None, "Amazon Prime Visa": None, "Target RedCard": None}
        for name, want in cases.items():   # which institution a name is; not logos
            self.assertEqual(brands.brand(name), want, name)

    def test_same_institution_by_brand_then_by_name(self):
        cases = [("E*TRADE from Morgan Stanley", "E*Trade", True),     # by brand, and by name
                 ("Wealthfront Inc", "Wealthfront", True),               # no brand: by name
                 ("Merrill", "Bank of America", True),                   # the same brand; the names alone didn't say so
                 ("Bank of America", "BofA Securities", True),           # by brand: "bofasecurities" doesn't hold "ofamerica"
                 ("Chase", "Citibank", False), ("Ally", "Wealthfront", False), (None, "Chase", False)]
        for a, b, want in cases:
            self.assertEqual(brands.same_institution(a, b), want, (a, b))
            self.assertEqual(brands.same_institution(b, a), want, (b, a))
        # Matching a bank account: a known brand on each side decides, either way; else the institution names, if they
        # agree; else it can't tell.
        self.assertIs(brands.institution_match(("Chase", None, "Card"), ("JPMorgan Chase", None, "Freedom")), True)
        self.assertIsNone(brands.institution_match((None, None, "Sapphire Reserve"), ("Citibank", None, "Card")))   # a card's name says nothing
        self.assertIs(brands.institution_match(("Ally Bank", None, "Savings"), ("Ally", None, "Online Savings")), True)
        self.assertIsNone(brands.institution_match(("Ally Bank", None, "Savings"), ("Ally", None, "Online Savings"), by_name=False))
        self.assertIs(brands.institution_match(("Chase", None, "Card"), ("Citibank", None, "Card"), by_name=False), False)
        self.assertIsNone(brands.institution_match(("Ally Bank", None, "Savings"), ("Wealthfront", None, "Cash")))

    def test_matching_without_card_names(self):
        """Matching a bank's account to yours (plaidbank's auto-match: by_name=False), before and after Runway stopped
        knowing which bank issues which card: an account whose institution is named matches as it did; one without
        (an account you added yourself) can no longer be told apart by its card's name, either way."""
        cases = [  # (yours: institution, display name, name), (the bank's: institution, official name, name), before, now
            (("Chase", None, "Sapphire Reserve"), ("JPMorgan Chase", "Chase Sapphire Reserve", "CSR"), True, True),
            (("Capital One", None, "Venture X"), ("Capital One", "Venture X Rewards", "Venture X"), True, True),
            (("Citi", None, "Double Cash"), ("Citibank Online", None, "Double Cash"), True, True),
            (("American Express", None, "Blue Cash"), ("American Express", None, "Blue Cash Preferred"), True, True),
            (("Wells Fargo", None, "Active Cash"), ("Wells Fargo", None, "Active Cash"), True, True),
            (("Chase", None, "Freedom"), ("Citibank Online", None, "Custom Cash"), False, False),
            (("Northwind Credit Union", None, "Checking"), ("Northwind CU", None, "Checking"), None, None),
            ((None, None, "Everyday Checking"), ("Chase", None, "Total Checking"), None, None),
            ((None, "Chase Freedom", "Card 1234"), ("Chase", None, "Freedom Unlimited"), True, True),   # the bank's name
            # no institution on yours, and only its card's name to go by: no longer known
            ((None, None, "Sapphire Preferred"), ("Chase", "Chase Sapphire Preferred", "Sapphire"), True, None),
            ((None, "CSP", "Card 1234"), ("Chase", None, "Freedom Unlimited"), True, None),
            ((None, None, "Venture X"), ("Capital One", "Venture X Rewards", "Venture X"), True, None),
            ((None, None, "Costco Anywhere Visa"), ("Citibank Online", None, "Costco Anywhere Visa Card"), True, None),
            ((None, None, "Gold Card"), ("American Express", None, "Gold Card"), True, None),
            ((None, None, "Target RedCard"), ("TD Bank", None, "Target Circle Card"), True, None),
            ((None, None, "Apple Card"), ("Goldman Sachs", None, "Apple Card"), True, None),
            (("Apple Card", None, "Apple Card"), ("Goldman Sachs Bank", None, "Apple Card"), True, None),
            ((None, None, "Sapphire Preferred"), ("Citibank Online", "Citi Double Cash", "Double Cash"), False, None),
            ((None, None, "Quicksilver"), ("Chase", None, "Freedom Flex"), False, None),
            ((None, None, "Delta SkyMiles Gold"), ("Chase", None, "Sapphire"), False, None),
            # wrongly ruled out before: the Amazon card is Chase's
            ((None, None, "Amazon Prime Visa"), ("Chase", None, "Prime Visa"), False, None),
        ]
        for ours, theirs, _before, now in cases:
            self.assertIs(brands.institution_match(ours, theirs, by_name=False), now, (ours, theirs))

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
            c.execute(insert(Account).values(id="cf", name="Chase Freedom", kind="credit"))   # no institution: its name says
            c.execute(insert(Account).values(id="vx", name="Venture X", kind="credit"))      # a card's name doesn't
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
            self.assertEqual(sorted(asked), ["brand:wealthfront", "site:chase.com", "site:citi.com", "site:fidelity.com"])
            self.assertTrue(asked["brand:wealthfront"].startswith("https://img.logo.dev/name/Wealthfront"))
            # once a logo has been fetched (a sync does it), the account uses it
            c.execute(update(Merchant)
                      .where(Merchant.id.in_(["brand:wealthfront", "site:chase.com"]))
                      .values(logo=png, logo_type="image/png"))
            got = brands.account_brands(c)
            self.assertEqual(got["w"]["src"], "/api/merchants/brand%3Awealthfront/logo")
            self.assertEqual(got["csr"]["src"], "/api/merchants/site%3Achase.com/logo")
            self.assertIsNone(got["f"]["src"])
            self.assertEqual(got["cf"]["src"], "/api/merchants/site%3Achase.com/logo")
            self.assertEqual((got["vx"]["src"], got["vx"]["initial"]), (None, "V"))   # its letter
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
                 (("Delta Dental", None), None), (("Targeted Ads LLC", None), None), (("Ringling Bros", None), None),
                 (("Costco Whse #123", None), "costco.com"), (("AMZN Mktp", None), "amazon.com"),
                 (("Starbucks", "APPLE PAY DEBIT CARD STARBUCKS"), "starbucks.com"),
                 (("POS Purchase", "DEBIT CARD PURCHASE COSTCO WHSE"), "costco.com"),
                 # a bill paid to the merchant itself is still the merchant's
                 (("AT&T Payment", None), "att.com"), (("Venmo Payment", None), "venmo.com"),
                 # a card's payment isn't the store's, whichever text says so
                 (("Payment To Costco Anywhere Visa", None), None), (("Amazon Corp Syf Paymnt", None), None),
                 (("Target Card Payment", None), None), (("Online Payment", "PAYMENT TO COSTCO ANYWHERE VISA"), None),
                 (("Costco Anywhere Visa", "CITI CARD ONLINE PAYMENT"), None)]
        for (payee, desc), want in cases:
            self.assertEqual(brands.merchant(payee, desc), want, payee)
        for payee in ("Payment To Costco Anywhere Visa", "Amazon Corp Syf Paymnt", "Target Card Payment"):
            self.assertIsNone(brands.merchant_name(payee), payee)   # nor its name

    def test_store_orders_still_leave_out_card_payments(self):
        from runway.retail.match import MERCHANT
        self.assertIsNone(MERCHANT["costco"].search("PAYMENT TO COSTCO ANYWHERE VISA"))
        self.assertIsNotNone(MERCHANT["costco"].search("COSTCO WHSE #0123"))
        self.assertIsNotNone(MERCHANT["amazon"].search("AMZN Mktp US"))


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
