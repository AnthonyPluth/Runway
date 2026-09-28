import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runway import categories, categorize, db, forecast, recurring, server, simplefin, splits  # noqa: E402

TODAY = date(2026, 9, 23)


def ts(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, 12, tzinfo=timezone.utc).timestamp())


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "t.db")
        db.init(self.path)
        self.conn = db.connect(self.path)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def acct(self, id, kind, balance, **kw):
        cols = {"id": id, "name": id, "kind": kind, "balance": balance, "balance_date": TODAY.isoformat(), **kw}
        self.conn.execute(
            f"INSERT INTO accounts({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", list(cols.values())
        )

    def stmt(self, card, balance, closed, due, minimum=None):
        """The card issuer's latest statement, as Plaid Liabilities reports it."""
        self.conn.execute("UPDATE accounts SET plaid_account_id=? WHERE id=?", (f"p-{card}", card))
        self.conn.execute("DELETE FROM card_statements WHERE plaid_account_id=?", (f"p-{card}",))
        self.conn.execute("INSERT INTO card_statements(plaid_account_id, item_id, last_statement_balance, last_statement_date, "
                          "next_due_date, minimum_payment) VALUES (?,?,?,?,?,?)", (f"p-{card}", "item", balance, closed, due, minimum))

    def cycle(self, card_id, today=None):
        card = dict(self.conn.execute("SELECT * FROM accounts WHERE id=?", (card_id,)).fetchone())
        return forecast.card_cycle(self.conn, card, today or TODAY, forecast.bank_statement(self.conn, card, today or TODAY))

    def tx(self, acct, posted, amount, desc="x", category=None, pending=0):
        n = self.conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        self.conn.execute(
            "INSERT INTO transactions(id, account_id, posted, amount, description, payee, category, pending) VALUES (?,?,?,?,?,?,?,?)",
            (f"{acct}|{n}", acct, posted, amount, desc, categorize.clean_payee(desc), category, pending),
        )


class PayeeTests(unittest.TestCase):
    def test_clean(self):
        cases = {
            "TST*XIAN FAMOUS FOODS - ": "Xian Famous Foods",
            "SQ *WILLIAM GREENBERG DES": "William Greenberg Des",
            "DD *DIMSUMSAM": "Dimsumsam",
            "LYFT   *SCHD AIR 09-20": "Lyft",
            "UBER *EATS PENDING": "Uber Eats",
            "JIMMY JOHNS - 3956 - ECOM": "Jimmy Johns",
            "CVS/PHARMACY ##02671": "Cvs/pharmacy",
            "AMZN Mktp US*2K3AB1": "Amzn Mktp Us",
            "": "",
        }
        for raw, want in cases.items():
            self.assertEqual(categorize.clean_payee(raw), want, raw)


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


class ForecastTests(Base):
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

    def test_budget_covered_by_a_recurring_item_is_left_out(self):
        self.conn.execute("INSERT INTO recurring(id, name, account_id, amount, frequency, anchor_date) VALUES (7, 'Grocery box','chk',-100,'monthly','2026-09-01')")
        self.conn.execute("UPDATE transactions SET recurring_id=7 WHERE category='Groceries'")
        self.conn.execute("INSERT INTO budgets(category, amount) VALUES ('Groceries', 500)")
        b = forecast.build(self.conn, TODAY, 60)["budget"]
        self.assertEqual((b["used"], b["skipped"][0]["category"]), ([], "Groceries"))

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
        # opting back in brings the drain back
        self.conn.execute("UPDATE accounts SET daily_spend=1 WHERE id='chk'")
        fc = forecast.build(self.conn, TODAY, 30)
        self.assertGreater(fc["accounts"][0]["daily_spend"], 0)

    def test_card_without_bank_statements_warns(self):
        self.conn.execute("DELETE FROM card_statements")
        fc = forecast.build(self.conn, TODAY, 30)
        self.assertIn("Plaid hasn’t sent a statement for cc yet", fc["warnings"][0])     # linked, statement not in yet
        self.assertEqual((fc["cards"], fc["unlinked_cards"]), ([], [{"id": "cc", "name": "cc", "owed_now": 900.0, "linked": True}]))
        self.conn.execute("UPDATE accounts SET plaid_account_id=NULL WHERE id='cc'")                  # not linked at all
        self.assertIn("cc isn’t linked through Plaid yet", forecast.build(self.conn, TODAY, 30)["warnings"][0])

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


