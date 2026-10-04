"""The forecast's card payment modes."""

from sqlalchemy import delete, insert, update

from runway.domain import forecast
from runway.storage.models import Budget, CardStatement, Category, Override, Recurring, Transaction
from tests import forecast_support as fs
from tests.shared import TODAY, LedgerCase


class PaymentModeTests(LedgerCase):
    """Cards paid in full, the minimum or a fixed amount, with what isn't paid carried to the next statement."""
    card_setup = fs.card_setup
    no_writes = fs.no_writes
    pay = fs.pay
    minimum = staticmethod(fs.minimum)
    INSURANCE = 450.0
    EST1 = 300 + INSURANCE
    EST2 = INSURANCE

    def setUp(self):
        super().setUp()
        self.card_setup()
        self.conn.execute(insert(Recurring).values(name="Insurance", account_id="cc", amount=-self.INSURANCE,
                                                   frequency="monthly", anchor_date="2026-09-25"))

    def payments(self, fc):
        return {e["date"]: -e["amount"] for e in fc["events"] if e["kind"] == "card"}

    def card(self, fc):
        return next(c for c in fc["cards"] if c["id"] == "cc")

    def interest_warned(self, fc):
        return any("doesn’t count the interest" in w for w in fc["warnings"])

    def test_paid_in_full_by_default(self):
        fc = forecast.build(self.conn, TODAY, 90)
        c = self.card(fc)
        self.assertEqual((c["pay_mode"], c["payment"], c["carried"]), ("full", 600.0, 0.0))
        self.assertEqual(self.payments(fc), {"2026-10-05": 600.0, "2026-11-05": round(self.EST1, 2), "2026-12-07": self.EST2})
        self.assertFalse(self.interest_warned(fc))
        self.pay("revolve")
        self.assertEqual(self.card(forecast.build(self.conn, TODAY, 90))["pay_mode"], "full")

    def test_minimum_payment_carries_the_rest_across_two_cycles(self):
        self.stmt("cc", 800.0, "2026-09-10", "2026-10-05", minimum=250.0)
        self.pay("minimum")
        fc = forecast.build(self.conn, TODAY, 90)
        c = self.card(fc)
        self.assertEqual((c["pay_mode"], c["remaining"], c["payment"], c["carried"], c["minimum_estimated"]),
                         ("minimum", 600.0, 50.0, 550.0, False))
        s1 = 550 + self.EST1
        s2 = s1 - self.minimum(s1) + self.EST2
        self.assertEqual(self.payments(fc), {"2026-10-05": 50.0, "2026-11-05": self.minimum(s1), "2026-12-07": self.minimum(s2)})
        self.assertGreater(s2, s1)
        self.assertTrue(all(e["estimated"] for e in fc["events"] if e["kind"] == "card" and e["date"] > "2026-10-05"))
        self.assertTrue(self.interest_warned(fc))
        self.assertIn({"text": next(w for w in fc["warnings"] if "interest" in w), "href": "#setup/accounts", "setting": True}, fc["warning_links"])
        self.stmt("cc", 800.0, "2026-09-10", "2026-10-05", minimum=40.0)
        c = self.card(fc := forecast.build(self.conn, TODAY, 90))
        self.assertEqual((c["payment"], c["carried"]), (0.0, 600.0))
        self.assertNotIn("2026-10-05", self.payments(fc))

    def test_interest_on_the_carried_balance(self):
        self.stmt("cc", 800.0, "2026-09-10", "2026-10-05", minimum=250.0)
        self.pay("minimum", apr="24")
        fc = forecast.build(self.conn, TODAY, 90)
        i1 = (550 + self.EST1 / 2) * 0.02
        s1 = 550 + i1 + self.EST1
        p1 = self.minimum(s1, i1)
        i2 = (s1 - p1 + self.EST2 / 2) * 0.02
        s2 = s1 - p1 + i2 + self.EST2
        self.assertEqual(self.payments(fc), {"2026-10-05": 50.0, "2026-11-05": p1, "2026-12-07": self.minimum(s2, i2)})
        self.assertEqual(p1, round(s1 * 0.01 + i1, 2))
        self.assertFalse(self.interest_warned(fc))
        self.assertEqual((self.card(fc)["apr"], self.card(fc)["apr_source"]), (24.0, "you"))
        self.assertEqual(forecast.interest({"apr": 24.0}, 0.0, 1000.0), 0.0)
        self.assertEqual(forecast.interest({"apr": 24.0}, -50.0, 1000.0), 0.0)
        self.assertAlmostEqual(forecast.interest({"apr": 24.0}, 100.0, 1000.0), (100 + 500) * 0.02)
        self.pay("minimum", apr="0")
        fc = forecast.build(self.conn, TODAY, 90)
        self.assertEqual(self.payments(fc)["2026-11-05"], self.minimum(550 + self.EST1))
        self.assertFalse(self.interest_warned(fc))

    def test_the_issuers_apr_unless_you_entered_one(self):
        self.conn.execute(update(CardStatement).where(CardStatement.plaid_account_id == "p-cc").values(purchase_apr=29.99))
        self.pay("minimum")
        c = self.card(fc := forecast.build(self.conn, TODAY, 90))
        self.assertEqual((c["apr"], c["apr_source"]), (29.99, "issuer"))
        self.assertFalse(self.interest_warned(fc))
        self.pay("minimum", apr="18")
        self.assertEqual((self.card(forecast.build(self.conn, TODAY, 90))["apr"], self.card(forecast.build(self.conn, TODAY, 90))["apr_source"]),
                         (18.0, "you"))
        self.conn.execute(update(CardStatement).where(CardStatement.plaid_account_id == "p-cc").values(purchase_apr=None))
        self.pay("minimum")
        c = self.card(fc := forecast.build(self.conn, TODAY, 90))
        self.assertEqual((c["apr"], c["apr_source"]), (None, None))
        self.assertTrue(self.interest_warned(fc))
        self.assertEqual(forecast.payment_plan(self.conn, "cc", 21.5)["apr_source"], "issuer")

    def test_minimum_without_one_from_the_bank(self):
        self.conn.execute(delete(Transaction).where(Transaction.description == "PAYMENT THANK YOU"))
        self.stmt("cc", 2000.0, "2026-09-10", "2026-10-05")
        self.pay("minimum")
        fc = forecast.build(self.conn, TODAY, 90)
        c = self.card(fc)
        self.assertEqual((c["payment"], c["carried"], c["minimum_estimated"]), (25.0, 1975.0, True))
        self.assertIn("cc: the bank didn’t report a minimum payment, so the forecast pays the larger of $25 and 1% of the "
                      "statement plus its interest.", fc["warnings"])
        plan = {"pay_mode": "minimum", "pay_amount": None, "apr": None}
        self.assertEqual([forecast.statement_payment(plan, s) for s in (10.0, 300.0, 2000.0, 5000.0)], [10.0, 25.0, 25.0, 50.0])
        self.assertEqual(forecast.statement_payment(plan, 5000.0, charged=5000 * 0.30 / 12), 175.0)
        self.assertEqual(forecast.statement_payment(plan, 300.0, 0.0), 0.0)

    def test_fixed_amount(self):
        self.pay("fixed", amount="300")
        fc = forecast.build(self.conn, TODAY, 90)
        c = self.card(fc)
        self.assertEqual((c["pay_mode"], c["pay_amount"], c["payment"], c["carried"]), ("fixed", 300.0, 100.0, 500.0))
        self.assertEqual(self.payments(fc), {"2026-10-05": 100.0, "2026-11-05": 300.0, "2026-12-07": 300.0})
        self.assertTrue(self.interest_warned(fc))
        self.pay("fixed", amount="5000")
        fc = forecast.build(self.conn, TODAY, 90)
        self.assertEqual((self.card(fc)["payment"], self.card(fc)["carried"]), (600.0, 0.0))
        self.assertEqual(self.payments(fc), {"2026-10-05": 600.0, "2026-11-05": round(self.EST1, 2), "2026-12-07": self.EST2})
        self.assertFalse(self.interest_warned(fc))
        self.pay("fixed")
        fc = forecast.build(self.conn, TODAY, 90)
        self.assertEqual(self.card(fc)["payment"], 600.0)
        self.assertIn("cc: no amount entered for its fixed payment, so the forecast pays each statement in full.", fc["warnings"])

    def test_an_edited_payment_sets_what_carries(self):
        self.stmt("cc", 800.0, "2026-09-10", "2026-10-05", minimum=250.0)
        self.pay("minimum")
        self.conn.execute(insert(Override).values(key="cardclose:cc:2026-09-10", amount=-500.0))
        fc = forecast.build(self.conn, TODAY, 90)
        self.assertEqual((self.card(fc)["payment"], self.card(fc)["carried"]), (500.0, 100.0))
        self.assertEqual(self.payments(fc)["2026-11-05"], 25.0)
        self.pay("full")
        fc = forecast.build(self.conn, TODAY, 90)
        self.assertEqual((self.card(fc)["payment"], self.card(fc)["carried"]), (600.0, 0.0))
        self.assertEqual(self.payments(fc)["2026-10-05"], 500.0)

    def test_a_credit_on_the_card_carries_into_the_next_statement(self):
        self.tx("cc", "2026-09-22", 1000.0, "STORE REFUND", "Refunds")
        fc = forecast.build(self.conn, TODAY, 90)
        c = self.card(fc)
        self.assertEqual((c["pay_mode"], c["new_charges"], c["credit"], c["payment"], c["carried"]), ("full", 0.0, 700.0, 600.0, 0.0))
        s1 = -700 + self.INSURANCE
        self.assertEqual(self.payments(fc), {"2026-10-05": 600.0, "2026-12-07": round(s1 + self.EST2, 2)})
        self.assertTrue(all(e["amount"] < 0 for e in fc["events"] if e["kind"] == "card"))
        self.assertFalse(self.interest_warned(fc))
        self.pay("fixed", amount="5000")
        self.conn.execute(insert(Override).values(key="cardclose:cc:2026-10-10", amount=-50.0))
        fc = forecast.build(self.conn, TODAY, 90)
        self.assertNotIn("2026-11-05", self.payments(fc))
        self.assertEqual(self.payments(fc)["2026-12-07"], round(s1 + self.EST2, 2))


    def test_a_credit_carries_with_budgets(self):
        self.tx("cc", "2026-09-22", 1000.0, "STORE REFUND", "Refunds")
        self.conn.execute(insert(Budget).values(category="Groceries", amount=500))
        self.conn.execute(update(Category).where(Category.name == "Groceries").values(pay_with="cc"))
        fc = forecast.build(self.conn, TODAY, 90)
        s1 = -700 + self.INSURANCE + 300 + 500 / 31 * 10
        p = self.payments(fc)
        self.assertEqual(set(p), {"2026-10-05", "2026-11-05", "2026-12-07"})
        self.assertEqual(p["2026-10-05"], 600.0)
        self.assertAlmostEqual(p["2026-11-05"], s1, delta=0.01)
        self.assertAlmostEqual(p["2026-12-07"], self.INSURANCE + 500 / 31 * 21 + 500 / 30 * 10, delta=0.01)

    def test_with_budgets_a_cards_statements_are_its_budgets_plus_its_charges(self):
        self.conn.execute(insert(Budget).values(category="Groceries", amount=500))
        self.conn.execute(update(Category).where(Category.name == "Groceries").values(pay_with="cc"))
        fc = forecast.build(self.conn, TODAY, 90)
        oct10 = 300 + self.INSURANCE + 300 + 500 / 31 * 10
        nov10 = self.INSURANCE + 500 / 31 * 21 + 500 / 30 * 10
        p = self.payments(fc)
        self.assertEqual(set(p), {"2026-10-05", "2026-11-05", "2026-12-07"})
        self.assertAlmostEqual(p["2026-11-05"], oct10, delta=0.01)
        self.assertAlmostEqual(p["2026-12-07"], nov10, delta=0.01)
        est = [e for e in fc["events"] if e["kind"] == "card" and e["estimated"]]
        self.assertTrue(est and all(e["key"].startswith("cardclose:cc:") and not e.get("assumed_cycle") for e in est))
        self.conn.execute(update(Category).where(Category.name == "Groceries").values(pay_with="chk"))
        fc = forecast.build(self.conn, TODAY, 90)
        self.assertEqual(self.payments(fc), {"2026-10-05": 600.0, "2026-11-05": self.EST1, "2026-12-07": self.EST2})
        i = fc["dates"].index("2026-10-20")
        self.assertAlmostEqual(fc["total"][i - 1] - fc["total"][i], 500 / 31, delta=0.01)

    def test_with_budgets_a_recurring_charge_on_the_card_counts_once(self):
        self.tx("cc", "2025-10-20", -600.0, "STREAMFLIX YEARLY", "Subscriptions")
        self.conn.execute(insert(Recurring).values(name="Streamflix", account_id="cc", amount=-600, frequency="yearly",
                                                   anchor_date="2025-10-20", match="streamflix"))
        self.assertEqual(self.payments(forecast.build(self.conn, TODAY, 90))["2026-12-07"], self.EST2 + 600)
        self.conn.execute(insert(Budget).values(category="Groceries", amount=500))
        self.conn.execute(update(Category).where(Category.name == "Groceries").values(pay_with="cc"))
        nov10 = self.EST2 + 500 / 31 * 21 + 500 / 30 * 10
        self.assertAlmostEqual(self.payments(forecast.build(self.conn, TODAY, 90))["2026-12-07"], nov10 + 600, delta=0.01)
        self.conn.execute(insert(Budget).values(category="Subscriptions", amount=50))
        self.conn.execute(update(Category).where(Category.name == "Subscriptions").values(pay_with="chk"))
        fc = forecast.build(self.conn, TODAY, 90)
        self.assertAlmostEqual(self.payments(fc)["2026-12-07"], nov10 + 600, delta=0.01)
        self.assertEqual(fs.drop(self, fc, "2026-10-20"), 0.0)
        self.assertAlmostEqual(fs.drop(self, fc, "2026-11-17"), 50 / 30, delta=0.01)
        self.conn.execute(update(Category).where(Category.name == "Subscriptions").values(pay_with="cc"))
        self.assertAlmostEqual(self.payments(forecast.build(self.conn, TODAY, 90))["2026-12-07"],
                               nov10 + 50 / 31 * 21 + 50 / 30 * 10, delta=0.01)

    def test_with_budgets_a_carried_balance_is_charged_interest_on_the_budgeted_charges_too(self):
        self.conn.execute(insert(Budget).values(category="Groceries", amount=500))
        self.conn.execute(update(Category).where(Category.name == "Groceries").values(pay_with="cc"))
        self.pay("minimum", apr="24")
        fc = forecast.build(self.conn, TODAY, 90)
        oct10 = 300 + self.INSURANCE + 300 + 500 / 31 * 10
        interest = (600 + oct10 / 2) * 0.24 / 12
        self.assertAlmostEqual(self.payments(fc)["2026-11-05"], self.minimum(600 + interest + oct10, interest), delta=0.01)

    def test_paying_the_minimum_writes_nothing(self):
        self.stmt("cc", 800.0, "2026-09-10", "2026-10-05", minimum=250.0)
        self.pay("minimum", apr="24")
        self.conn.execute(insert(Override).values(key="cardclose:cc:2026-09-10", amount=-300.0))
        self.conn.execute(insert(Budget).values(category="Groceries", amount=500))
        self.conn.execute(update(Category).where(Category.name == "Groceries").values(pay_with="cc"))
        self.no_writes(AssertionError("the forecast wrote to the database"))
        fc = forecast.build(self.conn, TODAY, 90)
        self.assertEqual((self.card(fc)["pay_mode"], self.card(fc)["payment"]), ("minimum", 300.0))

    def test_a_payment_edited_to_nothing_stays_on_the_overview(self):
        self.stmt("cc", 800.0, "2026-09-10", "2026-10-05", minimum=250.0)
        self.pay("minimum")
        self.conn.execute(insert(Override).values(key="cardclose:cc:2026-09-10", amount=0.0))
        fc = forecast.build(self.conn, TODAY, 90)
        e = next(e for e in fc["events"] if e["key"] == "cardclose:cc:2026-09-10")
        self.assertEqual((e["amount"], e["overridden"], e["original_amount"]), (0.0, True, -50.0))
        self.assertEqual((self.card(fc)["payment"], self.card(fc)["carried"]), (0.0, 600.0))
        self.assertEqual(self.payments(fc)["2026-11-05"], self.minimum(600 + self.EST1))
        self.conn.execute(insert(Override).values(key="cardclose:cc:2026-10-10", amount=0.0))
        fc = forecast.build(self.conn, TODAY, 90)
        e = next(e for e in fc["events"] if e["key"] == "cardclose:cc:2026-10-10")
        self.assertEqual((e["amount"], e["original_amount"]), (0.0, -self.minimum(600 + self.EST1)))
        self.assertEqual(self.payments(fc)["2026-12-07"], self.minimum(600 + self.EST1 + self.EST2))

    def test_an_edited_payment_above_the_statement_comes_off_the_next_one(self):
        self.stmt("cc", 800.0, "2026-09-10", "2026-10-05", minimum=250.0)
        self.pay("minimum")
        self.conn.execute(insert(Override).values(key="cardclose:cc:2026-09-10", amount=-1000.0))
        fc = forecast.build(self.conn, TODAY, 90)
        self.assertEqual((self.card(fc)["payment"], self.card(fc)["carried"]), (1000.0, -400.0))
        e = next(e for e in fc["events"] if e["key"] == "cardclose:cc:2026-09-10")
        self.assertEqual((e["amount"], e["overridden"], e["original_amount"]), (-1000.0, True, -50.0))
        payments = self.payments(fc)
        s1 = -400 + self.EST1
        self.assertEqual(payments["2026-10-05"], 1000.0)
        self.assertEqual(payments["2026-11-05"], 25.0)
        self.assertEqual(payments["2026-12-07"], self.minimum(s1 - 25 + self.EST2))
        self.assertTrue(all(e["amount"] < 0 for e in fc["events"] if e["kind"] == "card"))

    def test_budgeted_charges_are_paid_the_way_the_card_is_set(self):
        self.stmt("cc", 800.0, "2026-09-10", "2026-10-05", minimum=250.0)
        self.pay("minimum")
        self.conn.execute(insert(Budget).values(category="Groceries", amount=500))
        self.conn.execute(update(Category).where(Category.name == "Groceries").values(pay_with="cc"))
        fc = forecast.build(self.conn, TODAY, 90)
        payments = self.payments(fc)
        self.assertEqual(payments["2026-10-05"], 50.0)
        charged = 300 + self.INSURANCE + 300 + 500 / 31 * 10
        s1 = 550 + charged
        self.assertAlmostEqual(payments["2026-11-05"], self.minimum(s1), delta=0.01)
        s2 = s1 - self.minimum(s1) + self.INSURANCE + 500 / 31 * 21 + 500 / 30 * 10
        self.assertAlmostEqual(payments["2026-12-07"], self.minimum(s2), delta=0.01)
        self.pay("minimum", apr="24")
        fc = forecast.build(self.conn, TODAY, 90)
        i1 = (550 + charged / 2) * 0.02
        self.assertAlmostEqual(self.payments(fc)["2026-11-05"], self.minimum(550 + i1 + charged, i1), delta=0.01)
