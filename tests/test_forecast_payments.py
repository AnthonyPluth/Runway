"""The forecast's card payment modes."""

from datetime import date

from sqlalchemy import delete, insert, update

from runway.domain import forecast
from runway.storage.models import Account, Budget, CardStatement, Category, Override, Recurring, Transaction
from tests import forecast_support as fs
from tests.shared import TODAY, LedgerCase, add_tx


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
        self.tx("cc", "2026-09-22", 1500.0, "STORE REFUND", "Refunds")
        fc = forecast.build(self.conn, TODAY, 90)
        c = self.card(fc)
        self.assertEqual((c["pay_mode"], c["remaining"], c["new_charges"], c["credit"], c["payment"], c["carried"]),
                         ("full", 0.0, 0.0, 600.0, 0.0, 0.0))
        s1 = -600 + self.INSURANCE
        self.assertEqual(self.payments(fc), {"2026-12-07": round(s1 + self.EST2, 2)})
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
        s1 = -100 + self.INSURANCE + 300 + 500 / 31 * 10
        p = self.payments(fc)
        self.assertEqual(set(p), {"2026-11-05", "2026-12-07"})
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


class RemainingDueTests(LedgerCase):
    """A closed statement's payment is what's left of it: payments and posted credits since the close come off it, new
    charges don't add to it, and one with nothing left isn't paid. The card's statement is $800 with $200 paid and $300
    charged since it closed (forecast_support.card_setup)."""
    card_setup = fs.card_setup
    pay = fs.pay

    def setUp(self):
        super().setUp()
        self.card_setup()

    def card(self, fc):
        return next(c for c in fc["cards"] if c["id"] == "cc")

    def payments(self, fc):
        return {e["date"]: -e["amount"] for e in fc["events"] if e["kind"] == "card"}

    def test_a_refund_after_the_close_comes_off_the_payment(self):
        self.tx("cc", "2026-09-21", 150.0, "STORE REFUND", "Refunds")
        fc = forecast.build(self.conn, TODAY, 90)
        c = self.card(fc)
        self.assertEqual((c["paid_since_close"], c["credits_since_close"], c["remaining"], c["payment"], c["carried"]),
                         (200.0, 150.0, 450.0, 450.0, 0.0))
        self.assertEqual((c["new_charges"], c["credit"]), (300.0, 0.0))
        self.assertEqual(self.payments(fc), {"2026-10-05": 450.0, "2026-11-05": 300.0})

    def test_paid_off_by_payments_and_credits_leaves_no_payment(self):
        self.tx("cc", "2026-09-18", 100.10, "STORE REFUND", "Refunds")
        self.tx("cc", "2026-09-19", 499.90, "STATEMENT CREDIT", "Travel")
        fc = forecast.build(self.conn, TODAY, 90)
        c = self.card(fc)
        self.assertEqual((c["remaining"], c["payment"], c["carried"], c["credit"]), (0.0, 0.0, 0.0, 0.0))
        self.assertEqual(c["new_charges"], 300.0)
        self.assertEqual(self.payments(fc), {"2026-11-05": 300.0})
        self.assertFalse(any("no payment has shown up" in w for w in fc["warnings"]))

    def test_a_cent_left_is_still_paid(self):
        self.tx("cc", "2026-09-18", 599.99, "STORE REFUND", "Refunds")
        self.assertEqual(self.payments(forecast.build(self.conn, TODAY, 90))["2026-10-05"], 0.01)

    def test_new_charges_dont_add_to_it(self):
        self.tx("cc", "2026-09-22", -1000.0, "TV", "Shopping")
        fc = forecast.build(self.conn, TODAY, 90)
        self.assertEqual((self.card(fc)["remaining"], self.card(fc)["payment"]), (600.0, 600.0))
        self.assertEqual(self.payments(fc), {"2026-10-05": 600.0, "2026-11-05": 1300.0})

    def test_a_pending_credit_waits_until_it_posts(self):
        self.tx("cc", "2026-09-22", 150.0, "STORE REFUND", "Refunds", pending=1)
        fc = forecast.build(self.conn, TODAY, 90)
        c = self.card(fc)
        self.assertEqual((c["credits_since_close"], c["remaining"], c["payment"], c["new_charges"]), (0.0, 600.0, 600.0, 150.0))
        self.assertEqual(self.payments(fc), {"2026-10-05": 600.0, "2026-11-05": 150.0})

    def test_a_minimum_or_fixed_payment_is_never_more_than_whats_left(self):
        self.stmt("cc", 800.0, "2026-09-10", "2026-10-05", minimum=250.0)
        self.pay("minimum")
        self.tx("cc", "2026-09-18", 500.0, "STORE REFUND", "Refunds")
        c = self.card(forecast.build(self.conn, TODAY, 90))
        self.assertEqual((c["remaining"], c["payment"], c["carried"]), (100.0, 50.0, 50.0))
        self.tx("cc", "2026-09-19", 80.0, "STORE REFUND", "Refunds")
        c = self.card(forecast.build(self.conn, TODAY, 90))
        self.assertEqual((c["remaining"], c["payment"], c["carried"]), (20.0, 20.0, 0.0))
        self.pay("fixed", amount="300")
        c = self.card(forecast.build(self.conn, TODAY, 90))
        self.assertEqual((c["remaining"], c["payment"], c["carried"]), (20.0, 20.0, 0.0))

    def test_a_statement_you_entered_comes_down_the_same_way(self):
        self.conn.execute(insert(Override).values(key="stmt:cc:2026-09-10", amount=-700.0))
        self.tx("cc", "2026-09-18", 250.0, "STORE REFUND", "Refunds")
        c = self.card(forecast.build(self.conn, TODAY, 90))
        self.assertEqual((c["statement_balance"], c["remaining"], c["payment"]), (700.0, 250.0, 250.0))