class RecurringTests(Base):
    def setUp(self):
        super().setUp()
        self.acct("chk", "checking", 3000.0)
        # Electric bill: same merchant, different amount every month, plus a refund from them.
        for d, amt in [("2026-06-14", -91.20), ("2026-07-15", -143.77), ("2026-08-14", -160.02), ("2026-09-15", -118.40)]:
            self.tx("chk", d, amt, "COMED ELECTRIC PAYMENT 88812")
        self.tx("chk", "2026-09-02", 12.00, "COMED ELECTRIC REFUND")
        self.tx("chk", "2026-09-10", -60.0, "TARGET")
        self.conn.execute("INSERT INTO recurring(name, account_id, amount, frequency, anchor_date, match, amount_mode) "
                          "VALUES ('Electric','chk',-120,'monthly','2026-06-15','comed electric','avg3')")
        self.rid = self.conn.execute("SELECT id FROM recurring").fetchone()[0]

    def test_auto_match_by_merchant_any_amount(self):
        n = recurring.auto_match(self.conn)
        self.assertEqual(n, 4)  # all four bills, not the refund, not Target
        linked = [r[0] for r in self.conn.execute("SELECT amount FROM transactions WHERE recurring_id=? ORDER BY posted", (self.rid,))]
        self.assertEqual(linked, [-91.20, -143.77, -160.02, -118.40])

    def test_never_match_is_respected(self):
        recurring.link(self.conn, "chk|0", None)  # "not recurring"
        recurring.auto_match(self.conn)
        self.assertEqual(self.conn.execute("SELECT recurring_id FROM transactions WHERE id='chk|0'").fetchone()[0], 0)

    def test_amount_modes_and_forecast(self):
        recurring.auto_match(self.conn)
        item = dict(self.conn.execute("SELECT * FROM recurring").fetchone())
        hist = recurring.matched(self.conn, self.rid)
        self.assertEqual(recurring.expected_amount(item, hist), round((-118.40 - 160.02 - 143.77) / 3, 2))
        self.assertEqual(recurring.expected_amount({**item, "amount_mode": "last"}, hist), -118.40)
        self.assertEqual(recurring.expected_amount({**item, "amount_mode": "fixed"}, hist), -120.0)
        fc = forecast.build(self.conn, TODAY, 60)
        elec = [e for e in fc["events"] if e.get("recurring_id") == self.rid]
        # Sep 15 already posted, so the next ones are Oct 15 and Nov 15 (a Sunday: Monday the 16th) at the 3-month average
        self.assertEqual([e["date"] for e in elec], ["2026-10-15", "2026-11-16"])
        self.assertEqual(elec[0]["amount"], round((-118.40 - 160.02 - 143.77) / 3, 2))

    def test_early_payment_not_counted_twice(self):
        self.tx("chk", "2026-10-12", -130.0, "COMED ELECTRIC PAYMENT")  # paid 3 days early
        recurring.auto_match(self.conn)
        fc = forecast.build(self.conn, date(2026, 10, 13), 40)
        dates = [e["date"] for e in fc["events"] if e.get("recurring_id") == self.rid]
        self.assertEqual(dates, ["2026-11-16"])   # Nov 15 is a Sunday

    def test_override_one_occurrence(self):
        recurring.auto_match(self.conn)
        self.conn.execute("INSERT INTO overrides(key, amount) VALUES (?, ?)", (f"rec:{self.rid}:2026-10-15", -250.0))
        fc = forecast.build(self.conn, TODAY, 60)
        e = next(e for e in fc["events"] if e["date"] == "2026-10-15")
        self.assertEqual((e["amount"], e["overridden"]), (-250.0, True))
        self.assertAlmostEqual(fc["total"][-1], 3000 + sum(x["amount"] for x in fc["events"]), places=2)

    def test_create_from_transaction_and_link_teaches_match(self):
        self.tx("chk", "2026-09-05", -15.99, "NETFLIX.COM 8665797172")
        tid = self.conn.execute("SELECT id FROM transactions WHERE description LIKE 'NETFLIX%'").fetchone()[0]
        rid = recurring.create_from_transaction(self.conn, tid, "monthly")
        item = self.conn.execute("SELECT * FROM recurring WHERE id=?", (rid,)).fetchone()
        self.assertEqual((item["amount"], item["frequency"], item["match"]), (-15.99, "monthly", "netflix.com"))
        # an item without merchant text learns it from the first link
        self.conn.execute("INSERT INTO recurring(name, account_id, amount, frequency, anchor_date) VALUES ('Store','chk',-60,'monthly','2026-09-10')")
        sid = self.conn.execute("SELECT id FROM recurring WHERE name='Store'").fetchone()[0]
        recurring.link(self.conn, self.conn.execute("SELECT id FROM transactions WHERE description='TARGET'").fetchone()[0], sid)
        self.assertEqual(self.conn.execute("SELECT match FROM recurring WHERE id=?", (sid,)).fetchone()[0], "target")


