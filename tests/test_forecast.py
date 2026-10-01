"""The forecast: dates and schedules, card payments, and the balance chart."""
import unittest
from datetime import date, timedelta

from runway import db, forecast, recurring
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
        self.assertEqual(est["2026-11-05"], 1066.67)   # cycle in progress: 300 charged so far, so the average wins
        self.assertEqual(est["2026-12-07"], 1066.67)   # Dec 5 is a Saturday: paid Monday
        # if the cycle in progress is already past the average, it's at least what's been charged
        self.tx("cc", "2026-09-21", -2000.0, "LAPTOP", "Shopping")
        est = {e["date"]: -e["amount"] for e in forecast.build(self.conn, TODAY, 90)["events"] if e["estimated"]}
        self.assertEqual(est["2026-11-05"], 2300.0)

    def test_sticking_to_the_budget(self):
        # $500/month on Groceries, paid with the card. $200 already spent in September, so $300 over Sep 24-30.
        self.conn.execute("INSERT INTO budgets(category, amount, pay_with) VALUES ('Groceries', 500, 'cc')")
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
        self.conn.execute("INSERT INTO budgets(category, amount, pay_with) VALUES ('Groceries', 310, 'chk')")
        fc = forecast.build(self.conn, TODAY, 40)
        b, days = fc["budget"], fc["dates"]
        out = {c["date"]: c for c in b["changes"] if c["kind"] == "budget"}
        self.assertEqual((out["2026-09-24"]["category"], out["2026-09-24"]["amount"]), ("Groceries", -15.71))
        self.assertEqual(out["2026-10-15"]["amount"], -10.0)        # October: 310 / 31
        self.assertEqual(len(out), 40)                              # every day after today
        drop = b["total"][days.index("2026-10-14")] - b["total"][days.index("2026-10-15")]
        self.assertAlmostEqual(drop, 10.0 - sum(e["amount"] for e in fc["events"] if e["date"] == "2026-10-15"), places=2)

    def grocery_box(self, link=True):
        """A $100 grocery box from checking on the 1st of each month (Oct 1, Nov 1 in the forecast)."""
        self.conn.execute("INSERT INTO recurring(id, name, account_id, amount, frequency, anchor_date) VALUES (7, 'Grocery box','chk',-100,'monthly','2026-09-01')")
        self.tx("chk", "2026-09-01", -100.0, "GROCERY BOX", "Groceries")
        if link:
            self.conn.execute("UPDATE transactions SET recurring_id=7 WHERE description='GROCERY BOX'")

    def test_a_budget_counts_its_recurring_payments_once(self):
        # $500 on Groceries from checking includes the $100 box: $400 a month more, not $500 on top of it
        self.grocery_box()
        self.conn.execute("INSERT INTO budgets(category, amount, pay_with) VALUES ('Groceries', 500, 'chk')")
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
        self.conn.execute("INSERT INTO budgets(category, amount, pay_with) VALUES ('Groceries', 80, 'chk')")
        b = forecast.build(self.conn, TODAY, 60)["budget"]
        self.assertEqual((b["used"], b["skipped"][0]), ([], {"category": "Groceries", "reason": "a recurring item already covers it"}))

    def test_a_recurring_item_with_nothing_linked_yet_takes_its_category_from_what_it_matches(self):
        self.grocery_box(link=False)
        self.conn.execute("INSERT INTO budgets(category, amount, pay_with) VALUES ('Groceries', 500, 'chk')")
        fc = forecast.build(self.conn, TODAY, 60)
        self.assertEqual({e["category"] for e in fc["events"] if e.get("recurring_id") == 7}, {"Groceries"})
        daily = {c["date"]: -c["amount"] for c in fc["budget"]["changes"] if c["kind"] == "budget"}
        self.assertEqual(daily["2026-10-15"], round(400 / 31, 2))     # counted once all the same

    def test_statement_you_entered_wins(self):
        key = self.cycle("cc")["statement_key"]
        self.assertEqual(key, "stmt:cc:2026-09-10")
        self.conn.execute("INSERT INTO overrides(key, amount) VALUES (?, 950)", (key,))
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
        self.conn.execute("UPDATE accounts SET balance=900, owed_positive=1 WHERE id='cc'")
        card = next(c for c in forecast.build(self.conn, TODAY, 30)["cards"] if c["id"] == "cc")
        self.assertEqual((card["owed_now"], card["statement_balance"]), (900.0, 800.0))

    def test_build(self):
        self.conn.execute(
            "INSERT INTO recurring(name, account_id, amount, frequency, anchor_date) VALUES ('Paycheck','chk',3000,'biweekly','2026-09-18')"
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
        self.conn.execute("INSERT INTO recurring(name, account_id, amount, frequency, anchor_date) VALUES ('Paycheck','chk',3000,'biweekly','2026-09-18')")
        self.conn.execute("INSERT INTO recurring(name, account_id, amount, frequency, anchor_date) VALUES ('Gym','chk',-50,'monthly','2026-09-05')")
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
        self.conn.execute("UPDATE accounts SET daily_spend=1 WHERE id='chk'")
        db.set_setting(self.conn, "primary_account", "chk")
        self.conn.execute("DELETE FROM settings WHERE key='migrated_daily_spend_off'")  # pretend it's a pre-update database
        self.conn.commit()
        db.init(self.path)  # existing databases: everyday-spending drain gets switched off once
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
        self.conn.execute("UPDATE accounts SET daily_spend=1 WHERE id='chk'")
        fc = forecast.build(self.conn, TODAY, 30)
        self.assertGreater(fc["accounts"][0]["daily_spend"], 0)
        self.assertTrue(fc["accounts"][0]["daily_spend_on"])
        self.assertEqual(fc["accounts"][0]["daily_spend"], fc["accounts"][0]["daily_spend_estimate"])

    def test_card_without_bank_statements_warns(self):
        self.conn.execute("DELETE FROM card_statements")
        fc = forecast.build(self.conn, TODAY, 30)
        self.assertIn("Plaid hasn’t sent a statement for cc yet", fc["warnings"][0])     # linked, statement not in yet
        self.assertEqual((fc["cards"], fc["unlinked_cards"]), ([], [{"id": "cc", "name": "cc", "owed_now": 900.0, "linked": True}]))
        self.assertEqual(fc["warning_links"], [{"text": fc["warnings"][0], "href": "#setup/connections"}])
        self.conn.execute("UPDATE accounts SET plaid_account_id=NULL WHERE id='cc'")                  # not linked at all
        fc = forecast.build(self.conn, TODAY, 30)
        self.assertIn("cc isn’t linked through Plaid yet", fc["warnings"][0])
        self.assertEqual(fc["warning_links"][0]["href"], "#setup/connections")   # nothing from Plaid to match: connect the bank
        self.conn.execute("INSERT INTO plaid_accounts(plaid_account_id, item_id, name, type) VALUES ('pcc', 'it', 'Visa', 'credit')")
        fc = forecast.build(self.conn, TODAY, 30)
        self.assertIn("Plaid has 1 card waiting to be matched", fc["warnings"][0])
        self.assertEqual(fc["warning_links"][0]["href"], "#setup/accounts")      # matched under “New from Plaid”

    def test_warnings_link_to_where_they_are_fixed(self):
        self.conn.execute("UPDATE accounts SET pay_from=NULL WHERE id='cc'")
        fc = forecast.build(self.conn, TODAY, 30)
        self.assertEqual(fc["warning_links"], [{"text": "cc: choose which account pays it in Settings.", "href": "#setup/accounts"}])
        self.assertEqual(fc["warnings"], ["cc: choose which account pays it in Settings."])   # plain text, as MCP clients read it

    def test_paid_statement_no_event(self):
        self.tx("cc", "2026-09-22", 600.0, "PAYMENT", "Credit Card Payment")
        self.conn.execute("UPDATE accounts SET balance=-300 WHERE id='cc'")  # the payment lowers what's owed
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
        rate = forecast.daily_spend_rate(self.conn, "chk", TODAY, ["netflix"])
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
        self.assertEqual(self.conn.execute("SELECT dates FROM recurring WHERE id=?", (r["id"],)).fetchone()[0], "04-15,10-15")
        with self.assertRaises(server.ApiError):
            server.api_recurring_add(self.conn, None, {"name": "X", "account_id": "chk", "amount": -1, "frequency": "dates",
                                                       "dates": "whenever", "anchor_date": "2026-01-01"})

    def test_missed_payments(self):
        from runway import recurring
        self.acct("chk", "checking", 1000.0)
        self.tx("chk", "2026-06-01", -5.0, "OPENING", "Other")                      # history starts here
        self.conn.execute("INSERT INTO recurring(id, name, account_id, amount, frequency, anchor_date, match, active) "
                          "VALUES (1, 'Gym', 'chk', -40, 'monthly', '2026-07-05', 'gym', 1)")
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
        self.conn.execute("DELETE FROM recurring_dismissed")
        self.tx("chk", "2026-09-06", -40.0, "CLUB FEE", "Other")
        tid = self.conn.execute("SELECT id FROM transactions WHERE description='CLUB FEE'").fetchone()[0]
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
        self.conn.execute("INSERT INTO recurring(name, account_id, amount, frequency, anchor_date, match) "
                          "VALUES ('Rent','chk',-2000,'monthly','2026-07-01','landlord')")
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
        self.conn.execute("UPDATE transactions SET posted='2026-09-09' WHERE description='PAYMENT THANK YOU' AND amount=600")
        self.conn.execute("UPDATE transactions SET posted='2026-09-11' WHERE account_id='chk' AND amount=-600")
        self.assertEqual(self.cycle("cc")["paid_since_close"], 200.0)
        self.conn.execute("UPDATE transactions SET posted='2026-09-23' WHERE description='PAYMENT THANK YOU' AND amount=600")
        # an account that pays two cards can't tell whose it is, so it waits for the card
        self.acct("cc2", "credit", -50.0, pay_from="chk")
        self.conn.execute("DELETE FROM transactions WHERE id=(SELECT MAX(id) FROM transactions WHERE account_id='cc')")
        self.assertEqual(self.cycle("cc")["paid_since_close"], 200.0)

    def test_recurring_card_charges_count_on_a_new_card(self):
        before = {e["date"]: e["amount"] for e in forecast.build(self.conn, TODAY, 60)["events"] if e["estimated"]}
        self.conn.execute("INSERT INTO recurring(name, account_id, amount, frequency, anchor_date) "
                          "VALUES ('Streaming','cc',-15,'monthly','2026-09-30')")
        after = {e["date"]: e["amount"] for e in forecast.build(self.conn, TODAY, 60)["events"] if e["estimated"]}
        self.assertAlmostEqual(after["2026-11-05"], before["2026-11-05"] - 15, places=2)

    def test_two_dates_on_the_same_business_day_are_two_payments(self):
        item = {"frequency": "dates", "dates": "10-10, 10-11", "anchor_date": "2026-01-01", "amount": -100}
        days = forecast.occurrences(item, date(2026, 10, 1), date(2026, 10, 31))
        self.assertEqual(len(days), 2)
        self.assertEqual(days[0], days[1])


if __name__ == "__main__":
    unittest.main(verbosity=1)
