import unittest
from datetime import date

from sqlalchemy import func, select

from runway.storage import db
from runway.domain import demo, forecast, portfolio
from runway.domain import retail
from runway.storage.models import Account, Recurring, RetailItem, RetailOrder, Transaction
from tests.shared import DbCase


@unittest.skipIf(db.using_postgres(), "needs an empty database of its own")
class DemoTests(DbCase):
    def test_seeds_an_empty_database_once(self):
        n = demo.seed(self.c, today=date(2026, 9, 28))
        self.assertGreater(n, 100)
        self.assertEqual(self.c.execute(select(func.count()).select_from(Account)).fetchone()[0], 5)
        self.assertEqual(self.c.execute(select(func.count()).select_from(Recurring)).fetchone()[0], len(demo.BILLS))
        self.assertTrue(db.get_setting(self.c, "simplefin_access_url").endswith(".invalid/simplefin"))
        fc = forecast.build(self.c, date(2026, 9, 28), 30)
        self.assertEqual(fc["warnings"], ["Travel Mastercard: choose which account pays it in Settings."])   # one Overview can put away
        card = next(c for c in fc["cards"] if c["id"] == "demo-card")
        self.assertEqual((card["statement_source"], card["last_close"], card["due_date"], card["statement_stale"]),
                         ("manual", "2026-09-28", "2026-10-23", False))
        self.assertGreater(card["statement_balance"], 0)
        self.assertEqual((card["paid_since_close"], card["remaining"]), (0.0, card["statement_balance"]))
        with self.assertRaises(SystemExit):
            demo.seed(self.c)

    def test_the_ai_buttons_data_is_opt_in(self):
        def waiting():
            return self.c.execute(select(func.count()).select_from(Transaction).where(Transaction.needs_review == 1)).scalar()
        demo.seed(self.c, today=date(2026, 9, 28))
        self.assertIsNone(db.get_setting(self.c, "openrouter_api_key"))
        self.assertEqual(waiting(), 0)
        self.assertEqual(self.c.execute(select(func.count()).select_from(RetailItem)).scalar(), 0)
        demo.seed_ai_buttons(self.c, today=date(2026, 9, 28))
        self.assertEqual(db.get_setting(self.c, "openrouter_api_key"), "sk-or-demo-not-a-real-key")
        self.assertEqual(waiting(), 3)
        self.assertEqual(self.c.execute(select(func.count()).select_from(RetailItem).where(RetailItem.category.is_(None))).scalar(), 2)
        self.assertEqual(retail.token_owner(self.c), {"sub": "demo", "email": "demo@example.invalid"})

    def test_the_receipt_is_opt_in(self):
        demo.seed(self.c, today=date(2026, 10, 9))
        self.assertIsNone(self.c.execute(select(Transaction.id).where(Transaction.id == "demo-card|demo-receipt")).scalar())
        demo.seed_receipt(self.c, today=date(2026, 10, 9))
        items = self.c.execute(select(RetailItem.category, RetailItem.quantity).where(RetailItem.order_id == "target:900-0000-0000001")).fetchall()
        self.assertGreater(len(items), 5)
        self.assertEqual(sum(1 for category, _ in items if category is None), 1)
        self.assertTrue(any(qty > 1 for _, qty in items))
        tx = self.c.execute(select(Transaction.amount).where(Transaction.id == "demo-card|demo-receipt")).scalar()
        total, subtotal, tax = self.c.execute(select(RetailOrder.total, RetailOrder.subtotal, RetailOrder.tax)).fetchone()
        self.assertAlmostEqual(-tx, total, places=2)
        self.assertAlmostEqual(subtotal + tax, total, places=2)

    def test_the_brokerage_has_a_gain_a_loss_and_a_flat_day(self):
        demo.seed(self.c, today=date(2026, 10, 9))
        self.assertEqual(portfolio.holdings(self.c), [])   # opt-in, so tests built on the plain sample see no investments
        demo.seed_investments(self.c, date(2026, 10, 9))
        by = {h["ticker"]: h for h in portfolio.holdings(self.c)}
        self.assertEqual(set(by), {"DEMOTM", "DEMOIN", "DEMOSI"})
        self.assertGreater(by["DEMOTM"]["day_change"], 0)
        self.assertLess(by["DEMOIN"]["day_change"], 0)
        self.assertAlmostEqual(by["DEMOTM"]["day_change"], 42 * (284.95 - 281.40), places=2)
        self.assertAlmostEqual(by["DEMOIN"]["day_change_pct"], 67.55 / 68.20 - 1, places=5)
        self.assertLess(abs(by["DEMOSI"]["day_change_pct"]), 0.001)

    def test_the_card_has_a_part_payment_since_its_statement(self):
        demo.seed(self.c, today=date(2026, 10, 9))
        card = next(c for c in forecast.build(self.c, date(2026, 10, 9), 30)["cards"] if c["id"] == "demo-card")
        self.assertEqual((card["last_close"], card["paid_since_close"]), ("2026-09-28", demo.PART_PAYMENT))
        self.assertAlmostEqual(card["remaining"], card["statement_balance"] - demo.PART_PAYMENT, places=2)
        self.assertGreater(card["remaining"], 0)


if __name__ == "__main__":
    unittest.main()
