"""The forecast: dates and schedules, card payments, and the balance chart."""
import unittest
from unittest import mock
from datetime import date, timedelta

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import OperationalError

from runway import churning, db, forecast, plaidapi, recurring
from runway import settings_keys as sk
from runway.models import Account, Budget, CardStatement, Category, ChurnCard, Override, PlaidAccount, Recurring, RecurringDismissed, Transaction
from tests.shared import TODAY, LedgerCase


class DateTests(unittest.TestCase):
    def test_next_after(self):
        self.assertEqual(forecast.next_after(date(2026, 9, 5), 2), date(2026, 10, 2))
        self.assertEqual(forecast.next_after(date(2026, 9, 5), 30), date(2026, 9, 30))
        self.assertEqual(forecast.next_after(date(2026, 1, 31), 31), date(2026, 2, 28))

    def test_scheduled_dates(self):
        m = {"anchor_date": "2026-01-31", "frequency": "monthly", "end_date": None}
        self.assertEqual(forecast.scheduled(m, TODAY, date(2026, 12, 31)),
                         [date(2026, 9, 30), date(2026, 10, 31), date(2026, 11, 30), date(2026, 12, 31)])
        b = {"anchor_date": "2026-09-18", "frequency": "biweekly", "end_date": None}
        self.assertEqual(forecast.scheduled(b, TODAY, date(2026, 10, 31)), [date(2026, 10, 2), date(2026, 10, 16), date(2026, 10, 30)])
        f = {"anchor_date": "2026-10-10", "frequency": "monthly", "end_date": "2026-11-15"}
        self.assertEqual(forecast.scheduled(f, TODAY, date(2026, 12, 31)), [date(2026, 10, 10), date(2026, 11, 10)])
        y = {"anchor_date": "2025-12-01", "frequency": "yearly", "end_date": None}
        self.assertEqual(forecast.scheduled(y, TODAY, date(2027, 12, 31)), [date(2026, 12, 1), date(2027, 12, 1)])

    def test_money_moves_on_business_days(self):
        from runway import bankdays
        pay = {"frequency": "semimonthly", "dates": "15,31", "anchor_date": "2026-01-01", "end_date": None, "amount": 4180}
        self.assertEqual(forecast.occurrences(pay, date(2026, 1, 31), date(2026, 3, 31)),
                         [date(2026, 2, 13), date(2026, 2, 27), date(2026, 3, 13), date(2026, 3, 31)])
        gym = {"frequency": "monthly", "anchor_date": "2026-07-05", "end_date": None, "amount": -40}
        self.assertEqual(forecast.occurrences(gym, date(2026, 8, 31), date(2026, 9, 30)), [date(2026, 9, 8)])
        self.assertEqual(bankdays.next_business_day(date(2026, 10, 10)), date(2026, 10, 13))
        self.assertFalse(bankdays.is_business_day(date(2027, 7, 5)))
        self.assertTrue(bankdays.is_business_day(date(2026, 7, 3)))
        self.assertFalse(bankdays.is_business_day(date(2026, 11, 26)))
        rent = {"frequency": "monthly", "anchor_date": "2026-01-31", "end_date": None, "amount": -1500}
        self.assertEqual(forecast.occurrences(rent, date(2026, 11, 1), date(2026, 11, 30)), [date(2026, 11, 2), date(2026, 11, 30)])


