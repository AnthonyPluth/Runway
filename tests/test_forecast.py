"""The forecast: dates and schedules, card payments, and the balance chart."""
import unittest
from unittest import mock
from datetime import date, timedelta

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import OperationalError

from runway import churning, db, forecast, plaidapi, recurring
from runway import settings_keys as sk
from runway.models import Account, Budget, CardStatement, ChurnCard, Override, PlaidAccount, Recurring, RecurringDismissed, Transaction
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
        # future anchor
        f = {"anchor_date": "2026-10-10", "frequency": "monthly", "end_date": "2026-11-15"}
        self.assertEqual(forecast.scheduled(f, TODAY, date(2026, 12, 31)), [date(2026, 10, 10), date(2026, 11, 10)])
        y = {"anchor_date": "2025-12-01", "frequency": "yearly", "end_date": None}
        self.assertEqual(forecast.scheduled(y, TODAY, date(2027, 12, 31)), [date(2026, 12, 1), date(2027, 12, 1)])

    def test_money_moves_on_business_days(self):
        from runway import bankdays
        # Paychecks on the 15th and the last day: a weekend or holiday moves them to the business day before.
        pay = {"frequency": "semimonthly", "dates": "15,31", "anchor_date": "2026-01-01", "end_date": None, "amount": 4180}
        self.assertEqual(forecast.occurrences(pay, date(2026, 1, 31), date(2026, 3, 31)),
                         [date(2026, 2, 13), date(2026, 2, 27), date(2026, 3, 13), date(2026, 3, 31)])   # Sun 15th, Sat 28th, Sun 15th
        # Money out moves to the business day after, skipping bank holidays (Labor Day, Columbus Day).
        gym = {"frequency": "monthly", "anchor_date": "2026-07-05", "end_date": None, "amount": -40}
        self.assertEqual(forecast.occurrences(gym, date(2026, 8, 31), date(2026, 9, 30)), [date(2026, 9, 8)])   # Sat 5th, Mon 7th Labor Day
        self.assertEqual(bankdays.next_business_day(date(2026, 10, 10)), date(2026, 10, 13))
        # Federal Reserve rules: a Sunday holiday is observed Monday; a Saturday one isn't moved to Friday.
        self.assertFalse(bankdays.is_business_day(date(2027, 7, 5)))    # July 4 2027 is a Sunday
        self.assertTrue(bankdays.is_business_day(date(2026, 7, 3)))     # July 4 2026 is a Saturday
        self.assertFalse(bankdays.is_business_day(date(2026, 11, 26)))  # Thanksgiving
        # A date just outside the window can move into it: Sat Oct 31 -> Mon Nov 2.
        rent = {"frequency": "monthly", "anchor_date": "2026-01-31", "end_date": None, "amount": -1500}
        self.assertEqual(forecast.occurrences(rent, date(2026, 11, 1), date(2026, 11, 30)), [date(2026, 11, 2), date(2026, 11, 30)])