class CategorizeTests(Base):
    def setUp(self):
        super().setUp()
        self.acct("chk", "checking", 100.0)
        self.acct("cc", "credit", -50.0)

    def test_pipeline_with_fake_ai(self):
        self.tx("chk", "2026-09-01", 0.0, "REDEMPTION FROM CORE ACCOUNT SPAXX")
        self.tx("chk", "2026-09-01", -300.0, "DIRECT DEBIT CITI AUTOPAY PAYMENT")
        self.tx("cc", "2026-09-02", 300.0, "AUTOPAY PAYMENT THANK YOU")
        self.tx("cc", "2026-09-03", -12.0, "TST*XIAN FAMOUS FOODS")
        self.tx("cc", "2026-09-04", -40.0, "MYSTERY MERCHANT 123")
        self.conn.execute("INSERT INTO rules(match, category) VALUES ('xian famous', 'Restaurants')")
        db.set_setting(self.conn, "openrouter_api_key", "k")
        seen = {}

        def fake(key, model, prompt):
            seen["prompt"], seen["model"] = prompt, model
            return 'Sure! [{"i": 0, "category": "shopping", "confidence": 0.6}]'

        counts = categorize.categorize(self.conn, None, caller=fake)
        self.assertEqual(counts, {"auto": 3, "rule": 1, "history": 0, "ai": 0, "review": 1})
        self.assertIn("MYSTERY MERCHANT", seen["prompt"])
        self.assertEqual(seen["model"], categorize.DEFAULT_MODEL)
        row = self.conn.execute("SELECT * FROM transactions WHERE description LIKE 'MYSTERY%'").fetchone()
        self.assertEqual((row["category"], row["needs_review"], row["category_source"]), ("Shopping", 1, "ai"))
        cats = dict(self.conn.execute("SELECT description, category FROM transactions").fetchall())
        self.assertEqual(cats["DIRECT DEBIT CITI AUTOPAY PAYMENT"], "Credit Card Payment")
        self.assertEqual(cats["AUTOPAY PAYMENT THANK YOU"], "Credit Card Payment")

    def test_one_question_per_merchant_and_history_reuse(self):
        for d in range(5):
            self.tx("cc", f"2026-09-0{d + 1}", -3.0, "MTA*NYCT PAYGO")
        self.tx("cc", "2026-09-06", 5.0, "MTA*NYCT PAYGO")  # a refund is asked about separately
        db.set_setting(self.conn, "openrouter_api_key", "k")
        asked = []

        def fake(key, model, prompt):
            items = json.loads(prompt.split("Transactions (JSON):\n")[1].split("\n\nReply")[0])
            asked.extend(items)
            return json.dumps([{"i": it["i"], "category": "Public Transit" if it["amount"] < 0 else "Refunds", "confidence": 0.97} for it in items])

        counts = categorize.categorize(self.conn, None, caller=fake)
        self.assertEqual(len(asked), 2)
        self.assertEqual(counts["ai"], 6)
        # Next sync: same merchant is settled from history without asking again.
        self.tx("cc", "2026-09-09", -3.0, "MTA*NYCT PAYGO")
        asked.clear()
        counts = categorize.categorize(self.conn, None, caller=fake)
        self.assertEqual((len(asked), counts["history"]), (0, 1))

    def test_edits_during_slow_ai_call_are_not_blocked_or_overwritten(self):
        self.tx("cc", "2026-09-04", -40.0, "MYSTERY")
        self.conn.commit()
        db.set_setting(self.conn, "openrouter_api_key", "k")
        other = db.connect(self.path)
        other.execute("PRAGMA busy_timeout=1000")  # fail fast if the lock were still held
        edited = {}

        def slow(key, model, prompt):
            # While "waiting on the model", the user changes the same transaction from the UI.
            categorize.set_category(other, "cc|0", "Groceries")
            other.commit()
            edited["ok"] = True
            return '[{"i": 0, "category": "Shopping", "confidence": 0.99}]'

        categorize.categorize(self.conn, None, caller=slow)
        self.conn.commit()
        other.close()
        self.assertTrue(edited.get("ok"))
        row = self.conn.execute("SELECT category, category_source FROM transactions WHERE id='cc|0'").fetchone()
        self.assertEqual(tuple(row), ("Groceries", "manual"))

    def test_review_suggestions_need_confirmation(self):
        for d in range(3):
            self.tx("cc", f"2026-09-0{d + 1}", -9.0, "SQ *BLUE BOTTLE")
        self.tx("cc", "2026-09-05", -30.0, "Z & H GRILL CORP")
        self.conn.execute("UPDATE transactions SET needs_review=1")
        db.set_setting(self.conn, "openrouter_api_key", "k")

        def fake(key, model, prompt):
            items = json.loads(prompt.split("Transactions (JSON):\n")[1].split("\n\nReply")[0])
            return json.dumps([{"i": it["i"], "category": "Coffee & Snacks" if "Blue" in it["payee"] else "Restaurants",
                                "confidence": 0.9} for it in items])

        sug = categorize.suggest_for_review(self.conn, caller=fake)
        self.assertEqual([(s["merchant"], s["count"], s["category"]) for s in sug],
                         [("Blue Bottle", 3, "Coffee & Snacks"), ("Z & H Grill Corp", 1, "Restaurants")])
        # nothing applied yet
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM transactions WHERE needs_review=1").fetchone()[0], 4)
        n = categorize.apply_to_group(self.conn, sug[0]["tx_ids"], "Coffee & Snacks", remember=True)
        self.assertEqual(n, 3)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM transactions WHERE needs_review=1").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT category FROM rules WHERE match='blue bottle'").fetchone()[0], "Coffee & Snacks")

    def test_sync_can_skip_ai(self):
        self.tx("cc", "2026-09-04", -40.0, "MYSTERY")
        db.set_setting(self.conn, "openrouter_api_key", "k")
        db.set_setting(self.conn, "auto_ai_on_sync", "0")
        called = []
        counts = categorize.categorize(self.conn, None, caller=lambda *a: called.append(1) or "[]")
        self.assertEqual((called, counts["review"]), ([], 1))

    def test_ai_failure_goes_to_review(self):
        self.tx("cc", "2026-09-04", -40.0, "MYSTERY")
        db.set_setting(self.conn, "openrouter_api_key", "k")

        def boom(*a):
            raise RuntimeError("OpenRouter HTTP 401: bad key")

        counts = categorize.categorize(self.conn, None, caller=boom)
        self.assertEqual(counts["review"], 1)
        self.assertIn("401", db.get_setting(self.conn, "last_llm_error"))

    def test_bad_category_rejected(self):
        parsed = categorize.parse_ai_reply('[{"i":0,"category":"Snacks","confidence":0.99},{"i":1,"category":"Groceries","confidence":"0.9"}]',
                                           ["Groceries"])
        self.assertEqual(parsed, {0: (None, 0.0), 1: ("Groceries", 0.9)})
        self.assertEqual(categorize.parse_ai_reply("no json here", ["Groceries"]), {})

    def test_remember_creates_rule_and_propagates(self):
        self.tx("cc", "2026-09-03", -12.0, "SQ *BLUE BOTTLE 123")
        self.tx("cc", "2026-09-10", -9.0, "SQ *BLUE BOTTLE 456")
        self.conn.execute("UPDATE transactions SET needs_review=1")
        n = categorize.set_category(self.conn, "cc|0", "Coffee & Snacks", remember=True)
        self.assertEqual(n, 1)
        self.assertEqual(self.conn.execute("SELECT match FROM rules").fetchone()[0], "blue bottle")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM transactions WHERE needs_review=1").fetchone()[0], 0)
        with self.assertRaises(ValueError):
            categorize.set_category(self.conn, "cc|0", "Nope")