class ForecastTests(LedgerCase):
    def setUp(self):
        super().setUp()
        self.card_setup()

    def card_setup(self):
        self.acct("chk", "checking", 5000.0)
        self.acct("cc", "credit", -900.0, pay_from="chk")
        self.stmt("cc", 800.0, "2026-09-10", "2026-10-05", minimum=40.0)
        self.tx("cc", "2026-09-12", -100.0, "COFFEE", "Restaurants")
        self.tx("cc", "2026-09-20", -200.0, "GROCER", "Groceries")
        self.tx("cc", "2026-09-15", 200.0, "PAYMENT THANK YOU", "Credit Card Payment")
        self.tx("cc", "2026-09-01", -800.0, "STUFF", "Shopping")

    def test_statement_from_the_bank(self):
        info = self.cycle("cc")
        self.assertEqual(info["last_close"], "2026-09-10")
        self.assertEqual(info["minimum_payment"], 40.0)
        self.assertEqual(info["statement_balance"], 800.0)
        self.assertEqual(info["paid_since_close"], 200.0)
        self.assertEqual(info["remaining"], 600.0)
        self.assertEqual(info["due_date"], "2026-10-05")
        self.assertEqual(info["new_charges"], 300.0)

    def drop(self, fc, day):
        """How much the forecast's total falls on a day (what goes out, less what comes in)."""
        i = fc["dates"].index(day)
        return fc["total"][i - 1] - fc["total"][i]

    def test_future_statements_are_whats_charged_and_whats_scheduled(self):
        self.tx("cc", "2026-06-20", -1200.0, "TRIP", "Travel")
        self.tx("cc", "2026-08-20", -400.0, "GROCER", "Groceries")
        est = {e["date"]: -e["amount"] for e in forecast.build(self.conn, TODAY, 90)["events"] if e["estimated"]}
        self.assertEqual(est, {"2026-11-05": 300.0})
        self.tx("cc", "2026-09-21", -2000.0, "LAPTOP", "Shopping")
        est = {e["date"]: -e["amount"] for e in forecast.build(self.conn, TODAY, 90)["events"] if e["estimated"]}
        self.assertEqual(est, {"2026-11-05": 2300.0})
        self.conn.execute(insert(Recurring).values(name="Streaming", account_id="cc", amount=-15, frequency="monthly",
                                                   anchor_date="2026-09-30"))
        est = {e["date"]: -e["amount"] for e in forecast.build(self.conn, TODAY, 90)["events"] if e["estimated"]}
        self.assertEqual(est, {"2026-11-05": 2315.0, "2026-12-07": 15.0})

    def test_sticking_to_the_budget(self):
        self.conn.execute(insert(Budget).values(category="Groceries", amount=500))
        self.conn.execute(update(Category).where(Category.name == "Groceries").values(pay_with="cc"))
        fc = forecast.build(self.conn, TODAY, 90)
        b = fc["budget"]
        self.assertEqual(set(b), {"used", "skipped", "monthly"})
        self.assertEqual((b["monthly"], [u["account_id"] for u in b["used"]], b["skipped"]), (500.0, ["cc"], []))
        self.assertEqual((b["used"][0]["category"], b["used"][0]["account"], b["used"][0]["chosen"]), ("Groceries", "cc", True))
        card = {e["date"]: -e["amount"] for e in fc["events"] if e["kind"] == "card"}
        self.assertEqual(card["2026-10-05"], 600.0)
        self.assertAlmostEqual(card["2026-11-05"], 300 + 300 + 500 / 31 * 10, delta=0.01)
        self.assertAlmostEqual(card["2026-12-07"], 500 / 31 * 21 + 500 / 30 * 10, delta=0.01)
        self.assertTrue(all(e["estimated"] for e in fc["events"] if e["kind"] == "card" and e["date"] > "2026-10-05"))
        self.assertEqual(fc["total"][fc["dates"].index("2026-10-20")], fc["total"][fc["dates"].index("2026-10-25")])
        self.assertAlmostEqual(self.drop(fc, "2026-11-05"), card["2026-11-05"], places=2)
        self.assertEqual({e["kind"] for e in fc["events"]}, {"card"})

    def test_an_income_budget_changes_nothing(self):
        self.conn.execute(insert(Recurring).values(name="Paycheck", account_id="chk", amount=3000, frequency="biweekly",
                                                   anchor_date="2026-09-18"))
        self.conn.execute(insert(Budget).values(category="Groceries", amount=600))
        before = forecast.build(self.conn, TODAY, 60)
        self.conn.execute(insert(Budget).values(category="Income", amount=6500))
        after = forecast.build(self.conn, TODAY, 60)
        self.assertEqual([p["category"] for p in forecast.budget_plan(self.conn, TODAY)], ["Groceries"])
        for k in ("events", "total", "budget", "spend"):
            self.assertEqual(after.get(k), before.get(k), k)
        self.assertEqual(after["accounts"][0]["series"], before["accounts"][0]["series"])

    def test_a_budget_paid_from_checking_comes_out_day_by_day(self):
        self.conn.execute(insert(Budget).values(category="Groceries", amount=310))
        self.conn.execute(update(Category).where(Category.name == "Groceries").values(pay_with="chk"))
        fc = forecast.build(self.conn, TODAY, 40)
        self.assertEqual([(u["category"], u["account_id"]) for u in fc["budget"]["used"]], [("Groceries", "chk")])
        self.assertEqual(fc["total"][0], 5000.0)
        self.assertAlmostEqual(self.drop(fc, "2026-09-24"), 110 / 7, delta=0.01)
        self.assertAlmostEqual(self.drop(fc, "2026-10-15"), 10.0, delta=0.01)
        from runway import bankdays
        business = [d for d in fc["dates"][1:] if bankdays.is_business_day(date.fromisoformat(d))]
        self.assertTrue(all(self.drop(fc, d) > 0 for d in business))
        self.assertTrue(all(self.drop(fc, d) == 0 for d in fc["dates"][1:] if d not in business))
        self.assertEqual(fc["accounts"][0]["series"], fc["total"])
        self.assertEqual([(e["date"], e["kind"]) for e in fc["events"]], [("2026-10-05", "card")])
        oct5 = fc["events"][0]
        self.assertAlmostEqual(oct5["balance_after"], 5000 - 110 - 310 / 31 * 5 - 600, delta=0.01)
        self.assertEqual(oct5["balance_after"], fc["total"][fc["dates"].index("2026-10-05")])
        self.assertAlmostEqual(fc["total"][-1], 5000 - 600 - 110 - 310 - 310 / 30 * 2, delta=0.02)

    def test_budgeted_spending_from_checking_goes_out_on_banking_days(self):
        from runway import bankdays
        self.conn.execute(insert(Budget).values(category="Groceries", amount=310))
        self.conn.execute(update(Category).where(Category.name == "Groceries").values(pay_with="chk"))
        fc = forecast.build(self.conn, TODAY, 60)
        spend = fc["spend"]
        self.assertNotIn("2026-10-17", spend)
        self.assertNotIn("2026-10-18", spend)
        self.assertEqual((spend["2026-10-16"], spend["2026-10-19"]), (10.0, 30.0))
        self.assertEqual(self.drop(fc, "2026-10-17"), 0)
        self.assertAlmostEqual(self.drop(fc, "2026-10-19"), 30.0, places=2)
        self.assertFalse(bankdays.is_business_day(date(2026, 10, 12)))
        self.assertFalse({"2026-10-10", "2026-10-11", "2026-10-12"} & spend.keys())
        self.assertEqual(spend["2026-10-13"], 40.0)
        self.assertAlmostEqual(sum(v for d, v in spend.items() if d.startswith("2026-10")), 300.0, places=2)
        self.assertEqual(spend["2026-11-02"], round(10 + 2 * 310 / 30, 2))
        self.assertEqual(max(spend), "2026-11-20")
        self.assertAlmostEqual(sum(spend.values()), 110 + 310 + 310 / 30 * 20, places=1)
        self.assertEqual(fc["total"][-1], fc["total"][fc["dates"].index("2026-11-20")])
        self.assertEqual(fc["accounts"][0]["spend"], spend)

    def test_the_readout_has_each_accounts_budgeted_spending(self):
        self.conn.execute(update(Account).where(Account.id == "chk").values(in_forecast=1))
        self.acct("chk2", "checking", 1000.0, in_forecast=1)
        for name, amount, acct in (("Groceries", 310, "chk"), ("Travel", 62, "chk2")):
            self.conn.execute(insert(Budget).values(category=name, amount=amount))
            self.conn.execute(update(Category).where(Category.name == name).values(pay_with=acct))
        fc = forecast.build(self.conn, TODAY, 40)
        each = {a["id"]: a["spend"] for a in fc["accounts"]}
        self.assertEqual((each["chk"]["2026-10-19"], each["chk2"]["2026-10-19"], fc["spend"]["2026-10-19"]), (30.0, 6.0, 36.0))
        self.assertEqual(set(fc["spend"]), set(each["chk"]) | set(each["chk2"]))
        self.assertTrue(set(fc["spend"]) <= set(fc["dates"]))
        self.assertTrue(all(v > 0 for v in fc["spend"].values()))
        self.assertNotIn(fc["today"], fc["spend"])

    def test_a_budget_paid_with_a_card_is_charged_any_day(self):
        self.conn.execute(insert(Budget).values(category="Groceries", amount=310))
        self.conn.execute(update(Category).where(Category.name == "Groceries").values(pay_with="cc"))
        fc = forecast.build(self.conn, TODAY, 90)
        self.assertEqual((fc["spend"], fc["accounts"][0]["spend"]), ({}, {}))
        card = {e["date"]: -e["amount"] for e in fc["events"] if e["kind"] == "card"}
        self.assertAlmostEqual(card["2026-11-05"], 300 + 110 + 10 * 10, places=2)
        self.assertAlmostEqual(card["2026-12-07"], 21 * 10 + 10 * 310 / 30, places=2)

    def test_balance_after_takes_in_the_days_budgeted_spending(self):
        self.conn.execute(insert(Budget).values(category="Groceries", amount=310))
        self.conn.execute(update(Category).where(Category.name == "Groceries").values(pay_with="chk"))
        self.conn.execute(insert(Recurring).values(name="Gym", account_id="chk", amount=-50, frequency="monthly",
                                                   anchor_date="2026-10-15"))
        fc = forecast.build(self.conn, TODAY, 40)
        days = fc["dates"]
        gym = next(e for e in fc["events"] if e["name"] == "Gym")
        self.assertEqual(gym["date"], "2026-10-15")
        self.assertEqual(gym["balance_after"], fc["total"][days.index("2026-10-15")])
        self.assertAlmostEqual(gym["balance_after"], fc["total"][days.index("2026-10-14")] - 10 - 50, delta=0.01)

    def grocery_box(self, link=True):
        """A $100 grocery box from checking on the 1st of each month (Oct 1, Nov 1 in the forecast). Not linked, the
        September one was a $60 first box: too far from $100 to be linked on its own."""
        self.conn.execute(insert(Recurring).values(id=7, name="Grocery box", account_id="chk", amount=-100,
                                                   frequency="monthly", anchor_date="2026-09-01"))
        self.tx("chk", "2026-09-01", -100.0 if link else -60.0, "GROCERY BOX", "Groceries")
        if link:
            self.conn.execute(update(Transaction)
                              .where(Transaction.description == "GROCERY BOX").values(recurring_id=7))

    def test_a_budget_counts_its_recurring_payments_once(self):
        self.grocery_box()
        self.conn.execute(insert(Budget).values(category="Groceries", amount=500))
        self.conn.execute(update(Category).where(Category.name == "Groceries").values(pay_with="chk"))
        fc = forecast.build(self.conn, TODAY, 60)
        box = [e for e in fc["events"] if e.get("recurring_id") == 7]
        self.assertEqual([(e["date"], e["category"]) for e in box], [("2026-10-01", "Groceries"), ("2026-11-02", "Groceries")])
        self.assertAlmostEqual(self.drop(fc, "2026-10-15"), 400 / 31, delta=0.01)
        self.assertAlmostEqual(self.drop(fc, "2026-11-17"), 400 / 30, delta=0.01)
        self.assertAlmostEqual(self.drop(fc, "2026-09-24"), 200 / 7, delta=0.01)
        self.assertAlmostEqual(self.drop(fc, "2026-11-02"), 100 + 400 / 31 + 2 * 400 / 30, delta=0.01)

    def test_a_budget_its_recurring_payments_cover_adds_nothing(self):
        self.grocery_box()
        self.conn.execute(insert(Budget).values(category="Groceries", amount=80))
        self.conn.execute(update(Category).where(Category.name == "Groceries").values(pay_with="chk"))
        b = forecast.build(self.conn, TODAY, 60)["budget"]
        self.assertEqual((b["used"], b["skipped"][0]), ([], {"category": "Groceries", "reason": "a recurring item already covers it"}))

    def test_a_recurring_item_with_nothing_linked_yet_takes_its_category_from_what_it_matches(self):
        self.grocery_box(link=False)
        self.conn.execute(insert(Budget).values(category="Groceries", amount=500))
        self.conn.execute(update(Category).where(Category.name == "Groceries").values(pay_with="chk"))
        fc = forecast.build(self.conn, TODAY, 60)
        self.assertEqual({e["category"] for e in fc["events"] if e.get("recurring_id") == 7}, {"Groceries"})
        self.assertAlmostEqual(self.drop(fc, "2026-10-15"), 400 / 31, delta=0.01)

    def test_statement_you_entered_wins(self):
        key = self.cycle("cc")["statement_key"]
        self.assertEqual(key, "stmt:cc:2026-09-10")
        self.conn.execute(insert(Override).values(key=key, amount=950))
        info = self.cycle("cc")
        self.assertEqual((info["statement_balance"], info["statement_reported"], info["statement_set"], info["remaining"]),
                         (950.0, 800.0, True, 750.0))
        fc = forecast.build(self.conn, TODAY, 30)
        self.assertIn(("2026-10-05", -750.0), [(e["date"], e["amount"]) for e in fc["events"] if e["kind"] == "card"])
        self.stmt("cc", 300.0, "2026-10-10", "2026-11-05")
        info = self.cycle("cc", date(2026, 10, 12))
        self.assertEqual((info["statement_set"], info["statement_balance"]), (False, 300.0))

    def test_owed_positive_convention(self):
        self.conn.execute(update(Account).where(Account.id == "cc").values(balance=900, owed_positive=1))
        card = next(c for c in forecast.build(self.conn, TODAY, 30)["cards"] if c["id"] == "cc")
        self.assertEqual((card["owed_now"], card["statement_balance"]), (900.0, 800.0))

    def test_build(self):
        self.conn.execute(
            insert(Recurring).values(name="Paycheck", account_id="chk", amount=3000, frequency="biweekly",
                                     anchor_date="2026-09-18")
        )
        fc = forecast.build(self.conn, TODAY, 60)
        names = [(e["date"], e["name"], e["amount"], e["estimated"]) for e in fc["events"]]
        self.assertIn(("2026-10-05", "cc statement", -600.0, False), names)
        self.assertIn(("2026-10-02", "Paycheck", 3000.0, False), names)
        est = [e for e in fc["events"] if e["estimated"]]
        self.assertEqual([(e["date"], e["amount"]) for e in est], [("2026-11-05", -300.0)])
        self.assertAlmostEqual(fc["total"][-1], 5000 + sum(e["amount"] for e in fc["events"]), places=2)
        self.assertEqual(len(fc["total"]), 61)
        self.assertEqual(fc["warnings"], [])
        self.assertIsNone(fc["budget"])

    def test_balance_after_each_event(self):
        self.conn.execute(insert(Recurring).values(name="Paycheck", account_id="chk", amount=3000,
                                                   frequency="biweekly", anchor_date="2026-09-18"))
        self.conn.execute(insert(Recurring).values(name="Gym", account_id="chk", amount=-50, frequency="monthly",
                                                   anchor_date="2026-09-05"))
        fc = forecast.build(self.conn, TODAY, 60)
        bal = 5000.0
        for e in fc["events"]:
            bal += e["amount"]
            self.assertAlmostEqual(e["balance_after"], bal, places=2, msg=e["name"])
            same_day = [x for x in fc["events"] if x["date"] == e["date"]]
            if e is same_day[-1]:
                self.assertAlmostEqual(fc["total"][fc["dates"].index(e["date"])], bal, places=2)
        self.assertEqual(next(e for e in fc["events"] if e["kind"] == "card")["category"], "Credit Card Payment")

    def test_primary_only_and_flat_between_events(self):
        self.acct("sav", "savings", 20000.0)
        self.acct("chk2", "checking", 300.0)
        for i in range(60):
            self.tx("chk", (TODAY - timedelta(days=i)).isoformat(), -25.0, "TARGET", "Groceries")
        db.set_setting(self.conn, "primary_account", "chk")
        fc = forecast.build(self.conn, TODAY, 30)
        self.assertEqual([a["id"] for a in fc["accounts"]], ["chk"])
        self.assertEqual(fc["primary_id"], "chk")
        s, dates = fc["total"], fc["dates"]
        event_days = {e["date"] for e in fc["events"]}
        for i in range(1, len(s)):
            if dates[i] not in event_days:
                self.assertEqual(s[i], s[i - 1], f"balance moved on {dates[i]} with nothing scheduled")
        self.assertFalse({"daily_spend", "daily_spend_on", "daily_spend_estimate"} & set(fc["accounts"][0]))
        self.assertFalse(any("everyday_before" in e for e in fc["events"]))

    def test_card_without_bank_statements_warns(self):
        self.conn.execute(delete(CardStatement))
        fc = forecast.build(self.conn, TODAY, 30)
        self.assertEqual(fc["warnings"], ["Plaid hasn’t sent a statement for cc yet. Enter its latest statement so its "
                                          "payment is in the forecast."])
        self.assertEqual((fc["cards"], fc["unlinked_cards"]), ([], [{"id": "cc", "name": "cc", "owed_now": 900.0, "linked": True}]))
        self.assertEqual(fc["warning_links"], [{"text": fc["warnings"][0], "href": "#setup/accounts?account=cc", "setting": True}])
        self.conn.execute(update(Account).where(Account.id == "cc").values(plaid_account_id=None))
        fc = forecast.build(self.conn, TODAY, 30)
        self.assertEqual(fc["warnings"], ["Enter cc’s latest statement so its payment is in the forecast."])
        self.assertEqual(fc["warning_links"][0]["href"], "#setup/accounts?account=cc")
        self.conn.execute(insert(PlaidAccount).values(plaid_account_id="pcc", item_id="it", name="Visa", type="credit"))
        with mock.patch.object(plaidapi, "configured", return_value=True):
            fc = forecast.build(self.conn, TODAY, 30)
        self.assertEqual(fc["warnings"][0], "Enter cc’s latest statement (or link it through Plaid) so its payment is in the forecast.")
        self.assertIn("Plaid has 1 card waiting to be matched", fc["warnings"][1])
        self.assertEqual(fc["warning_links"][1]["href"], "#setup/accounts")

    def test_warnings_link_to_where_they_are_fixed(self):
        self.conn.execute(update(Account).where(Account.id == "cc").values(pay_from=None))
        fc = forecast.build(self.conn, TODAY, 30)
        self.assertEqual(fc["warning_links"], [{"text": "cc: choose which account pays it in Settings.", "href": "#setup/accounts", "setting": True}])
        self.assertEqual(fc["warnings"], ["cc: choose which account pays it in Settings."])

    def test_paid_statement_no_event(self):
        self.tx("cc", "2026-09-22", 600.0, "PAYMENT", "Credit Card Payment")
        self.conn.execute(update(Account).where(Account.id == "cc").values(balance=-300))
        info = self.cycle("cc")
        self.assertEqual((info["statement_balance"], info["remaining"]), (800.0, 0.0))
        fc = forecast.build(self.conn, TODAY, 30)
        self.assertFalse(any(e["name"] == "cc statement" for e in fc["events"]))

    def test_suggest_recurring(self):
        for d in ["2026-06-01", "2026-07-01", "2026-08-01", "2026-09-01"]:
            self.tx("chk", d, -2500.0, "MORTGAGE CO", "Mortgage")
        for d in ["2026-08-14", "2026-08-28", "2026-09-11"]:
            self.tx("chk", d, 3100.0, "ACME PAYROLL", "Income")
        s = {x["match"]: x for x in forecast.suggest_recurring(self.conn, TODAY)}
        self.assertEqual(s["mortgage co"]["frequency"], "monthly")
        self.assertEqual(s["acme payroll"]["frequency"], "biweekly")
        self.assertEqual(s["acme payroll"]["amount"], 3100.0)


