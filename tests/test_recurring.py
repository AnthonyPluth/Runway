"""Bills & income: matching recurring payments and their amounts."""
import unittest
from datetime import date

from sqlalchemy import insert, select, update

from runway import forecast, recurring
from runway.models import Override, Recurring, Transaction
from tests.shared import TODAY, LedgerCase


class RecurringTests(LedgerCase):
    def setUp(self):
        super().setUp()
        self.acct("chk", "checking", 3000.0)
        # Electric bill: same merchant, different amount every month, plus a refund from them.
        for d, amt in [("2026-06-14", -91.20), ("2026-07-15", -143.77), ("2026-08-14", -160.02), ("2026-09-15", -118.40)]:
            self.tx("chk", d, amt, "COMED ELECTRIC PAYMENT 88812")
        self.tx("chk", "2026-09-02", 12.00, "COMED ELECTRIC REFUND")
        self.tx("chk", "2026-09-10", -60.0, "TARGET")
        self.conn.execute(insert(Recurring).values(name="Electric", account_id="chk", amount=-120, frequency="monthly",
                                                   anchor_date="2026-06-15", match="comed electric",
                                                   amount_mode="avg3"))
        self.rid = self.conn.execute(select(Recurring.id)).fetchone()[0]

    def test_auto_match_by_merchant_any_amount(self):
        n = recurring.auto_match(self.conn)
        self.assertEqual(n, 4)  # all four bills, not the refund, not Target
        linked = [r[0] for r in self.conn.execute(select(Transaction.amount)
                                                  .where(Transaction.recurring_id == self.rid)
                                                  .order_by(Transaction.posted))]
        self.assertEqual(linked, [-91.20, -143.77, -160.02, -118.40])

    def test_never_match_is_respected(self):
        recurring.link(self.conn, "chk|0", None)  # "not recurring"
        recurring.auto_match(self.conn)
        self.assertEqual(self.conn.execute(select(Transaction.recurring_id)
                                           .where(Transaction.id == "chk|0")).fetchone()[0], 0)

    def test_amount_modes_and_forecast(self):
        recurring.auto_match(self.conn)
        item = dict(self.conn.execute(select(Recurring)).fetchone())
        hist = recurring.matched(self.conn, self.rid)
        self.assertEqual(recurring.expected_amount(item, hist), round((-118.40 - 160.02 - 143.77) / 3, 2))
        self.assertEqual(recurring.expected_amount({**item, "amount_mode": "last"}, hist), -118.40)
        self.assertEqual(recurring.expected_amount({**item, "amount_mode": "fixed"}, hist), -120.0)
        fc = forecast.build(self.conn, TODAY, 60)
        elec = [e for e in fc["events"] if e.get("recurring_id") == self.rid]
        # Sep 15 already posted, so the next ones are Oct 15 and Nov 15 (a Sunday: Monday the 16th) at the 3-month average
        self.assertEqual([e["date"] for e in elec], ["2026-10-15", "2026-11-16"])
        self.assertEqual(elec[0]["amount"], round((-118.40 - 160.02 - 143.77) / 3, 2))

    def test_early_payment_not_counted_twice(self):
        self.tx("chk", "2026-10-12", -130.0, "COMED ELECTRIC PAYMENT")  # paid 3 days early
        recurring.auto_match(self.conn)
        fc = forecast.build(self.conn, date(2026, 10, 13), 40)
        dates = [e["date"] for e in fc["events"] if e.get("recurring_id") == self.rid]
        self.assertEqual(dates, ["2026-11-16"])   # Nov 15 is a Sunday

    def test_override_one_occurrence(self):
        recurring.auto_match(self.conn)
        self.conn.execute(insert(Override).values(key=f"rec:{self.rid}:2026-10-15", amount=-250.0))
        fc = forecast.build(self.conn, TODAY, 60)
        e = next(e for e in fc["events"] if e["date"] == "2026-10-15")
        self.assertEqual((e["amount"], e["overridden"]), (-250.0, True))
        self.assertAlmostEqual(fc["total"][-1], 3000 + sum(x["amount"] for x in fc["events"]), places=2)

    def test_create_from_transaction_and_link_teaches_match(self):
        self.tx("chk", "2026-09-05", -15.99, "NETFLIX.COM 8665797172")
        tid = self.conn.execute(select(Transaction.id).where(Transaction.description.like("NETFLIX%"))).fetchone()[0]
        rid = recurring.create_from_transaction(self.conn, tid, "monthly")
        item = self.conn.execute(select(Recurring).where(Recurring.id == rid)).fetchone()
        self.assertEqual((item["amount"], item["frequency"], item["match"]), (-15.99, "monthly", "netflix.com"))
        # an item without merchant text learns it from the first link
        self.conn.execute(insert(Recurring).values(name="Store", account_id="chk", amount=-60, frequency="monthly",
                                                   anchor_date="2026-09-10"))
        sid = self.conn.execute(select(Recurring.id).where(Recurring.name == "Store")).fetchone()[0]
        recurring.link(self.conn, self.conn.execute(select(Transaction.id)
                                                    .where(Transaction.description == "TARGET")).fetchone()[0], sid)
        self.assertEqual(self.conn.execute(select(Recurring.match).where(Recurring.id == sid)).fetchone()[0], "target")


class RecurringAmountTests(LedgerCase):
    def test_linking_one_charge_doesnt_link_the_whole_merchant(self):
        self.acct("cc", "credit", 0.0)
        self.tx("cc", "2026-09-01", -14.99, "AMAZON PRIME")
        for d, amt in (("2026-09-03", -86.40), ("2026-09-08", -5.29), ("2026-09-12", -212.00)):
            self.tx("cc", d, amt, "AMAZON")
        self.tx("cc", "2026-08-01", -14.99, "AMAZON")
        self.conn.execute(insert(Recurring).values(name="Prime", account_id="cc", amount=-14.99, frequency="monthly",
                                                   anchor_date="2026-08-01"))
        rid = self.conn.execute(select(Recurring.id)).fetchone()[0]
        recurring.link(self.conn, "cc|0", rid)   # learns "amazon prime"...
        self.conn.execute(update(Recurring).where(Recurring.id == rid).values(match="amazon"))   # ...or plain "amazon"
        recurring.auto_match(self.conn, [rid])
        linked = sorted(r[0] for r in self.conn.execute(select(Transaction.amount)
                                                        .where(Transaction.recurring_id == rid)))
        self.assertEqual(linked, [-14.99, -14.99])


if __name__ == "__main__":
    unittest.main()