class ForecastTests(LedgerCase):
    def setUp(self):
        super().setUp()
        self.card_setup()

    def card_setup(self):
        self.acct("chk", "checking", 5000.0, daily_spend=0)
        # Card owes 900 now (negative = owed). Its last statement: $800, closed Sep 10, due Oct 5.
        self.acct("cc", "credit", -900.0, pay_from="chk")
        self.stmt("cc", 800.0, "2026-09-10", "2026-10-05", minimum=40.0)
        # Since the Sep 10 close: 300 of new charges and a 200 payment toward the Aug statement.
        self.tx("cc", "2026-09-12", -100.0, "COFFEE", "Restaurants")
        self.tx("cc", "2026-09-20", -200.0, "GROCER", "Groceries")
        self.tx("cc", "2026-09-15", 200.0, "PAYMENT THANK YOU", "Credit Card Payment")
        # Before the close (part of the statement)
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

    def test_future_statements_use_three_cycle_average(self):
        self.tx("cc", "2026-06-05", -50.0, "OLD", "Shopping")         # history reaches back before the oldest cycle
        self.tx("cc", "2026-06-20", -1200.0, "TRIP", "Travel")        # Jun 10 - Jul 10: 1200
        self.tx("cc", "2026-07-20", -900.0, "STORE", "Shopping")      # Jul 10 - Aug 10: 900 - 100 refund = 800
        self.tx("cc", "2026-07-25", 100.0, "STORE REFUND", "Refunds")
        self.tx("cc", "2026-08-20", -400.0, "GROCER", "Groceries")     # Aug 10 - Sep 10: 400 + 800 = 1200
        self.tx("cc", "2026-08-25", 500.0, "PAYMENT", "Credit Card Payment")   # payments don't count as spending
        fc = forecast.build(self.conn, TODAY, 90)
        card = next(c for c in fc["cards"] if c["id"] == "cc")
        self.assertEqual((card["avg_monthly_spend"], card["avg_cycles"]), (1066.67, 3))
        est = {e["date"]: -e["amount"] for e in fc["events"] if e["estimated"]}
        # cycle in progress (Sep 10 to Oct 10, 17 of its 30 days left): the 300 charged so far plus the average's share
        # of the days left
        self.assertEqual(est["2026-11-05"], round(300 + 1066.67 * 17 / 30, 2))
        self.assertEqual(est["2026-12-07"], 1066.67)   # later cycles: the average. Dec 5 is a Saturday: paid Monday
        # the day before the close, it's nearly all what's been charged
        est = {e["date"]: -e["amount"] for e in forecast.build(self.conn, date(2026, 10, 9), 90)["events"] if e["estimated"]}
        self.assertEqual(est["2026-11-05"], round(300 + 1066.67 / 30, 2))
        # charges already past the average stay in, and the days left add to them
        self.tx("cc", "2026-09-21", -2000.0, "LAPTOP", "Shopping")
        est = {e["date"]: -e["amount"] for e in forecast.build(self.conn, TODAY, 90)["events"] if e["estimated"]}
        self.assertEqual(est["2026-11-05"], round(2300 + 1066.67 * 17 / 30, 2))

    def test_sticking_to_the_budget(self):
        # $500/month on Groceries, paid with the card. $200 already spent in September, so $300 over Sep 24-30.
        self.conn.execute(insert(Budget).values(category="Groceries", amount=500, pay_with="cc"))
        fc = forecast.build(self.conn, TODAY, 90)
        b = fc["budget"]
        self.assertEqual((b["monthly"], [u["account_id"] for u in b["used"]]), (500.0, ["cc"]))
        paid = lambda due: fc["budget"]["total"][fc["dates"].index(due) - 1] - fc["budget"]["total"][fc["dates"].index(due)]
        # Oct 5: the closed September statement ($600 left), same as the regular forecast
        self.assertAlmostEqual(paid("2026-10-05"), 600.0, places=2)
        # Nov 5: $300 charged so far this cycle + $300 (rest of Sept) + 10 days of October at 500/31
        self.assertAlmostEqual(paid("2026-11-05"), 300 + 300 + 500 / 31 * 10, places=1)
        # Dec 5 (a Saturday, so paid Monday the 7th): Oct 11-31 and Nov 1-10
        self.assertAlmostEqual(paid("2026-12-07"), 500 / 31 * 21 + 500 / 30 * 10, places=1)
        # no daily drain: nothing moves between those dates
        self.assertEqual(b["total"][fc["dates"].index("2026-10-20")], b["total"][fc["dates"].index("2026-10-25")])
        # the table's view: each budgeted card payment, and nothing taken from checking day by day
        card = {c["date"]: c for c in b["changes"] if c["kind"] == "card"}
        self.assertAlmostEqual(-card["2026-11-05"]["amount"], paid("2026-11-05"), places=1)
        self.assertEqual((card["2026-11-05"]["charged"], card["2026-12-07"]["charged"]), (300.0, 0.0))
        self.assertNotIn("2026-10-05", card)                       # the closed statement is the forecast's own event
        self.assertEqual([c for c in b["changes"] if c["kind"] == "budget"], [])

    def test_a_budget_paid_from_checking_comes_out_day_by_day(self):
        # $310/month on Groceries from checking; $200 spent in September, so $110 over Sep 24-30 ($15.71 a day)
        self.conn.execute(insert(Budget).values(category="Groceries", amount=310, pay_with="chk"))
        fc = forecast.build(self.conn, TODAY, 40)
        b, days = fc["budget"], fc["dates"]
        out = {c["date"]: c for c in b["changes"] if c["kind"] == "budget"}
        self.assertEqual((out["2026-09-24"]["category"], out["2026-09-24"]["amount"]), ("Groceries", -15.71))
        self.assertEqual(out["2026-10-15"]["amount"], -10.0)        # October: 310 / 31
        self.assertEqual(len(out), 40)                              # every day after today
        drop = b["total"][days.index("2026-10-14")] - b["total"][days.index("2026-10-15")]
        self.assertAlmostEqual(drop, 10.0 - sum(e["amount"] for e in fc["events"] if e["date"] == "2026-10-15"), places=2)

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
        # $500 on Groceries from checking includes the $100 box: $400 a month more, not $500 on top of it
        self.grocery_box()
        self.conn.execute(insert(Budget).values(category="Groceries", amount=500, pay_with="chk"))
        fc = forecast.build(self.conn, TODAY, 60)
        box = [e for e in fc["events"] if e.get("recurring_id") == 7]
        self.assertEqual([(e["date"], e["category"]) for e in box], [("2026-10-01", "Groceries"), ("2026-11-02", "Groceries")])
        daily = {c["date"]: -c["amount"] for c in fc["budget"]["changes"] if c["kind"] == "budget"}
        self.assertEqual(daily["2026-10-15"], round(400 / 31, 2))     # October: 500 - 100
        self.assertEqual(daily["2026-11-15"], round(400 / 30, 2))     # November: its box is Nov 2 (the 1st is a Sunday)
        # September: $300 spent (the $100 box and $200 at the grocer), $200 left over Sep 24-30
        self.assertEqual(daily["2026-09-24"], round(200 / 7, 2))

    def test_a_budget_its_recurring_payments_cover_adds_nothing(self):
        self.grocery_box()
        self.conn.execute(insert(Budget).values(category="Groceries", amount=80, pay_with="chk"))
        b = forecast.build(self.conn, TODAY, 60)["budget"]
        self.assertEqual((b["used"], b["skipped"][0]), ([], {"category": "Groceries", "reason": "a recurring item already covers it"}))

    def test_a_recurring_item_with_nothing_linked_yet_takes_its_category_from_what_it_matches(self):
        self.grocery_box(link=False)
        self.conn.execute(insert(Budget).values(category="Groceries", amount=500, pay_with="chk"))
        fc = forecast.build(self.conn, TODAY, 60)
        self.assertEqual({e["category"] for e in fc["events"] if e.get("recurring_id") == 7}, {"Groceries"})
        daily = {c["date"]: -c["amount"] for c in fc["budget"]["changes"] if c["kind"] == "budget"}
        self.assertEqual(daily["2026-10-15"], round(400 / 31, 2))     # counted once all the same

    def test_statement_you_entered_wins(self):
        key = self.cycle("cc")["statement_key"]
        self.assertEqual(key, "stmt:cc:2026-09-10")
        self.conn.execute(insert(Override).values(key=key, amount=950))
        info = self.cycle("cc")
        self.assertEqual((info["statement_balance"], info["statement_reported"], info["statement_set"], info["remaining"]),
                         (950.0, 800.0, True, 750.0))
        fc = forecast.build(self.conn, TODAY, 30)
        self.assertIn(("2026-10-05", -750.0), [(e["date"], e["amount"]) for e in fc["events"] if e["kind"] == "card"])
        # once the bank reports the next statement, the entered amount no longer applies
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
        self.assertTrue(est and est[0]["date"] == "2026-11-05")
        # Oct 10 close: 300 so far + daily rate * 17 days
        card = next(c for c in fc["cards"] if c["id"] == "cc")
        self.assertAlmostEqual(-est[0]["amount"], round(300 + card["daily_rate"] * 17, 2), places=1)
        # Series arithmetic: last value = start + sum(events) (no daily spend on chk)
        self.assertAlmostEqual(fc["total"][-1], 5000 + sum(e["amount"] for e in fc["events"]), places=2)
        self.assertEqual(len(fc["total"]), 61)
        self.assertEqual(fc["warnings"], [])

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
            # and it agrees with the chart on that day once all of the day's items are in
            same_day = [x for x in fc["events"] if x["date"] == e["date"]]
            if e is same_day[-1]:
                self.assertAlmostEqual(fc["total"][fc["dates"].index(e["date"])], bal, places=2)
        self.assertEqual(next(e for e in fc["events"] if e["kind"] == "card")["category"], "Credit Card Payment")

    def test_primary_only_and_flat_between_events(self):
        self.acct("sav", "savings", 20000.0)
        self.acct("chk2", "checking", 300.0)
        for i in range(60):  # plenty of everyday spending in history
            self.tx("chk", (TODAY - timedelta(days=i)).isoformat(), -25.0, "TARGET", "Groceries")
        # an account still marked for everyday spending by an older version: nothing is taken out for it any more
        self.conn.execute(update(Account).where(Account.id == "chk").values(daily_spend=1))
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
                                          "payment is in the forecast."])                  # linked, statement not in yet
        self.assertEqual((fc["cards"], fc["unlinked_cards"]), ([], [{"id": "cc", "name": "cc", "owed_now": 900.0, "linked": True}]))
        self.assertEqual(fc["warning_links"], [{"text": fc["warnings"][0], "href": "#setup/accounts?account=cc", "setting": True}])   # the card's row
        self.conn.execute(update(Account).where(Account.id == "cc").values(plaid_account_id=None))                  # not linked at all
        fc = forecast.build(self.conn, TODAY, 30)
        self.assertEqual(fc["warnings"], ["Enter cc’s latest statement so its payment is in the forecast."])   # no Plaid: not mentioned
        self.assertEqual(fc["warning_links"][0]["href"], "#setup/accounts?account=cc")
        self.conn.execute(insert(PlaidAccount).values(plaid_account_id="pcc", item_id="it", name="Visa", type="credit"))
        with mock.patch.object(plaidapi, "configured", return_value=True):   # with Plaid set up, it's offered too
            fc = forecast.build(self.conn, TODAY, 30)
        self.assertEqual(fc["warnings"][0], "Enter cc’s latest statement (or link it through Plaid) so its payment is in the forecast.")
        self.assertIn("Plaid has 1 card waiting to be matched", fc["warnings"][1])
        self.assertEqual(fc["warning_links"][1]["href"], "#setup/accounts")      # matched under “New from Plaid”

    def test_warnings_link_to_where_they_are_fixed(self):
        self.conn.execute(update(Account).where(Account.id == "cc").values(pay_from=None))
        fc = forecast.build(self.conn, TODAY, 30)
        self.assertEqual(fc["warning_links"], [{"text": "cc: choose which account pays it in Settings.", "href": "#setup/accounts", "setting": True}])
        self.assertEqual(fc["warnings"], ["cc: choose which account pays it in Settings."])   # plain text, as MCP clients read it

    def test_paid_statement_no_event(self):
        self.tx("cc", "2026-09-22", 600.0, "PAYMENT", "Credit Card Payment")
        self.conn.execute(update(Account).where(Account.id == "cc").values(balance=-300))  # the payment lowers what's owed
        info = self.cycle("cc")
        self.assertEqual((info["statement_balance"], info["remaining"]), (800.0, 0.0))
        fc = forecast.build(self.conn, TODAY, 30)
        self.assertFalse(any(e["name"] == "cc statement" for e in fc["events"]))

    def test_daily_spend_excludes_transfers_and_recurring(self):
        for i in range(30):
            self.tx("chk", (TODAY - timedelta(days=i)).isoformat(), -10.0, "TARGET", "Groceries")
        self.tx("chk", "2026-09-01", -2500.0, "MORTGAGE CO", "Mortgage")  # recurring, excluded by match
        self.tx("chk", "2026-09-02", -700.0, "CHASE CREDIT CRD AUTOPAY", "Credit Card Payment")
        self.tx("chk", "2026-09-03", -60.0, "NETFLIX", "Subscriptions")
        rate = forecast.daily_spend_rate(self.conn, "chk", TODAY, [{"match": "netflix", "amount": -60}])
        self.assertAlmostEqual(rate, 300 / 30, places=2)

    def test_daily_spend_only_leaves_out_what_looks_like_the_recurring_payment(self):
        for i in range(30):
            self.tx("chk", (TODAY - timedelta(days=i)).isoformat(), -10.0, "TARGET", "Groceries")
        self.tx("chk", "2026-09-05", -14.99, "AMAZON PRIME", "Subscriptions")    # the Prime fee: its recurring item's
        self.tx("chk", "2026-09-06", -35.0, "AMAZON MKTPLACE", "Shopping")       # orders: everyday spending
        self.tx("chk", "2026-09-08", -85.0, "AMAZON.COM", "Shopping")
        prime = {"name": "Prime", "match": "amazon", "amount": -14.99, "amount_mode": "fixed", "amount_min": 10, "amount_max": 20}
        rate = forecast.daily_spend_rate(self.conn, "chk", TODAY, [prime])
        self.assertAlmostEqual(rate, (300 + 35 + 85) / 30, places=2)
        # an item without an amount range can't tell them apart: everything with its text is left out, as matching does
        rate = forecast.daily_spend_rate(self.conn, "chk", TODAY, [{**prime, "amount_min": None, "amount_max": None}])
        self.assertAlmostEqual(rate, 300 / 30, places=2)
        # any of its texts counts
        rate = forecast.daily_spend_rate(self.conn, "chk", TODAY, [{**prime, "match": "prime video\namazon"}])
        self.assertAlmostEqual(rate, (300 + 35 + 85) / 30, places=2)

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
                         ["2026-02-15", "2026-02-28"])        # 31st becomes the last day of a short month
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
        self.tx("chk", "2026-06-01", -5.0, "OPENING", "Other")                      # history starts here
        self.conn.execute(insert(Recurring).values(id=1, name="Gym", account_id="chk", amount=-40, frequency="monthly",
                                                   anchor_date="2026-07-05", match="gym", active=1))
        for d in ("2026-07-05", "2026-08-07"):                                      # paid July, August (2 days late)
            self.tx("chk", d, -40.0, "GYM MEMBERSHIP", "Other")
        recurring.auto_match(self.conn)
        today = date(2026, 9, 23)
        m = recurring.missed(self.conn, today)
        self.assertEqual([x["date"] for x in m], ["2026-09-08"])                   # September never came (Sat 5th, then Labor Day)
        self.assertEqual(recurring.missed(self.conn, date(2026, 9, 12)), [])       # still inside the grace window
        recurring.dismiss(self.conn, m[0]["key"])
        self.assertEqual(recurring.missed(self.conn, today), [])
        # linking the payment that did happen also clears it
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
        # Statement due Sep 5; later ones due Oct 5, Nov 5 and Dec 5, a Saturday, so paid Monday Dec 7: past the Dec 5 end.
        self.stmt("cc", 800.0, "2026-08-10", "2026-09-05")
        today = date(2026, 9, 6)
        fc = forecast.build(self.conn, today, 90)
        self.assertEqual(fc["dates"][-1], "2026-12-05")
        self.assertTrue(all(fc["dates"][0] <= e["date"] <= fc["dates"][-1] for e in fc["events"]))
        self.assertNotIn("2026-12-07", [e["date"] for e in fc["events"]])

    def test_an_old_statement_is_a_warning_not_a_crash(self):
        self.stmt("cc", 800.0, "2026-06-10", "2026-07-05")   # the bank hasn't sent anything since June
        fc = forecast.build(self.conn, TODAY, 90)
        self.assertTrue(all(e["date"] >= TODAY.isoformat() for e in fc["events"]))
        self.assertTrue(any("hasn't sent the statement after Jun 10" in w for w in fc["warnings"]), fc["warnings"])
        # Nothing to change in Settings: the bank's still to send it (so a phone doesn't send you to a computer).
        stale = next(w for w in fc["warning_links"] if "hasn't sent the statement" in w["text"])
        self.assertEqual((stale["href"], stale["setting"]), ("#setup/connections", False))

    def test_a_late_payment_is_flagged_as_not_a_setting(self):
        self.stmt("cc", 800.0, "2026-08-10", "2026-09-05")   # due Sat Sep 5, paid Tue Sep 8 (Labor Day): nothing came
        fc = forecast.build(self.conn, date(2026, 9, 10), 30)
        late = next(w for w in fc["warning_links"] if "no payment has shown up yet" in w["text"])
        self.assertEqual((late["href"], late["setting"]), ("#setup/accounts", False))   # paying the card puts it right

    def test_a_payment_due_today_is_in_todays_balance(self):
        today = date(2026, 10, 5)   # the $600 left on the September statement is due today
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
        self.assertEqual(rent(date(2026, 10, 1)), [("2026-10-01", None)])                  # due today
        self.assertEqual(rent(date(2026, 10, 3)), [("2026-10-03", "2026-10-01")])          # two days late: still coming
        self.assertEqual(rent(date(2026, 10, 8)), [])                                      # past the window: missed instead
        self.assertEqual([m["date"] for m in recurring.missed(self.conn, date(2026, 10, 8))], ["2026-10-01"])
        self.tx("chk", "2026-10-02", -2000.0, "LANDLORD LLC", "Rent")                       # it came, a day late
        recurring.auto_match(self.conn)
        self.assertEqual(rent(date(2026, 10, 3)), [])

    def test_a_card_payment_in_transit_counts_once(self):
        # $600 left checking after the close, but hasn't reached the card yet
        self.tx("chk", "2026-09-22", -600.0, "CHASE CREDIT CRD AUTOPAY", "Credit Card Payment")
        info = self.cycle("cc")
        self.assertEqual((info["paid_since_close"], info["remaining"]), (800.0, 0.0))
        self.assertFalse(any(e["kind"] == "card" and not e["estimated"] for e in forecast.build(self.conn, TODAY, 30)["events"]))
        # once it reaches the card, it's still the one payment
        self.tx("cc", "2026-09-23", 600.0, "PAYMENT THANK YOU", "Credit Card Payment")
        self.assertEqual(self.cycle("cc")["paid_since_close"], 800.0)
        # one that reached the card just before the close (and so is in the statement) isn't counted again
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
        # an account that pays two cards can't tell whose it is, so it waits for the card
        self.acct("cc2", "credit", -50.0, pay_from="chk")
        self.conn.execute(delete(Transaction).where(
            Transaction.id == select(func.max(Transaction.id)).where(Transaction.account_id == "cc").scalar_subquery()))
        self.assertEqual(self.cycle("cc")["paid_since_close"], 200.0)

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
    """What the forecast assumes about pending transactions, card cycles and the budget scenario."""
    card_setup = ForecastTests.card_setup

    def setUp(self):
        super().setUp()
        self.card_setup()

    def estimates(self, today=TODAY, days=90):
        return {e["date"]: -e["amount"] for e in forecast.build(self.conn, today, days)["events"] if e["estimated"]}

    def three_cycles(self):
        """Spending history on the card for an average of 1066.67 over the cycles closing Jul 10, Aug 10 and Sep 10."""
        self.tx("cc", "2026-06-05", -50.0, "OLD", "Shopping")
        self.tx("cc", "2026-06-20", -1200.0, "TRIP", "Travel")
        self.tx("cc", "2026-07-20", -800.0, "STORE", "Shopping")
        self.tx("cc", "2026-08-20", -400.0, "GROCER", "Groceries")

    def test_pending_transactions_are_in_the_starting_balance(self):
        self.tx("chk", "2026-09-22", -400.0, "HARDWARE STORE", "Shopping", pending=1)
        self.tx("chk", "2026-09-23", 25.0, "STORE REFUND", "Refunds", pending=1)
        fc = forecast.build(self.conn, TODAY, 30)
        acct = fc["accounts"][0]
        self.assertEqual((acct["balance"], acct["pending"]), (4625.0, -375.0))
        self.assertEqual(fc["total"][0], 4625.0)
        self.assertEqual(fc["events"][0]["balance_after"], 4625.0 + fc["events"][0]["amount"])
        # a pending paycheck already linked to its recurring item is in the balance, and not again as an event
        self.conn.execute(insert(Recurring).values(name="Paycheck", account_id="chk", amount=3000, frequency="biweekly",
                                                   anchor_date="2026-09-09", match="acme payroll"))
        self.tx("chk", "2026-09-23", 3000.0, "ACME PAYROLL", "Income", pending=1)
        recurring.auto_match(self.conn)   # as a sync does
        fc = forecast.build(self.conn, TODAY, 30)
        self.assertEqual((fc["accounts"][0]["balance"], fc["accounts"][0]["pending"]), (7625.0, 2625.0))
        self.assertEqual([e["date"] for e in fc["events"] if e["name"] == "Paycheck"], ["2026-10-07", "2026-10-21"])
        # the budget scenario starts from the same balance
        self.conn.execute(insert(Budget).values(category="Groceries", amount=500, pay_with="cc"))
        self.assertEqual(forecast.build(self.conn, TODAY, 30)["budget"]["total"][0], 7625.0)

    def test_pending_isnt_added_to_a_balance_that_already_has_it(self):
        self.tx("chk", "2026-09-22", -400.0, "HARDWARE STORE", "Shopping", pending=1)
        # the bank's available balance is $400 less than its balance: the balance leaves the pending debit out
        self.conn.execute(update(Account).where(Account.id == "chk").values(available=4600.0))
        acct = forecast.build(self.conn, TODAY, 30)["accounts"][0]
        self.assertEqual((acct["balance"], acct["pending"]), (4600.0, -400.0))
        # the same as the balance: the bank has taken it out already, so it isn't taken out twice
        self.conn.execute(update(Account).where(Account.id == "chk").values(available=5000.0))
        acct = forecast.build(self.conn, TODAY, 30)["accounts"][0]
        self.assertEqual((acct["balance"], acct["pending"]), (5000.0, 0.0))

    def test_pending_is_added_when_the_available_balance_has_an_overdraft_line_in_it(self):
        # A $500 overdraft line in the available balance puts it above the balance, so it can't tell whether the
        # balance has the pending debit: it's added, as it would be without an available balance.
        self.tx("chk", "2026-09-22", -400.0, "HARDWARE STORE", "Shopping", pending=1)
        for available in (5100.0, 5500.0):   # the debit taken out of it, or not
            self.conn.execute(update(Account).where(Account.id == "chk").values(available=available))
            acct = forecast.build(self.conn, TODAY, 30)["accounts"][0]
            self.assertEqual((acct["balance"], acct["pending"]), (4600.0, -400.0), available)

    def test_old_pending_rows_are_left_out(self):
        # A sync only re-reads the last 14 days, so a pending row older than that may be one that dropped off.
        self.tx("chk", (TODAY - timedelta(days=20)).isoformat(), -300.0, "OLD HOLD", "Shopping", pending=1)
        self.tx("chk", (TODAY - timedelta(days=14)).isoformat(), -50.0, "GAS STATION", "Auto", pending=1)
        acct = forecast.build(self.conn, TODAY, 30)["accounts"][0]
        self.assertEqual((acct["balance"], acct["pending"]), (4950.0, -50.0))
        # Plaid deletes pending rows it no longer has, so a 20-day-old one of its own is a real hold (a hotel, say)
        self.conn.execute(insert(Transaction).values(id="chk|pl:hold1", account_id="chk", amount=-200.0, pending=1,
                                                     posted=(TODAY - timedelta(days=20)).isoformat(), description="HOTEL"))
        acct = forecast.build(self.conn, TODAY, 30)["accounts"][0]
        self.assertEqual((acct["balance"], acct["pending"]), (4750.0, -250.0))

    def test_old_simplefin_pending_card_rows_are_left_out_of_the_cycle(self):
        # As in the starting balance: a SimpleFIN pending charge older than its 14-day refresh may have posted since
        # under a new id, so counting it too would count the charge twice. Plaid's are kept accurate, and count.
        later = date(2026, 10, 5)   # the statement that closed Sep 10 is still the latest
        before = self.cycle("cc", later)["new_charges"]
        self.tx("cc", "2026-09-15", -70.0, "COFFEE", "Dining", pending=1)   # 20 days old: SimpleFIN's, not re-read
        self.tx("cc", "2026-09-16", -70.0, "COFFEE", "Dining")              # ... and posted under its new id
        self.assertEqual(self.cycle("cc", later)["new_charges"], before + 70)
        self.tx("cc", "2026-09-25", -30.0, "LUNCH", "Dining", pending=1)    # within the 14 days: counts
        self.assertEqual(self.cycle("cc", later)["new_charges"], before + 100)
        self.conn.execute(insert(Transaction).values(id="cc|pl:hold1", account_id="cc", amount=-200.0, pending=1,
                                                     posted="2026-09-15", description="HOTEL", category="Travel"))
        self.assertEqual(self.cycle("cc", later)["new_charges"], before + 300)

    def paycheck_pending(self):
        """A biweekly $1,000 paycheck, today's pending (and linked to it once the forecast matches it)."""
        self.conn.execute(insert(Recurring).values(name="Paycheck", account_id="chk", amount=1000, frequency="biweekly",
                                                   anchor_date="2026-09-09", match="acme payroll"))
        self.tx("chk", "2026-09-23", 1000.0, "ACME PAYROLL", "Income", pending=1)
        recurring.auto_match(self.conn)   # as a sync does

    def test_a_pending_paycheck_counts_though_the_available_balance_leaves_it_out(self):
        # Banks don't add pending deposits to the available balance: here it's the same as the balance.
        self.paycheck_pending()
        self.conn.execute(update(Account).where(Account.id == "chk").values(available=5000.0))
        fc = forecast.build(self.conn, TODAY, 30)
        self.assertEqual((fc["accounts"][0]["balance"], fc["accounts"][0]["pending"]), (6000.0, 1000.0))
        self.assertNotIn("2026-09-23", [e["date"] for e in fc["events"] if e["name"] == "Paycheck"])   # in the balance

    def test_pending_debits_and_a_paycheck(self):
        self.paycheck_pending()
        self.tx("chk", "2026-09-22", -375.0, "HARDWARE STORE", "Shopping", pending=1)
        # available has the debits taken out (and not the paycheck): both are added
        self.conn.execute(update(Account).where(Account.id == "chk").values(available=4625.0))
        acct = forecast.build(self.conn, TODAY, 30)["accounts"][0]
        self.assertEqual((acct["balance"], acct["pending"]), (5625.0, 625.0))
        # the balance already has the debits (available is the same): only the paycheck is added
        self.conn.execute(update(Account).where(Account.id == "chk").values(available=5000.0))
        acct = forecast.build(self.conn, TODAY, 30)["accounts"][0]
        self.assertEqual((acct["balance"], acct["pending"]), (6000.0, 1000.0))

    def test_an_overpayment_comes_off_the_next_statement(self):
        # $1,200 paid on the $800 statement (the current balance, say), with $100 of new charges since the close
        self.conn.execute(update(Transaction).where(Transaction.description == "GROCER").values(posted="2026-09-01"))
        self.conn.execute(update(Transaction).where(Transaction.description == "PAYMENT THANK YOU").values(amount=1200.0))
        info = self.cycle("cc")
        self.assertEqual((info["paid_since_close"], info["remaining"], info["new_charges"]), (1200.0, 0.0, 0.0))
        # $500 charged since: $400 of it is paid already
        self.tx("cc", "2026-09-20", -400.0, "GROCER", "Groceries")
        self.assertEqual(self.cycle("cc")["new_charges"], 100.0)
        # paying less than the statement leaves the new charges alone
        self.conn.execute(update(Transaction).where(Transaction.amount == 1200.0).values(amount=500.0))
        self.assertEqual((self.cycle("cc")["remaining"], self.cycle("cc")["new_charges"]), (300.0, 500.0))

    def test_large_one_offs_are_flagged(self):
        self.tx("chk", "2026-08-01", -2000.0, "LANDLORD LLC", "Rent")
        self.tx("chk", "2026-09-01", -2000.0, "LANDLORD LLC", "Rent")
        self.tx("chk", "2026-08-15", -5000.0, "STATE UNIVERSITY", "Education")
        self.tx("chk", "2026-09-10", -1500.0, "CHASE CREDIT CRD AUTOPAY", "Credit Card Payment")   # a transfer
        self.tx("chk", "2026-05-01", -9000.0, "ROOFING CO", "Home")                                 # over 90 days ago
        self.tx("chk", "2026-09-12", -900.0, "DR SMITH DDS", "Medical")                             # under $1,000
        self.tx("chk", "2026-09-14", -3000.0, "PENDING THING", "Shopping", pending=1)
        fc = forecast.build(self.conn, TODAY, 30)
        text = "3 payments over $1,000 in the last 90 days aren’t in the forecast (State University, Landlord Llc); " \
               "add them as recurring items."
        self.assertIn({"text": text, "href": "#recurring", "setting": True}, fc["warning_links"])
        # rent as a recurring item: it links, and only the tuition is left
        self.conn.execute(insert(Recurring).values(name="Rent", account_id="chk", amount=-2000, frequency="monthly",
                                                   anchor_date="2026-08-01", match="landlord"))
        fc = forecast.build(self.conn, TODAY, 30)
        self.assertIn("1 payment over $1,000 in the last 90 days isn’t in the forecast (State University); "
                      "add it as a recurring item.", fc["warnings"])
        # marked "not recurring": it's a one-off, and no longer flagged
        self.conn.execute(update(Transaction).where(Transaction.description == "STATE UNIVERSITY").values(recurring_id=0))
        self.assertFalse(any("over $1,000" in w for w in forecast.build(self.conn, TODAY, 30)["warnings"]))
        # more than three payees: the three biggest, and an ellipsis
        for i, name in enumerate(["ALPHA", "BRAVO", "CHARLIE", "DELTA"]):
            self.tx("chk", f"2026-09-0{i + 2}", -1100.0 - 100 * i, name, "Shopping")
        self.assertIn("4 payments over $1,000 in the last 90 days aren’t in the forecast (Delta, Charlie, Bravo…); "
                      "add them as recurring items.", forecast.build(self.conn, TODAY, 30)["warnings"])

    def test_recurring_charges_the_average_leaves_out(self):
        self.three_cycles()
        # a gym the card has paid every month since May (in the average), a yearly insurance premium, and a new
        # subscription that hasn't charged yet
        self.conn.execute(insert(Recurring).values(name="Gym", account_id="cc", amount=-30, frequency="monthly",
                                                   anchor_date="2026-05-15", match="gym"))
        for d in ("2026-05-15", "2026-06-15", "2026-07-15", "2026-08-15", "2026-09-15"):
            self.tx("cc", d, -30.0, "GYM", "Fitness")
        recurring.auto_match(self.conn)   # as a sync does
        self.conn.execute(insert(Recurring).values(name="Insurance", account_id="cc", amount=-600, frequency="yearly",
                                                   anchor_date="2025-10-20", match="insurer"))
        self.conn.execute(insert(Recurring).values(name="Streaming", account_id="cc", amount=-15, frequency="monthly",
                                                   anchor_date="2026-09-30", match="streamflix"))
        fc = forecast.build(self.conn, TODAY, 120)
        card = next(c for c in fc["cards"] if c["id"] == "cc")
        avg, charged = card["avg_monthly_spend"], card["new_charges"]
        self.assertEqual((avg, charged), (1096.67, 330.0))
        est = {e["date"]: -e["amount"] for e in fc["events"] if e["estimated"]}
        self.assertEqual(est["2026-11-05"], round(charged + avg * 17 / 30 + 15, 2))   # Streaming on Sep 30
        self.assertEqual(est["2026-12-07"], round(avg + 600 + 15, 2))                # Insurance Oct 20, Streaming Oct 30
        self.assertEqual(est["2027-01-05"], round(avg + 15, 2))                      # Streaming Nov 30
        # once the subscription's been on the card since before the averaged cycles, it's in the average
        self.conn.execute(update(Recurring).where(Recurring.name == "Streaming").values(anchor_date="2026-05-30"))
        for d in ("2026-05-30", "2026-06-30", "2026-07-30", "2026-08-30"):
            self.tx("cc", d, -15.0, "STREAMFLIX", "Subscriptions")
        recurring.auto_match(self.conn)   # as a sync does
        card = next(c for c in forecast.build(self.conn, TODAY, 120)["cards"] if c["id"] == "cc")
        self.assertEqual(card["avg_monthly_spend"], 1111.67)
        self.assertEqual(self.estimates(days=120)["2027-01-05"], card["avg_monthly_spend"])

    def test_a_quarterly_charge_in_the_averaged_cycles_counts_once(self):
        # $300 a quarter, paid Jul 20 (in the Jul 10 - Aug 10 cycle) and due again Oct 20
        self.three_cycles()
        self.conn.execute(insert(Recurring).values(name="Water", account_id="cc", amount=-300, frequency="quarterly",
                                                   anchor_date="2026-07-20", match="water co"))
        self.tx("cc", "2026-07-20", -300.0, "WATER CO", "Utilities")
        recurring.auto_match(self.conn)   # as a sync does
        fc = forecast.build(self.conn, TODAY, 120)
        card = next(c for c in fc["cards"] if c["id"] == "cc")
        self.assertEqual(card["avg_monthly_spend"], 1066.67)   # without it: not $100 of it in every cycle
        est = {e["date"]: -e["amount"] for e in fc["events"] if e["estimated"]}
        self.assertEqual(est["2026-11-05"], round(300 + 1066.67 * 17 / 30, 2))
        self.assertEqual(est["2026-12-07"], round(1066.67 + 300, 2))   # the whole of it in the cycle it's due
        self.assertEqual(est["2027-01-05"], 1066.67)

    def test_a_subscription_started_inside_the_averaged_cycles_counts_once(self):
        # $90 a month since Jul 25: in two of the three averaged cycles, so $60 of the average and $90 on top before
        self.three_cycles()
        self.conn.execute(insert(Recurring).values(name="Meal kit", account_id="cc", amount=-90, frequency="monthly",
                                                   anchor_date="2026-07-25", match="meal kit"))
        for d in ("2026-07-25", "2026-08-25"):
            self.tx("cc", d, -90.0, "MEAL KIT", "Groceries")
        recurring.auto_match(self.conn)   # as a sync does
        fc = forecast.build(self.conn, TODAY, 120)
        card = next(c for c in fc["cards"] if c["id"] == "cc")
        self.assertEqual(card["avg_monthly_spend"], 1066.67)
        est = {e["date"]: -e["amount"] for e in fc["events"] if e["estimated"]}
        self.assertEqual(est["2026-11-05"], round(300 + 1066.67 * 17 / 30 + 90, 2))   # Sep 25
        self.assertEqual(est["2026-12-07"], round(1066.67 + 90, 2))                   # Oct 25

    def test_a_budget_on_a_card_paid_from_outside_the_forecast_is_skipped(self):
        self.acct("sav", "savings", 20000.0)    # not forecast: checking is the only checking account
        self.acct("cc2", "credit", -50.0, pay_from="sav")
        self.stmt("cc2", 50.0, "2026-09-10", "2026-10-05")
        self.conn.execute(insert(Budget).values(category="Groceries", amount=500, pay_with="cc2"))
        b = forecast.build(self.conn, TODAY, 60)["budget"]
        self.assertEqual(b["used"], [])
        self.assertEqual(b["skipped"], [{"category": "Groceries", "reason": "its card isn't paid from a forecast account"}])
        self.assertEqual(b["monthly"], 0.0)

    def test_a_budget_on_a_card_with_no_statement_yet_still_counts(self):
        # A new card, paid from checking, with no statement yet and $40 on it: $310 a month of Travel is budgeted on it.
        self.acct("cc3", "credit", -40.0, pay_from="chk")
        self.conn.execute(insert(Budget).values(category="Travel", amount=310, pay_with="cc3"))
        fc = forecast.build(self.conn, TODAY, 90)
        b = fc["budget"]
        self.assertEqual(([u["account_id"] for u in b["used"]], b["skipped"]), (["cc3"], []))
        self.assertEqual(b["monthly"], 310.0)
        # Its cycle is taken to close at each month's end, paid in full 25 days later (the next business day). The
        # September one has the rest of September's $310 and the $40 it owes now; Oct 25 is a Sunday.
        card = {c["date"]: c for c in b["changes"] if c["kind"] == "card"}
        self.assertEqual((card["2026-10-26"]["amount"], card["2026-10-26"]["charged"]), (-350.0, 40.0))
        self.assertEqual(card["2026-10-26"]["account_id"], "chk")
        self.assertEqual(card["2026-11-25"]["amount"], -310.0)                    # October
        days = fc["dates"]
        drop = lambda d: b["total"][days.index(d) - 1] - b["total"][days.index(d)]
        base = lambda d: fc["total"][days.index(d) - 1] - fc["total"][days.index(d)]
        self.assertAlmostEqual(drop("2026-10-26") - base("2026-10-26"), 350.0, places=2)
        # The forecast itself has nothing for a card without a statement: it isn't changed
        self.assertFalse(any(e.get("card_id") == "cc3" for e in fc["events"]))

    def test_a_budget_on_a_card_with_no_statement_reads_the_banks_sign(self):
        # Plaid reports what a card owes as a positive number (owed_positive): $40 owed is in the first statement, and a
        # $20 credit comes off it, as the bank would bill it.
        self.acct("cc3", "credit", 40.0, pay_from="chk", owed_positive=1)
        self.conn.execute(insert(Budget).values(category="Travel", amount=310, pay_with="cc3"))
        card = {c["date"]: c for c in forecast.build(self.conn, TODAY, 90)["budget"]["changes"] if c["kind"] == "card"}
        self.assertEqual((card["2026-10-26"]["amount"], card["2026-10-26"]["charged"]), (-350.0, 40.0))
        self.conn.execute(update(Account).where(Account.id == "cc3").values(balance=-20.0))
        card = {c["date"]: c for c in forecast.build(self.conn, TODAY, 90)["budget"]["changes"] if c["kind"] == "card"}
        self.assertEqual((card["2026-10-26"]["amount"], card["2026-10-26"]["charged"]), (-290.0, 0.0))
        self.assertEqual(card["2026-11-25"]["amount"], -310.0)
        # A credit bigger than a month's charges carries on into the next statement
        self.conn.execute(update(Account).where(Account.id == "cc3").values(balance=-400.0))
        card = {c["date"]: c for c in forecast.build(self.conn, TODAY, 90)["budget"]["changes"] if c["kind"] == "card"}
        self.assertNotIn("2026-10-26", card)                                       # $310 - $400: nothing to pay
        self.assertEqual(card["2026-11-25"]["amount"], -220.0)                     # $310 - the $90 left

    def test_a_budget_on_a_card_with_no_statement_is_paid_the_way_the_card_is_set(self):
        # A new card set to pay a fixed $100, with $310 a month budgeted on it and nothing owed yet: $100 each time, and
        # the rest carries over (with a month's interest once it does, at the 24% APR entered for it).
        self.acct("cc3", "credit", 0.0, pay_from="chk")
        db.set_setting(self.conn, sk.card_pay_mode("cc3"), "fixed")
        db.set_setting(self.conn, sk.card_pay_amount("cc3"), "100")
        db.set_setting(self.conn, sk.card_apr("cc3"), "24")
        self.conn.execute(insert(Budget).values(category="Travel", amount=310, pay_with="cc3"))
        card = {c["date"]: c for c in forecast.build(self.conn, TODAY, 90)["budget"]["changes"] if c["kind"] == "card"}
        self.assertEqual((card["2026-10-26"]["amount"], card["2026-11-25"]["amount"]), (-100.0, -100.0))
        # paid in full when nothing's set
        db.set_setting(self.conn, sk.card_pay_mode("cc3"), "full")
        card = {c["date"]: c for c in forecast.build(self.conn, TODAY, 90)["budget"]["changes"] if c["kind"] == "card"}
        self.assertEqual((card["2026-10-26"]["amount"], card["2026-11-25"]["amount"]), (-310.0, -310.0))

    def test_a_budget_on_a_card_with_no_statement_and_no_paying_account_is_skipped(self):
        self.acct("cc3", "credit", 0.0)
        self.conn.execute(insert(Budget).values(category="Travel", amount=310, pay_with="cc3"))
        b = forecast.build(self.conn, TODAY, 60)["budget"]
        self.assertEqual(b["skipped"], [{"category": "Travel", "reason": "its card isn't paid from a forecast account"}])

    def test_a_budget_spends_what_it_carried_over(self):
        # $310 a month from checking, rolling over since August, when $250 was spent: $60 carried into September
        self.tx("chk", "2026-08-12", -250.0, "GROCER", "Groceries")
        self.conn.execute(insert(Budget).values(category="Groceries", amount=310, pay_with="chk", rollover_from="2026-08"))
        b = forecast.build(self.conn, TODAY, 40)["budget"]
        out = {c["date"]: -c["amount"] for c in b["changes"] if c["kind"] == "budget"}
        self.assertEqual(out["2026-09-24"], round((310 + 60 - 200) / 7, 2))   # $200 spent in September, over Sep 24-30
        self.assertEqual(out["2026-10-15"], 10.0)                             # October: 310 / 31 (its carry-over isn't known yet)

    def test_a_payment_in_transit_waits_for_a_card_without_a_paying_account(self):
        self.tx("chk", "2026-09-22", -600.0, "CHASE CREDIT CRD AUTOPAY", "Credit Card Payment")
        self.assertEqual(self.cycle("cc")["paid_since_close"], 800.0)
        self.acct("cc2", "credit", -600.0)      # a new card, not told yet which account pays it: could be this one's
        self.assertEqual(self.cycle("cc")["paid_since_close"], 200.0)
        self.acct("sav", "savings", 100.0)
        self.conn.execute(update(Account).where(Account.id == "cc2").values(pay_from="sav"))
        self.assertEqual(self.cycle("cc")["paid_since_close"], 800.0)

    def test_card_payments_are_keyed_by_their_closing_date(self):
        events = [e for e in forecast.build(self.conn, TODAY, 90)["events"] if e["kind"] == "card"]
        self.assertEqual([(e["date"], e["key"]) for e in events],
                         [("2026-10-05", "cardclose:cc:2026-09-10"), ("2026-11-05", "cardclose:cc:2026-10-10"),
                          ("2026-12-07", "cardclose:cc:2026-11-10")])
        # an amount set on the October statement's estimate still applies when the bank's statement has a different
        # due date
        self.conn.execute(insert(Override).values(key="cardclose:cc:2026-10-10", amount=-123.0))
        self.assertEqual(self.estimates()["2026-11-05"], 123.0)
        self.stmt("cc", 500.0, "2026-10-10", "2026-11-06")
        self.conn.execute(update(Account).where(Account.id == "cc").values(balance=-500.0))
        e = next(e for e in forecast.build(self.conn, date(2026, 10, 12), 30)["events"] if e["kind"] == "card")
        self.assertEqual((e["date"], e["key"], e["amount"], e["overridden"]), ("2026-11-06", "cardclose:cc:2026-10-10", -123.0, True))

    def test_an_amount_set_under_the_old_due_date_key_still_applies(self):
        self.conn.execute(insert(Override).values(key="card:cc:2026-11-05", amount=-77.0))
        self.conn.execute(insert(Override).values(key="card:cc:2026-10-05", amount=-66.0))
        fc = forecast.build(self.conn, TODAY, 60)
        self.assertEqual({e["key"]: e["amount"] for e in fc["events"] if e["kind"] == "card"},
                         {"cardclose:cc:2026-09-10": -66.0, "cardclose:cc:2026-10-10": -77.0})
        # moved to the new keys (the old ones are gone), so putting one back (removing the event's key) works
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
        # a page load mustn't wait for a sync's write lock
        self.three_cycles()
        self.conn.execute(insert(Recurring).values(name="Rent", account_id="chk", amount=-2000, frequency="monthly",
                                                   anchor_date="2026-08-01", match="landlord"))
        self.tx("chk", "2026-09-01", -2000.0, "LANDLORD LLC", "Rent")             # not linked yet
        self.tx("chk", "2026-09-22", -40.0, "GAS STATION", "Auto", pending=1)
        self.conn.execute(insert(Override).values(key="cardclose:cc:2026-10-10", amount=-123.0))
        self.conn.execute(insert(Budget).values(category="Groceries", amount=500, pay_with="cc", rollover_from="2026-08"))
        self.no_writes(AssertionError("the forecast wrote to the database"))
        fc = forecast.build(self.conn, TODAY, 90)
        self.assertEqual(next(e for e in fc["events"] if e["key"] == "cardclose:cc:2026-10-10")["amount"], -123.0)

    def test_an_old_key_still_applies_when_it_cant_be_moved_yet(self):
        self.conn.execute(insert(Override).values(key="card:cc:2026-11-05", amount=-77.0))
        patch = self.no_writes(OperationalError("UPDATE override", {}, Exception("database is locked")))
        e = next(e for e in forecast.build(self.conn, TODAY, 60)["events"] if e["key"] == "cardclose:cc:2026-10-10")
        self.assertEqual((e["amount"], e["overridden"]), (-77.0, True))
        self.assertEqual(self.conn.execute(select(Override.key)).scalars(), ["card:cc:2026-11-05"])   # not yet
        patch.stop()   # the lock's free: it moves the next time
        forecast.build(self.conn, TODAY, 60)
        self.assertEqual(self.conn.execute(select(Override.key)).scalars(), ["cardclose:cc:2026-10-10"])

    def test_an_old_key_on_a_due_date_that_is_another_closing_date_stays_put(self):
        # Closing the 31st, due the 28th: the Jan 31 statement was due Feb 28, the day the next one closes. It's paid,
        # so it has no event, and its old edit (keyed by that due date) mustn't land on the Feb 28 statement.
        self.stmt("cc", 800.0, "2027-01-31", "2027-02-28")
        self.tx("cc", "2027-02-05", 800.0, "PAYMENT THANK YOU", "Credit Card Payment")
        self.tx("cc", "2027-02-08", -100.0, "COFFEE", "Restaurants")
        self.conn.execute(insert(Override).values(key="card:cc:2027-02-28", amount=-5.0))
        events = [e for e in forecast.build(self.conn, date(2027, 2, 10), 60)["events"] if e["kind"] == "card"]
        self.assertEqual([(e["key"], e["date"]) for e in events],
                         [("cardclose:cc:2027-02-28", "2027-03-29")])   # due Mar 28, a Sunday: paid the 29th
        self.assertFalse(events[0].get("overridden"))
        self.assertEqual(events[0]["amount"], -100.0)

    def test_a_later_payment_doesnt_count_for_an_earlier_occurrence(self):
        today = date(2026, 10, 5)
        history = [{"posted": "2026-10-01", "amount": -50.0}]
        for due in (date(2026, 10, 5), date(2026, 9, 28)):   # paid early, or a little late
            item = {"frequency": "monthly", "anchor_date": due.isoformat(), "amount": -50.0}
            with self.subTest(due=due):
                self.assertIsNone(recurring.still_due(item, due, recurring.paid_by_occurrence(item, history), today, -50.0))
        item = {"frequency": "monthly", "anchor_date": "2026-09-01", "amount": -50.0}
        paid = recurring.paid_by_occurrence(item, history)
        self.assertIsNone(recurring.still_due(item, date(2026, 10, 1), paid, today, -50.0))         # October's...
        self.assertNotIn(date(2026, 9, 1), paid)                                                     # ...not September's