class ScheduleAndMissedTests(LedgerCase):
    def test_dates_each_year_and_twice_a_month(self):
        from runway import forecast
        tax = {"frequency": "dates", "dates": "04-15,10-15", "anchor_date": "2026-01-01", "end_date": None}
        self.assertEqual([d.isoformat() for d in forecast.scheduled(tax, date(2026, 1, 1), date(2027, 12, 31))],
                         ["2026-04-15", "2026-10-15", "2027-04-15", "2027-10-15"])
        pay = {"frequency": "semimonthly", "dates": "15,31", "anchor_date": "2026-01-01", "end_date": None}
        self.assertEqual([d.isoformat() for d in forecast.scheduled(pay, date(2026, 1, 31), date(2026, 3, 1))],
                         ["2026-02-15", "2026-02-28"])
        q = {"frequency": "quarterly", "anchor_date": "2026-01-10", "end_date": None}
        self.assertEqual([d.isoformat() for d in forecast.scheduled(q, date(2026, 1, 1), date(2026, 12, 31))],
                         ["2026-01-10", "2026-04-10", "2026-07-10", "2026-10-10"])
        self.assertEqual(forecast.parse_dates("Apr 15, oct 15th", "dates"), [(4, 15), (10, 15)])
        with self.assertRaises(ValueError):
            forecast.parse_dates("13-40", "dates")

    def test_api_accepts_and_normalises_dates(self):
        from runway import server
        self.acct("chk", "checking", 1000.0)
        r = server.api_recurring_add(self.conn, None, {"name": "Property tax", "account_id": "chk", "amount": -2400,
                                                       "frequency": "dates", "dates": "Apr 15, Oct 15", "anchor_date": "2026-01-01"})
        self.assertEqual(self.conn.execute(select(Recurring.dates)
                                           .where(Recurring.id == r["id"])).fetchone()[0], "04-15,10-15")
        with self.assertRaises(server.ApiError):
            server.api_recurring_add(self.conn, None, {"name": "X", "account_id": "chk", "amount": -1, "frequency": "dates",
                                                       "dates": "whenever", "anchor_date": "2026-01-01"})

    def test_missed_payments(self):
        from runway import recurring
        self.acct("chk", "checking", 1000.0)
        self.tx("chk", "2026-06-01", -5.0, "OPENING", "Other")
        self.conn.execute(insert(Recurring).values(id=1, name="Gym", account_id="chk", amount=-40, frequency="monthly",
                                                   anchor_date="2026-07-05", match="gym", active=1))
        for d in ("2026-07-05", "2026-08-07"):
            self.tx("chk", d, -40.0, "GYM MEMBERSHIP", "Other")
        recurring.auto_match(self.conn)
        today = date(2026, 9, 23)
        m = recurring.missed(self.conn, today)
        self.assertEqual([x["date"] for x in m], ["2026-09-08"])
        self.assertEqual(recurring.missed(self.conn, date(2026, 9, 12)), [])
        recurring.dismiss(self.conn, m[0]["key"])
        self.assertEqual(recurring.missed(self.conn, today), [])
        self.conn.execute(delete(RecurringDismissed))
        self.tx("chk", "2026-09-06", -40.0, "CLUB FEE", "Other")
        tid = self.conn.execute(select(Transaction.id).where(Transaction.description == "CLUB FEE")).fetchone()[0]
        recurring.link(self.conn, tid, 1)
        self.assertEqual(recurring.missed(self.conn, today), [])