class CategoryTests(Base):
    def setUp(self):
        super().setUp()
        self.acct("cc", "credit", -10.0)
        self.tx("cc", "2026-09-01", -12.0, "CHIPOTLE", "Restaurants")
        self.conn.execute("INSERT INTO rules(match, category) VALUES ('chipotle', 'Restaurants')")
        self.conn.execute("INSERT INTO budgets(category, amount) VALUES ('Restaurants', 300)")

    def test_add_sub_rename_remove(self):
        categories.add(self.conn, "Fast food", parent="Restaurants")
        sub = self.conn.execute("SELECT * FROM categories WHERE name='Fast food'").fetchone()
        self.assertEqual((sub["parent"], sub["is_transfer"], sub["is_income"]), ("Restaurants", 0, 0))
        with self.assertRaises(categories.CategoryError):
            categories.add(self.conn, "Burgers", parent="Fast food")  # one level only
        with self.assertRaises(categories.CategoryError):
            categories.add(self.conn, "restaurants")  # duplicate, any case
        tree = [c["name"] for c in categories.all_categories(self.conn)]
        self.assertEqual(tree[tree.index("Restaurants") + 1], "Fast food")
        # rename carries transactions, rules, budgets and children along
        categories.rename(self.conn, "Restaurants", "Dining")
        self.assertEqual(self.conn.execute("SELECT category FROM transactions").fetchone()[0], "Dining")
        self.assertEqual(self.conn.execute("SELECT category FROM rules").fetchone()[0], "Dining")
        self.assertEqual(self.conn.execute("SELECT category FROM budgets").fetchone()[0], "Dining")
        self.assertEqual(self.conn.execute("SELECT parent FROM categories WHERE name='Fast food'").fetchone()[0], "Dining")
        # can't remove a parent that still has subcategories, or a built-in
        with self.assertRaises(categories.CategoryError):
            categories.remove(self.conn, "Dining")
        with self.assertRaises(categories.CategoryError):
            categories.remove(self.conn, "Credit Card Payment")
        categories.remove(self.conn, "Fast food")
        moved = categories.remove(self.conn, "Dining", move_to="Other")
        self.assertEqual(moved, 1)
        self.assertEqual(self.conn.execute("SELECT category FROM transactions").fetchone()[0], "Other")
        self.assertEqual(self.conn.execute("SELECT category FROM rules").fetchone()[0], "Other")
        self.assertIsNone(self.conn.execute("SELECT 1 FROM budgets").fetchone())

    def test_move_rollups_and_flatten(self):
        categories.add(self.conn, "Food")
        categories.move(self.conn, "Restaurants", "Food")
        categories.add(self.conn, "Groceries & more", parent="Food")
        with self.assertRaises(categories.CategoryError):
            categories.add(self.conn, "Burgers", parent="Restaurants")      # no sub-subcategories
        with self.assertRaises(categories.CategoryError):
            categories.move(self.conn, "Food", "Other")                     # Food has subcategories
        with self.assertRaises(categories.CategoryError):
            categories.move(self.conn, "Other", "Restaurants")              # under a subcategory
        tree = {c["name"]: c for c in categories.all_categories(self.conn)}
        self.assertEqual((tree["Restaurants"]["path"], tree["Restaurants"]["depth"], tree["Restaurants"]["top"]), (["Food", "Restaurants"], 1, "Food"))
        self.assertEqual(categories.top_level(self.conn, "Restaurants"), "Food")
        # budget and reports roll subcategories into the parent
        self.tx("cc", "2026-09-02", -8.0, "FIVE GUYS", "Groceries & more")
        self.conn.commit()
        from runway import server
        b = {c["name"]: c for c in server.api_budget(self.conn, {"month": ["2026-09"]}, None)["categories"]}
        self.assertEqual((b["Food"]["spent"], b["Restaurants"]["spent"]), (20.0, 12.0))
        cf = server.api_cashflow(self.conn, {"month": ["2026-09"]}, None)
        food = next(n for n in cf["spending"] if n["name"] == "Food")
        self.assertEqual((food["value"], sorted(k["name"] for k in food["children"])), (20.0, ["Groceries & more", "Restaurants"]))
        self.assertEqual(len(server.api_transactions(self.conn, {"category": ["Food"]}, None)["items"]), 2)
        # anything nested deeper by an earlier version moves up under its top-level category
        self.conn.execute("INSERT INTO categories(name, is_transfer, is_income, parent) VALUES ('Burgers', 0, 0, 'Restaurants')")
        self.assertEqual(categories.flatten(self.conn), 1)
        self.assertEqual(self.conn.execute("SELECT parent FROM categories WHERE name='Burgers'").fetchone()[0], "Food")
        categories.move(self.conn, "Burgers", None)
        categories.move(self.conn, "Burgers", "Income")
        self.assertEqual(self.conn.execute("SELECT is_income FROM categories WHERE name='Burgers'").fetchone()[0], 1)
        with self.assertRaises(categories.CategoryError):
            categories.move(self.conn, "Transfer", "Food")

    def test_spent_drilldown_matches_budget(self):
        from runway import server
        categories.add(self.conn, "Food")
        categories.move(self.conn, "Restaurants", "Food")
        self.acct("loan", "loan", -5000.0)
        self.tx("cc", "2026-09-05", -30.0, "SUSHI", "Restaurants")
        self.tx("cc", "2026-09-06", 5.0, "SUSHI REFUND", "Restaurants")      # refunds count against spending
        self.tx("cc", "2026-08-30", -99.0, "LAST MONTH", "Restaurants")       # other month
        self.tx("loan", "2026-09-07", -40.0, "ODD LOAN ITEM", "Food")         # not an account the budget counts
        self.conn.commit()
        spent = {c["name"]: c["spent"] for c in server.api_budget(self.conn, {"month": ["2026-09"]}, None)["categories"]}
        items = server.api_transactions(self.conn, {"category": ["Food"], "month": ["2026-09"], "scope": ["budget"]}, None)["items"]
        self.assertEqual(round(-sum(t["amount"] for t in items), 2), spent["Food"])
        self.assertEqual(sorted(t["description"] for t in items), ["CHIPOTLE", "SUSHI", "SUSHI REFUND"])

    def test_remove_without_target_sends_to_review(self):
        categories.remove(self.conn, "Restaurants")
        row = self.conn.execute("SELECT category, needs_review FROM transactions").fetchone()
        self.assertEqual(tuple(row), (None, 1))
        self.assertIsNone(self.conn.execute("SELECT 1 FROM rules").fetchone())


