"""Changing many transactions at once."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runway import categorize, db, splits


class BulkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "b.db")
        db.init(self.path)
        self.c = db.connect(self.path)
        self.c.execute("INSERT INTO accounts(id, name, kind, balance) VALUES ('cc', 'Card', 'credit', 0)")
        for i, (amt, desc, cat, review) in enumerate([(-10, "SQ *JOES 1", None, 1), (-12, "SQ *JOES 2", "Shopping", 1),
                                                      (-100, "TARGET", "Shopping", 0), (-5, "OTHER", "Other", 1)]):
            self.c.execute("INSERT INTO transactions(id, account_id, posted, amount, description, payee, category, category_source, needs_review) "
                           "VALUES (?,?,?,?,?,?,?,?,?)", (f"t{i}", "cc", "2026-09-01", amt, desc, desc, cat, "ai" if cat else None, review))
        splits.set_splits(self.c, "t2", [{"amount": -60, "category": "Groceries"}, {"amount": -40, "category": "Shopping"}])

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def rows(self):
        return {r["id"]: tuple(r)[1:] for r in self.c.execute(
            "SELECT id, payee, category, category_source, needs_review, is_split FROM transactions ORDER BY id")}

    def test_category_rename_and_reviewed(self):
        self.assertEqual(categorize.bulk_update(self.c, ["t0", "t1", "t2", "nope"], category="Coffee & Snacks"), 3)
        r = self.rows()
        self.assertEqual(r["t0"][1:], ("Coffee & Snacks", "manual", 0, 0))
        self.assertEqual(r["t2"][1:], ("Coffee & Snacks", "manual", 0, 0))          # a split goes back together
        self.assertEqual(splits.get(self.c, "t2"), [])
        categorize.bulk_update(self.c, ["t0", "t1"], payee="Joe's Coffee")
        self.assertEqual({self.rows()[t][0] for t in ("t0", "t1")}, {"Joe's Coffee"})
        categorize.bulk_update(self.c, ["t3"], reviewed=True)
        self.assertEqual(self.rows()["t3"][1:4], ("Other", "manual", 0))

    def test_checks(self):
        for args, kw in [([], {"category": "Shopping"}), (["t0"], {}), (["t0"], {"category": "Nope"}),
                         ([str(i) for i in range(categorize.MAX_BULK + 1)], {"reviewed": True})]:
            with self.assertRaises(ValueError):
                categorize.bulk_update(self.c, args, **kw)


if __name__ == "__main__":
    unittest.main()
