"""The forecast's assumptions: what it takes to be coming, and how card payments are placed."""
from datetime import date, timedelta

from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import OperationalError

from runway import db, forecast, recurring
from runway import settings_keys as sk
from runway.models import Account, Budget, Category, Override, Recurring, Transaction
from tests import forecast_support as fs
from tests.shared import TODAY, LedgerCase


class ForecastAssumptionTests(LedgerCase):
    """What the forecast assumes about pending transactions, card cycles and budgets."""
    card_setup = fs.card_setup
    no_writes = fs.no_writes

    def setUp(self):
        super().setUp()
        self.card_setup()

    def estimates(self, today=TODAY, days=90):
        return {e["date"]: -e["amount"] for e in forecast.build(self.conn, today, days)["events"] if e["estimated"]}

    def test_pending_transactions_are_in_the_starting_balance(self):
        self.tx("chk", "2026-09-22", -400.0, "HARDWARE STORE", "Shopping", pending=1)
        self.tx("chk", "2026-09-23", 25.0, "STORE REFUND", "Refunds", pending=1)
        fc = forecast.build(self.conn, TODAY, 30)
        acct = fc["accounts"][0]
        self.assertEqual((acct["balance"], acct["pending"]), (4625.0, -375.0))
        self.assertEqual(fc["total"][0], 4625.0)
        self.assertEqual(fc["events"][0]["balance_after"], 4625.0 + fc["events"][0]["amount"])
        self.conn.execute(insert(Recurring).values(name="Paycheck", account_id="chk", amount=3000, frequency="biweekly",
                                                   anchor_date="2026-09-09", match="acme payroll"))
        self.tx("chk", "2026-09-23", 3000.0, "ACME PAYROLL", "Income", pending=1)
        recurring.auto_match(self.conn)
        fc = forecast.build(self.conn, TODAY, 30)
        self.assertEqual((fc["accounts"][0]["balance"], fc["accounts"][0]["pending"]), (7625.0, 2625.0))
        self.assertEqual([e["date"] for e in fc["events"] if e["name"] == "Paycheck"], ["2026-10-07", "2026-10-21"])
        self.conn.execute(insert(Budget).values(category="Groceries", amount=500))
        self.conn.execute(update(Category).where(Category.name == "Groceries").values(pay_with="chk"))
        fc = forecast.build(self.conn, TODAY, 30)
        self.assertEqual(fc["total"][0], 7625.0)
        self.assertLess(fc["total"][1], 7625.0)

    def test_pending_isnt_added_to_a_balance_that_already_has_it(self):
        self.tx("chk", "2026-09-22", -400.0, "HARDWARE STORE", "Shopping", pending=1)
        self.conn.execute(update(Account).where(Account.id == "chk").values(available=4600.0))
        acct = forecast.build(self.conn, TODAY, 30)["accounts"][0]
        self.assertEqual((acct["balance"], acct["pending"]), (4600.0, -400.0))
        self.conn.execute(update(Account).where(Account.id == "chk").values(available=5000.0))
        acct = forecast.build(self.conn, TODAY, 30)["accounts"][0]
        self.assertEqual((acct["balance"], acct["pending"]), (5000.0, 0.0))

    def test_pending_is_added_when_the_available_balance_has_an_overdraft_line_in_it(self):
        self.tx("chk", "2026-09-22", -400.0, "HARDWARE STORE", "Shopping", pending=1)
        for available in (5100.0, 5500.0):
            self.conn.execute(update(Account).where(Account.id == "chk").values(available=available))
            acct = forecast.build(self.conn, TODAY, 30)["accounts"][0]
            self.assertEqual((acct["balance"], acct["pending"]), (4600.0, -400.0), available)

    def test_old_pending_rows_are_left_out(self):
        self.tx("chk", (TODAY - timedelta(days=20)).isoformat(), -300.0, "OLD HOLD", "Shopping", pending=1)
        self.tx("chk", (TODAY - timedelta(days=14)).isoformat(), -50.0, "GAS STATION", "Auto", pending=1)
        acct = forecast.build(self.conn, TODAY, 30)["accounts"][0]
        self.assertEqual((acct["balance"], acct["pending"]), (4950.0, -50.0))
        self.conn.execute(insert(Transaction).values(id="chk|pl:hold1", account_id="chk", amount=-200.0, pending=1,
                                                     posted=(TODAY - timedelta(days=20)).isoformat(), description="HOTEL"))
        acct = forecast.build(self.conn, TODAY, 30)["accounts"][0]
        self.assertEqual((acct["balance"], acct["pending"]), (4750.0, -250.0))

    def test_old_simplefin_pending_card_rows_are_left_out_of_the_cycle(self):
        later = date(2026, 10, 5)
        before = self.cycle("cc", later)["new_charges"]
        self.tx("cc", "2026-09-15", -70.0, "COFFEE", "Dining", pending=1)
        self.tx("cc", "2026-09-16", -70.0, "COFFEE", "Dining")
        self.assertEqual(self.cycle("cc", later)["new_charges"], before + 70)
        self.tx("cc", "2026-09-25", -30.0, "LUNCH", "Dining", pending=1)
        self.assertEqual(self.cycle("cc", later)["new_charges"], before + 100)
        self.conn.execute(insert(Transaction).values(id="cc|pl:hold1", account_id="cc", amount=-200.0, pending=1,
                                                     posted="2026-09-15", description="HOTEL", category="Travel"))
        self.assertEqual(self.cycle("cc", later)["new_charges"], before + 300)

    def paycheck_pending(self):
        """A biweekly $1,000 paycheck, today's pending (and linked to it once the forecast matches it)."""
        self.conn.execute(insert(Recurring).values(name="Paycheck", account_id="chk", amount=1000, frequency="biweekly",
                                                   anchor_date="2026-09-09", match="acme payroll"))
        self.tx("chk", "2026-09-23", 1000.0, "ACME PAYROLL", "Income", pending=1)
        recurring.auto_match(self.conn)

    def test_a_pending_paycheck_counts_though_the_available_balance_leaves_it_out(self):
        self.paycheck_pending()
        self.conn.execute(update(Account).where(Account.id == "chk").values(available=5000.0))
        fc = forecast.build(self.conn, TODAY, 30)
        self.assertEqual((fc["accounts"][0]["balance"], fc["accounts"][0]["pending"]), (6000.0, 1000.0))
        self.assertNotIn("2026-09-23", [e["date"] for e in fc["events"] if e["name"] == "Paycheck"])

    def test_pending_debits_and_a_paycheck(self):
        self.paycheck_pending()
        self.tx("chk", "2026-09-22", -375.0, "HARDWARE STORE", "Shopping", pending=1)
        self.conn.execute(update(Account).where(Account.id == "chk").values(available=4625.0))
        acct = forecast.build(self.conn, TODAY, 30)["accounts"][0]
        self.assertEqual((acct["balance"], acct["pending"]), (5625.0, 625.0))
        self.conn.execute(update(Account).where(Account.id == "chk").values(available=5000.0))
        acct = forecast.build(self.conn, TODAY, 30)["accounts"][0]
        self.assertEqual((acct["balance"], acct["pending"]), (6000.0, 1000.0))

    def test_an_overpayment_comes_off_the_next_statement(self):
        self.conn.execute(update(Transaction).where(Transaction.description == "GROCER").values(posted="2026-09-01"))
        self.conn.execute(update(Transaction).where(Transaction.description == "PAYMENT THANK YOU").values(amount=1200.0))
        info = self.cycle("cc")
        self.assertEqual((info["paid_since_close"], info["remaining"], info["new_charges"]), (1200.0, 0.0, 0.0))
        self.tx("cc", "2026-09-20", -400.0, "GROCER", "Groceries")
        self.assertEqual(self.cycle("cc")["new_charges"], 100.0)
        self.conn.execute(update(Transaction).where(Transaction.amount == 1200.0).values(amount=500.0))
        self.assertEqual((self.cycle("cc")["remaining"], self.cycle("cc")["new_charges"]), (300.0, 500.0))

    def test_large_one_offs_are_flagged(self):
        self.tx("chk", "2026-08-01", -2000.0, "LANDLORD LLC", "Rent")
        self.tx("chk", "2026-09-01", -2000.0, "LANDLORD LLC", "Rent")
        self.tx("chk", "2026-08-15", -5000.0, "STATE UNIVERSITY", "Education")
        self.tx("chk", "2026-09-10", -1500.0, "CHASE CREDIT CRD AUTOPAY", "Credit Card Payment")
        self.tx("chk", "2026-05-01", -9000.0, "ROOFING CO", "Home")
        self.tx("chk", "2026-09-12", -900.0, "DR SMITH DDS", "Medical")
        self.tx("chk", "2026-09-14", -3000.0, "PENDING THING", "Shopping", pending=1)
        fc = forecast.build(self.conn, TODAY, 30)
        text = "3 payments over $1,000 in the last 90 days aren’t in the forecast (State University, Landlord Llc); " \
               "add them as recurring items."
        self.assertIn({"text": text, "href": "#recurring", "setting": True}, fc["warning_links"])
        self.conn.execute(insert(Recurring).values(name="Rent", account_id="chk", amount=-2000, frequency="monthly",
                                                   anchor_date="2026-08-01", match="landlord"))
        fc = forecast.build(self.conn, TODAY, 30)
        self.assertIn("1 payment over $1,000 in the last 90 days isn’t in the forecast (State University); "
                      "add it as a recurring item.", fc["warnings"])
        self.conn.execute(update(Transaction).where(Transaction.description == "STATE UNIVERSITY").values(recurring_id=0))
        self.assertFalse(any("over $1,000" in w for w in forecast.build(self.conn, TODAY, 30)["warnings"]))
        for i, name in enumerate(["ALPHA", "BRAVO", "CHARLIE", "DELTA"]):
            self.tx("chk", f"2026-09-0{i + 2}", -1100.0 - 100 * i, name, "Shopping")
        self.assertIn("4 payments over $1,000 in the last 90 days aren’t in the forecast (Delta, Charlie, Bravo…); "
                      "add them as recurring items.", forecast.build(self.conn, TODAY, 30)["warnings"])

    def test_recurring_charges_on_the_card_are_in_its_statements(self):
        self.conn.execute(insert(Recurring).values(name="Gym", account_id="cc", amount=-30, frequency="monthly",
                                                   anchor_date="2026-05-15", match="gym"))
        for d in ("2026-05-15", "2026-06-15", "2026-07-15", "2026-08-15", "2026-09-15"):
            self.tx("cc", d, -30.0, "GYM", "Fitness")
        recurring.auto_match(self.conn)
        self.conn.execute(insert(Recurring).values(name="Insurance", account_id="cc", amount=-600, frequency="yearly",
                                                   anchor_date="2025-10-20", match="insurer"))
        self.conn.execute(insert(Recurring).values(name="Streaming", account_id="cc", amount=-15, frequency="monthly",
                                                   anchor_date="2026-09-30", match="streamflix"))
        fc = forecast.build(self.conn, TODAY, 120)
        self.assertEqual(next(c for c in fc["cards"] if c["id"] == "cc")["new_charges"], 330.0)
        est = {e["date"]: -e["amount"] for e in fc["events"] if e["estimated"]}
        self.assertEqual(est, {"2026-11-05": 330 + 15,
                               "2026-12-07": 30 + 600 + 15,
                               "2027-01-05": 30 + 15})
        self.assertFalse(any(e["kind"] == "recurring" for e in fc["events"]))

    def test_a_budget_on_a_card_paid_from_outside_the_forecast_is_skipped(self):
        self.acct("sav", "savings", 20000.0)
        self.acct("cc2", "credit", -50.0, pay_from="sav")
        self.stmt("cc2", 50.0, "2026-09-10", "2026-10-05")
        self.conn.execute(insert(Budget).values(category="Groceries", amount=500))
        self.conn.execute(update(Category).where(Category.name == "Groceries").values(pay_with="cc2"))
        b = forecast.build(self.conn, TODAY, 60)["budget"]
        self.assertEqual(b["used"], [])
        self.assertEqual(b["skipped"], [{"category": "Groceries", "reason": "its card isn't paid from a forecast account"}])
        self.assertEqual(b["monthly"], 0.0)

    def no_statement_payments(self, card_id, today=TODAY, days=90):
        """The forecast's payments of a card with no statement, by date."""
        return {e["date"]: e["amount"] for e in forecast.build(self.conn, today, days)["events"] if e.get("card_id") == card_id}

    def test_a_budget_on_a_card_with_no_statement_yet_still_counts(self):
        self.acct("cc3", "credit", -40.0, pay_from="chk")
        self.conn.execute(insert(Budget).values(category="Travel", amount=310))
        self.conn.execute(update(Category).where(Category.name == "Travel").values(pay_with="cc3"))
        fc = forecast.build(self.conn, TODAY, 90)
        b = fc["budget"]
        self.assertEqual(([u["account_id"] for u in b["used"]], b["skipped"]), (["cc3"], []))
        self.assertEqual(b["monthly"], 310.0)
        card = [e for e in fc["events"] if e.get("card_id") == "cc3"]
        self.assertEqual([(e["date"], e["amount"]) for e in card], [("2026-10-26", -350.0), ("2026-11-25", -310.0)])
        for e in card:
            self.assertEqual((e["kind"], e["account_id"], e["name"], e["category"], e["estimated"], e["assumed_cycle"]),
                             ("card", "chk", "cc3 statement", "Credit Card Payment", True, True))
            self.assertNotIn("key", e)
        i = fc["dates"].index("2026-10-26")
        self.assertEqual(fc["total"][i - 1] - fc["total"][i], 350.0)
        self.assertEqual(card[0]["balance_after"], fc["total"][i])

    def test_a_card_with_no_statement_has_its_recurring_charges_in_its_assumed_statements(self):
        self.acct("cc3", "credit", 0.0, pay_from="chk")
        self.conn.execute(insert(Budget).values(category="Travel", amount=310))
        self.conn.execute(update(Category).where(Category.name == "Travel").values(pay_with="cc3"))
        self.conn.execute(insert(Recurring).values(id=9, name="Streaming", account_id="cc3", amount=-15, frequency="monthly",
                                                   anchor_date="2026-10-15"))
        self.assertEqual(self.no_statement_payments("cc3"), {"2026-10-26": -310.0, "2026-11-25": -325.0})

    def test_a_budget_on_a_card_with_no_statement_reads_the_banks_sign(self):
        self.acct("cc3", "credit", 40.0, pay_from="chk", owed_positive=1)
        self.conn.execute(insert(Budget).values(category="Travel", amount=310))
        self.conn.execute(update(Category).where(Category.name == "Travel").values(pay_with="cc3"))
        self.assertEqual(self.no_statement_payments("cc3")["2026-10-26"], -350.0)
        self.conn.execute(update(Account).where(Account.id == "cc3").values(balance=-20.0))
        card = self.no_statement_payments("cc3")
        self.assertEqual((card["2026-10-26"], card["2026-11-25"]), (-290.0, -310.0))
        self.conn.execute(update(Account).where(Account.id == "cc3").values(balance=-400.0))
        self.assertEqual(self.no_statement_payments("cc3"), {"2026-11-25": -220.0})

    def test_a_budget_on_a_card_with_no_statement_is_paid_the_way_the_card_is_set(self):
        self.acct("cc3", "credit", 0.0, pay_from="chk")
        db.set_setting(self.conn, sk.card_pay_mode("cc3"), "fixed")
        db.set_setting(self.conn, sk.card_pay_amount("cc3"), "100")
        db.set_setting(self.conn, sk.card_apr("cc3"), "24")
        self.conn.execute(insert(Budget).values(category="Travel", amount=310))
        self.conn.execute(update(Category).where(Category.name == "Travel").values(pay_with="cc3"))
        self.assertEqual(self.no_statement_payments("cc3"), {"2026-10-26": -100.0, "2026-11-25": -100.0})
        db.set_setting(self.conn, sk.card_pay_mode("cc3"), "full")
        self.assertEqual(self.no_statement_payments("cc3"), {"2026-10-26": -310.0, "2026-11-25": -310.0})

    drop = fs.drop

    def medical(self, medical=400.0, dentist=None, dentist_pays=None, medical_pays="chk"):
        from runway import categories
        self.acct("cc3", "credit", 0.0, pay_from="chk")
        categories.add(self.conn, "Dentist", "Medical")
        self.conn.execute(insert(Budget).values(category="Medical", amount=medical))
        if dentist is not None:
            self.conn.execute(insert(Budget).values(category="Dentist", amount=dentist))
        self.conn.execute(update(Category).where(Category.name == "Medical").values(pay_with=medical_pays))
        self.conn.execute(update(Category).where(Category.name == "Dentist").values(pay_with=dentist_pays))
        fc = forecast.build(self.conn, TODAY, 90)
        cards = {e["date"]: -e["amount"] for e in fc["events"] if e.get("card_id") == "cc3"}
        used = [(u["category"], u["account_id"], u["amount"]) for u in fc["budget"]["used"]]
        return fc, cards, used

    def test_a_parents_budget_alone_goes_on_its_account(self):
        fc, cards, used = self.medical()
        self.assertEqual((used, fc["budget"]["monthly"], fc["budget"]["skipped"]), ([("Medical", "chk", 400.0)], 400.0, []))
        self.assertAlmostEqual(self.drop(fc, "2026-09-24"), 400 / 7, delta=0.01)
        self.assertAlmostEqual(self.drop(fc, "2026-10-15"), 400 / 31, delta=0.01)
        self.assertEqual(cards, {})

    def test_a_category_with_an_account_but_no_budget_changes_nothing(self):
        alone = self.medical()
        self.tearDown(); self.setUp()
        fc, cards, used = self.medical(dentist_pays="cc3")
        self.assertEqual((fc["total"], fc["events"], used), (alone[0]["total"], alone[0]["events"], alone[2]))
        self.assertEqual(cards, {})

    def test_a_subcategorys_budget_goes_on_its_own_card_and_the_rest_on_the_parents(self):
        self.tx("chk", "2026-09-05", -30.0, "DENTAL", "Dentist")
        self.tx("chk", "2026-09-06", -50.0, "CLINIC", "Medical")
        fc, cards, used = self.medical(dentist=100.0, dentist_pays="cc3")
        self.assertEqual((used, fc["budget"]["monthly"]), ([("Medical", "chk", 300.0), ("Dentist", "cc3", 100.0)], 400.0))
        self.assertAlmostEqual(self.drop(fc, "2026-10-15"), 300 / 31, delta=0.01)
        self.assertEqual((cards["2026-10-26"], cards["2026-11-25"]), (70.0, 100.0))
        self.assertAlmostEqual(self.drop(fc, "2026-09-24"), 250 / 7, delta=0.01)
        plan = forecast.budget_plan(self.conn, TODAY)
        split = forecast.budget_days(self.conn, TODAY, 90, plan, [], {"chk"})["Medical"]
        whole = forecast.budget_days(self.conn, TODAY, 90, [{**p, "parts": []} for p in plan], [], {"chk"})["Medical"]
        self.assertEqual([s["category"] for s in split], ["Medical", "Dentist"])
        for d, v in whole[0]["days"].items():
            self.assertAlmostEqual(sum(s["days"][d] for s in split), v, places=9)

    def test_a_subcategorys_budget_bigger_than_its_parents_leaves_the_parent_nothing(self):
        fc, cards, used = self.medical(medical=100.0, dentist=150.0, dentist_pays="cc3")
        self.assertEqual((used, fc["budget"]["monthly"], fc["budget"]["skipped"]), ([("Dentist", "cc3", 100.0)], 100.0, []))
        self.assertEqual(self.drop(fc, "2026-10-15"), 0.0)
        self.assertEqual(cards["2026-11-25"], 100.0)

    def test_a_subcategorys_budget_without_an_account_goes_on_its_parents(self):
        fc, cards, used = self.medical(dentist=100.0)
        self.assertEqual((used, fc["budget"]["monthly"]), ([("Medical", "chk", 400.0)], 400.0))
        self.assertAlmostEqual(self.drop(fc, "2026-10-15"), 400 / 31, delta=0.01)
        self.assertEqual(cards, {})

    def test_a_subcategorys_card_paid_from_outside_the_forecast_is_skipped_on_its_own(self):
        self.acct("sav", "savings", 20000.0)
        self.acct("cc2", "credit", 0.0, pay_from="sav")
        fc, _cards, used = self.medical(dentist=100.0, dentist_pays="cc2")
        self.assertEqual((used, fc["budget"]["monthly"]), ([("Medical", "chk", 300.0)], 300.0))
        self.assertEqual(fc["budget"]["skipped"], [{"category": "Dentist", "reason": "its card isn't paid from a forecast account"}])

    def test_a_charge_on_a_subcategorys_card_is_in_its_budget_and_one_in_the_parents_isnt(self):
        _fc, cards, _used = self.medical(dentist=100.0, dentist_pays="cc3")
        self.conn.execute(insert(Recurring).values(name="Ortho", account_id="cc3", amount=-30, frequency="monthly",
                                                   anchor_date="2026-10-15", match="ortho"))
        self.tx("cc3", "2026-08-15", -30.0, "ORTHO", "Dentist")
        payments = lambda fc: {e["date"]: -e["amount"] for e in fc["events"] if e.get("card_id") == "cc3"}
        self.assertEqual(payments(forecast.build(self.conn, TODAY, 90)), cards)
        self.conn.execute(update(Transaction).where(Transaction.description == "ORTHO").values(category="Medical"))
        fc = forecast.build(self.conn, TODAY, 90)
        self.assertEqual(payments(fc), {**cards, "2026-11-25": cards["2026-11-25"] + 30})
        self.assertAlmostEqual(self.drop(fc, "2026-10-15"), 270 / 31, delta=0.01)

    def test_a_parents_usual_account_leaves_out_subcategories_with_their_own(self):
        from runway import categories
        categories.add(self.conn, "Dentist", "Medical")
        self.tx("chk", "2026-09-06", -50.0, "CLINIC", "Medical")
        self.tx("cc", "2026-09-05", -500.0, "DENTAL", "Dentist")
        self.conn.execute(insert(Budget), [{"category": "Medical", "amount": 400}, {"category": "Dentist", "amount": 100}])
        usual = lambda: next(p["usual"] for p in forecast.budget_plan(self.conn, TODAY) if p["category"] == "Medical")
        self.assertEqual(usual(), "cc")
        self.conn.execute(update(Category).where(Category.name == "Dentist").values(pay_with="cc"))
        self.assertEqual(usual(), "chk")

    def test_a_budget_on_a_card_with_no_statement_and_no_paying_account_is_skipped(self):
        self.acct("cc3", "credit", 0.0)
        self.conn.execute(insert(Budget).values(category="Travel", amount=310))
        self.conn.execute(update(Category).where(Category.name == "Travel").values(pay_with="cc3"))
        b = forecast.build(self.conn, TODAY, 60)["budget"]
        self.assertEqual(b["skipped"], [{"category": "Travel", "reason": "its card isn't paid from a forecast account"}])

    def test_a_budget_spends_what_it_carried_over(self):
        self.tx("chk", "2026-08-12", -250.0, "GROCER", "Groceries")
        self.conn.execute(insert(Budget).values(category="Groceries", amount=310, rollover_from="2026-08"))
        self.conn.execute(update(Category).where(Category.name == "Groceries").values(pay_with="chk"))
        fc = forecast.build(self.conn, TODAY, 40)
        drop = lambda d: fc["total"][fc["dates"].index(d) - 1] - fc["total"][fc["dates"].index(d)]
        self.assertAlmostEqual(drop("2026-09-24"), (310 + 60 - 200) / 7, delta=0.01)
        self.assertAlmostEqual(drop("2026-10-15"), 10.0, delta=0.01)

    def test_a_payment_in_transit_waits_for_a_card_without_a_paying_account(self):
        self.tx("chk", "2026-09-22", -600.0, "CHASE CREDIT CRD AUTOPAY", "Credit Card Payment")
        self.assertEqual(self.cycle("cc")["paid_since_close"], 800.0)
        self.acct("cc2", "credit", -600.0)
        self.assertEqual(self.cycle("cc")["paid_since_close"], 200.0)
        self.acct("sav", "savings", 100.0)
        self.conn.execute(update(Account).where(Account.id == "cc2").values(pay_from="sav"))
        self.assertEqual(self.cycle("cc")["paid_since_close"], 800.0)

    def test_card_payments_are_keyed_by_their_closing_date(self):
        self.conn.execute(insert(Recurring).values(name="Streaming", account_id="cc", amount=-15, frequency="monthly",
                                                   anchor_date="2026-09-30"))
        events = [e for e in forecast.build(self.conn, TODAY, 90)["events"] if e["kind"] == "card"]
        self.assertEqual([(e["date"], e["key"]) for e in events],
                         [("2026-10-05", "cardclose:cc:2026-09-10"), ("2026-11-05", "cardclose:cc:2026-10-10"),
                          ("2026-12-07", "cardclose:cc:2026-11-10")])
        self.conn.execute(insert(Override).values(key="cardclose:cc:2026-10-10", amount=-123.0))
        e = next(e for e in forecast.build(self.conn, TODAY, 90)["events"] if e.get("key") == "cardclose:cc:2026-10-10")
        self.assertEqual((e["date"], e["amount"]), ("2026-11-05", -123.0))
        self.stmt("cc", 500.0, "2026-10-10", "2026-11-06")
        self.conn.execute(update(Account).where(Account.id == "cc").values(balance=-500.0))
        e = next(e for e in forecast.build(self.conn, date(2026, 10, 12), 30)["events"] if e["kind"] == "card")
        self.assertEqual((e["date"], e["key"], e["amount"], e["overridden"]), ("2026-11-06", "cardclose:cc:2026-10-10", -123.0, True))

    def test_an_amount_set_under_the_old_due_date_key_still_applies(self):
        self.conn.execute(insert(Override).values(key="card:cc:2026-11-05", amount=-77.0))
        self.conn.execute(insert(Override).values(key="card:cc:2026-10-05", amount=-66.0))
        fc, moving = forecast.project(self.conn, TODAY, 60)
        self.assertEqual({e["key"]: e["amount"] for e in fc["events"] if e["kind"] == "card"},
                         {"cardclose:cc:2026-09-10": -66.0, "cardclose:cc:2026-10-10": -77.0})
        self.assertEqual(fc, forecast.build(self.conn, TODAY, 60))
        self.assertEqual(sorted(self.conn.execute(select(Override.key).where(Override.key.like("card%"))).scalars()),
                         ["card:cc:2026-10-05", "card:cc:2026-11-05"])
        forecast.move_old_keys(self.conn, moving)
        self.assertEqual(sorted(self.conn.execute(select(Override.key).where(Override.key.like("card%"))).scalars()),
                         ["cardclose:cc:2026-09-10", "cardclose:cc:2026-10-10"])
        self.conn.execute(delete(Override).where(Override.key == "cardclose:cc:2026-10-10"))
        e = next(e for e in forecast.build(self.conn, TODAY, 60)["events"] if e["key"] == "cardclose:cc:2026-10-10")
        self.assertFalse(e.get("overridden"))

    def test_building_the_forecast_writes_nothing(self):
        self.conn.execute(insert(Recurring).values(name="Rent", account_id="chk", amount=-2000, frequency="monthly",
                                                   anchor_date="2026-08-01", match="landlord"))
        self.tx("chk", "2026-09-01", -2000.0, "LANDLORD LLC", "Rent")
        self.tx("chk", "2026-09-22", -40.0, "GAS STATION", "Auto", pending=1)
        self.conn.execute(insert(Override).values(key="cardclose:cc:2026-10-10", amount=-123.0))
        self.conn.execute(insert(Budget).values(category="Groceries", amount=500, rollover_from="2026-08"))
        self.conn.execute(update(Category).where(Category.name == "Groceries").values(pay_with="cc"))
        self.no_writes(AssertionError("the forecast wrote to the database"))
        fc = forecast.build(self.conn, TODAY, 90)
        self.assertEqual(next(e for e in fc["events"] if e["key"] == "cardclose:cc:2026-10-10")["amount"], -123.0)

    def test_an_old_key_still_applies_when_it_cant_be_moved_yet(self):
        self.conn.execute(insert(Override).values(key="card:cc:2026-11-05", amount=-77.0))
        patch = self.no_writes(OperationalError("UPDATE override", {}, Exception("database is locked")))
        fc, moving = forecast.project(self.conn, TODAY, 60)
        forecast.move_old_keys(self.conn, moving)
        e = next(e for e in fc["events"] if e["key"] == "cardclose:cc:2026-10-10")
        self.assertEqual((e["amount"], e["overridden"]), (-77.0, True))
        self.assertEqual(self.conn.execute(select(Override.key)).scalars(), ["card:cc:2026-11-05"])
        patch.stop()
        forecast.move_old_keys(self.conn, forecast.project(self.conn, TODAY, 60)[1])
        self.assertEqual(self.conn.execute(select(Override.key)).scalars(), ["cardclose:cc:2026-10-10"])

    def test_an_old_key_on_a_due_date_that_is_another_closing_date_stays_put(self):
        self.stmt("cc", 800.0, "2027-01-31", "2027-02-28")
        self.tx("cc", "2027-02-05", 800.0, "PAYMENT THANK YOU", "Credit Card Payment")
        self.tx("cc", "2027-02-08", -100.0, "COFFEE", "Restaurants")
        self.conn.execute(insert(Override).values(key="card:cc:2027-02-28", amount=-5.0))
        events = [e for e in forecast.build(self.conn, date(2027, 2, 10), 60)["events"] if e["kind"] == "card"]
        self.assertEqual([(e["key"], e["date"]) for e in events],
                         [("cardclose:cc:2027-02-28", "2027-03-29")])
        self.assertFalse(events[0].get("overridden"))
        self.assertEqual(events[0]["amount"], -100.0)

    def test_a_later_payment_doesnt_count_for_an_earlier_occurrence(self):
        today = date(2026, 10, 5)
        history = [{"posted": "2026-10-01", "amount": -50.0}]
        for due in (date(2026, 10, 5), date(2026, 9, 28)):
            item = {"frequency": "monthly", "anchor_date": due.isoformat(), "amount": -50.0}
            with self.subTest(due=due):
                self.assertIsNone(recurring.still_due(item, due, recurring.paid_by_occurrence(item, history), today, -50.0))
        item = {"frequency": "monthly", "anchor_date": "2026-09-01", "amount": -50.0}
        paid = recurring.paid_by_occurrence(item, history)
        self.assertIsNone(recurring.still_due(item, date(2026, 10, 1), paid, today, -50.0))
        self.assertNotIn(date(2026, 9, 1), paid)