class SplitTests(Base):
    """One transaction spread across categories: the parts, not the transaction, are what gets counted."""

    def setUp(self):
        super().setUp()
        self.acct("cc", "credit", -100.0)
        self.tx("cc", "2026-09-10", -100.0, "TARGET", "Shopping")
        self.tx_id = self.conn.execute("SELECT id FROM transactions").fetchone()[0]

    def split(self, *parts):
        return splits.set_splits(self.conn, self.tx_id, [{"amount": a, "category": c} for a, c in parts])

    def test_parts_must_add_up_to_the_transaction(self):
        with self.assertRaises(splits.SplitError):
            self.split((-60.0, "Groceries"), (-30.0, "Shopping"))
        with self.assertRaises(splits.SplitError):
            self.split((-100.0, "Groceries"))                       # a split needs two parts
        with self.assertRaises(splits.SplitError):
            self.split((-60.0, "Groceries"), (-40.0, "Nonsense"))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM tx_splits").fetchone()[0], 0)

    def test_budget_and_reports_count_the_parts(self):
        self.conn.execute("INSERT INTO budgets(category, amount) VALUES ('Groceries', 500)")
        self.split((-60.0, "Groceries"), (-40.0, "Shopping"))
        self.conn.commit()
        spent = {c["name"]: c["spent"] for c in server.api_budget(self.conn, {"month": ["2026-09"]}, None)["categories"]}
        self.assertEqual((spent["Groceries"], spent["Shopping"]), (60.0, 40.0))
        self.assertEqual(forecast.budget_plan(self.conn, date(2026, 9, 20))[0]["spent"], 60.0)
        cf = server.api_cashflow(self.conn, {"month": ["2026-09"]}, None)
        self.assertEqual({n["name"]: n["value"] for n in cf["spending"]}, {"Groceries": 60.0, "Shopping": 40.0})

    def test_the_list_shows_and_filters_by_the_parts(self):
        self.split((-60.0, "Groceries"), (-40.0, "Shopping"))
        self.conn.commit()
        item = server.api_transactions(self.conn, {}, None)["items"][0]
        self.assertEqual(item["is_split"], 1)
        self.assertEqual([(s["category"], s["amount"]) for s in item["splits"]], [("Groceries", -60.0), ("Shopping", -40.0)])
        for cat in ("Groceries", "Shopping"):
            self.assertEqual(len(server.api_transactions(self.conn, {"category": [cat]}, None)["items"]), 1)
        self.assertEqual(server.api_transactions(self.conn, {"category": ["Travel"]}, None)["items"], [])

    def test_categorizing_a_split_transaction_puts_it_back_together(self):
        self.split((-60.0, "Groceries"), (-40.0, "Shopping"))
        categorize.set_category(self.conn, self.tx_id, "Travel")
        row = self.conn.execute("SELECT category, is_split FROM transactions").fetchone()
        self.assertEqual((row["category"], row["is_split"]), ("Travel", 0))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM tx_splits").fetchone()[0], 0)

    def test_rules_and_review_leave_a_split_alone(self):
        self.split((-60.0, "Groceries"), (-40.0, "Shopping"))
        self.conn.execute("UPDATE transactions SET category=NULL, category_source=NULL")
        server.api_rule_add(self.conn, None, {"match": "target", "category": "Travel", "apply": True})
        categorize.categorize(self.conn, use_ai=False)
        row = self.conn.execute("SELECT category, is_split, needs_review FROM transactions").fetchone()
        self.assertEqual((row["category"], row["is_split"], row["needs_review"]), (None, 1, 0))

    def test_renaming_and_removing_a_category_follow_the_parts(self):
        self.split((-60.0, "Groceries"), (-40.0, "Shopping"))
        categories.rename(self.conn, "Groceries", "Food shopping")
        self.assertEqual([s["category"] for s in splits.get(self.conn, self.tx_id)], ["Food shopping", "Shopping"])
        categories.remove(self.conn, "Food shopping")   # no replacement: the whole split goes back to Review
        row = self.conn.execute("SELECT category, is_split, needs_review FROM transactions").fetchone()
        self.assertEqual((row["category"], row["is_split"], row["needs_review"]), (None, 0, 1))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM tx_splits").fetchone()[0], 0)

    def test_a_split_pending_transaction_keeps_its_parts_when_it_posts(self):
        self.split((-60.0, "Groceries"), (-40.0, "Shopping"))
        self.tx("cc", "2026-09-11", -100.0, "TARGET")
        posted = self.conn.execute("SELECT id FROM transactions WHERE id<>?", (self.tx_id,)).fetchone()[0]
        splits.carry_over(self.conn, self.tx_id, posted, -100.0)
        self.assertEqual(len(splits.get(self.conn, posted)), 2)
        self.assertEqual(self.conn.execute("SELECT is_split FROM transactions WHERE id=?", (posted,)).fetchone()[0], 1)
        # a different amount means the parts no longer describe it, so they go
        self.split((-60.0, "Groceries"), (-40.0, "Shopping"))
        splits.carry_over(self.conn, self.tx_id, posted, -120.0)
        self.assertEqual(splits.get(self.conn, self.tx_id), [])

    def test_parts_of_a_deleted_transaction_are_pruned(self):
        self.split((-60.0, "Groceries"), (-40.0, "Shopping"))
        self.conn.execute("DELETE FROM transactions WHERE id=?", (self.tx_id,))
        splits.prune(self.conn)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM tx_splits").fetchone()[0], 0)


class SimpleFinStoreTests(Base):
    def payload(self, txs, balance="-123.45"):
        return {"errors": [], "accounts": [{
            "org": {"name": "Chase"}, "id": "A1", "name": "Sapphire Reserve", "currency": "USD",
            "balance": balance, "available-balance": "0", "balance-date": ts(TODAY), "transactions": txs}]}

    def test_store_and_pending_carryover(self):
        p1 = self.payload([
            {"id": "t1", "posted": ts(date(2026, 9, 20)), "amount": "-10.00", "description": "SQ *CAFE"},
            {"id": "p1", "posted": 0, "transacted_at": ts(date(2026, 9, 22)), "amount": "-5.00", "description": "UBER *TRIP", "pending": True},
        ])
        new = simplefin.store_payload(self.conn, p1, date(2026, 9, 1))
        self.assertEqual(set(new), {"A1|t1", "A1|p1"})
        a = self.conn.execute("SELECT * FROM accounts").fetchone()
        self.assertEqual((a["kind"], a["balance"], a["org"]), ("credit", -123.45, "Chase"))
        self.conn.execute("UPDATE transactions SET category='Rideshare & Taxi' WHERE id='A1|p1'")
        # Pending posts with a new id: category carries over, not reported as new.
        p2 = self.payload([
            {"id": "t1", "posted": ts(date(2026, 9, 20)), "amount": "-10.00", "description": "SQ *CAFE"},
            {"id": "t2", "posted": ts(date(2026, 9, 23)), "amount": "-5.00", "description": "UBER *TRIP"},
        ])
        new2 = simplefin.store_payload(self.conn, p2, date(2026, 9, 1))
        self.assertEqual(new2, [])
        self.assertEqual(self.conn.execute("SELECT category FROM transactions WHERE id='A1|t2'").fetchone()[0], "Rideshare & Taxi")
        self.assertIsNone(self.conn.execute("SELECT 1 FROM transactions WHERE id='A1|p1'").fetchone())
        p3 = self.payload([{"id": "p9", "posted": 0, "transacted_at": ts(date(2026, 9, 23)), "amount": "-7.00",
                            "description": "LYFT *RIDE", "pending": True}])
        simplefin.store_payload(self.conn, p3, date(2026, 9, 1))
        self.conn.execute("UPDATE transactions SET category='Rideshare & Taxi' WHERE id='A1|p9'")
        p4 = self.payload([{"id": "p10", "posted": 0, "transacted_at": ts(date(2026, 9, 23)), "amount": "-7.00",
                            "description": "LYFT *RIDE", "pending": True}])
        self.assertEqual(simplefin.store_payload(self.conn, p4, date(2026, 9, 1)), [])
        self.assertEqual(self.conn.execute("SELECT category FROM transactions WHERE id='A1|p10'").fetchone()[0], "Rideshare & Taxi")

    def test_sync_chunks_backfill(self):
        calls = []

        def fake_fetch(url, start, end):
            calls.append((start, end))
            return self.payload([{"id": f"t{len(calls)}", "posted": ts(start), "amount": "-1", "description": "X"}])

        r = simplefin.sync(self.conn, "https://u:p@h/simplefin", today=TODAY, fetch=fake_fetch)
        self.assertTrue(r["backfill"])
        self.assertEqual(calls[0][0], TODAY - timedelta(days=simplefin.BACKFILL_DAYS))
        self.assertEqual(calls[-1][1], TODAY)
        for s, e in calls:
            self.assertLessEqual((e - s).days + 1, simplefin.CHUNK_DAYS)
        for (s1, e1), (s2, _) in zip(calls, calls[1:]):
            self.assertEqual(s2, e1 + timedelta(days=1))
        calls.clear()
        r = simplefin.sync(self.conn, "https://u:p@h/simplefin", today=TODAY, fetch=fake_fetch)
        self.assertFalse(r["backfill"])
        self.assertEqual(calls, [(TODAY - timedelta(days=simplefin.REFRESH_DAYS), TODAY)])

    def test_kind_guess(self):
        self.assertEqual(simplefin.guess_kind("Venture X"), "credit")
        self.assertEqual(simplefin.guess_kind("Mortgage 1588"), "loan")
        self.assertEqual(simplefin.guess_kind("Fidelity Joint"), "checking")