class PaymentModeTests(LedgerCase):
    """Cards paid in full, the minimum or a fixed amount, with what isn't paid carried to the next statement."""
    card_setup = ForecastTests.card_setup
    three_cycles = ForecastAssumptionTests.three_cycles
    no_writes = ForecastAssumptionTests.no_writes
    EST1 = 300 + 1066.67 * 17 / 30   # the cycle closing Oct 10: charged so far plus the average's share of the days left
    EST2 = 1066.67                   # the one closing Nov 10: the average

    def setUp(self):
        super().setUp()
        self.card_setup()
        self.three_cycles()

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
        # Oct 10's statement comes out below zero (-700 + the average's share of the days left): nothing to pay, and
        # what's left of the credit comes off Nov 10's
        s1 = -700 + 1066.67 * 17 / 30
        self.assertEqual(self.payments(fc), {"2026-10-05": 600.0, "2026-12-07": round(s1 + self.EST2, 2)})
        self.assertTrue(all(e["amount"] < 0 for e in fc["events"] if e["kind"] == "card"))   # never a negative payment
        self.assertFalse(self.interest_warned(fc))   # a credit isn't a balance carried
        # not paying in full, an edit left from before on a statement that now has nothing to pay isn't a payment: no
        # event, and the credit is untouched
        self.pay("fixed", amount="5000")
        self.conn.execute(insert(Override).values(key="cardclose:cc:2026-10-10", amount=-50.0))
        fc = forecast.build(self.conn, TODAY, 90)
        self.assertNotIn("2026-11-05", self.payments(fc))
        self.assertEqual(self.payments(fc)["2026-12-07"], round(-700 + 1066.67 * 17 / 30 + self.EST2, 2))


    def test_a_credit_carries_with_budgets_on_both_lines(self):
        self.tx("cc", "2026-09-22", 1000.0, "STORE REFUND", "Refunds")
        self.conn.execute(insert(Budget).values(category="Groceries", amount=500, pay_with="cc"))
        fc = forecast.build(self.conn, TODAY, 90)
        # the forecast: the credit, then Groceries' $300 left this month over its 7 days and $500 a month after, plus the
        # average outside Groceries (933.33) as before: Oct 10's comes to $290.18
        outside = (1200 + 800 + 800) / 3
        s1 = -700 + outside * 17 / 30 + 300 + 500 / 31 * 10
        self.assertEqual(self.payments(fc), {"2026-10-05": 600.0, "2026-11-05": round(s1, 2),
                                             "2026-12-07": round(outside + 500 / 31 * 21 + 500 / 30 * 10, 2)})
        # the budget line: Oct 10's statement is the credit plus budgeted charges, still below zero
        card = {c["date"]: -c["amount"] for c in fc["budget"]["changes"] if c["kind"] == "card"}
        s1 = -700 + 300 + 500 / 31 * 10
        self.assertNotIn("2026-11-05", card)
        self.assertAlmostEqual(card["2026-12-07"], s1 + 500 / 31 * 21 + 500 / 30 * 10, places=1)

    def test_with_budgets_a_cards_statements_are_its_budgets_plus_its_other_spending(self):
        # Groceries ($500 a month, $200 spent this month) is paid with the card. The card's other spending averaged
        # 933.33 over its last three statements (1,200, 800 and 1,200 less the $400 of groceries).
        self.conn.execute(insert(Budget).values(category="Groceries", amount=500, pay_with="cc"))
        fc = forecast.build(self.conn, TODAY, 90)
        outside = (1200 + 800 + 800) / 3
        self.assertEqual(self.card(fc)["avg_outside"], round(outside, 2))
        oct10 = 300 + outside * 17 / 30 + 300 + 500 / 31 * 10    # on the card already, the rest of its share, Groceries to Oct 10
        nov10 = outside + 500 / 31 * 21 + 500 / 30 * 10
        self.assertEqual(self.payments(fc), {"2026-10-05": 600.0, "2026-11-05": round(oct10, 2), "2026-12-07": round(nov10, 2)})
        est = [e for e in fc["events"] if e["kind"] == "card" and e["estimated"]]
        self.assertTrue(est and all(e["from_budgets"] for e in est))
        # A budget paid from checking isn't on the card: the card's statements are only its spending outside budgets
        self.conn.execute(update(Budget).where(Budget.category == "Groceries").values(pay_with="chk"))
        fc = forecast.build(self.conn, TODAY, 90)
        self.assertEqual(self.payments(fc), {"2026-10-05": 600.0, "2026-11-05": round(300 + outside * 17 / 30, 2),
                                             "2026-12-07": round(outside, 2)})

    def test_with_budgets_a_recurring_charge_on_the_card_counts_once(self):
        # A $600 yearly subscription on the card, due Oct 20 (last year's is older than the averaged statements)
        self.tx("cc", "2025-10-20", -600.0, "STREAMFLIX YEARLY", "Subscriptions")
        self.conn.execute(insert(Recurring).values(name="Streamflix", account_id="cc", amount=-600, frequency="yearly",
                                                   anchor_date="2025-10-20", match="streamflix"))
        self.assertEqual(self.payments(forecast.build(self.conn, TODAY, 90))["2026-12-07"], round(self.EST2 + 600, 2))
        # A budget for Groceries leaves it on the card, on top of the budgets ...
        self.conn.execute(insert(Budget).values(category="Groceries", amount=500, pay_with="cc"))
        outside = (1200 + 800 + 800) / 3
        nov10 = outside + 500 / 31 * 21 + 500 / 30 * 10
        self.assertEqual(self.payments(forecast.build(self.conn, TODAY, 90))["2026-12-07"], round(nov10 + 600, 2))
        # ... and one for Subscriptions (paid from checking) has it already, so it isn't added again
        self.conn.execute(insert(Budget).values(category="Subscriptions", amount=50, pay_with="chk"))
        self.assertEqual(self.payments(forecast.build(self.conn, TODAY, 90))["2026-12-07"], round(nov10, 2))

    def test_with_budgets_a_carried_balance_is_charged_interest_on_the_budgeted_charges_too(self):
        self.conn.execute(insert(Budget).values(category="Groceries", amount=500, pay_with="cc"))
        self.pay("minimum", apr="24")
        fc = forecast.build(self.conn, TODAY, 90)
        outside = (1200 + 800 + 800) / 3
        # Sep 10's $800, minimum $40, already met by the $200 paid: $600 carries into Oct 10's statement. Carrying a
        # balance ends the grace period, so its interest is on the $600 and on half the cycle's charges, budgeted ones too.
        oct10 = 300 + outside * 17 / 30 + 300 + 500 / 31 * 10
        interest = (600 + oct10 / 2) * 0.24 / 12
        self.assertEqual(self.payments(fc)["2026-11-05"], self.minimum(600 + interest + oct10, interest))

    def test_paying_the_minimum_writes_nothing(self):
        # the payment plan is read from settings, never written, while the forecast is built
        self.stmt("cc", 800.0, "2026-09-10", "2026-10-05", minimum=250.0)
        self.pay("minimum", apr="24")
        self.conn.execute(insert(Override).values(key="cardclose:cc:2026-09-10", amount=-300.0))
        self.conn.execute(insert(Budget).values(category="Groceries", amount=500, pay_with="cc"))
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
        s1 = -400 + self.EST1   # Oct 10's statement: $400 lower than the cycle's charges ($504.45, so the $25 floor)
        self.assertEqual(payments["2026-10-05"], 1000.0)
        self.assertEqual(payments["2026-11-05"], 25.0)
        self.assertEqual(payments["2026-12-07"], self.minimum(s1 - 25 + self.EST2))
        self.assertTrue(all(e["amount"] < 0 for e in fc["events"] if e["kind"] == "card"))   # never a negative payment

    def test_the_budget_scenario_pays_the_same_way(self):
        self.stmt("cc", 800.0, "2026-09-10", "2026-10-05", minimum=250.0)
        self.pay("minimum")
        self.conn.execute(insert(Budget).values(category="Groceries", amount=500, pay_with="cc"))
        fc = forecast.build(self.conn, TODAY, 90)
        paid = lambda due: fc["budget"]["total"][fc["dates"].index(due) - 1] - fc["budget"]["total"][fc["dates"].index(due)]
        self.assertAlmostEqual(paid("2026-10-05"), 50.0, places=2)   # the closed statement: the forecast's own event
        charged = 300 + 300 + 500 / 31 * 10   # as in test_sticking_to_the_budget
        s1 = 550 + charged
        card = {c["date"]: c for c in fc["budget"]["changes"] if c["kind"] == "card"}
        self.assertAlmostEqual(-card["2026-11-05"]["amount"], self.minimum(s1), places=1)
        s2 = s1 - self.minimum(s1) + 500 / 31 * 21 + 500 / 30 * 10
        self.assertAlmostEqual(-card["2026-12-07"]["amount"], self.minimum(s2), places=1)
        # with an APR, the budget line charges the same interest as the forecast
        self.pay("minimum", apr="24")
        fc = forecast.build(self.conn, TODAY, 90)
        card = {c["date"]: c for c in fc["budget"]["changes"] if c["kind"] == "card"}
        i1 = (550 + charged / 2) * 0.02
        self.assertAlmostEqual(-card["2026-11-05"]["amount"], self.minimum(550 + i1 + charged, i1), places=1)