class ForecastEdgeTests(LedgerCase):
    """Card payments at the edges of the chart, payments due today or a few days late, and payments in transit."""
    card_setup = ForecastTests.card_setup

    def setUp(self):
        super().setUp()
        self.card_setup()

    def test_a_payment_pushed_past_the_last_day_is_left_off(self):
        self.stmt("cc", 800.0, "2026-08-10", "2026-09-05")
        today = date(2026, 9, 6)
        fc = forecast.build(self.conn, today, 90)
        self.assertEqual(fc["dates"][-1], "2026-12-05")
        self.assertTrue(all(fc["dates"][0] <= e["date"] <= fc["dates"][-1] for e in fc["events"]))
        self.assertNotIn("2026-12-07", [e["date"] for e in fc["events"]])

    def test_an_old_statement_is_a_warning_not_a_crash(self):
        self.stmt("cc", 800.0, "2026-06-10", "2026-07-05")
        fc = forecast.build(self.conn, TODAY, 90)
        self.assertTrue(all(e["date"] >= TODAY.isoformat() for e in fc["events"]))
        self.assertTrue(any("hasn't sent the statement after Jun 10" in w for w in fc["warnings"]), fc["warnings"])
        stale = next(w for w in fc["warning_links"] if "hasn't sent the statement" in w["text"])
        self.assertEqual((stale["href"], stale["setting"]), ("#setup/connections", False))

    def test_a_late_payment_is_flagged_as_not_a_setting(self):
        self.stmt("cc", 800.0, "2026-08-10", "2026-09-05")
        fc = forecast.build(self.conn, date(2026, 9, 10), 30)
        late = next(w for w in fc["warning_links"] if "no payment has shown up yet" in w["text"])
        self.assertEqual((late["href"], late["setting"]), ("#setup/accounts", False))

    def test_a_payment_due_today_is_in_todays_balance(self):
        today = date(2026, 10, 5)
        fc = forecast.build(self.conn, today, 30)
        e = next(e for e in fc["events"] if e["kind"] == "card" and not e["estimated"])
        self.assertEqual((e["date"], e["amount"], e["balance_after"]), ("2026-10-05", -600.0, 4400.0))
        self.assertEqual(fc["total"][0], 4400.0)
        self.assertAlmostEqual(fc["total"][-1], 5000 + sum(x["amount"] for x in fc["events"]), places=2)

    def test_rent_due_today_or_a_few_days_late_stays_in(self):
        for d in ("2026-07-01", "2026-08-01", "2026-09-01"):
            self.tx("chk", d, -2000.0, "LANDLORD LLC", "Rent")
        self.conn.execute(insert(Recurring).values(name="Rent", account_id="chk", amount=-2000, frequency="monthly",
                                                   anchor_date="2026-07-01", match="landlord"))
        recurring.auto_match(self.conn)

        def rent(today):
            return [(e["date"], e.get("late_from")) for e in forecast.build(self.conn, today, 20)["events"] if e["name"] == "Rent"]
        self.assertEqual(rent(date(2026, 10, 1)), [("2026-10-01", None)])
        self.assertEqual(rent(date(2026, 10, 3)), [("2026-10-03", "2026-10-01")])
        self.assertEqual(rent(date(2026, 10, 8)), [])
        self.assertEqual([m["date"] for m in recurring.missed(self.conn, date(2026, 10, 8))], ["2026-10-01"])
        self.tx("chk", "2026-10-02", -2000.0, "LANDLORD LLC", "Rent")
        recurring.auto_match(self.conn)
        self.assertEqual(rent(date(2026, 10, 3)), [])

    def test_a_card_payment_in_transit_counts_once(self):
        self.tx("chk", "2026-09-22", -600.0, "CHASE CREDIT CRD AUTOPAY", "Credit Card Payment")
        info = self.cycle("cc")
        self.assertEqual((info["paid_since_close"], info["remaining"]), (800.0, 0.0))
        self.assertFalse(any(e["kind"] == "card" and not e["estimated"] for e in forecast.build(self.conn, TODAY, 30)["events"]))
        self.tx("cc", "2026-09-23", 600.0, "PAYMENT THANK YOU", "Credit Card Payment")
        self.assertEqual(self.cycle("cc")["paid_since_close"], 800.0)
        self.conn.execute(update(Transaction)
                          .where(Transaction.description == "PAYMENT THANK YOU", Transaction.amount == 600)
                          .values(posted="2026-09-09"))
        self.conn.execute(update(Transaction)
                          .where(Transaction.account_id == "chk", Transaction.amount == -600)
                          .values(posted="2026-09-11"))
        self.assertEqual(self.cycle("cc")["paid_since_close"], 200.0)
        self.conn.execute(update(Transaction)
                          .where(Transaction.description == "PAYMENT THANK YOU", Transaction.amount == 600)
                          .values(posted="2026-09-23"))
        self.acct("cc2", "credit", -50.0, pay_from="chk")
        self.conn.execute(delete(Transaction).where(
            Transaction.id == select(func.max(Transaction.id)).where(Transaction.account_id == "cc").scalar_subquery()))
        self.assertEqual(self.cycle("cc")["paid_since_close"], 200.0)

    def test_recurring_charges_on_cards_are_listed_apart(self):
        self.conn.execute(update(Account).where(Account.id == "cc").values(display_name="Travel Card"))
        before = forecast.build(self.conn, TODAY, 60)["events"]
        self.conn.execute(insert(Recurring).values(name="Streaming", account_id="cc", amount=-15, frequency="monthly",
                                                   anchor_date="2026-09-30"))
        fc = forecast.build(self.conn, TODAY, 60)
        self.assertEqual([(e["date"], e["name"], e["amount"], e["kind"], e["account_id"], e["account"]) for e in fc["charges"]],
                         [("2026-09-30", "Streaming", -15.0, "recurring", "cc", "Travel Card"),
                          ("2026-10-30", "Streaming", -15.0, "recurring", "cc", "Travel Card")])
        self.assertNotIn("Streaming", [e["name"] for e in fc["events"]])
        self.assertEqual([(e["date"], e["name"]) for e in fc["events"]], [(e["date"], e["name"]) for e in before])
        self.assertEqual(forecast.build(self.conn, TODAY, 5)["charges"], [])

    def test_recurring_card_charges_count_on_a_new_card(self):
        before = {e["date"]: e["amount"] for e in forecast.build(self.conn, TODAY, 60)["events"] if e["estimated"]}
        self.conn.execute(insert(Recurring).values(name="Streaming", account_id="cc", amount=-15, frequency="monthly",
                                                   anchor_date="2026-09-30"))
        after = {e["date"]: e["amount"] for e in forecast.build(self.conn, TODAY, 60)["events"] if e["estimated"]}
        self.assertAlmostEqual(after["2026-11-05"], before["2026-11-05"] - 15, places=2)

    def test_two_dates_on_the_same_business_day_are_two_payments(self):
        item = {"frequency": "dates", "dates": "10-10, 10-11", "anchor_date": "2026-01-01", "amount": -100}
        days = forecast.occurrences(item, date(2026, 10, 1), date(2026, 10, 31))
        self.assertEqual(len(days), 2)
        self.assertEqual(days[0], days[1])


