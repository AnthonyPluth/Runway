"""Institution logos for accounts, and bundled logos for big merchants."""
import os
import tempfile
import unittest

from runway import brands, db


class BrandTests(unittest.TestCase):
    def test_names(self):
        cases = {"Chase": "chase", "JPMorgan Chase Bank": "chase", "CSP (Sara)": "chase", "Capital One": "capital-one",
                 "Venture X (Anthony)": "capital-one", "Citi": "citibank", "American Express": "american-express",
                 "Amex Card": "american-express", "E*TRADE from Morgan Stanley": "e-trade", "Fidelity Investments": "fidelity",
                 "Wealthfront": None, "Mortgage 1588": None}
        for name, want in cases.items():
            self.assertEqual(brands.brand(name), want, name)

    def test_every_logo_is_bundled(self):
        folder = os.path.join(os.path.dirname(brands.__file__), "static", "banks")
        for _p, slug in brands.PATTERNS:
            self.assertTrue(os.path.exists(os.path.join(folder, f"{slug}.svg")), slug)

    def test_accounts_use_the_institution_first(self):
        path = os.path.join(tempfile.mkdtemp(), "b.db")
        db.init(path)
        with db.session(path) as c:
            c.execute("INSERT INTO plaid_items(item_id, access_token, institution_name, products) VALUES ('i','t','Citibank','transactions')")
            c.execute("INSERT INTO plaid_accounts(plaid_account_id, item_id, name) VALUES ('p1','i','Double Cash')")
            c.execute("INSERT INTO accounts(id, name, kind, plaid_account_id) VALUES ('a1','My card','credit','p1')")
            c.execute("INSERT INTO accounts(id, name, org, kind) VALUES ('a2','Joint','Wealthfront','savings')")
            got = brands.account_brands(c)
        self.assertEqual(got["a1"], {"logo": "citibank", "institution": "Citibank", "initial": "C"})
        self.assertEqual(got["a2"], {"logo": None, "institution": "Wealthfront", "initial": "W"})



class MerchantLogoTests(unittest.TestCase):
    FOLDER = os.path.join(os.path.dirname(brands.__file__), "static", "merchants")

    def test_names(self):
        cases = [(("Target", "TARGET T-1234 MINNEAPOLIS MN"), "target"), (("AMZN Mktp US*2K4", None), "amazon"),
                 (("Amazon Prime Video", None), "amazon-prime-video"), (("Uber Eats", None), "uber-eats"),
                 (("Uber", "UBER *TRIP"), "uber"), (("Walmart Supercenter", None), "walmart"),
                 (("WAL-MART #1234", None), "walmart"), (("Starbucks Store 99", None), "starbucks"),
                 (("Joe's Coffee", "SQ *JOES COFFEE"), None), (("Payroll", "ACME CORP DIRECT DEP"), None),
                 (("POS Purchase", "COSTCO WHSE #0001"), "costco"),  # the description names it when the payee doesn't
                 (("Delta Dental", None), None), (("Targeted Ads LLC", None), None), (("Ringling Bros", None), None)]
        for (payee, desc), want in cases:
            self.assertEqual(brands.merchant(payee, desc), want, payee)

    def test_every_logo_is_bundled(self):
        for _p, slug in brands.MERCHANT_PATTERNS:
            self.assertTrue(os.path.exists(os.path.join(self.FOLDER, f"{slug}.svg")), slug)

    def test_bundled_logos_are_inert(self):
        for name in os.listdir(self.FOLDER):
            if name.endswith(".svg"):
                with open(os.path.join(self.FOLDER, name), encoding="utf-8") as f:
                    svg = f.read().lower()
                self.assertNotRegex(svg, r"<script|<foreignobject|\son[a-z]+\s*=|(href|src)\s*=\s*[\"']?\s*(https?:|//|javascript:)", name)


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
