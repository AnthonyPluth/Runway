"""A golden test for the forecast: a varied, made-up ledger (cards with and without statements, budgets with parts and
rollover, annual fees, transfers, edits under old and new keys) built on several days and horizons, compared with what
the forecast gave for it when the file was written (tests/fixtures/forecast_golden.json). It's there so a refactor of
forecast.build can show it changes nothing (exactly, on SQLite; Postgres's sums can differ in a float's last bits,
so there it's to a millionth); a change meant to change the forecast rewrites the file:

    RUNWAY_WRITE_GOLDEN=1 python -m unittest tests.test_forecast_golden
"""
import json
import os
from datetime import date

from sqlalchemy import insert, update

from runway import db, forecast
from runway import settings_keys as sk
from runway.models import Budget, Category, ChurnCard, Override, Recurring
from tests.shared import TODAY, LedgerCase

GOLDEN = os.path.join(os.path.dirname(__file__), "fixtures", "forecast_golden.json")
RUNS = [(TODAY, 90), (TODAY, 30), (date(2026, 10, 11), 90), (date(2026, 10, 20), 60), (date(2026, 12, 30), 120), (date(2027, 1, 31), 400)]


class GoldenForecastTests(LedgerCase):
    def ledger(self):
        c = self.conn
        # Two checking accounts in the forecast, a savings account that isn't, and five cards.
        self.acct("chk", "checking", 6200.0, in_forecast=1)
        self.acct("chk2", "checking", 1800.0, in_forecast=1)
        self.acct("sav", "savings", 12000.0, in_forecast=0)
        self.acct("cc", "credit", -1400.0, pay_from="chk")                 # the issuer's statement (Plaid)
        self.acct("cc2", "credit", -2300.0, pay_from="chk2")               # a statement you entered, paying the minimum
        self.acct("cc3", "credit", -250.0, pay_from="chk")                 # no statement: month-end cycles
        self.acct("cc4", "credit", -500.0, pay_from="chk")                 # a statement entered long ago (stale)
        self.acct("cc5", "credit", -100.0)                                 # nobody said what pays it
        self.stmt("cc", 1100.0, "2026-09-10", "2026-10-05", minimum=35.0)
        self.manual("cc2", 2000.0, "2026-09-03", "2026-09-28", minimum=50.0)
        self.manual("cc4", 480.0, "2026-07-01", "2026-07-26")
        db.set_setting(c, sk.card_pay_mode("cc2"), "minimum")
        db.set_setting(c, sk.card_apr("cc2"), "22.9")
        db.set_setting(c, sk.card_pay_mode("cc3"), "fixed")
        db.set_setting(c, sk.card_pay_amount("cc3"), "100")
        db.set_setting(c, sk.card_apr("cc3"), "19.99")

        # History: card charges since the closes, payments, a pending charge, a big one-off, and months of groceries for
        # the budget that rolls over.
        self.tx("cc", "2026-09-01", -1100.0, "STUFF", "Shopping")
        self.tx("cc", "2026-09-12", -84.37, "BISTRO", "Restaurants")
        self.tx("cc", "2026-09-19", -212.08, "GREEN GROCER", "Groceries")
        self.tx("cc", "2026-09-15", 300.0, "PAYMENT THANK YOU", "Credit Card Payment")
        self.tx("cc", "2026-08-28", -16.99, "STREAMCO", "Subscriptions")
        self.tx("cc2", "2026-09-05", -61.5, "TAQUERIA", "Restaurants")
        self.tx("cc2", "2026-09-17", -4.75, "BEAN BAR", "Coffee & Snacks")
        self.tx("cc2", "2026-09-20", 25.0, "REFUND", "Refunds")
        self.tx("cc3", "2026-09-09", -45.0, "IRONWORKS GYM", "Entertainment")
        self.tx("chk", "2026-09-22", -38.12, "FUEL STOP", "Auto & Gas", pending=1)
        self.tx("chk", "2026-08-30", -2500.0, "FURNITURE BARN", "Shopping")
        self.tx("chk", "2026-08-21", -118.4, "POWER CO", "Utilities")
        self.tx("chk2", "2026-08-20", -500.0, "SAVINGS XFER", "Transfer")
        for month, amount in (("06", 410.0), ("07", 655.25), ("08", 530.1)):
            self.tx("cc", f"2026-{month}-14", -amount, "GREEN GROCER", "Groceries")

        # Recurring items: pay twice a month, rent, a utility that's late, a transfer, a one-time repair, and charges on
        # two of the cards.
        for values in (
                dict(name="Paycheck", account_id="chk", amount=3200, frequency="semimonthly", dates="15,31",
                     anchor_date="2026-01-01"),
                dict(name="Rent", account_id="chk", amount=-1850, frequency="monthly", anchor_date="2026-01-01"),
                dict(name="Power", account_id="chk", amount=-120, frequency="monthly", anchor_date="2026-08-21",
                     match="power co", amount_mode="average"),
                dict(name="To savings", account_id="chk2", amount=-500, frequency="monthly", anchor_date="2026-01-20",
                     match="savings xfer"),
                dict(name="Car repair", account_id="chk2", amount=-700, frequency="once", anchor_date="2026-10-20"),
                dict(name="Streaming", account_id="cc", amount=-16.99, frequency="monthly", anchor_date="2026-09-28",
                     match="streamco"),
                dict(name="Gym", account_id="cc3", amount=-45, frequency="monthly", anchor_date="2026-10-09",
                     match="ironworks gym"),
                dict(name="Insurance", account_id="chk2", amount=-640, frequency="semiannual", anchor_date="2026-05-31")):
            c.execute(insert(Recurring).values(**values))

        # Budgets: Groceries on cc (rolling over since June), Restaurants on cc2 with Coffee & Snacks (its subcategory)
        # on cc3, fees from checking, Shopping on the stale card, and Utilities from checking.
        c.execute(update(Category).where(Category.name == "Coffee & Snacks").values(parent="Restaurants"))
        for cat, amount, pay_with, rollover in (("Groceries", 600, "cc", "2026-06"), ("Restaurants", 300, "cc2", None),
                                                ("Coffee & Snacks", 80, "cc3", None), ("Fees & Interest", 100, "chk", None),
                                                ("Shopping", 200, "cc4", None), ("Utilities", 150, None, None)):
            c.execute(insert(Budget).values(category=cat, amount=amount, rollover_from=rollover))
            if pay_with:
                c.execute(update(Category).where(Category.name == cat).values(pay_with=pay_with))

        # Churning cards: fees on cc and cc3, one on a card not linked to an account, one you mean to close first.
        for values in (dict(product="Voyager", opened_on="2024-10-14", annual_fee=95.0, account_id="cc"),
                       dict(product="Summit", opened_on="2025-11-02", annual_fee=250.0, account_id="cc3"),
                       dict(product="Harbor", opened_on="2023-12-05", annual_fee=450.0),
                       dict(product="Meadow", opened_on="2025-10-20", annual_fee=550.0, account_id="cc2", plan="close")):
            c.execute(insert(ChurnCard).values(owner="Alex", issuer="chase", **values))

        # Edits: a paycheck, a card payment under the old due-date key, and one under its closing-date key.
        c.execute(insert(Override).values(key="rec:1:2026-10-15", amount=3000.0))
        c.execute(insert(Override).values(key="card:cc:2026-11-05", amount=-900.0))
        c.execute(insert(Override).values(key="cardclose:cc2:2026-10-03", amount=-400.0))

    def test_the_forecast_is_what_it_was(self):
        self.ledger()
        got = {f"{today.isoformat()}+{days}": forecast.build(self.conn, today, days) for today, days in RUNS}
        got = json.loads(json.dumps(got, sort_keys=True))
        if os.environ.get("RUNWAY_WRITE_GOLDEN"):
            with open(GOLDEN, "w") as f:
                json.dump(got, f, indent=1, sort_keys=True, ensure_ascii=False)
                f.write("\n")
        with open(GOLDEN) as f:
            want = json.load(f)
        for run in want:
            with self.subTest(run=run):
                if db.using_postgres():   # its sums can differ from SQLite's in the last bits of a float
                    self.assert_close(got[run], want[run], run)
                else:
                    self.assertEqual(got[run], want[run])
        self.assertEqual(sorted(got), sorted(want))

    def assert_close(self, got, want, where):
        if isinstance(want, float) and isinstance(got, (int, float)):
            self.assertAlmostEqual(got, want, delta=1e-6, msg=where)
        elif isinstance(want, dict):
            self.assertEqual(sorted(got), sorted(want), where)
            for k in want:
                self.assert_close(got[k], want[k], f"{where}.{k}")
        elif isinstance(want, list):
            self.assertEqual(len(got), len(want), where)
            for i, (g, w) in enumerate(zip(got, want, strict=True)):
                self.assert_close(g, w, f"{where}[{i}]")
        else:
            self.assertEqual(got, want, where)