class ForecastAssumptionTests(LedgerCase):
    """What the forecast assumes about pending transactions, card cycles and budgets."""
    card_setup = ForecastTests.card_setup

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

    drop = ForecastTests.drop

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

    def no_writes(self, fail: Exception):
        """Make every INSERT, UPDATE or DELETE on self.conn raise `fail`. Returns the patch, to stop it early."""
        execute = self.conn.execute

        def guarded(stmt, params=None):
            if getattr(stmt, "is_dml", False):
                raise fail
            return execute(stmt, params)
        patch = mock.patch.object(self.conn, "execute", guarded)
        patch.start()
        self.addCleanup(patch.stop)
        return patch

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


class PaymentModeTests(LedgerCase):
    """Cards paid in full, the minimum or a fixed amount, with what isn't paid carried to the next statement."""
    card_setup = ForecastTests.card_setup
    no_writes = ForecastAssumptionTests.no_writes
    INSURANCE = 450.0
    EST1 = 300 + INSURANCE
    EST2 = INSURANCE

    def setUp(self):
        super().setUp()
        self.card_setup()
        self.conn.execute(insert(Recurring).values(name="Insurance", account_id="cc", amount=-self.INSURANCE,
                                                   frequency="monthly", anchor_date="2026-09-25"))

    def pay(self, mode, amount=None, apr=None):
        db.set_setting(self.conn, sk.card_pay_mode("cc"), mode)
        db.set_setting(self.conn, sk.card_pay_amount("cc"), amount)
        db.set_setting(self.conn, sk.card_apr("cc"), apr)

    def payments(self, fc):
        return {e["date"]: -e["amount"] for e in fc["events"] if e["kind"] == "card"}

    def card(self, fc):
        return next(c for c in fc["cards"] if c["id"] == "cc")

    def interest_warned(self, fc):
        return any("doesn’t count the interest" in w for w in fc["warnings"])

    @staticmethod
    def minimum(statement, interest=0.0):
        """The minimum without one from the issuer: the larger of $25 and 1% of the statement plus its interest."""
        return round(min(statement, max(25.0, statement * 0.01 + interest)), 2)

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
        self.assertEqual(ForecastTests.drop(self, fc, "2026-10-20"), 0.0)
        self.assertAlmostEqual(ForecastTests.drop(self, fc, "2026-11-17"), 50 / 30, delta=0.01)
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


class EstimatePartsTests(LedgerCase):
    """What an estimated statement is made of (forecast.estimate_parts), on ForecastTests' card: its statements close the
    10th and are due the 5th; $300 charged since the Sep 10 close."""
    pay = PaymentModeTests.pay

    def setUp(self):
        super().setUp()
        ForecastTests.card_setup(self)

    def estimates(self, days=90, card="cc"):
        """{payment date: estimate} for a card's estimated statements, each checked to add up to the cent."""
        out = {}
        for e in forecast.build(self.conn, TODAY, days)["events"]:
            if e["kind"] != "card" or not e["estimated"] or e["card_id"] != card:
                continue
            est = e["estimate"]
            cents = lambda v: round(v * 100)
            parts = [est.get(k, 0.0) for k in ("charged_so_far", "owed_now", "budgets_total", "recurring_total", "fees_total",
                                                "carried", "interest")]
            self.assertEqual(sum(map(cents, parts)), cents(est["statement"]), est)
            for name in ("budgets", "recurring", "fees"):
                self.assertEqual(name in est, f"{name}_total" in est, est)
                if name in est:
                    self.assertEqual(sum(cents(i["amount"]) for i in est[name]), cents(est[f"{name}_total"]), est)
            self.assertEqual(est["total"], -e["amount"])
            out[e["date"]] = est
        return out

    def test_whats_charged_and_the_budgets_on_the_card(self):
        self.conn.execute(insert(Budget).values(category="Groceries", amount=500))
        self.conn.execute(update(Category).where(Category.name == "Groceries").values(pay_with="cc"))
        self.conn.execute(insert(Budget).values(category="Restaurants", amount=93))
        self.conn.execute(update(Category).where(Category.name == "Restaurants").values(pay_with="cc"))
        est = self.estimates()
        oct10 = est["2026-11-05"]
        self.assertEqual((oct10["close"], oct10["due"], oct10["charged_so_far"]), ("2026-10-10", "2026-11-05", 300.0))
        self.assertEqual([b["category"] for b in oct10["budgets"]], ["Groceries", "Restaurants"])
        self.assertAlmostEqual(oct10["budgets"][0]["amount"], 300 + 500 / 31 * 10, delta=0.01)
        self.assertAlmostEqual(oct10["budgets"][1]["amount"], 93 / 31 * 10, delta=0.01)
        self.assertAlmostEqual(oct10["total"], 300 + 300 + 593 / 31 * 10, delta=0.01)
        for k in ("recurring", "fees", "carried", "interest", "pay_mode", "owed_now", "assumed_cycle"):
            self.assertNotIn(k, oct10)
        nov10 = est["2026-12-07"]
        self.assertNotIn("charged_so_far", nov10)
        self.assertAlmostEqual(nov10["budgets_total"], 593 / 31 * 21 + 593 / 30 * 10, delta=0.01)

    def test_recurring_charges_and_an_annual_fee(self):
        self.conn.execute(insert(Recurring).values(name="Insurance", account_id="cc", amount=-600, frequency="yearly",
                                                   anchor_date="2025-10-20", match="insurer"))
        self.conn.execute(insert(Recurring).values(name="Streaming", account_id="cc", amount=-15, frequency="monthly",
                                                   anchor_date="2026-09-30"))
        AnnualFeeTests.churn(self, opened="2023-11-02", account_id="cc")
        nov10 = self.estimates(days=120)["2026-12-07"]
        self.assertEqual(nov10["recurring"], [{"name": "Insurance", "amount": 600.0}, {"name": "Streaming", "amount": 15.0}])
        self.assertEqual(nov10["fees"], [{"name": "Sapphire annual fee", "amount": 95.0}])
        self.assertEqual(nov10["total"], 710.0)

    def test_paying_the_minimum_with_interest(self):
        self.conn.execute(insert(Recurring).values(name="Insurance", account_id="cc", amount=-450, frequency="monthly",
                                                   anchor_date="2026-09-25"))
        self.stmt("cc", 800.0, "2026-09-10", "2026-10-05", minimum=250.0)
        self.pay("minimum", apr="24")
        oct10 = self.estimates()["2026-11-05"]
        i1 = (550 + 750 / 2) * 0.02
        self.assertEqual((oct10["charged_so_far"], oct10["recurring_total"]), (300.0, 450.0))
        self.assertEqual((oct10["carried"], oct10["interest"], oct10["apr"]), (550.0, round(i1, 2), 24.0))
        self.assertEqual(oct10["statement"], round(550 + i1 + 750, 2))
        self.assertEqual((oct10["total"], oct10["pay_mode"]), (PaymentModeTests.minimum(oct10["statement"], i1), "minimum"))

    def test_a_card_with_no_statement_yet(self):
        self.acct("cc3", "credit", -40.0, pay_from="chk")
        self.conn.execute(insert(Budget).values(category="Travel", amount=310))
        self.conn.execute(update(Category).where(Category.name == "Travel").values(pay_with="cc3"))
        est = self.estimates(card="cc3")
        sep = est["2026-10-26"]
        self.assertEqual((sep["assumed_cycle"], sep["close"], sep["due"], sep["owed_now"]), (True, "2026-09-30", "2026-10-25", 40.0))
        self.assertEqual((sep["budgets"], sep["total"]), ([{"category": "Travel", "amount": 310.0}], 350.0))
        self.assertNotIn("charged_so_far", sep)
        oct_ = est["2026-11-25"]
        self.assertEqual((oct_["close"], oct_["total"]), ("2026-10-31", 310.0))
        self.assertNotIn("owed_now", oct_)

    def test_an_amount_you_set_is_not_an_estimate(self):
        self.conn.execute(insert(Budget).values(category="Groceries", amount=500))
        self.conn.execute(update(Category).where(Category.name == "Groceries").values(pay_with="cc"))
        self.conn.execute(insert(Override).values(key="cardclose:cc:2026-10-10", amount=-123.0))
        fc = forecast.build(self.conn, TODAY, 90)
        e = next(e for e in fc["events"] if e.get("key") == "cardclose:cc:2026-10-10")
        self.assertEqual((e["amount"], e["overridden"], e["estimated"]), (-123.0, True, False))
        self.assertNotIn("estimate", e)
        nxt = next(e for e in fc["events"] if e.get("key") == "cardclose:cc:2026-11-10")
        self.assertTrue(nxt["estimated"] and nxt["estimate"])

    def test_an_amount_you_set_on_a_learned_recurring_amount_is_not_an_estimate(self):
        rid = self.conn.execute(insert(Recurring).values(name="Power", account_id="chk", amount=-120, frequency="monthly",
                                                         anchor_date="2026-08-15", match="power co", amount_mode="avg3")).lastrowid
        events = lambda: {e["key"]: e for e in forecast.build(self.conn, TODAY, 60)["events"] if e.get("recurring_id") == rid}
        self.assertTrue(events()[f"rec:{rid}:2026-10-15"]["estimated"])
        self.conn.execute(insert(Override).values(key=f"rec:{rid}:2026-10-15", amount=-140.0))
        got = events()
        self.assertEqual((got[f"rec:{rid}:2026-10-15"]["amount"], got[f"rec:{rid}:2026-10-15"]["estimated"]), (-140.0, False))
        self.assertGreater(len(got), 1)
        self.assertTrue(all(e["estimated"] for k, e in got.items() if k != f"rec:{rid}:2026-10-15"))

    def test_to_cents(self):
        self.assertEqual(forecast.to_cents([1 / 3, 1 / 3, 1 / 3], 1.0), [0.34, 0.33, 0.33])
        self.assertEqual(forecast.to_cents([0.005, 0.005], 0.0), [0.0, 0.0])
        self.assertEqual(forecast.to_cents([10.004, 5.003, -2.0], 13.01), [10.01, 5.0, -2.0])
        self.assertEqual(forecast.to_cents([], 0.0), [])


