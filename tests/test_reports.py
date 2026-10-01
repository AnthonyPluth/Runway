"""Reports: spending over time, merchants, income against spending, and the breakdown."""
import unittest

from sqlalchemy import insert, update

from runway import categories, reports, splits
from runway.models import Account, Transaction
from tests.shared import DbCase


class ReportTests(DbCase):
    def setUp(self):
        super().setUp()
        self.c.execute(insert(Account), [{"id": "chk", "name": "Checking", "kind": "checking", "balance": 0},
                                         {"id": "cc", "name": "Card", "kind": "credit", "balance": 0},
                                         {"id": "old", "name": "Old", "kind": "credit", "balance": 0}])
        self.c.execute(update(Account).where(Account.id == "old").values(hidden=1))
        categories.add(self.c, "Fast food", parent="Restaurants")
        self.n = 0
        rows = [
            ("chk", "2026-07-01", 3000, "PAYROLL", "Income"),
            ("chk", "2026-08-01", 3000, "PAYROLL", "Income"),
            ("chk", "2026-09-01", 3100, "PAYROLL", "Income"),
            ("cc", "2026-07-05", -100, "Whole Foods", "Groceries"),
            ("cc", "2026-08-05", -150, "Whole Foods", "Groceries"),
            ("cc", "2026-08-06", 20, "Whole Foods", "Groceries"),        # a refund lowers spending
            ("cc", "2026-09-05", -120, "Whole Foods", "Groceries"),
            ("cc", "2026-09-07", -12, "Shake Shack", "Fast food"),
            ("cc", "2026-09-08", -40, "Nice Place", "Restaurants"),
            ("chk", "2026-09-10", -500, "CARD PAYMENT", "Credit Card Payment"),   # not spending
            ("cc", "2026-09-11", -30, "Mystery", None),                           # uncategorized money out is
            ("old", "2026-09-12", -999, "Hidden account", "Shopping"),             # not counted: hidden account
        ]
        for acct, day, amt, payee, cat in rows:
            self.tx(acct, day, amt, payee, cat)
        target = self.tx("cc", "2026-09-15", -100, "Target", "Shopping")
        splits.set_splits(self.c, target, [{"amount": -60, "category": "Groceries"}, {"amount": -40, "category": "Shopping"}])

    def tx(self, acct, day, amt, payee, cat):
        self.n += 1
        tid = f"t{self.n}"
        self.c.execute(insert(Transaction).values(id=tid, account_id=acct, posted=day, amount=amt,
                                                  description=payee.upper(), payee=payee, category=cat))
        return tid

    def test_month_pace(self):
        from datetime import date
        p = reports.month_pace(self.c, date(2026, 9, 10))
        self.assertEqual((p["month"], p["prev_month"], len(p["this"]), len(p["last"])), ("2026-09", "2026-08", 10, 31))
        # Sep 1-10: groceries 120, fast food 12, restaurants 40 (the card payment and the hidden account don't count)
        self.assertEqual(p["spent"], 172)
        self.assertEqual((p["this"][4], p["this"][6]), (120, 132))
        # August: 150 on the 5th, then a 20 refund on the 6th
        self.assertEqual((p["last"][4], p["last_same_point"], p["last_total"]), (150, 130, 130))

    def test_spending_over_time(self):
        d = reports.spending_over_time(self.c, "2026-09", 3)
        self.assertEqual(d["months"], ["2026-07", "2026-08", "2026-09"])
        by = {s["name"]: s["values"] for s in d["series"]}
        self.assertEqual(by["Groceries"], [100.0, 130.0, 180.0])      # 120 + the split's 60
        self.assertEqual(by["Restaurants"], [0.0, 0.0, 52.0])          # a subcategory counts toward its top
        self.assertEqual(by["Shopping"], [0.0, 0.0, 40.0])
        self.assertEqual(by["Uncategorized"], [0.0, 0.0, 30.0])
        self.assertNotIn("Credit Card Payment", by)
        self.assertEqual(d["totals"], [100.0, 130.0, 302.0])
        m = {s["name"] for s in reports.spending_over_time(self.c, "2026-09", 3, "merchant")["series"]}
        self.assertEqual(m, {"Whole Foods", "Target", "Nice Place", "Mystery", "Shake Shack"})
        with self.assertRaises(ValueError):
            reports.spending_over_time(self.c, "2026-09", 3, "planet")

    def test_the_rest_fold_into_everything_else(self):
        for i in range(10):
            self.tx("cc", "2026-09-20", -(i + 1), f"Shop {i}", "Shopping")
        d = reports.spending_over_time(self.c, "2026-09", 2, "merchant")
        self.assertEqual(len(d["series"]), reports.TOP + 1)
        self.assertTrue(d["series"][-1]["other"])
        self.assertAlmostEqual(sum(s["values"][1] for s in d["series"]), d["totals"][1])

    def test_income_vs_spending(self):
        d = reports.income_vs_spending(self.c, "2026-09", 3)
        sept = d["months"][-1]
        self.assertEqual((sept["income"], sept["spending"], sept["net"]), (3100.0, 302.0, 2798.0))
        self.assertAlmostEqual(sept["rate"], 2798 / 3100, places=4)
        self.assertEqual((d["year"]["income"], d["year"]["spending"]), (9100.0, 532.0))

    def test_months_before_the_first_transaction_are_left_out(self):
        # History starts in July: a 12-month view is July to September, and the year counts three months, not nine
        d = reports.income_vs_spending(self.c, "2026-09", 12)
        self.assertEqual([m["month"] for m in d["months"]], ["2026-07", "2026-08", "2026-09"])
        self.assertEqual((d["year"]["months"], d["year"]["income"]), (3, 9100.0))
        s = reports.spending_over_time(self.c, "2026-09", 12)
        self.assertEqual(s["months"], ["2026-07", "2026-08", "2026-09"])
        self.assertEqual(s["totals"], [100.0, 130.0, 302.0])
        # A hidden account's older transactions don't stretch the history back
        self.tx("old", "2026-01-03", -5, "Old shop", "Shopping")
        self.assertEqual(reports.income_vs_spending(self.c, "2026-09", 12)["year"]["months"], 3)
        # A gap after the first transaction is a real $0 month, so it stays
        self.assertEqual(len(reports.income_vs_spending(self.c, "2026-11", 12)["months"]), 5)

    def test_no_transactions_yet_shows_just_this_month(self):
        self.c.execute(Transaction.__table__.delete())
        d = reports.income_vs_spending(self.c, "2026-09", 12)
        self.assertEqual([m["month"] for m in d["months"]], ["2026-09"])
        self.assertEqual(d["year"]["months"], 1)
        self.assertEqual(reports.spending_over_time(self.c, "2026-09", 6)["months"], ["2026-09"])

    def test_merchants_and_one_merchant(self):
        d = reports.merchants(self.c, "2026-07-01", "2026-10-01")
        top = d["merchants"][0]
        self.assertEqual((top["name"], top["total"], top["count"], top["category"]), ("Whole Foods", 350.0, 4, "Groceries"))
        target = next(m for m in d["merchants"] if m["name"] == "Target")
        self.assertEqual((target["total"], target["count"], target["category"]), (100.0, 1, "Groceries"))   # one visit, split
        one = reports.merchant(self.c, "whole foods", "2026-09", 3)
        self.assertEqual(one["values"], [100.0, 130.0, 120.0])
        self.assertEqual(len(one["transactions"]), 4)

    def test_breakdown_and_its_transactions(self):
        tree = reports.breakdown(self.c, "2026-09-01", "2026-10-01")["tree"]
        self.assertEqual(tree["value"], 302.0)
        rest = next(c for c in tree["children"] if c["name"] == "Restaurants")
        self.assertEqual([(c["name"], c["value"]) for c in rest["children"]], [("Restaurants (general)", 40.0), ("Fast food", 12.0)])
        groc = next(c for c in tree["children"] if c["name"] == "Groceries")
        self.assertEqual([c["name"] for c in groc["children"]], ["Whole Foods", "Target"])   # no subcategories: straight to merchants
        txs = reports.transactions(self.c, "2026-09-01", "2026-10-01", "Groceries", "Target")
        self.assertEqual([(t["amount"], t["part"]) for t in txs], [(-60.0, False)])


if __name__ == "__main__":
    unittest.main()