class MockBridge(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        import base64
        auth = self.headers.get("Authorization", "")
        MockBridge.user_agent = self.headers.get("User-Agent")
        if auth != "Basic " + base64.b64encode(b"user:p@ss").decode():
            body = b"<html><body>Forbidden: bad credentials</body></html>"
            self.send_response(403); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
        MockBridge.last_path = self.path
        body = json.dumps({"errors": [], "accounts": []}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)


class SimpleFinHttpTests(unittest.TestCase):
    def test_fetch_with_basic_auth(self):
        srv = HTTPServer(("127.0.0.1", 0), MockBridge)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            url = f"http://user:p%40ss@127.0.0.1:{srv.server_port}/simplefin"
            out = simplefin.fetch_accounts(url, date(2026, 9, 1), date(2026, 9, 23))
            self.assertEqual(out["accounts"], [])
            self.assertTrue(MockBridge.last_path.startswith("/simplefin/accounts?start-date="))
            self.assertIn("pending=1", MockBridge.last_path)
            self.assertTrue(MockBridge.user_agent.startswith("Runway/"))
            with self.assertRaises(simplefin.SimpleFinError) as cm:
                simplefin.fetch_accounts(f"http://user:wrong@127.0.0.1:{srv.server_port}/simplefin", date(2026, 9, 1))
            self.assertIn('server said: "Forbidden: bad credentials"', str(cm.exception))
        finally:
            srv.shutdown()

    def test_bad_setup_token(self):
        with self.assertRaises(simplefin.SimpleFinError):
            simplefin.claim_setup_token("not a token!!")


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["RUNWAY_DATA"] = cls.tmp.name
        from runway import server
        db.init()
        cls.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.tmp.cleanup()
        os.environ.pop("RUNWAY_DATA", None)

    def test_sync_schedule(self):
        from runway import server
        at = lambda s: datetime.fromisoformat(s)
        self.assertTrue(server.daily_due(None))
        self.assertFalse(server.daily_due("2026-09-25T06:30:00", at("2026-09-25T23:00")))   # already synced today
        self.assertFalse(server.daily_due("2026-09-24T22:00:00", at("2026-09-25T05:00")))   # a new day, but before 6am
        self.assertTrue(server.daily_due("2026-09-24T22:00:00", at("2026-09-25T06:15")))
        self.assertTrue(server.daily_due("2026-09-24T04:00:00", at("2026-09-25T05:00")))    # over 24 hours

    def test_sync_on_visit_needs_a_connection(self):
        self.assertEqual(self.req("POST", "/api/sync/auto"), (200, {"started": False}))

    def req(self, method, path, body=None, headers=None):
        h = {"X-Runway": "1", "Content-Type": "application/json", **(headers or {})}
        r = urllib.request.Request(self.base + path, method=method, headers=h,
                                   data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_state_and_static(self):
        code, st = self.req("GET", "/api/state")
        self.assertEqual(code, 200)
        self.assertFalse(st["connected"])
        with urllib.request.urlopen(self.base + "/") as resp:
            self.assertIn(b"Runway", resp.read())
        with urllib.request.urlopen(self.base + "/app.js") as resp:
            self.assertEqual(resp.headers["Content-Type"].split(";")[0], "text/javascript")

    def test_csrf_header_required(self):
        code, _ = self.req("POST", "/api/settings", {"horizon_days": 60}, headers={"X-Runway": "0"})
        self.assertEqual(code, 403)

    def test_foreign_host_rejected(self):
        r = urllib.request.Request(self.base + "/api/state", headers={"Host": "evil.example"})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(r)
        self.assertEqual(cm.exception.code, 403)

    def test_settings_and_overview(self):
        self.assertEqual(self.req("POST", "/api/settings", {"openrouter_api_key": "sk-or-x", "llm_model": "openai/gpt-4o-mini"})[0], 200)
        _, st = self.req("GET", "/api/state")
        self.assertTrue(st["has_api_key"])
        self.assertEqual(st["llm_model"], "openai/gpt-4o-mini")
        self.assertNotIn("sk-or-x", json.dumps(st))  # key never sent back to the page
        code, fc = self.req("GET", "/api/overview?days=30")
        self.assertEqual(code, 200)
        self.assertEqual(len(fc["dates"]), 31)

    def test_budget_and_rules_api(self):
        with db.session() as c:
            c.execute("INSERT INTO accounts(id, name, kind, balance) VALUES ('b1','Card','credit',-10) ON CONFLICT(id) DO NOTHING")
            today = date.today()
            c.execute("INSERT INTO transactions(id, account_id, posted, amount, description, payee, category) VALUES "
                      "('b1|1','b1',?, -80,'WHOLE FOODS','Whole Foods','Groceries'), ('b1|2','b1',?, -45,'SHELL','Shell','Auto & Gas'),"
                      "('b1|3','b1',?, 900,'PAYMENT','Payment','Credit Card Payment')",
                      (today.isoformat(), today.isoformat(), today.isoformat()))
        self.assertEqual(self.req("POST", "/api/budget", {"category": "Groceries", "amount": 500})[0], 200)
        self.assertEqual(self.req("POST", "/api/budget", {"category": "Income", "amount": 5})[0], 400)
        _, b = self.req("GET", "/api/budget")
        g = next(c for c in b["categories"] if c["name"] == "Groceries")
        self.assertEqual((g["budget"], g["spent"], g["left"]), (500.0, 80.0, 420.0))
        gas = next(c for c in b["categories"] if c["name"] == "Auto & Gas")
        self.assertIsNone(gas["budget"])
        self.assertFalse(any(c["name"] == "Credit Card Payment" for c in b["categories"]))
        # rules: add, edit, apply
        self.req("POST", "/api/rules", {"match": "whole", "category": "Shopping"})
        _, rules = self.req("GET", "/api/rules")
        rid = next(r["id"] for r in rules if r["match"] == "whole")
        self.assertEqual(self.req("POST", f"/api/rules/{rid}", {"match": "whole foods", "category": "Shopping"})[0], 200)
        _, res = self.req("POST", f"/api/rules/{rid}/apply", {})
        self.assertEqual(res["updated"], 1)
        _, res = self.req("POST", f"/api/rules/{rid}/apply", {})
        self.assertEqual(res["updated"], 0)   # counts only what changed

    def test_subcategory_rollup_and_cashflow(self):
        today = date.today()
        d = today.isoformat()
        self.assertEqual(self.req("POST", "/api/categories", {"name": "Fast food", "parent": "Restaurants"})[0], 200)
        with db.session() as c:
            c.execute("INSERT INTO accounts(id, name, kind, balance) VALUES ('cf','Checking','checking',100) ON CONFLICT(id) DO NOTHING")
            c.execute("INSERT INTO transactions(id, account_id, posted, amount, description, payee, category) VALUES "
                      "('cf|1','cf',?, -20,'SHAKE SHACK','Shake Shack','Fast food'),"
                      "('cf|2','cf',?, -50,'NICE PLACE','Nice Place','Restaurants'),"
                      "('cf|3','cf',?, 3000,'PAYROLL','Payroll','Income'),"
                      "('cf|4','cf',?, -700,'CARD PAYMENT','Card Payment','Credit Card Payment'),"
                      "('cf|5','cf',?, -30,'MYSTERY','Mystery',NULL)", (d, d, d, d, d))
        self.req("POST", "/api/budget", {"category": "Restaurants", "amount": 100})
        _, b = self.req("GET", "/api/budget")
        r = next(c for c in b["categories"] if c["name"] == "Restaurants")
        ff = next(c for c in b["categories"] if c["name"] == "Fast food")
        self.assertEqual((r["spent"], r["own_spent"], r["has_children"]), (70.0, 50.0, True))
        self.assertEqual((ff["spent"], ff["parent"]), (20.0, "Restaurants"))
        _, cf = self.req("GET", "/api/cashflow")
        rest = next(n for n in cf["spending"] if n["name"] == "Restaurants")
        self.assertEqual(rest["value"], 70.0)
        self.assertEqual(sorted((k["name"], k["value"]) for k in rest["children"]), [("Fast food", 20.0), ("Restaurants (general)", 50.0)])
        self.assertIn({"name": "Uncategorized", "value": 30.0, "children": []}, cf["spending"])
        self.assertFalse(any(n["name"] == "Credit Card Payment" for n in cf["spending"]))  # transfers never count
        self.assertEqual(next(n for n in cf["income"] if n["name"] == "Income")["value"], 3000.0)
        # removing via the API
        code, res = self.req("POST", "/api/categories/remove", {"name": "Fast food", "move_to": "Restaurants"})
        self.assertEqual((code, res["moved"]), (200, 1))
        self.assertEqual(self.req("POST", "/api/categories/remove", {"name": "Transfer"})[0], 400)

    def test_recurring_validation(self):
        code, err = self.req("POST", "/api/recurring", {"name": "x", "account_id": "nope", "amount": "1", "anchor_date": "2026-09-01"})
        self.assertEqual(code, 400)
        self.assertIn("error", err)


if __name__ == "__main__":
    unittest.main(verbosity=1)


class ScheduleAndMissedTests(Base):
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


class OwnerTests(unittest.TestCase):
    def test_first_names(self):
        from runway import oidc
        self.assertEqual(oidc.first_name("Anthony Pluth", "a@x.com"), "Anthony")
        self.assertEqual(oidc.first_name(None, "sara.smith@x.com"), "Sara")
        self.assertEqual(oidc.first_name("sara@x.com", "sara@x.com", "Sara Jane"), "Sara")


class ForecastEdgeTests(Base):
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


class CategorizeFixTests(Base):
    def setUp(self):
        super().setUp()
        self.acct("chk", "checking", 1000.0)

    def test_autopay_needs_a_card(self):
        cat = lambda d: categorize.heuristic_category({"description": d, "amount": -100}, "checking")
        for bill in ("COMCAST XFINITY AUTOPAY", "STATE FARM AUTOPAY", "CITY WATER EPAY", "CAPITAL ONE AUTO FINANCE PMT",
                     "CHASE MORTGAGE AUTOPAY"):
            self.assertIsNone(cat(bill), bill)
        for card in ("CHASE CREDIT CRD AUTOPAY", "CAPITAL ONE MOBILE PMT", "AMEX EPAYMENT ACH PMT", "DISCOVER E-PAYMENT",
                     "CITI AUTOPAY PAYMENT", "BARCLAYCARD US AUTOPAY", "APPLECARD GSBANK PAYMENT"):
            self.assertEqual(cat(card), "Credit Card Payment", card)

    def test_your_rules_beat_the_built_in_guess(self):
        from runway import rules
        rules.save(self.conn, {"match": "chase credit", "category": "Transfer"})
        self.tx("chk", "2026-09-10", -300.0, "CHASE CREDIT CRD AUTOPAY")
        categorize.categorize(self.conn, use_ai=False)
        row = self.conn.execute("SELECT category, category_source FROM transactions").fetchone()
        self.assertEqual((row["category"], row["category_source"]), ("Transfer", "rule"))

    def test_a_split_that_cant_be_made_goes_to_review_and_the_sync_goes_on(self):
        import json
        self.conn.execute("INSERT INTO rules(match, split) VALUES ('costco', ?)",
                          (json.dumps([{"category": "Groceries", "percent": 60}, {"category": "Gone", "percent": 40}]),))
        self.tx("chk", "2026-09-10", -250.0, "COSTCO WHSE")
        self.tx("chk", "2026-09-11", -20.0, "COFFEE")
        categorize.categorize(self.conn, use_ai=False)
        row = self.conn.execute("SELECT is_split, needs_review FROM transactions WHERE description='COSTCO WHSE'").fetchone()
        self.assertEqual((row["is_split"], row["needs_review"]), (0, 1))

    def test_renaming_or_removing_a_category_follows_order_items(self):
        self.conn.execute("INSERT INTO retail_orders(id, retailer, order_number) VALUES ('amazon:1','amazon','1')")
        self.conn.execute("INSERT INTO retail_items(order_id, title, amount, category, category_source) "
                          "VALUES ('amazon:1','Oats',5,'Groceries','manual')")
        self.conn.execute("INSERT INTO retail_item_memory(key, category) VALUES ('oats','Groceries')")
        categories.rename(self.conn, "Groceries", "Food")
        self.assertEqual(self.conn.execute("SELECT category FROM retail_items").fetchone()[0], "Food")
        self.assertEqual(self.conn.execute("SELECT category FROM retail_item_memory").fetchone()[0], "Food")
        categories.remove(self.conn, "Food")
        self.assertIsNone(self.conn.execute("SELECT category_source FROM retail_items").fetchone()[0])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM retail_item_memory").fetchone()[0], 0)


class SplitAmountChangeTests(Base):
    def setUp(self):
        super().setUp()
        self.acct("cc", "credit", -50.0)
        self.tx("cc", "2026-09-10", -50.0, "BISTRO", "Restaurants")
        self.tx_id = self.conn.execute("SELECT id FROM transactions").fetchone()[0]

    def test_parts_follow_a_new_amount(self):
        splits.set_splits(self.conn, self.tx_id, [{"amount": -30, "category": "Restaurants"}, {"amount": -20, "category": "Gifts & Donations"}])
        self.conn.execute("UPDATE transactions SET amount=-60 WHERE id=?", (self.tx_id,))   # posted with the tip
        splits.follow_amount(self.conn, self.tx_id, -60.0)
        self.assertEqual([p["amount"] for p in splits.get(self.conn, self.tx_id)], [-36.0, -24.0])
        self.assertEqual(self.conn.execute("SELECT needs_review FROM transactions").fetchone()[0], 1)

    def test_odd_cents_still_add_up(self):
        splits.set_splits(self.conn, self.tx_id, [{"amount": -16.67, "category": "Restaurants"},
                                                  {"amount": -16.67, "category": "Groceries"},
                                                  {"amount": -16.66, "category": "Shopping"}])
        splits.follow_amount(self.conn, self.tx_id, -51.01)
        self.assertEqual(round(sum(p["amount"] for p in splits.get(self.conn, self.tx_id)), 2), -51.01)

    def test_simplefin_update_in_place_rescales(self):
        def payload(amount):
            return {"errors": [], "accounts": [{"org": {"name": "Bank"}, "id": "A1", "name": "Card", "currency": "USD",
                                                "balance": amount, "balance-date": ts(TODAY), "transactions": [
                                                    {"id": "t1", "posted": ts(date(2026, 9, 20)), "amount": amount, "description": "BISTRO"}]}]}
        simplefin.store_payload(self.conn, payload("-50.00"), date(2026, 9, 1))
        splits.set_splits(self.conn, "A1|t1", [{"amount": -30, "category": "Restaurants"}, {"amount": -20, "category": "Shopping"}])
        simplefin.store_payload(self.conn, payload("-60.00"), date(2026, 9, 1))   # same id, posted with the tip
        self.assertEqual([p["amount"] for p in splits.get(self.conn, "A1|t1")], [-36.0, -24.0])


class ReportRefundTests(Base):
    def test_refunds_lower_spending_and_uncategorized_money_in_isnt_income(self):
        from runway import reports
        self.acct("chk", "checking", 0.0)
        self.tx("chk", "2026-09-01", 3000.0, "ACME PAYROLL", "Income")
        self.tx("chk", "2026-09-05", -400.0, "STORE", "Shopping")
        self.tx("chk", "2026-09-06", 100.0, "STORE REFUND", "Refunds")
        self.tx("chk", "2026-09-07", 500.0, "ZELLE FROM SAM")                     # not categorized yet
        m = reports.income_vs_spending(self.conn, "2026-09", 2)["months"][-1]
        self.assertEqual((m["income"], m["spending"]), (3000.0, 300.0))
        self.conn.commit()
        cf = server.api_cashflow(self.conn, {"month": ["2026-09"]}, None)
        self.assertEqual(cf["total_in"], 3000.0)


class RecurringAmountTests(Base):
    def test_linking_one_charge_doesnt_link_the_whole_merchant(self):
        self.acct("cc", "credit", 0.0)
        self.tx("cc", "2026-09-01", -14.99, "AMAZON PRIME")
        for d, amt in (("2026-09-03", -86.40), ("2026-09-08", -5.29), ("2026-09-12", -212.00)):
            self.tx("cc", d, amt, "AMAZON")
        self.tx("cc", "2026-08-01", -14.99, "AMAZON")
        self.conn.execute("INSERT INTO recurring(name, account_id, amount, frequency, anchor_date) VALUES ('Prime','cc',-14.99,'monthly','2026-08-01')")
        rid = self.conn.execute("SELECT id FROM recurring").fetchone()[0]
        recurring.link(self.conn, "cc|0", rid)   # learns "amazon prime"...
        self.conn.execute("UPDATE recurring SET match='amazon' WHERE id=?", (rid,))   # ...or plain "amazon"
        recurring.auto_match(self.conn, [rid])
        linked = sorted(r[0] for r in self.conn.execute("SELECT amount FROM transactions WHERE recurring_id=?", (rid,)))
        self.assertEqual(linked, [-14.99, -14.99])
