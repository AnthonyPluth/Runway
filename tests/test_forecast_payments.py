"""The forecast's card payment modes."""

from sqlalchemy import delete, insert, update

from runway import forecast
from runway.models import Budget, CardStatement, Category, Override, Recurring, Transaction
from tests import forecast_support as fs
from tests.shared import TODAY, LedgerCase


class PaymentModeTests(LedgerCase):
    """Cards paid in full, the minimum or a fixed amount, with what isn't paid carried to the next statement."""
    card_setup = fs.card_setup
    no_writes = fs.no_writes
    pay = fs.pay
    minimum = staticmethod(fs.minimum)
    INSURANCE = 450.0                # a recurring charge on the card on the 25th (Sep 25, Oct 26, the 25th a Sunday)
    EST1 = 300 + INSURANCE           # the cycle closing Oct 10: charged so far plus Sep 25's insurance
    EST2 = INSURANCE                 # the one closing Nov 10: Oct 26's insurance

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
        # an unknown mode in settings reads as full
        self.pay("revolve")
        self.assertEqual(self.card(forecast.build(self.conn, TODAY, 90))["pay_mode"], "full")

    def test_minimum_payment_carries_the_rest_across_two_cycles(self):
        # The issuer's minimum is $250; the $200 paid since the close counts toward it, so $50 is left to pay.
        self.stmt("cc", 800.0, "2026-09-10", "2026-10-05", minimum=250.0)
        self.pay("minimum")
        fc = forecast.build(self.conn, TODAY, 90)
        c = self.card(fc)
        self.assertEqual((c["pay_mode"], c["remaining"], c["payment"], c["carried"], c["minimum_estimated"]),
                         ("minimum", 600.0, 50.0, 550.0, False))
        s1 = 550 + self.EST1                 # Oct 10: the $550 carried plus the cycle's charges (no APR: no interest)
        s2 = s1 - self.minimum(s1) + self.EST2   # Nov 10: what the minimum left, plus that cycle's charges
        self.assertEqual(self.payments(fc), {"2026-10-05": 50.0, "2026-11-05": self.minimum(s1), "2026-12-07": self.minimum(s2)})
        self.assertGreater(s2, s1)   # paying the minimum, the balance grows
        self.assertTrue(all(e["estimated"] for e in fc["events"] if e["kind"] == "card" and e["date"] > "2026-10-05"))
        self.assertTrue(self.interest_warned(fc))
        self.assertIn({"text": next(w for w in fc["warnings"] if "interest" in w), "href": "#setup/accounts", "setting": True}, fc["warning_links"])
        # the $40 minimum is paid already: nothing goes out on Oct 5, and all $600 carries
        self.stmt("cc", 800.0, "2026-09-10", "2026-10-05", minimum=40.0)
        c = self.card(fc := forecast.build(self.conn, TODAY, 90))
        self.assertEqual((c["payment"], c["carried"]), (0.0, 600.0))
        self.assertNotIn("2026-10-05", self.payments(fc))

    def test_interest_on_the_carried_balance(self):
        self.stmt("cc", 800.0, "2026-09-10", "2026-10-05", minimum=250.0)
        self.pay("minimum", apr="24")
        fc = forecast.build(self.conn, TODAY, 90)
        # 24% a year, 2% a month: on what was carried, and (no grace period while a balance carries) on the cycle's new
        # charges, as if they posted evenly through it
        i1 = (550 + self.EST1 / 2) * 0.02
        s1 = 550 + i1 + self.EST1
        p1 = self.minimum(s1, i1)
        i2 = (s1 - p1 + self.EST2 / 2) * 0.02
        s2 = s1 - p1 + i2 + self.EST2
        self.assertEqual(self.payments(fc), {"2026-10-05": 50.0, "2026-11-05": p1, "2026-12-07": self.minimum(s2, i2)})
        self.assertEqual(p1, round(s1 * 0.01 + i1, 2))   # above the $25 floor: the interest is in it
        self.assertFalse(self.interest_warned(fc))
        self.assertEqual((self.card(fc)["apr"], self.card(fc)["apr_source"]), (24.0, "you"))
        # nothing carried, nothing charged on the new purchases: the grace period
        self.assertEqual(forecast.interest({"apr": 24.0}, 0.0, 1000.0), 0.0)
        self.assertEqual(forecast.interest({"apr": 24.0}, -50.0, 1000.0), 0.0)
        self.assertAlmostEqual(forecast.interest({"apr": 24.0}, 100.0, 1000.0), (100 + 500) * 0.02)
        # a 0% APR (a promotion) is an APR: no interest, and no warning
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
        # neither: no APR, and the warning
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
        self.assertEqual((c["payment"], c["carried"], c["minimum_estimated"]), (25.0, 1975.0, True))   # 1% of $2,000 is $20
        self.assertIn("cc: the bank didn’t report a minimum payment, so the forecast pays the larger of $25 and 1% of the "
                      "statement plus its interest.", fc["warnings"])
        plan = {"pay_mode": "minimum", "pay_amount": None, "apr": None}
        self.assertEqual([forecast.statement_payment(plan, s) for s in (10.0, 300.0, 2000.0, 5000.0)], [10.0, 25.0, 25.0, 50.0])
        # $5,000 at 30%: $125 of interest a month, which the minimum covers (2% alone would be $100, and the balance grows)
        self.assertEqual(forecast.statement_payment(plan, 5000.0, charged=5000 * 0.30 / 12), 175.0)
        self.assertEqual(forecast.statement_payment(plan, 300.0, 0.0), 0.0)   # the issuer says nothing is due

    def test_fixed_amount(self):
        self.pay("fixed", amount="300")
        fc = forecast.build(self.conn, TODAY, 90)
        c = self.card(fc)
        # $300 toward the $800 statement, $200 of it paid already
        self.assertEqual((c["pay_mode"], c["pay_amount"], c["payment"], c["carried"]), ("fixed", 300.0, 100.0, 500.0))
        self.assertEqual(self.payments(fc), {"2026-10-05": 100.0, "2026-11-05": 300.0, "2026-12-07": 300.0})
        self.assertTrue(self.interest_warned(fc))
        # never more than what's owed: a big fixed amount pays the statement off
        self.pay("fixed", amount="5000")
        fc = forecast.build(self.conn, TODAY, 90)
        self.assertEqual((self.card(fc)["payment"], self.card(fc)["carried"]), (600.0, 0.0))
        self.assertEqual(self.payments(fc), {"2026-10-05": 600.0, "2026-11-05": round(self.EST1, 2), "2026-12-07": self.EST2})
        self.assertFalse(self.interest_warned(fc))
        # without an amount, it's paid in full, and the forecast says why
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
        self.assertEqual(self.payments(fc)["2026-11-05"], 25.0)   # 1% of $100 + EST1 is under the $25 floor
        # paid in full, an edit changes that payment only, as before
        self.pay("full")
        fc = forecast.build(self.conn, TODAY, 90)
        self.assertEqual((self.card(fc)["payment"], self.card(fc)["carried"]), (600.0, 0.0))
        self.assertEqual(self.payments(fc)["2026-10-05"], 500.0)

    def test_a_credit_on_the_card_carries_into_the_next_statement(self):
        # A $1,000 refund since the close against $300 of charges: a $700 credit, even paying in full
        self.tx("cc", "2026-09-22", 1000.0, "STORE REFUND", "Refunds")
        fc = forecast.build(self.conn, TODAY, 90)
        c = self.card(fc)
        self.assertEqual((c["pay_mode"], c["new_charges"], c["credit"], c["payment"], c["carried"]), ("full", 0.0, 700.0, 600.0, 0.0))
        # Oct 10's statement comes out below zero (-700 + Sep 25's insurance): nothing to pay, and what's left of the
        # credit comes off Nov 10's
        s1 = -700 + self.INSURANCE
        self.assertEqual(self.payments(fc), {"2026-10-05": 600.0, "2026-12-07": round(s1 + self.EST2, 2)})
        self.assertTrue(all(e["amount"] < 0 for e in fc["events"] if e["kind"] == "card"))   # never a negative payment
        self.assertFalse(self.interest_warned(fc))   # a credit isn't a balance carried
        # not paying in full, an edit left from before on a statement that now has nothing to pay isn't a payment: no
        # event, and the credit is untouched
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
        # the credit, then Groceries' $300 left this month over its 7 days and $500 a month after, plus the insurance:
        # Oct 10's comes to $211.29
        s1 = -700 + self.INSURANCE + 300 + 500 / 31 * 10
        p = self.payments(fc)
        self.assertEqual(set(p), {"2026-10-05", "2026-11-05", "2026-12-07"})
        self.assertEqual(p["2026-10-05"], 600.0)
        self.assertAlmostEqual(p["2026-11-05"], s1, delta=0.01)
        self.assertAlmostEqual(p["2026-12-07"], self.INSURANCE + 500 / 31 * 21 + 500 / 30 * 10, delta=0.01)

    def test_with_budgets_a_cards_statements_are_its_budgets_plus_its_charges(self):
        # Groceries ($500 a month, $200 spent this month) is paid with the card, on top of what's on it since the close
        # and its insurance.
        self.conn.execute(insert(Budget).values(category="Groceries", amount=500))
        self.conn.execute(update(Category).where(Category.name == "Groceries").values(pay_with="cc"))
        fc = forecast.build(self.conn, TODAY, 90)
        oct10 = 300 + self.INSURANCE + 300 + 500 / 31 * 10    # on the card already, insurance, Groceries to Oct 10
        nov10 = self.INSURANCE + 500 / 31 * 21 + 500 / 30 * 10
        p = self.payments(fc)
        self.assertEqual(set(p), {"2026-10-05", "2026-11-05", "2026-12-07"})
        self.assertAlmostEqual(p["2026-11-05"], oct10, delta=0.01)
        self.assertAlmostEqual(p["2026-12-07"], nov10, delta=0.01)
        est = [e for e in fc["events"] if e["kind"] == "card" and e["estimated"]]
        self.assertTrue(est and all(e["key"].startswith("cardclose:cc:") and not e.get("assumed_cycle") for e in est))
        # A budget paid from checking isn't on the card: the card's statements are only its own charges, and checking
        # pays the budget day by day instead
        self.conn.execute(update(Category).where(Category.name == "Groceries").values(pay_with="chk"))
        fc = forecast.build(self.conn, TODAY, 90)
        self.assertEqual(self.payments(fc), {"2026-10-05": 600.0, "2026-11-05": self.EST1, "2026-12-07": self.EST2})
        i = fc["dates"].index("2026-10-20")
        self.assertAlmostEqual(fc["total"][i - 1] - fc["total"][i], 500 / 31, delta=0.01)

    def test_with_budgets_a_recurring_charge_on_the_card_counts_once(self):
        # A $600 yearly subscription on the card, due Oct 20
        self.tx("cc", "2025-10-20", -600.0, "STREAMFLIX YEARLY", "Subscriptions")
        self.conn.execute(insert(Recurring).values(name="Streamflix", account_id="cc", amount=-600, frequency="yearly",
                                                   anchor_date="2025-10-20", match="streamflix"))
        self.assertEqual(self.payments(forecast.build(self.conn, TODAY, 90))["2026-12-07"], self.EST2 + 600)
        # A budget for Groceries leaves it on the card, on top of the budgets ...
        self.conn.execute(insert(Budget).values(category="Groceries", amount=500))
        self.conn.execute(update(Category).where(Category.name == "Groceries").values(pay_with="cc"))
        nov10 = self.EST2 + 500 / 31 * 21 + 500 / 30 * 10
        self.assertAlmostEqual(self.payments(forecast.build(self.conn, TODAY, 90))["2026-12-07"], nov10 + 600, delta=0.01)
        # ... and one for Subscriptions paid from checking doesn't take it off the card: it's charged to the card, so it's
        # on the card's statement and comes off October's Subscriptions budget instead, which it uses up ($50 - $600)
        self.conn.execute(insert(Budget).values(category="Subscriptions", amount=50))
        self.conn.execute(update(Category).where(Category.name == "Subscriptions").values(pay_with="chk"))
        fc = forecast.build(self.conn, TODAY, 90)
        self.assertAlmostEqual(self.payments(fc)["2026-12-07"], nov10 + 600, delta=0.01)
        self.assertEqual(fs.drop(self, fc, "2026-10-20"), 0.0)                        # nothing from checking in October
        self.assertAlmostEqual(fs.drop(self, fc, "2026-11-17"), 50 / 30, delta=0.01)   # November's is all there
        # nor when that budget is paid with the card: the budget's $50 a month is on it instead
        self.conn.execute(update(Category).where(Category.name == "Subscriptions").values(pay_with="cc"))
        self.assertAlmostEqual(self.payments(forecast.build(self.conn, TODAY, 90))["2026-12-07"],
                               nov10 + 50 / 31 * 21 + 50 / 30 * 10, delta=0.01)

    def test_with_budgets_a_carried_balance_is_charged_interest_on_the_budgeted_charges_too(self):
        self.conn.execute(insert(Budget).values(category="Groceries", amount=500))
        self.conn.execute(update(Category).where(Category.name == "Groceries").values(pay_with="cc"))
        self.pay("minimum", apr="24")
        fc = forecast.build(self.conn, TODAY, 90)
        # Sep 10's $800, minimum $40, already met by the $200 paid: $600 carries into Oct 10's statement. Carrying a
        # balance ends the grace period, so its interest is on the $600 and on half the cycle's charges, budgeted ones too.
        oct10 = 300 + self.INSURANCE + 300 + 500 / 31 * 10
        interest = (600 + oct10 / 2) * 0.24 / 12
        self.assertAlmostEqual(self.payments(fc)["2026-11-05"], self.minimum(600 + interest + oct10, interest), delta=0.01)

    def test_paying_the_minimum_writes_nothing(self):
        # the payment plan is read from settings, never written, while the forecast is built
        self.stmt("cc", 800.0, "2026-09-10", "2026-10-05", minimum=250.0)
        self.pay("minimum", apr="24")
        self.conn.execute(insert(Override).values(key="cardclose:cc:2026-09-10", amount=-300.0))
        self.conn.execute(insert(Budget).values(category="Groceries", amount=500))
        self.conn.execute(update(Category).where(Category.name == "Groceries").values(pay_with="cc"))
        self.no_writes(AssertionError("the forecast wrote to the database"))
        fc = forecast.build(self.conn, TODAY, 90)
        self.assertEqual((self.card(fc)["pay_mode"], self.card(fc)["payment"]), ("minimum", 300.0))

    def test_a_payment_edited_to_nothing_stays_on_the_overview(self):
        # Paying the minimum ($50 left of $250), edited to $0: the event stays, to put back, and all $600 carries
        self.stmt("cc", 800.0, "2026-09-10", "2026-10-05", minimum=250.0)
        self.pay("minimum")
        self.conn.execute(insert(Override).values(key="cardclose:cc:2026-09-10", amount=0.0))
        fc = forecast.build(self.conn, TODAY, 90)
        e = next(e for e in fc["events"] if e["key"] == "cardclose:cc:2026-09-10")
        self.assertEqual((e["amount"], e["overridden"], e["original_amount"]), (0.0, True, -50.0))
        self.assertEqual((self.card(fc)["payment"], self.card(fc)["carried"]), (0.0, 600.0))
        self.assertEqual(self.payments(fc)["2026-11-05"], self.minimum(600 + self.EST1))
        # an edited estimate too: it keeps the plan's amount as its original
        self.conn.execute(insert(Override).values(key="cardclose:cc:2026-10-10", amount=0.0))
        fc = forecast.build(self.conn, TODAY, 90)
        e = next(e for e in fc["events"] if e["key"] == "cardclose:cc:2026-10-10")
        self.assertEqual((e["amount"], e["original_amount"]), (0.0, -self.minimum(600 + self.EST1)))
        self.assertEqual(self.payments(fc)["2026-12-07"], self.minimum(600 + self.EST1 + self.EST2))

    def test_an_edited_payment_above_the_statement_comes_off_the_next_one(self):
        # Paying the minimum, but $1,000 entered for the $600 left on the statement (to clear the current balance): the
        # $400 over is a credit on the next statement, not counted again there
        self.stmt("cc", 800.0, "2026-09-10", "2026-10-05", minimum=250.0)
        self.pay("minimum")
        self.conn.execute(insert(Override).values(key="cardclose:cc:2026-09-10", amount=-1000.0))
        fc = forecast.build(self.conn, TODAY, 90)
        self.assertEqual((self.card(fc)["payment"], self.card(fc)["carried"]), (1000.0, -400.0))
        e = next(e for e in fc["events"] if e["key"] == "cardclose:cc:2026-09-10")
        self.assertEqual((e["amount"], e["overridden"], e["original_amount"]), (-1000.0, True, -50.0))   # "was" the minimum
        payments = self.payments(fc)
        s1 = -400 + self.EST1   # Oct 10's statement: $400 lower than the cycle's charges ($350, so the $25 floor)
        self.assertEqual(payments["2026-10-05"], 1000.0)
        self.assertEqual(payments["2026-11-05"], 25.0)
        self.assertEqual(payments["2026-12-07"], self.minimum(s1 - 25 + self.EST2))
        self.assertTrue(all(e["amount"] < 0 for e in fc["events"] if e["kind"] == "card"))   # never a negative payment

    def test_budgeted_charges_are_paid_the_way_the_card_is_set(self):
        self.stmt("cc", 800.0, "2026-09-10", "2026-10-05", minimum=250.0)
        self.pay("minimum")
        self.conn.execute(insert(Budget).values(category="Groceries", amount=500))
        self.conn.execute(update(Category).where(Category.name == "Groceries").values(pay_with="cc"))
        fc = forecast.build(self.conn, TODAY, 90)
        payments = self.payments(fc)
        self.assertEqual(payments["2026-10-05"], 50.0)   # the closed statement
        # the $550 it leaves, charged so far, the insurance, and Groceries
        charged = 300 + self.INSURANCE + 300 + 500 / 31 * 10
        s1 = 550 + charged
        self.assertAlmostEqual(payments["2026-11-05"], self.minimum(s1), delta=0.01)
        s2 = s1 - self.minimum(s1) + self.INSURANCE + 500 / 31 * 21 + 500 / 30 * 10
        self.assertAlmostEqual(payments["2026-12-07"], self.minimum(s2), delta=0.01)
        # with an APR, interest on the budgeted charges too
        self.pay("minimum", apr="24")
        fc = forecast.build(self.conn, TODAY, 90)
        i1 = (550 + charged / 2) * 0.02
        self.assertAlmostEqual(self.payments(fc)["2026-11-05"], self.minimum(550 + i1 + charged, i1), delta=0.01)