class PendingAtCloseTests(LedgerCase):
    """A charge still pending when its card's statement closed, dated on or before the close: the issuer's statement has
    only what had posted, so it isn't on it; it posts after the close, onto the next statement. The card's statement
    closed Oct 9 at $1,000, paid Nov 6; the next one closes Nov 9, paid Dec 7 (Dec 6 is a Sunday). A $2,500 charge dated
    Oct 8 and a $40 one dated Oct 9 are pending (through Plaid, which drops a hold the bank drops)."""
    pay = fs.pay
    TODAY = date(2026, 10, 11)

    def setUp(self):
        super().setUp()
        self.acct("chk", "checking", 10000.0)
        self.acct("cc", "credit", -1000.0, pay_from="chk")
        self.stmt("cc", 1000.0, "2026-10-09", "2026-11-06", minimum=40.0)
        self.tx("cc", "2026-10-02", -1000.0, "FURNITURE", "Shopping")
        self.hotel = add_tx(self.conn, "cc", "2026-10-08", -2500.0, id="cc|pl:hold-1", description="SEASIDE RESORT",
                            category="Travel", pending=1)
        self.diner = add_tx(self.conn, "cc", "2026-10-09", -40.0, id="cc|pl:hold-2", description="CORNER DINER",
                            category="Restaurants", pending=1)

    def build(self, today=None):
        return forecast.build(self.conn, today or self.TODAY, 90)

    def card(self, fc):
        return next(c for c in fc["cards"] if c["id"] == "cc")

    def payments(self, fc):
        return {e["date"]: -e["amount"] for e in fc["events"] if e["kind"] == "card"}

    def estimate(self, fc, day):
        return next(e["estimate"] for e in fc["events"] if e["kind"] == "card" and e["date"] == day)

    def post(self, tid, posted, amount, desc, category):
        """The bank posts a pending charge: Plaid sends the posted one, which replaces it."""
        self.conn.execute(delete(Transaction).where(Transaction.id == tid))
        self.tx("cc", posted, amount, desc, category)

    def test_a_charge_pending_at_the_close_is_on_the_next_statement(self):
        fc = self.build()
        c = self.card(fc)
        self.assertEqual((c["statement_balance"], c["payment"], c["new_charges"], c["pending_before_close"]),
                         (1000.0, 1000.0, 2540.0, 2540.0))
        self.assertEqual(self.payments(fc), {"2026-11-06": 1000.0, "2026-12-07": 2540.0})
        est = self.estimate(fc, "2026-12-07")
        self.assertEqual((est["close"], est["charged_so_far"], est["pending_before_close"], est["statement"], est["total"]),
                         ("2026-11-09", 0.0, 2540.0, 2540.0, 2540.0))

    def test_it_counts_once_with_whats_charged_since(self):
        self.tx("cc", "2026-10-10", -60.0, "GROCER", "Groceries")
        fc = self.build()
        self.assertEqual(self.payments(fc), {"2026-11-06": 1000.0, "2026-12-07": 2600.0})
        est = self.estimate(fc, "2026-12-07")
        self.assertEqual((est["charged_so_far"], est["pending_before_close"], est["statement"]), (60.0, 2540.0, 2600.0))

    def test_once_it_posts_its_a_charge_since_the_close(self):
        self.post(self.hotel, "2026-10-12", -2500.0, "SEASIDE RESORT", "Travel")
        fc = self.build(date(2026, 10, 13))
        c = self.card(fc)
        self.assertEqual((c["new_charges"], c["pending_before_close"]), (2540.0, 40.0))
        self.assertEqual(self.payments(fc), {"2026-11-06": 1000.0, "2026-12-07": 2540.0})
        est = self.estimate(fc, "2026-12-07")
        self.assertEqual((est["charged_so_far"], est["pending_before_close"]), (2500.0, 40.0))
        self.post(self.diner, "2026-10-12", -40.0, "CORNER DINER", "Restaurants")
        fc = self.build(date(2026, 10, 13))
        self.assertEqual(self.payments(fc), {"2026-11-06": 1000.0, "2026-12-07": 2540.0})
        self.assertNotIn("pending_before_close", self.estimate(fc, "2026-12-07"))

    def test_not_again_once_the_issuers_next_statement_has_it(self):
        self.post(self.hotel, "2026-10-12", -2500.0, "SEASIDE RESORT", "Travel")
        self.post(self.diner, "2026-10-12", -40.0, "CORNER DINER", "Restaurants")
        self.tx("cc", "2026-11-05", 1000.0, "PAYMENT THANK YOU", "Credit Card Payment")
        self.stmt("cc", 2540.0, "2026-11-09", "2026-12-06", minimum=50.0)
        fc = self.build(date(2026, 11, 10))
        c = self.card(fc)
        self.assertEqual((c["statement_balance"], c["payment"], c["new_charges"], c["pending_before_close"]),
                         (2540.0, 2540.0, 0.0, 0.0))
        self.assertEqual(self.payments(fc), {"2026-12-07": 2540.0})

    def test_a_charge_posted_by_the_close_is_in_the_statement_already(self):
        for tid in (self.hotel, self.diner):
            self.conn.execute(update(Transaction).where(Transaction.id == tid).values(pending=0))
        self.stmt("cc", 3540.0, "2026-10-09", "2026-11-06", minimum=70.0)
        fc = self.build()
        c = self.card(fc)
        self.assertEqual((c["payment"], c["new_charges"], c["pending_before_close"]), (3540.0, 0.0, 0.0))
        self.assertEqual(self.payments(fc), {"2026-11-06": 3540.0})

    def test_a_hold_the_bank_drops_drops_out(self):
        self.conn.execute(delete(Transaction).where(Transaction.id == self.hotel))
        fc = self.build()
        self.assertEqual(self.payments(fc), {"2026-11-06": 1000.0, "2026-12-07": 40.0})
        self.conn.execute(delete(Transaction).where(Transaction.id == self.diner))
        fc = self.build()
        self.assertEqual((self.card(fc)["new_charges"], self.card(fc)["pending_before_close"]), (0.0, 0.0))
        self.assertEqual(self.payments(fc), {"2026-11-06": 1000.0})

    def test_an_old_simplefin_hold_isnt_believed(self):
        # SimpleFIN doesn't re-read a pending row older than its refresh window: one that dropped off would linger.
        add_tx(self.conn, "cc", "2026-09-20", -700.0, description="OLD HOLD", category="Shopping", pending=1)
        self.assertEqual(self.card(self.build())["pending_before_close"], 2540.0)

    def test_a_simplefin_hold_left_on_a_card_that_moved_to_plaid_isnt_believed(self):
        # Nothing refreshes it any more, so one that posted under Plaid's id would be counted twice.
        self.conn.execute(update(Account).where(Account.id == "cc").values(provider="plaid"))
        add_tx(self.conn, "cc", "2026-10-08", -700.0, description="OLD HOLD", category="Shopping", pending=1)
        self.post(self.hotel, "2026-10-12", -2500.0, "SEASIDE RESORT", "Travel")
        c = self.card(self.build(date(2026, 10, 13)))
        self.assertEqual((c["new_charges"], c["pending_before_close"]), (2540.0, 40.0))

    def test_a_pending_credit_at_the_close_isnt_taken_off(self):
        add_tx(self.conn, "cc", "2026-10-09", 300.0, id="cc|pl:refund-1", description="STORE REFUND", category="Refunds", pending=1)
        add_tx(self.conn, "cc", "2026-10-09", 500.0, id="cc|pl:pay-1", description="PAYMENT THANK YOU",
               category="Credit Card Payment", pending=1)
        fc = self.build()
        c = self.card(fc)
        self.assertEqual((c["remaining"], c["new_charges"], c["pending_before_close"]), (1000.0, 2540.0, 2540.0))

    def test_a_card_with_only_posted_charges_is_as_before(self):
        for tid in (self.hotel, self.diner):
            self.conn.execute(delete(Transaction).where(Transaction.id == tid))
        self.tx("cc", "2026-10-10", -75.0, "GROCER", "Groceries")
        fc = self.build()
        self.assertEqual((self.card(fc)["new_charges"], self.card(fc)["pending_before_close"]), (75.0, 0.0))
        self.assertEqual(self.payments(fc), {"2026-11-06": 1000.0, "2026-12-07": 75.0})
        self.assertNotIn("pending_before_close", self.estimate(fc, "2026-12-07"))

    def test_a_statement_you_entered_the_same_way(self):
        self.conn.execute(delete(CardStatement))
        self.conn.execute(update(Account).where(Account.id == "cc").values(plaid_account_id=None))
        self.manual("cc", 1000.0, "2026-10-09", "2026-11-06")
        fc = self.build()
        c = self.card(fc)
        self.assertEqual((c["statement_source"], c["payment"], c["pending_before_close"]), ("manual", 1000.0, 2540.0))
        self.assertEqual(self.payments(fc), {"2026-11-06": 1000.0, "2026-12-07": 2540.0})

    def test_paid_the_minimum_its_in_the_next_statement(self):
        self.pay("minimum", apr="24")
        fc = self.build()
        carried = 1000.0 - 40.0
        interest = (carried + 2540.0 / 2) * 0.02
        statement = carried + interest + 2540.0
        est = self.estimate(fc, "2026-12-07")
        self.assertEqual((est["pending_before_close"], est["carried"]), (2540.0, carried))
        self.assertAlmostEqual(est["statement"], statement, delta=0.01)
        self.assertEqual(self.payments(fc)["2026-12-07"], fs.minimum(statement, interest))
