"""Bank account bonuses (runway/bank_bonuses.py): direct deposits counted, deadlines, safe to close, eligibility,
the Upcoming items, bonus income per year and the push alert."""
import os
import tempfile
import unittest
from datetime import date

from runway import bank_bonuses as bb
from runway import churning, db, notify
from runway.churning import ChurnError

TODAY = date(2026, 9, 29)
CATS = {"Paycheck": {"is_transfer": 0, "is_income": 1, "top": "Income"}, "Refunds": {"is_transfer": 0, "is_income": 1, "top": "Refunds"},
        "Transfer": {"is_transfer": 1, "is_income": 0, "top": "Transfer"}, "Groceries": {"is_transfer": 0, "is_income": 0, "top": "Groceries"}}


def bonus(id=1, owner="Alex", bank="Chase", **kw):
    return {"id": id, "owner": owner, "bank": bank, "account_type": "checking", "opened_on": "2026-08-01", "bonus": 300,
            "status": "open", "account_id": "chk", **kw}


def tx(posted, amount, category=None, payee="", description=""):
    return {"posted": posted, "amount": amount, "category": category, "payee": payee, "description": description}


class LogicTests(unittest.TestCase):
    def test_direct_deposits(self):
        dd = lambda **kw: bb.is_direct_deposit({"amount": 1000, "category": None, "payee": "", "description": "", **kw}, CATS)
        self.assertTrue(dd(category="Paycheck"))
        self.assertTrue(dd(description="ACME CORP PAYROLL PPD"))     # not categorized yet, looks like payroll
        self.assertTrue(dd(payee="Gusto"))
        self.assertFalse(dd(category="Refunds"))                     # a refund isn't a paycheck
        self.assertFalse(dd(category="Transfer", description="PAYROLL"))   # moved from your own account
        self.assertFalse(dd(description="Zelle from Sam"))
        self.assertFalse(dd(amount=-50, category="Paycheck"))

    def test_progress_and_state(self):
        b = bonus(dd_total=2000, dd_count=2, debit_count=3, min_balance=1500)
        rows = [tx("2026-07-31", 5000, "Paycheck"),                 # before it was opened
                tx("2026-08-15", 1200, "Paycheck"), tx("2026-08-29", 900, None, description="DIR DEP ACME"),
                tx("2026-08-30", 500, "Transfer"), tx("2026-09-01", -20, "Groceries"), tx("2026-09-02", -40, None),
                tx("2026-09-03", -300, "Transfer"),                   # moving money out isn't a purchase
                tx("2026-11-15", 3000, "Paycheck")]                   # after the deadline (Oct 30)
        p = bb.progress(b, rows, CATS, 1600)
        self.assertEqual((p["dd_total"], p["dd_count"], p["debits"], p["balance_ok"], p["met"]), (2100, 2, 2, True, False))
        self.assertEqual(bb.state(b, p, TODAY), "active")
        p = bb.progress(b, [*rows, tx("2026-09-10", -5, None)], CATS, 1600)
        self.assertTrue(p["met"])
        self.assertEqual(bb.state(b, p, TODAY), "met")
        self.assertFalse(bb.progress(b, [*rows, tx("2026-09-10", -5, None)], CATS, 100)["met"])   # balance too low
        manual = bb.progress({**b, "account_id": None, "manual_dd": 2500, "manual_debits": 3}, [], CATS, None)
        self.assertEqual((manual["dd_total"], manual["dd_count"], manual["balance_ok"], manual["met"]), (2500, None, None, True))
        missed = bb.progress(b, [], CATS, 0)
        self.assertEqual(bb.state(b, missed, date(2026, 11, 1)), "missed")
        self.assertEqual(bb.state({**b, "received_on": "2026-10-01"}, missed, TODAY), "received")
        self.assertEqual(bb.state({**b, "status": "closed"}, missed, TODAY), "closed")
        self.assertEqual(bb.state({**b, "status": "pending"}, missed, TODAY), "met")

    def test_dates(self):
        b = bonus()
        self.assertEqual(bb.deadline(b), date(2026, 10, 30))                   # 90 days by default
        self.assertEqual(bb.deadline({**b, "deadline_days": 60}), date(2026, 9, 30))
        self.assertEqual(bb.deadline({**b, "deadline": "2026-12-31"}), date(2026, 12, 31))
        self.assertEqual(bb.expected_on(b), date(2026, 12, 29))                # + 60 days to post
        # Safe to close: the latest of the days to keep it open, the balance hold, and the bonus posting
        self.assertEqual(bb.safe_close_on({**b, "keep_open_days": 180}), date(2027, 1, 28))
        self.assertEqual(bb.safe_close_on({**b, "keep_open_days": 30, "received_on": "2026-10-20"}), date(2026, 10, 20))
        self.assertEqual(bb.safe_close_on({**b, "received_on": "2026-10-20", "hold_until": "2026-11-30"}), date(2026, 12, 1))
        # Fee reminders on the day of the month it was opened, while there's a fee
        self.assertEqual(bb.next_fee_reminder({**b, "monthly_fee": 12, "opened_on": "2026-01-31"}, TODAY), date(2026, 9, 30))
        self.assertIsNone(bb.next_fee_reminder({**b, "monthly_fee": 0}, TODAY))
        self.assertIsNone(bb.next_fee_reminder({**b, "monthly_fee": 12, "status": "closed"}, TODAY))

    def test_eligibility(self):
        old = bonus(1, received_on="2025-06-01", status="closed", repeat_months=24)
        self.assertEqual(bb.eligibility(old, [old], TODAY), {"status": "later", "on": "2027-06-01", "override": False,
                                                              "why": "24 months after the last bonus (2025-06-01)"})
        self.assertEqual(bb.eligibility({**old, "once_per_lifetime": 1}, [], TODAY)["status"], "never")
        self.assertEqual(bb.eligibility({**old, "repeat_months": None}, [], TODAY)["status"], "unknown")
        self.assertEqual(bb.eligibility({**old, "eligible_on": "2026-01-01"}, [], TODAY)["status"], "now")
        current = bonus(2)
        self.assertEqual(bb.eligibility(old, [old, current], TODAY)["status"], "in_progress")   # the same bank, still going
        self.assertEqual(bb.eligibility(bonus(3, bank="Citi"), [old], TODAY)["status"], "in_progress")
        sams = bonus(4, owner="Sam", status="closed")
        self.assertEqual(bb.eligibility(sams, [old, sams], TODAY)["status"], "now")


class DbTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        path = os.path.join(self.tmp.name, "t.db")
        db.init(path)
        self.c = db.connect(path)
        self.c.execute("INSERT INTO accounts(id, name, kind, balance) VALUES ('chk', 'Chase Total', 'checking', 1600)")
        self.c.execute("INSERT INTO accounts(id, name, kind) VALUES ('cc', 'Card', 'credit')")

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def test_overview_upcoming_income_and_alerts(self):
        a = bb.save(self.c, {"owner": "Alex", "bank": "Chase", "opened_on": "2026-08-01", "bonus": 300, "account_id": "chk",
                             "dd_total": 500, "monthly_fee": 12, "fee_waiver": "$500 direct deposit a month", "keep_open_days": 180,
                             "early_close_fee": 25})
        bb.save(self.c, {"owner": "Sam", "bank": "SoFi", "account_type": "savings", "opened_on": "2025-01-10", "bonus": 250,
                         "received_on": "2025-03-01", "received_amount": 275, "repeat_months": 24})
        bb.save(self.c, {"owner": "Sam", "bank": "Citi", "opened_on": "2025-12-01", "bonus": 500, "status": "closed",
                         "received_on": "2026-02-01"})
        self.c.execute("INSERT INTO transactions(id, account_id, posted, amount, category) VALUES ('t1', 'chk', '2026-08-15', 300, 'Income')")
        out = churning.overview(self.c, TODAY)
        self.assertEqual(out["people"], ["Alex", "Sam"])
        self.assertEqual(out["five24"]["Sam"]["count"], 0)
        chase = out["bank"][0]
        self.assertEqual((chase["state"], chase["progress"]["dd_total"], chase["due"]), ("active", 300, "2026-10-30"))
        self.assertEqual(chase["safe_close_on"], "2027-01-28")   # 180 days, and after it posts (Dec 29)
        sofi = out["bank"][1]
        self.assertEqual((sofi["state"], sofi["status"], sofi["eligibility"]["on"]), ("received", "received", "2027-03-01"))
        self.assertEqual(out["bank_income"], {"Sam": {"2025": 275.0, "2026": 500.0}})
        kinds = [(u["date"], u["kind"]) for u in out["upcoming"]]
        self.assertEqual(kinds, [("2026-10-01", "bank_fee"), ("2026-10-30", "bank_due"), ("2026-12-29", "bank_post"),
                                 ("2027-01-28", "bank_close"), ("2027-03-01", "bank_eligible")])
        due = out["upcoming"][1]
        self.assertEqual((due["detail"], due["warn"]), ("$200 more direct deposits", False))
        p = {**notify.DEFAULTS, "card_due": False, "low_balance": False, "missed": False, "big_charge": False, "sync_failed": False}
        self.assertEqual([x["key"] for x in notify.alerts(self.c, date(2026, 10, 20), p)], [f"bankbonus:{a}:2026-10-30"])
        self.assertEqual(notify.alerts(self.c, date(2026, 10, 20), {**p, "churn_bonus": False}), [])
        bb.remove(self.c, a)
        self.assertEqual(len(churning.overview(self.c, TODAY)["bank"]), 2)

    def test_validation(self):
        base = {"owner": "Alex", "bank": "Chase", "opened_on": "2026-08-01", "bonus": 300}
        for bad, msg in (({"bank": ""}, "bank"), ({"bonus": ""}, "bonus"), ({"account_type": "cd"}, "checking"),
                         ({"account_id": "cc"}, "checking or savings"), ({"status": "gone"}, "Status"),
                         ({"dd_count": 1.5}, "whole"), ({"received_on": "2026-01-01"}, "before"),
                         ({"closed_on": "2026-01-01"}, "before"), ({"hold_until": "later"}, "date"), ({"owner": "Joint"}, "one person")):
            with self.subTest(bad=bad), self.assertRaisesRegex(ChurnError, msg):
                bb.save(self.c, {**base, **bad})
        bid = bb.save(self.c, base)
        row = self.c.execute("SELECT account_type, deadline_days, post_days, monthly_fee, status, once_per_lifetime "
                             "FROM churn_bank_bonuses WHERE id=?", (bid,)).fetchone()
        self.assertEqual(tuple(row), ("checking", 90, 60, 0.0, "open", 0))
        bb.save(self.c, {"received_on": "2026-10-01"}, bid)   # a day it posted: received
        self.assertEqual(self.c.execute("SELECT status FROM churn_bank_bonuses WHERE id=?", (bid,)).fetchone()[0], "received")
        with self.assertRaisesRegex(ChurnError, "not found"):
            bb.save(self.c, {"bank": "X"}, 999)


if __name__ == "__main__":
    unittest.main()
