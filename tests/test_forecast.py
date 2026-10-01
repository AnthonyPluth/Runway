"""The forecast: dates and schedules, card payments, and the balance chart."""
import unittest
from datetime import date, timedelta

from sqlalchemy import delete, func, insert, select, update

from runway import db, forecast, recurring
from runway.models import Account, Budget, CardStatement, Override, PlaidAccount, Recurring, RecurringDismissed, Transaction
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
        db.set_setting(self.conn, "primary_account", "chk")   # chk's everyday-spending drain is off (the default)
        fc = forecast.build(self.conn, TODAY, 30)
        self.assertEqual([a["id"] for a in fc["accounts"]], ["chk"])
        self.assertEqual(fc["primary_id"], "chk")
        s, dates = fc["total"], fc["dates"]
        event_days = {e["date"] for e in fc["events"]}
        for i in range(1, len(s)):
            if dates[i] not in event_days:
                self.assertEqual(s[i], s[i - 1], f"balance moved on {dates[i]} with nothing scheduled")
        # switched off, the forecast still says what it would take out, for Overview's "about $25 a day"
        acct = fc["accounts"][0]
        self.assertEqual((acct["daily_spend"], acct["daily_spend_on"]), (0.0, False))
        self.assertGreater(acct["daily_spend_estimate"], 0)
        # opting back in brings the drain back
        self.conn.execute(update(Account).where(Account.id == "chk").values(daily_spend=1))
        fc = forecast.build(self.conn, TODAY, 30)
        self.assertGreater(fc["accounts"][0]["daily_spend"], 0)
        self.assertTrue(fc["accounts"][0]["daily_spend_on"])
        self.assertEqual(fc["accounts"][0]["daily_spend"], fc["accounts"][0]["daily_spend_estimate"])

    def test_card_without_bank_statements_warns(self):
        self.conn.execute(delete(CardStatement))
        fc = forecast.build(self.conn, TODAY, 30)
        self.assertIn("Plaid hasn’t sent a statement for cc yet", fc["warnings"][0])     # linked, statement not in yet
        self.assertEqual((fc["cards"], fc["unlinked_cards"]), ([], [{"id": "cc", "name": "cc", "owed_now": 900.0, "linked": True}]))
        self.assertEqual(fc["warning_links"], [{"text": fc["warnings"][0], "href": "#setup/connections"}])
        self.conn.execute(update(Account).where(Account.id == "cc").values(plaid_account_id=None))                  # not linked at all
        fc = forecast.build(self.conn, TODAY, 30)
        self.assertIn("cc isn’t linked through Plaid yet", fc["warnings"][0])
        self.assertEqual(fc["warning_links"][0]["href"], "#setup/connections")   # nothing from Plaid to match: connect the bank
        self.conn.execute(insert(PlaidAccount).values(plaid_account_id="pcc", item_id="it", name="Visa", type="credit"))
        fc = forecast.build(self.conn, TODAY, 30)
        self.assertIn("Plaid has 1 card waiting to be matched", fc["warnings"][0])
        self.assertEqual(fc["warning_links"][0]["href"], "#setup/accounts")      # matched under “New from Plaid”

    def test_warnings_link_to_where_they_are_fixed(self):
        self.conn.execute(update(Account).where(Account.id == "cc").values(pay_from=None))
        fc = forecast.build(self.conn, TODAY, 30)
        self.assertEqual(fc["warning_links"], [{"text": "cc: choose which account pays it in Settings.", "href": "#setup/accounts"}])
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
        prime = {"name": "Prime", "match": "amazon", "amount": -14.99, "amount_mode": "fixed"}
        rate = forecast.daily_spend_rate(self.conn, "chk", TODAY, [prime])
        self.assertAlmostEqual(rate, (300 + 35 + 85) / 30, places=2)
        # an item without an amount can't tell them apart: everything with its text is left out, as before
        rate = forecast.daily_spend_rate(self.conn, "chk", TODAY, [{**prime, "amount": 0}])
        self.assertAlmostEqual(rate, 300 / 30, places=2)

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
    """What the forecast assumes about pending transactions, card cycles, everyday spending and the budget scenario."""
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

    def paycheck_pending(self):
        """A biweekly $1,000 paycheck, today's pending (and linked to it once the forecast matches it)."""
        self.conn.execute(insert(Recurring).values(name="Paycheck", account_id="chk", amount=1000, frequency="biweekly",
                                                   anchor_date="2026-09-09", match="acme payroll"))
        self.tx("chk", "2026-09-23", 1000.0, "ACME PAYROLL", "Income", pending=1)

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
        self.assertIn({"text": text, "href": "#budget/recurring"}, fc["warning_links"])
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
        # a gym the card has paid every month since June (in the average), a yearly insurance premium, and a new
        # subscription that hasn't charged yet
        self.conn.execute(insert(Recurring).values(name="Gym", account_id="cc", amount=-30, frequency="monthly",
                                                   anchor_date="2026-06-15", match="gym"))
        for d in ("2026-06-15", "2026-07-15", "2026-08-15", "2026-09-15"):
            self.tx("cc", d, -30.0, "GYM", "Fitness")
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
        self.conn.execute(update(Recurring).where(Recurring.name == "Streaming").values(anchor_date="2026-06-30"))
        for d in ("2026-06-30", "2026-07-30", "2026-08-30"):
            self.tx("cc", d, -15.0, "STREAMFLIX", "Subscriptions")
        card = next(c for c in forecast.build(self.conn, TODAY, 120)["cards"] if c["id"] == "cc")
        self.assertEqual(self.estimates(days=120)["2027-01-05"], card["avg_monthly_spend"])

    def test_a_budget_on_a_card_paid_from_outside_the_forecast_is_skipped(self):
        self.acct("sav", "savings", 20000.0)    # not forecast: checking is the only checking account
        self.acct("cc2", "credit", -50.0, pay_from="sav")
        self.stmt("cc2", 50.0, "2026-09-10", "2026-10-05")
        self.conn.execute(insert(Budget).values(category="Groceries", amount=500, pay_with="cc2"))
        b = forecast.build(self.conn, TODAY, 60)["budget"]
        self.assertEqual(b["used"], [])
        self.assertEqual(b["skipped"], [{"category": "Groceries", "reason": "its card isn't paid from a forecast account"}])
        self.assertEqual(b["monthly"], 0.0)

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

    def test_a_payment_that_posted_today_but_isnt_linked_yet_counts_once(self):
        for d in ("2026-08-01", "2026-09-01"):
            self.tx("chk", d, -2000.0, "LANDLORD LLC", "Rent")
        self.conn.execute(insert(Recurring).values(name="Rent", account_id="chk", amount=-2000, frequency="monthly",
                                                   anchor_date="2026-08-01", match="landlord"))
        recurring.auto_match(self.conn)
        today = date(2026, 10, 1)
        self.tx("chk", "2026-10-01", -2000.0, "LANDLORD LLC", "Rent")   # synced, not linked yet
        fc = forecast.build(self.conn, today, 20)
        self.assertEqual([e for e in fc["events"] if e["name"] == "Rent"], [])

    def test_a_later_payment_doesnt_count_for_an_earlier_occurrence(self):
        item = {"frequency": "monthly"}
        history = [{"posted": "2026-10-01"}]
        self.assertTrue(recurring.already_happened(item, date(2026, 10, 4), history, date(2026, 10, 5)))    # early
        self.assertTrue(recurring.already_happened(item, date(2026, 9, 27), history, date(2026, 10, 5)))    # a little late
        self.assertFalse(recurring.already_happened(item, date(2026, 9, 1), history, date(2026, 10, 5)))    # October's


if __name__ == "__main__":
    unittest.main(verbosity=1)
