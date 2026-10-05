"""The forecast: dates and schedules, the balance chart, and its edge cases."""
import unittest
from unittest import mock
from datetime import date, timedelta

from sqlalchemy import delete, func, insert, select, update

from runway.storage import db
from runway.domain import forecast, recurring
from runway.providers import plaidapi
from runway.storage.models import (Account, Budget, CardStatement, Category, Override, PlaidAccount, Recurring,
                                   RecurringDismissed, Transaction)
from tests import forecast_support as fs
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
        from runway.domain import bankdays
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

    def test_a_date_just_outside_the_window_can_move_into_it(self):
        pay = {"frequency": "monthly", "anchor_date": "2026-10-10", "end_date": None, "amount": 3000}
        self.assertEqual(forecast.occurrences(pay, date(2026, 9, 30), date(2026, 10, 9)), [date(2026, 10, 9)])
        bill = {"frequency": "monthly", "anchor_date": "2026-10-10", "end_date": None, "amount": -40}
        self.assertEqual(forecast.occurrences(bill, date(2026, 10, 10), date(2026, 10, 31)), [date(2026, 10, 13)])
        self.assertEqual(forecast.occurrences(bill, date(2026, 10, 1), date(2026, 10, 12)), [])


class ForecastTests(LedgerCase):
    card_setup = fs.card_setup
    drop = fs.drop

    def setUp(self):
        super().setUp()
        self.card_setup()

    def test_statement_from_the_bank(self):
        info = self.cycle("cc")
        self.assertEqual(info["last_close"], "2026-09-10")
        self.assertEqual(info["minimum_payment"], 40.0)
        self.assertEqual(info["statement_balance"], 800.0)
        self.assertEqual(info["paid_since_close"], 200.0)
        self.assertEqual(info["remaining"], 600.0)
        self.assertEqual(info["due_date"], "2026-10-05")
        self.assertEqual(info["new_charges"], 300.0)

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
        from runway.domain import bankdays
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
        from runway.domain import bankdays
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

    def test_a_match_text_with_a_wildcard_character_matches_only_itself(self):
        self.conn.execute(insert(Recurring).values(id=8, name="Club", account_id="chk", amount=-30, frequency="monthly",
                                                   anchor_date="2026-09-01", match="club_50"))
        self.tx("chk", "2026-08-01", -30.0, "CLUB_50", "Entertainment")
        for d in ("2026-07-02", "2026-08-02", "2026-09-02"):
            self.tx("chk", d, -12.0, "CLUBX50 BAR", "Restaurants")
        fc = forecast.build(self.conn, TODAY, 30)
        self.assertEqual({e["category"] for e in fc["events"] if e.get("recurring_id") == 8}, {"Entertainment"})

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
        from runway.domain import forecast
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
        from runway.domain import recurring
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