class AnnualFeeTests(LedgerCase):
    """Churning cards' annual fees, on ForecastTests' card (its statements close the 10th and are due the 5th)."""

    def setUp(self):
        super().setUp()
        ForecastTests.card_setup(self)

    def churn(self, opened="2024-10-20", product="Sapphire", annual_fee=95.0, **kw):
        self.conn.execute(insert(ChurnCard).values(owner="Alex", issuer="chase", product=product, opened_on=opened,
                                                   annual_fee=annual_fee, **kw))

    def payments(self, fc):
        return {e["date"]: e["amount"] for e in fc["events"] if e["kind"] == "card"}

    def test_fee_is_a_charge_on_the_card_paid_with_its_statement(self):
        before = forecast.build(self.conn, TODAY, 90)
        self.churn(account_id="cc")   # opened Oct 20: its fee is on October's statement, which closes Oct 10
        fc = forecast.build(self.conn, TODAY, 90)
        self.assertEqual(fc["fees"], [{
            "date": "2026-10-10", "amount": -95.0, "kind": "fee", "churn_card_id": 1, "name": "Sapphire annual fee",
            "account_id": "cc", "account": "cc", "category": "Fees & Interest", "paid_on": "2026-11-05", "paid_from": "chk"}])
        # Not taken out of checking on its own: it's in the Nov 5 payment of the statement it's on, and only there.
        self.assertNotIn("fee", [e["kind"] for e in fc["events"]])
        was, now = self.payments(before), self.payments(fc)
        self.assertAlmostEqual(now["2026-11-05"] - was["2026-11-05"], -95.0, places=2)
        self.assertEqual({d: a for d, a in now.items() if d != "2026-11-05"}, {d: a for d, a in was.items() if d != "2026-11-05"})
        i = fc["dates"].index("2026-11-05")
        self.assertAlmostEqual(fc["total"][i] - before["total"][i], -95.0, places=2)
        self.assertEqual(fc["total"][i - 1], before["total"][i - 1])
        card = next(c for c in fc["cards"] if c["id"] == "cc")
        self.assertEqual(card["annual_fees"], [{"date": "2026-10-10", "amount": -95.0, "category": "Fees & Interest"}])

    def test_a_product_change_keeps_the_accounts_anniversary(self):
        # Opened in October 2023, changed in July to a card with a fee: the fee is still October's, not July's.
        self.churn(opened="2023-10-20", product="Reserve", annual_fee=0.0, status="product_changed")
        self.churn(opened="2026-07-01", product="Preferred", account_id="cc", changed_from=1)
        fc = forecast.build(self.conn, TODAY, 90)
        self.assertEqual([(f["name"], f["date"]) for f in fc["fees"]], [("Preferred annual fee", "2026-10-10")])
        card = next(c for c in churning.load(self.conn)["cards"] if c["product"] == "Preferred")
        self.assertEqual(churning.next_fee(card, TODAY), date(2026, 10, 20))

    def test_fee_after_the_close_is_on_the_next_statement(self):
        # Paid in full each month, the fee lands on the statement after the one in progress when the closing day comes
        # first: anniversary month November, closing Nov 10, paid Dec 5 (a Saturday: Monday the 7th).
        before = self.payments(forecast.build(self.conn, TODAY, 90))
        self.churn(opened="2023-11-02", account_id="cc")
        fc = forecast.build(self.conn, TODAY, 90)
        self.assertEqual((fc["fees"][0]["date"], fc["fees"][0]["paid_on"]), ("2026-11-10", "2026-12-07"))
        self.assertAlmostEqual(self.payments(fc)["2026-12-07"] - before["2026-12-07"], -95.0, places=2)

    def test_fee_past_the_horizon_of_its_payment_is_only_listed(self):
        # Dec 10's statement is paid Jan 5, past a 80-day horizon: the fee is listed, its payment isn't in the chart.
        before = forecast.build(self.conn, TODAY, 80)
        self.churn(opened="2022-12-01", account_id="cc")
        fc = forecast.build(self.conn, TODAY, 80)
        self.assertEqual((fc["fees"][0]["date"], fc["fees"][0]["paid_on"]), ("2026-12-10", None))
        self.assertEqual(fc["total"], before["total"])

    def test_no_fee_for_closed_planned_free_or_charged_cards(self):
        self.churn(status="closed", closed_on="2026-01-05", account_id="cc")
        self.churn(status="product_changed", closed_on="2026-01-05", account_id="cc")
        self.churn(closed_on="2026-10-01", account_id="cc")                       # set to close before the fee
        self.churn(plan="close", account_id="cc")                                 # to close before the fee
        self.churn(plan="product_change", plan_date="2026-10-01", account_id="cc")
        self.churn(annual_fee=0.0, account_id="cc")
        self.churn(annual_fee=None, account_id="cc")
        self.churn(opened="2026-01-15", account_id="cc")                          # opened this year: no fee till next
        before = forecast.build(self.conn, TODAY, 90)
        self.assertEqual(before["fees"], [])
        # A plan already done, or one for after the fee, leaves the fee to come.
        self.churn(plan="close", plan_date="2026-10-20", account_id="cc")
        self.churn(plan="keep", account_id="cc")
        self.assertEqual(len(forecast.build(self.conn, TODAY, 90)["fees"]), 2)

    def test_no_fee_once_charged(self):
        self.churn(account_id="cc")
        self.tx("cc", "2025-10-10", -95.0, "ANNUAL FEE", "Fees & Interest")   # last year's: this year's is still to come
        self.assertEqual(len(forecast.build(self.conn, TODAY, 90)["fees"]), 1)
        self.tx("cc", "2026-09-21", 95.0, "ANNUAL FEE REFUND", "Refunds")        # a refund isn't the fee
        self.assertEqual(len(forecast.build(self.conn, TODAY, 90)["fees"]), 1)
        self.tx("cc", "2026-09-22", -95.0, "ANNUAL MEMBERSHIP FEE", "Fees & Interest", pending=1)   # charged early
        fc = forecast.build(self.conn, TODAY, 90)
        self.assertEqual(fc["fees"], [])
        card = next(c for c in fc["cards"] if c["id"] == "cc")
        self.assertEqual(card["annual_fees"], [])

    def test_late_fee_comes_today(self):
        # Anniversary (closing day) Oct 10, and it's the 15th: not charged yet, so it's still coming, today.
        self.churn(account_id="cc")
        self.churn(opened="2024-10-02", product="Unlinked")                      # can't tell: nothing
        fees = forecast.build(self.conn, date(2026, 10, 15), 60)["fees"]
        self.assertEqual([(f["name"], f["date"], f["late_from"]) for f in fees], [("Sapphire annual fee", "2026-10-15", "2026-10-10")])
        self.tx("cc", "2026-10-12", -95.0, "ANNUAL FEE")
        self.assertEqual(forecast.build(self.conn, date(2026, 10, 15), 60)["fees"], [])
        # November: October's anniversary is over.
        self.assertEqual(forecast.build(self.conn, date(2026, 11, 1), 60)["fees"], [])

    def test_a_recurring_item_for_the_fee_is_not_doubled(self):
        self.churn(account_id="cc")
        self.conn.execute(insert(Recurring).values(name="Sapphire fee", account_id="cc", amount=-95, frequency="yearly",
                                                   anchor_date="2025-10-12", active=1))
        self.assertEqual(forecast.build(self.conn, TODAY, 90)["fees"], [])
        # one that isn't a fee, or not near the anniversary, doesn't count
        self.conn.execute(update(Recurring).values(name="Sapphire travel credit"))
        self.assertEqual(len(forecast.build(self.conn, TODAY, 90)["fees"]), 1)
        self.conn.execute(update(Recurring).values(name="Sapphire fee", anchor_date="2026-03-12"))
        self.assertEqual(len(forecast.build(self.conn, TODAY, 90)["fees"]), 1)

    def test_unlinked_card_is_listed_without_touching_cash(self):
        before = forecast.build(self.conn, TODAY, 90)
        self.churn(opened="2020-11-30", product="Gold")             # no account: on the anniversary itself
        self.acct("hidden", "credit", -10.0, hidden=1)
        self.churn(opened="2021-12-05", product="Hidden", account_id="hidden")
        self.acct("sav", "savings", 100.0)
        self.churn(opened="2021-11-03", product="Odd", account_id="sav")  # linked to something that isn't a card
        fc = forecast.build(self.conn, TODAY, 90)
        self.assertEqual([(f["name"], f["date"], f["account_id"], f["paid_on"]) for f in fc["fees"]],
                         [("Odd annual fee", "2026-11-03", None, None), ("Gold annual fee", "2026-11-30", None, None),
                          ("Hidden annual fee", "2026-12-05", None, None)])
        self.assertEqual((fc["total"], self.payments(fc)), (before["total"], self.payments(before)))

    def test_a_card_without_a_statement_is_listed_on_its_anniversary(self):
        self.acct("cc2", "credit", -50.0, pay_from="chk")             # no statement: its payments aren't forecast
        self.churn(opened="2019-11-17", account_id="cc2")
        fees = forecast.build(self.conn, TODAY, 90)["fees"]
        self.assertEqual([(f["date"], f["account_id"], f["paid_on"]) for f in fees], [("2026-11-17", "cc2", None)])

    def test_short_months_and_leap_days(self):
        card = {"id": 1, "product": "Plat", "annual_fee": 695.0, "opened_on": "2024-02-29", "status": "open"}
        on = lambda today, end, closing=None: [f["date"] for f in forecast.annual_fees(self.conn, card, today, end, closing, [])]
        self.assertEqual(on(date(2027, 1, 15), date(2027, 3, 31)), ["2027-02-28"])     # Feb 29, not a leap year
        self.assertEqual(on(date(2027, 3, 1), date(2028, 3, 31)), ["2028-02-29"])      # and in one
        self.assertEqual(on(date(2027, 1, 15), date(2027, 3, 31), 31), ["2027-02-28"])  # closing the 31st
        self.assertEqual(on(date(2027, 1, 15), date(2027, 3, 31), 5), ["2027-02-05"])
        self.assertEqual(on(date(2024, 3, 1), date(2025, 1, 31)), [])                  # the first year: none yet
        # A long horizon has the next year's too; a fee month left over from before doesn't move it.
        card.update(opened_on="2023-10-31", fee_month=3)
        self.assertEqual(on(date(2026, 9, 23), date(2027, 11, 30)), ["2026-10-31", "2027-10-31"])
        self.assertEqual(on(date(2026, 11, 1), date(2027, 9, 30)), [])

    def test_budget_line_keeps_the_fee(self):
        self.conn.execute(insert(Budget).values(category="Groceries", amount=500, pay_with="cc"))
        before = forecast.build(self.conn, TODAY, 90)["budget"]
        self.churn(account_id="cc")
        fc = forecast.build(self.conn, TODAY, 90)
        paid = lambda b, d: b["total"][fc["dates"].index(d) - 1] - b["total"][fc["dates"].index(d)]
        self.assertAlmostEqual(paid(fc["budget"], "2026-11-05") - paid(before, "2026-11-05"), 95.0, places=2)
        # a budget for fees has it already
        self.conn.execute(insert(Budget).values(category="Fees & Interest", amount=10, pay_with="cc"))
        with_fees = forecast.build(self.conn, TODAY, 90)
        self.churn(product="Second", account_id="cc")
        again = forecast.build(self.conn, TODAY, 90)
        self.assertAlmostEqual(paid(again["budget"], "2026-11-05"), paid(with_fees["budget"], "2026-11-05"), places=2)

    def test_budget_line_keeps_the_fee_of_a_card_with_no_statement(self):
        # A new card with no statement yet, $310 a month budgeted on it and a $95 fee on Oct 20: the fee is on October's
        # assumed statement (closing Oct 31, paid Nov 25), with October's charges.
        self.acct("cc3", "credit", 0.0, pay_from="chk")
        self.conn.execute(insert(Budget).values(category="Travel", amount=310, pay_with="cc3"))
        self.churn(account_id="cc3")
        card = {c["date"]: c for c in forecast.build(self.conn, TODAY, 90)["budget"]["changes"] if c["kind"] == "card"}
        self.assertEqual((card["2026-10-26"]["amount"], card["2026-11-25"]["amount"]), (-310.0, -405.0))
        # a budget for fees has it already
        self.conn.execute(insert(Budget).values(category="Fees & Interest", amount=0.01, pay_with="cc3"))
        card = {c["date"]: c for c in forecast.build(self.conn, TODAY, 90)["budget"]["changes"] if c["kind"] == "card"}
        self.assertAlmostEqual(card["2026-11-25"]["amount"], -310.01, places=2)

    def test_budget_line_keeps_an_overdue_fee_of_a_card_with_no_statement(self):
        # Its anniversary was Sep 10 and the fee hasn't posted: it's expected today, on the first assumed statement.
        self.acct("cc3", "credit", 0.0, pay_from="chk")
        self.conn.execute(insert(Budget).values(category="Travel", amount=310, pay_with="cc3"))
        self.churn(opened="2024-09-10", account_id="cc3")
        card = {c["date"]: c for c in forecast.build(self.conn, TODAY, 90)["budget"]["changes"] if c["kind"] == "card"}
        self.assertEqual(card["2026-10-26"]["amount"], -405.0)


if __name__ == "__main__":
    unittest.main(verbosity=1)