class AnnualFeeTests(LedgerCase):
    """Churning cards' annual fees, on ForecastTests' card (its statements close the 10th and are due the 5th)."""

    def setUp(self):
        super().setUp()
        ForecastTests.card_setup(self)

    def churn(self, opened="2024-10-10", product="Sapphire", annual_fee=95.0, **kw):
        """A churning card; by default opened on the 10th, the fixture card's closing day, so its fee is on the close."""
        self.conn.execute(insert(ChurnCard).values(owner="Alex", issuer="chase", product=product, opened_on=opened,
                                                   annual_fee=annual_fee, **kw))

    def payments(self, fc):
        return {e["date"]: e["amount"] for e in fc["events"] if e["kind"] == "card"}

    def assert_paid_with_their_statements(self, fc):
        """Each fee's paid_on is the payment of its card's statement whose estimate has it: each statement's fees are
        the ones paid on its date, and every fee paid on a date is on a statement."""
        statements = [e for e in fc["events"] if e["kind"] == "card" and e.get("estimate")]
        for e in statements:
            paid = [{"name": f["name"], "amount": -f["amount"]} for f in fc["fees"]
                    if f["account_id"] == e["card_id"] and f["paid_on"] == e["date"]]
            self.assertEqual(e["estimate"].get("fees", []), paid, e)
        for f in fc["fees"]:
            if f["paid_on"]:
                self.assertIn((f["account_id"], f["paid_on"]), [(e["card_id"], e["date"]) for e in statements], f)

    def fees_budget(self, amount=150, pay_with="chk"):
        """A budget for Fees & Interest, paid from checking unless said otherwise, and Groceries ($500) on the card."""
        self.conn.execute(insert(Budget).values(category="Groceries", amount=500))
        self.conn.execute(update(Category).where(Category.name == "Groceries").values(pay_with="cc"))
        self.conn.execute(insert(Budget).values(category="Fees & Interest", amount=amount))
        self.conn.execute(update(Category).where(Category.name == "Fees & Interest").values(pay_with=pay_with))

    def test_a_fee_on_the_close_date_is_on_that_statement_with_a_fees_budget_paid_from_checking(self):
        self.fees_budget()
        before = forecast.build(self.conn, TODAY, 90)
        self.churn(account_id="cc")
        fc = forecast.build(self.conn, TODAY, 90)
        nov5 = next(e for e in fc["events"] if e["date"] == "2026-11-05" and e.get("card_id") == "cc")
        est = nov5["estimate"]
        groceries = 300 + 500 / 31 * 10
        self.assertEqual((est["close"], est["charged_so_far"], est["fees"]), ("2026-10-10", 300.0, [{"name": "Sapphire annual fee", "amount": 95.0}]))
        self.assertEqual([b["category"] for b in est["budgets"]], ["Groceries"])
        self.assertAlmostEqual(est["budgets_total"], groceries, delta=0.005)
        self.assertEqual(est["total"], round(300 + groceries + 95, 2))
        self.assertEqual(-nov5["amount"], est["total"])
        self.assertEqual(EstimatePartsTests.estimates(self)["2026-11-05"], est)
        self.assertAlmostEqual(ForecastTests.drop(self, fc, "2026-11-05") - 150 / 30, est["total"], delta=0.011)
        self.assertAlmostEqual(ForecastTests.drop(self, before, "2026-10-20"), 150 / 31, delta=0.01)
        self.assertAlmostEqual(ForecastTests.drop(self, fc, "2026-10-20"), 55 / 31, delta=0.01)
        october = [v for d, v in fc["spend"].items() if d.startswith("2026-10")]
        self.assertAlmostEqual(sum(october), 55 * 30 / 31, delta=0.005 * len(october))
        self.assertAlmostEqual(fc["total"][-1], before["total"][-1], delta=0.011)
        self.assertEqual((fc["fees"][0]["paid_on"], fc["fees"][0]["paid_from"]), ("2026-11-05", "chk"))
        self.assert_paid_with_their_statements(fc)

    def test_a_fee_the_day_after_the_close_is_on_the_next_statement_with_nothing_else_on_it(self):
        self.fees_budget()
        self.acct("cc2", "credit", 0.0, pay_from="chk")
        self.stmt("cc2", 0.0, "2026-09-10", "2026-10-05")
        cc2 = lambda fc: [(e["date"], e["amount"], e["estimate"]["close"]) for e in fc["events"] if e.get("card_id") == "cc2"]
        self.assertEqual(cc2(forecast.build(self.conn, TODAY, 90)), [])
        self.churn(opened="2024-10-11", account_id="cc2")
        fc = forecast.build(self.conn, TODAY, 90)
        self.assertEqual([(f["date"], f["paid_on"]) for f in fc["fees"]], [("2026-10-11", "2026-12-07")])
        self.assertEqual(cc2(fc), [("2026-12-07", -95.0, "2026-11-10")])
        self.assertEqual(next(e for e in fc["events"] if e.get("card_id") == "cc2")["estimate"]["fees"],
                         [{"name": "Sapphire annual fee", "amount": 95.0}])
        self.assert_paid_with_their_statements(fc)
        self.conn.execute(update(ChurnCard).values(opened_on="2024-10-10"))
        fc = forecast.build(self.conn, TODAY, 90)
        self.assertEqual(cc2(fc), [("2026-11-05", -95.0, "2026-10-10")])
        self.assert_paid_with_their_statements(fc)
        self.stmt("cc2", 0.0, "2026-10-10", "2026-11-05")
        fc = forecast.build(self.conn, date(2026, 10, 11), 90)
        self.assertEqual([(f["date"], f["late_from"], f["paid_on"]) for f in fc["fees"]], [("2026-10-11", "2026-10-10", "2026-12-07")])
        self.assertEqual(cc2(fc), [("2026-12-07", -95.0, "2026-11-10")])
        self.assert_paid_with_their_statements(fc)

    def test_a_fee_after_the_closing_day_is_dated_on_the_anniversary_as_churning_dates_it(self):
        self.acct("cc2", "credit", 0.0, pay_from="chk")
        self.stmt("cc2", 0.0, "2026-09-20", "2026-10-15")
        self.churn(opened="2024-10-21", product="Venture X", annual_fee=395.0, account_id="cc2")
        fc = forecast.build(self.conn, TODAY, 90)
        self.assertEqual([(f["date"], f["paid_on"]) for f in fc["fees"]], [("2026-10-21", "2026-12-15")])
        cc2 = [e for e in fc["events"] if e.get("card_id") == "cc2"]
        self.assertEqual([(e["date"], e["amount"], e["estimate"]["close"], e["estimate"]["fees"]) for e in cc2],
                         [("2026-12-15", -395.0, "2026-11-20", [{"name": "Venture X annual fee", "amount": 395.0}])])
        self.assert_paid_with_their_statements(fc)
        s = churning.state(self.conn, TODAY)
        card = next(c for c in s["cards"] if c["product"] == "Venture X")
        self.assertEqual(card["fee_due"], "2026-10-21")
        self.assertIn("2026-10-21", [u["date"] for u in churning.upcoming(s, TODAY) if u["kind"] == "fee"])

    def test_a_fee_that_posts_takes_the_place_of_the_projected_one(self):
        self.fees_budget()
        self.churn(account_id="cc")
        today = date(2026, 10, 10)
        before = forecast.build(self.conn, today, 60)
        self.assertEqual([f["date"] for f in before["fees"]], ["2026-10-10"])
        self.tx("cc", "2026-10-10", -95.0, "SAPPHIRE RENEWAL", "Fees & Interest")
        after = forecast.build(self.conn, today, 60)
        self.assertEqual(after["fees"], [])
        was, now = (next(e for e in fc["events"] if e.get("key") == "cardclose:cc:2026-10-10") for fc in (before, after))
        self.assertEqual((was["estimate"]["charged_so_far"], was["estimate"]["fees_total"]), (300.0, 95.0))
        self.assertEqual(now["estimate"]["charged_so_far"], 395.0)
        self.assertNotIn("fees", now["estimate"])
        self.assertEqual((now["amount"], now["date"]), (was["amount"], was["date"]))
        self.assertEqual((after["total"], after["spend"]), (before["total"], before["spend"]))

    def test_a_fee_charged_under_another_name_counts_as_charged(self):
        self.churn(account_id="cc")
        listed = lambda: [f["name"] for f in forecast.build(self.conn, TODAY, 90)["fees"]]
        self.tx("cc", "2026-09-14", -12.34, "INTEREST CHARGE", "Fees & Interest")
        self.tx("cc", "2026-09-15", -95.0, "OUTDOOR STORE", "Shopping")
        self.tx("cc", "2025-10-10", -95.0, "CARD RENEWAL", "Fees & Interest")
        self.tx("chk", "2026-09-16", -95.0, "CARD RENEWAL", "Fees & Interest")
        self.assertEqual(listed(), ["Sapphire annual fee"])
        self.assertFalse(forecast.fee_posted(self.conn, "cc", date(2026, 10, 10), 95.0))
        self.tx("cc", "2026-09-17", -95.0, "CARD RENEWAL", "Fees & Interest", pending=1)
        self.assertEqual(listed(), [])
        self.assertTrue(forecast.fee_posted(self.conn, "cc", date(2026, 10, 10), 95.0))
        self.assertFalse(forecast.fee_posted(self.conn, "cc", date(2026, 10, 10), 550.0))
        self.assertFalse(forecast.fee_posted(self.conn, "cc", date(2026, 10, 10)))

    def test_each_fee_is_paid_on_the_statement_that_has_it(self):
        self.fees_budget()
        self.acct("cc3", "credit", 0.0, pay_from="chk")
        self.churn(account_id="cc")
        self.churn(opened="2024-12-03", product="Gold", annual_fee=250.0, account_id="cc3")
        fc = forecast.build(self.conn, TODAY, 420)
        self.assertEqual([(f["name"], f["date"], f["paid_on"]) for f in fc["fees"]],
                         [("Sapphire annual fee", "2026-10-10", "2026-11-05"), ("Gold annual fee", "2026-12-03", "2027-01-25"),
                          ("Sapphire annual fee", "2027-10-10", "2027-11-05")])
        self.assert_paid_with_their_statements(fc)

    def test_fee_is_a_charge_on_the_card_paid_with_its_statement(self):
        before = forecast.build(self.conn, TODAY, 90)
        self.churn(account_id="cc")
        fc = forecast.build(self.conn, TODAY, 90)
        self.assertEqual(fc["fees"], [{
            "date": "2026-10-10", "amount": -95.0, "kind": "fee", "churn_card_id": 1, "name": "Sapphire annual fee",
            "account_id": "cc", "account": "cc", "category": "Fees & Interest", "paid_on": "2026-11-05", "paid_from": "chk"}])
        self.assertNotIn("fee", [e["kind"] for e in fc["events"]])
        was, now = self.payments(before), self.payments(fc)
        self.assertAlmostEqual(now["2026-11-05"] - was["2026-11-05"], -95.0, places=2)
        self.assertEqual({d: a for d, a in now.items() if d != "2026-11-05"}, {d: a for d, a in was.items() if d != "2026-11-05"})
        i = fc["dates"].index("2026-11-05")
        self.assertAlmostEqual(fc["total"][i] - before["total"][i], -95.0, places=2)
        self.assertEqual(fc["total"][i - 1], before["total"][i - 1])
        card = next(c for c in fc["cards"] if c["id"] == "cc")
        self.assertEqual(card["annual_fees"], [{"date": "2026-10-10", "amount": -95.0, "category": "Fees & Interest"}])
        self.assert_paid_with_their_statements(fc)

    def test_a_product_change_keeps_the_accounts_anniversary(self):
        self.churn(opened="2023-10-20", product="Reserve", annual_fee=0.0, status="product_changed")
        self.churn(opened="2026-07-01", product="Preferred", account_id="cc", changed_from=1)
        fc = forecast.build(self.conn, TODAY, 90)
        self.assertEqual([(f["name"], f["date"], f["paid_on"]) for f in fc["fees"]], [("Preferred annual fee", "2026-10-20", "2026-12-07")])
        card = next(c for c in churning.load(self.conn)["cards"] if c["product"] == "Preferred")
        self.assertEqual(churning.next_fee(card, TODAY), date(2026, 10, 20))

    def test_fee_after_the_close_is_on_the_next_statement(self):
        before = self.payments(forecast.build(self.conn, TODAY, 90))
        self.assertNotIn("2026-12-07", before)
        self.churn(opened="2023-10-15", account_id="cc")
        fc = forecast.build(self.conn, TODAY, 90)
        self.assertEqual((fc["fees"][0]["date"], fc["fees"][0]["paid_on"]), ("2026-10-15", "2026-12-07"))
        self.assertEqual(self.payments(fc), {**before, "2026-12-07": -95.0})
        self.assert_paid_with_their_statements(fc)

    def test_fee_past_the_horizon_of_its_payment_is_only_listed(self):
        before = forecast.build(self.conn, TODAY, 80)
        self.churn(opened="2022-12-10", account_id="cc")
        fc = forecast.build(self.conn, TODAY, 80)
        self.assertEqual((fc["fees"][0]["date"], fc["fees"][0]["paid_on"]), ("2026-12-10", None))
        self.assertEqual(fc["total"], before["total"])

    def test_no_fee_for_closed_planned_free_or_charged_cards(self):
        self.churn(status="closed", closed_on="2026-01-05", account_id="cc")
        self.churn(status="product_changed", closed_on="2026-01-05", account_id="cc")
        self.churn(closed_on="2026-10-01", account_id="cc")
        self.churn(plan="close", account_id="cc")
        self.churn(plan="product_change", plan_date="2026-10-01", account_id="cc")
        self.churn(annual_fee=0.0, account_id="cc")
        self.churn(annual_fee=None, account_id="cc")
        self.churn(opened="2026-01-15", account_id="cc")
        before = forecast.build(self.conn, TODAY, 90)
        self.assertEqual(before["fees"], [])
        self.churn(plan="close", plan_date="2026-10-20", account_id="cc")
        self.churn(plan="keep", account_id="cc")
        self.assertEqual(len(forecast.build(self.conn, TODAY, 90)["fees"]), 2)

    def test_no_fee_once_charged(self):
        self.churn(account_id="cc")
        self.tx("cc", "2025-10-10", -95.0, "ANNUAL FEE", "Fees & Interest")
        self.assertEqual(len(forecast.build(self.conn, TODAY, 90)["fees"]), 1)
        self.tx("cc", "2026-09-21", 95.0, "ANNUAL FEE REFUND", "Refunds")
        self.assertEqual(len(forecast.build(self.conn, TODAY, 90)["fees"]), 1)
        self.tx("cc", "2026-09-22", -95.0, "ANNUAL MEMBERSHIP FEE", "Fees & Interest", pending=1)
        fc = forecast.build(self.conn, TODAY, 90)
        self.assertEqual(fc["fees"], [])
        card = next(c for c in fc["cards"] if c["id"] == "cc")
        self.assertEqual(card["annual_fees"], [])

    def test_late_fee_comes_today(self):
        self.churn(account_id="cc")
        self.churn(opened="2024-10-02", product="Unlinked")
        fees = forecast.build(self.conn, date(2026, 10, 15), 60)["fees"]
        self.assertEqual([(f["name"], f["date"], f["late_from"]) for f in fees], [("Sapphire annual fee", "2026-10-15", "2026-10-10")])
        self.tx("cc", "2026-10-12", -95.0, "ANNUAL FEE")
        self.assertEqual(forecast.build(self.conn, date(2026, 10, 15), 60)["fees"], [])
        self.assertEqual(forecast.build(self.conn, date(2026, 11, 1), 60)["fees"], [])

    def test_a_recurring_item_for_the_fee_is_not_doubled(self):
        self.churn(account_id="cc")
        self.conn.execute(insert(Recurring).values(name="Sapphire fee", account_id="cc", amount=-95, frequency="yearly",
                                                   anchor_date="2025-10-12", active=1))
        self.assertEqual(forecast.build(self.conn, TODAY, 90)["fees"], [])
        self.conn.execute(update(Recurring).values(name="Sapphire travel credit"))
        self.assertEqual(len(forecast.build(self.conn, TODAY, 90)["fees"]), 1)
        self.conn.execute(update(Recurring).values(name="Sapphire fee", anchor_date="2026-03-12"))
        self.assertEqual(len(forecast.build(self.conn, TODAY, 90)["fees"]), 1)

    def test_unlinked_card_is_listed_without_touching_cash(self):
        before = forecast.build(self.conn, TODAY, 90)
        self.churn(opened="2020-11-30", product="Gold")
        self.acct("hidden", "credit", -10.0, hidden=1)
        self.churn(opened="2021-12-05", product="Hidden", account_id="hidden")
        self.acct("sav", "savings", 100.0)
        self.churn(opened="2021-11-03", product="Odd", account_id="sav")
        fc = forecast.build(self.conn, TODAY, 90)
        self.assertEqual([(f["name"], f["date"], f["account_id"], f["paid_on"]) for f in fc["fees"]],
                         [("Odd annual fee", "2026-11-03", None, None), ("Gold annual fee", "2026-11-30", None, None),
                          ("Hidden annual fee", "2026-12-05", None, None)])
        self.assertEqual((fc["total"], self.payments(fc)), (before["total"], self.payments(before)))

    def test_a_card_without_a_statement_is_listed_on_its_anniversary(self):
        self.acct("cc2", "credit", -50.0, pay_from="chk")
        self.churn(opened="2019-11-17", account_id="cc2")
        fees = forecast.build(self.conn, TODAY, 90)["fees"]
        self.assertEqual([(f["date"], f["account_id"], f["paid_on"]) for f in fees], [("2026-11-17", "cc2", None)])

    def test_short_months_and_leap_days(self):
        card = {"id": 1, "product": "Plat", "annual_fee": 695.0, "opened_on": "2024-02-29", "status": "open"}
        on = lambda today, end: [f["date"] for f in forecast.annual_fees(self.conn, card, today, end, [])]
        self.assertEqual(on(date(2027, 1, 15), date(2027, 3, 31)), ["2027-02-28"])
        self.assertEqual(on(date(2027, 3, 1), date(2028, 3, 31)), ["2028-02-29"])
        self.assertEqual(on(date(2024, 3, 1), date(2025, 1, 31)), [])
        card.update(opened_on="2023-10-31", fee_month=3)
        self.assertEqual(on(date(2026, 9, 23), date(2027, 11, 30)), ["2026-10-31", "2027-10-31"])
        self.assertEqual(on(date(2026, 11, 1), date(2027, 9, 30)), [])

    def test_a_budget_for_fees_has_the_fee_only_when_its_charged_to_the_card(self):
        self.conn.execute(insert(Budget).values(category="Groceries", amount=500))
        self.conn.execute(update(Category).where(Category.name == "Groceries").values(pay_with="cc"))
        before = self.payments(forecast.build(self.conn, TODAY, 90))
        self.churn(account_id="cc")
        self.assertAlmostEqual(self.payments(forecast.build(self.conn, TODAY, 90))["2026-11-05"] - before["2026-11-05"], -95.0, places=2)
        self.conn.execute(insert(Budget).values(category="Fees & Interest", amount=10))
        self.conn.execute(update(Category).where(Category.name == "Fees & Interest").values(pay_with="cc"))
        with_fees = self.payments(forecast.build(self.conn, TODAY, 90))
        self.churn(product="Second", account_id="cc")
        again = forecast.build(self.conn, TODAY, 90)
        self.assertEqual(len(again["fees"]), 2)
        self.assertEqual(self.payments(again), with_fees)
        est = next(e["estimate"] for e in again["events"] if e["date"] == "2026-11-05")
        self.assertNotIn("fees", est)
        self.conn.execute(update(Category).where(Category.name == "Fees & Interest").values(pay_with="chk"))
        fc = forecast.build(self.conn, TODAY, 90)
        self.assertAlmostEqual(self.payments(fc)["2026-11-05"], before["2026-11-05"] - 190.0, places=2)
        self.assertEqual(ForecastTests.drop(self, fc, "2026-10-20"), 0.0)
        self.assert_paid_with_their_statements(fc)

    def test_the_fee_of_a_card_with_no_statement_is_in_its_assumed_statement(self):
        self.acct("cc3", "credit", 0.0, pay_from="chk")
        self.conn.execute(insert(Budget).values(category="Travel", amount=310))
        self.conn.execute(update(Category).where(Category.name == "Travel").values(pay_with="cc3"))
        self.churn(account_id="cc3")
        fc = forecast.build(self.conn, TODAY, 90)
        card = {e["date"]: e["amount"] for e in fc["events"] if e.get("card_id") == "cc3"}
        self.assertEqual(card, {"2026-10-26": -310.0, "2026-11-25": -405.0})
        self.assertNotIn("fee", [e["kind"] for e in fc["events"]])
        self.assertEqual((fc["fees"][0]["paid_on"], fc["fees"][0]["paid_from"]), ("2026-11-25", "chk"))
        self.conn.execute(insert(Budget).values(category="Fees & Interest", amount=0.01))
        self.conn.execute(update(Category).where(Category.name == "Fees & Interest").values(pay_with="cc3"))
        card = {e["date"]: e["amount"] for e in forecast.build(self.conn, TODAY, 90)["events"] if e.get("card_id") == "cc3"}
        self.assertAlmostEqual(card["2026-11-25"], -310.01, places=2)

    def test_an_overdue_fee_of_a_card_with_no_statement_is_on_its_first_statement(self):
        self.acct("cc3", "credit", 0.0, pay_from="chk")
        self.conn.execute(insert(Budget).values(category="Travel", amount=310))
        self.conn.execute(update(Category).where(Category.name == "Travel").values(pay_with="cc3"))
        self.churn(opened="2024-09-10", account_id="cc3")
        card = {e["date"]: e["amount"] for e in forecast.build(self.conn, TODAY, 90)["events"] if e.get("card_id") == "cc3"}
        self.assertEqual(card["2026-10-26"], -405.0)

    def test_a_card_with_no_statement_and_only_a_fee_has_the_statement_that_pays_it(self):
        self.acct("cc3", "credit", -40.0, pay_from="chk")
        self.conn.execute(insert(Budget).values(category="Fees & Interest", amount=150))
        self.conn.execute(update(Category).where(Category.name == "Fees & Interest").values(pay_with="chk"))
        self.churn(account_id="cc3")
        fc = forecast.build(self.conn, TODAY, 90)
        card = {e["date"]: e for e in fc["events"] if e.get("card_id") == "cc3"}
        self.assertEqual({d: e["amount"] for d, e in card.items()}, {"2026-10-26": -40.0, "2026-11-25": -95.0})
        self.assertEqual((card["2026-11-25"]["estimate"]["fees"], card["2026-11-25"]["assumed_cycle"]),
                         ([{"name": "Sapphire annual fee", "amount": 95.0}], True))
        self.assertEqual([(f["account_id"], f["paid_on"], f["paid_from"]) for f in fc["fees"]], [("cc3", "2026-11-25", "chk")])
        self.assert_paid_with_their_statements(fc)
        self.assertAlmostEqual(ForecastTests.drop(self, fc, "2026-10-20"), (150 - 95) / 31, delta=0.01)
        self.conn.execute(update(Account).where(Account.id == "cc3").values(pay_from=None))
        fc = forecast.build(self.conn, TODAY, 90)
        self.assertEqual([(f["account_id"], f["paid_on"]) for f in fc["fees"]], [("cc3", None)])
        self.assertFalse(any(e.get("card_id") == "cc3" for e in fc["events"]))


if __name__ == "__main__":
    unittest.main(verbosity=1)
