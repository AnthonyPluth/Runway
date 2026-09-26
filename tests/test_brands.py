"""Institution logos for accounts."""
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


if __name__ == "__main__":
    unittest.main()
