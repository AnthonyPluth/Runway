"""Card statements entered by hand (runway/statements.py): the forecast uses them as it does Plaid's, Plaid's win, the
staleness rules, Overview's warnings, the card-due notification, and the API that adds and removes them."""
import unittest
from datetime import date, timedelta

from sqlalchemy import delete, insert, update

from runway import db, forecast, notify, statements
from runway import settings_keys as sk
from runway.models import Account, Budget, CardStatement, ManualStatement, Override, Recurring
from runway.server.api import accounts as api
from runway.server.common import ApiError
from tests.shared import TODAY, LedgerCase


class StalenessRuleTests(unittest.TestCase):
    def test_next_close_is_a_month_later_on_the_same_day(self):
        self.assertEqual(statements.next_close(date(2026, 8, 10)), date(2026, 9, 10))
        self.assertEqual(statements.next_close(date(2026, 1, 31)), date(2026, 2, 28))   # a shorter month: its last day
        self.assertEqual(statements.next_close(date(2026, 12, 15)), date(2027, 1, 15))

    def test_stale_a_few_days_after_the_next_close(self):
        close = date(2026, 8, 10)   # the next one closes Sep 10; GRACE_DAYS (5) later, it's late
        self.assertFalse(statements.is_stale(close, date(2026, 9, 10)))
        self.assertFalse(statements.is_stale(close, date(2026, 9, 10) + timedelta(days=statements.GRACE_DAYS)))
        self.assertTrue(statements.is_stale(close, date(2026, 9, 11) + timedelta(days=statements.GRACE_DAYS)))


class ManualStatementForecastTests(LedgerCase):
    def setUp(self):
        super().setUp()
        self.acct("chk", "checking", 5000.0, daily_spend=0)
        self.acct("cc", "credit", -900.0, pay_from="chk")
        self.tx("cc", "2026-09-12", -100.0, "COFFEE", "Restaurants")
        self.tx("cc", "2026-09-20", -200.0, "GROCER", "Groceries")
        self.tx("cc", "2026-09-15", 200.0, "PAYMENT THANK YOU", "Credit Card Payment")
        self.tx("cc", "2026-09-01", -800.0, "STUFF", "Shopping")

    def unplaid(self):
        self.conn.execute(delete(CardStatement))
        self.conn.execute(update(Account).where(Account.id == "cc").values(plaid_account_id=None))

    def subscription(self):
        """A $15 monthly charge on the card from Sep 30, so each estimated statement has something on it."""
        self.conn.execute(insert(Recurring).values(name="Streaming", account_id="cc", amount=-15, frequency="monthly",
                                                   anchor_date="2026-09-30"))

    @staticmethod
    def card_events(fc):
        return [(e["key"], e["date"], e["amount"], e["estimated"]) for e in fc["events"] if e["kind"] == "card"]

    def test_the_same_events_as_the_same_statement_from_plaid(self):
        self.subscription()
        self.stmt("cc", 800.0, "2026-09-10", "2026-10-05", minimum=40.0)
        plaid = forecast.build(self.conn, TODAY, 90)
        self.unplaid()
        self.manual("cc", 800.0, "2026-09-10", "2026-10-05", minimum=40.0)
        mine = forecast.build(self.conn, TODAY, 90)
        self.assertEqual(self.card_events(mine), self.card_events(plaid))
        self.assertGreater(len(self.card_events(mine)), 2)   # the statement's payment, and estimated ones after it
        p, m = plaid["cards"][0], mine["cards"][0]
        self.assertEqual((p["statement_source"], m["statement_source"]), ("plaid", "manual"))
        self.assertEqual({k: v for k, v in m.items() if k != "statement_source"}, {k: v for k, v in p.items() if k != "statement_source"})
        self.assertEqual((m["remaining"], m["due_date"], m["minimum_payment"], m["statement_stale"]), (600.0, "2026-10-05", 40.0, False))
        self.assertEqual(mine["warnings"], plaid["warnings"])
        self.assertEqual(mine["unlinked_cards"], [])

    def test_plaids_statement_wins(self):
        self.stmt("cc", 800.0, "2026-09-10", "2026-10-05")
        self.manual("cc", 999.0, "2026-09-12", "2026-10-09")   # entered too, but Plaid has one
        c = forecast.build(self.conn, TODAY, 30)["cards"][0]
        self.assertEqual((c["statement_source"], c["statement_balance"], c["due_date"]), ("plaid", 800.0, "2026-10-05"))
        self.conn.execute(delete(CardStatement))               # Plaid's gone: the one entered stands in
        c = forecast.build(self.conn, TODAY, 30)["cards"][0]
        self.assertEqual((c["statement_source"], c["statement_balance"], c["due_date"]), ("manual", 999.0, "2026-10-09"))

    def test_the_latest_one_entered_counts_and_future_cycles_follow_its_dates(self):
        self.manual("cc", 500.0, "2026-08-12", "2026-09-07")
        self.manual("cc", 800.0, "2026-09-12", "2026-10-07")
        self.manual("cc", 50.0, "2026-10-12", "2026-11-07")    # can't have closed yet: not used
        self.subscription()
        fc = forecast.build(self.conn, TODAY, 90)
        c = fc["cards"][0]
        self.assertEqual((c["last_close"], c["statement_balance"]), ("2026-09-12", 800.0))
        keys = [k for k, *_ in self.card_events(fc)]
        # closes on the 12th, due on the 7th: each payment is keyed by the statement's closing date
        self.assertEqual(keys[:3], ["cardclose:cc:2026-09-12", "cardclose:cc:2026-10-12", "cardclose:cc:2026-11-12"])

    def test_a_stale_statement_counts_only_up_to_its_due_date(self):
        # Closed Aug 10, due Sep 30. The next one should have closed Sep 10, and five days later it's late.
        self.manual("cc", 800.0, "2026-08-10", "2026-09-30")
        fc = forecast.build(self.conn, date(2026, 9, 15), 90)
        self.assertFalse(fc["cards"][0]["statement_stale"])
        self.assertTrue(any(est for *_, est in self.card_events(fc)))              # fresh: later statements estimated
        self.assertEqual(fc["warnings"], [])
        fc = forecast.build(self.conn, date(2026, 9, 16), 90)
        c = fc["cards"][0]
        self.assertTrue(c["statement_stale"])
        self.assertEqual(self.card_events(fc), [("cardclose:cc:2026-08-10", "2026-09-30", -600.0, False)])   # its own payment (less $200 paid)
        self.assertEqual(fc["warning_links"], [{"text": "Enter cc’s latest statement so its payment stays in the forecast.",
                                                "href": "#setup/accounts?account=cc", "setting": True}])
        fc = forecast.build(self.conn, date(2026, 10, 2), 90)                     # past its due date: nothing at all
        self.assertEqual(self.card_events(fc), [])
        self.assertIn("Enter cc’s latest statement so its payment stays in the forecast.", fc["warnings"])

    def pay(self, mode, amount=None, apr=None):
        db.set_setting(self.conn, sk.card_pay_mode("cc"), mode)
        db.set_setting(self.conn, sk.card_pay_amount("cc"), amount)
        db.set_setting(self.conn, sk.card_apr("cc"), apr)

    def test_paid_and_carried_the_way_plaids_would_be(self):
        # Paying the minimum at a 24% APR, with the Oct 5 payment edited: what carries into the estimated statements
        # after it is the same whether the statement came from Plaid or was entered.
        self.pay("minimum", apr="24")
        self.conn.execute(insert(Override).values(key="cardclose:cc:2026-09-10", amount=-450.0))
        self.stmt("cc", 800.0, "2026-09-10", "2026-10-05", minimum=250.0)
        plaid = forecast.build(self.conn, TODAY, 90)
        self.unplaid()
        self.manual("cc", 800.0, "2026-09-10", "2026-10-05", minimum=250.0)
        mine = forecast.build(self.conn, TODAY, 90)
        self.assertEqual(self.card_events(mine), self.card_events(plaid))
        m = mine["cards"][0]
        self.assertEqual((m["pay_mode"], m["remaining"], m["payment"], m["carried"], m["apr_source"]),
                         ("minimum", 600.0, 450.0, 150.0, "you"))
        self.assertEqual({k: v for k, v in m.items() if k != "statement_source"},
                         {k: v for k, v in plaid["cards"][0].items() if k != "statement_source"})
        self.assertTrue(any(est for *_, est in self.card_events(mine)))

    def test_a_stale_statement_pays_its_plan_and_carries_nothing_forward(self):
        # A fixed $300 toward the $800 statement, $200 of it paid already: $100 goes out, and with the statement stale
        # nothing after it is estimated, so the $500 left isn't carried anywhere.
        self.pay("fixed", amount="300")
        self.manual("cc", 800.0, "2026-08-10", "2026-09-30")
        fc = forecast.build(self.conn, date(2026, 9, 16), 90)
        c = fc["cards"][0]
        self.assertEqual((c["statement_stale"], c["payment"], c["carried"]), (True, 100.0, 500.0))
        self.assertEqual(self.card_events(fc), [("cardclose:cc:2026-08-10", "2026-09-30", -100.0, False)])

    def test_a_stale_card_has_no_estimated_statements_with_budgets_either(self):
        # Groceries is paid with the card, but its statement is stale: nothing is estimated from it, budgets or not.
        self.conn.execute(insert(Budget).values(category="Groceries", amount=500, pay_with="cc"))
        self.manual("cc", 800.0, "2026-07-10", "2026-08-05")
        fc = forecast.build(self.conn, TODAY, 90)
        self.assertEqual(self.card_events(fc), [])
        self.assertIn("Enter cc’s latest statement so its payment stays in the forecast.", fc["warnings"])
        self.assertEqual(len(set(fc["total"])), 1)   # nothing comes out of checking for it
        # so its budget isn't counted as spent, and says why
        self.assertEqual((fc["budget"]["used"], fc["budget"]["skipped"], fc["budget"]["monthly"]),
                         ([], [{"category": "Groceries", "reason": "its card's statement is out of date"}], 0.0))

    def test_the_statement_balance_can_be_corrected(self):
        self.manual("cc", 800.0, "2026-09-10", "2026-10-05")
        self.conn.execute(insert(Override).values(key="stmt:cc:2026-09-10", amount=700.0))
        c = forecast.build(self.conn, TODAY, 30)["cards"][0]
        self.assertEqual((c["statement_balance"], c["statement_reported"], c["statement_set"], c["remaining"]), (700.0, 800.0, True, 500.0))

    def test_card_due_notification(self):
        self.manual("cc", 800.0, "2026-09-10", "2026-10-05")
        p = {**notify.DEFAULTS, "card_due": True, "card_due_days": 14, "low_balance": False, "missed": False, "big_charge": False,
             "sync_failed": False}
        got = {a["key"]: a for a in notify.alerts(self.conn, TODAY, p)}
        self.assertIn("card:cc:2026-10-05", got)
        self.assertEqual(got["card:cc:2026-10-05"]["title"], "cc payment due Oct 5")
        self.assertTrue(got["card:cc:2026-10-05"]["body"].startswith("$600.00 comes out of chk"))


class StatementApiTests(LedgerCase):
    def setUp(self):
        super().setUp()
        self.today = date.today()
        self.acct("cc", "credit", -900.0)
        self.acct("chk", "checking", 100.0)

    def add(self, acct="cc", **body):
        close = self.today - timedelta(days=5)
        base = {"statement_date": close.isoformat(), "due_date": (close + timedelta(days=25)).isoformat(), "balance": "812.40"}
        return api.api_statement_add(self.c, {}, {**base, **body}, acct)

    def refused(self, status=400, **body):
        with self.assertRaises(ApiError) as e:
            self.add(**body)
        self.assertEqual(e.exception.status, status)
        return str(e.exception)

    def test_validation(self):
        self.assertIn("closing date", self.refused(statement_date=""))
        self.assertIn("due date", self.refused(due_date="soon"))
        self.assertIn("hasn't closed yet", self.refused(statement_date=(self.today + timedelta(days=1)).isoformat()))
        self.assertIn("after the closing date", self.refused(due_date=(self.today - timedelta(days=5)).isoformat()))
        self.assertIn("more than 90 days", self.refused(due_date=(self.today + timedelta(days=100)).isoformat()))
        self.assertIn("negative", self.refused(balance=-1))
        self.assertIn("statement balance", self.refused(balance=""))
        self.assertIn("number", self.refused(balance="nan"))
        self.assertIn("minimum payment", self.refused(minimum_payment=-5))
        self.assertIn("credit cards only", self.refused(acct="chk"))
        with self.assertRaises(ApiError) as e:
            self.add(acct="nope")
        self.assertEqual(e.exception.status, 404)
        self.assertEqual(self.c.execute(ManualStatement.__table__.select()).fetchall(), [])

    def test_add_replace_list_and_remove(self):
        r = self.add(minimum_payment="35")
        self.assertEqual((r["statement"]["balance"], r["statement"]["minimum_payment"]), (812.4, 35.0))
        self.add(balance=0)                     # the same closing date again: replaces it (a zero balance is fine)
        card = next(a for a in api.api_accounts(self.c, {}, {}) if a["id"] == "cc")
        self.assertEqual(len(card["statements"]), 1)
        self.assertEqual((card["statement"]["source"], card["statement"]["balance"], card["statement"]["minimum"]), ("manual", 0.0, None))
        self.assertFalse(card["statement"]["stale"])
        self.assertNotIn("statement", next(a for a in api.api_accounts(self.c, {}, {}) if a["id"] == "chk"))
        close = card["statements"][0]["statement_date"]
        self.assertEqual(api.api_statement_remove(self.c, {}, {}, "cc", close), {"ok": True})
        with self.assertRaises(ApiError) as e:
            api.api_statement_remove(self.c, {}, {}, "cc", close)
        self.assertEqual(e.exception.status, 404)
        card = next(a for a in api.api_accounts(self.c, {}, {}) if a["id"] == "cc")
        self.assertEqual((card["statement"], card["statements"]), (None, []))

    def test_plaids_statement_is_reported_as_plaids(self):
        from runway.models import PlaidAccount, PlaidItem
        self.c.execute(insert(PlaidItem).values(item_id="item", access_token="x", institution_name="Chase", products="liabilities"))
        self.c.execute(insert(PlaidAccount).values(plaid_account_id="p-cc", item_id="item", type="credit"))
        closed = (self.today - timedelta(days=3)).isoformat()
        self.stmt("cc", 640.5, closed, (self.today + timedelta(days=22)).isoformat(), minimum=25.0)
        self.add()
        card = next(a for a in api.api_accounts(self.c, {}, {}) if a["id"] == "cc")
        self.assertEqual(card["statement"], {"source": "plaid", "institution": "Chase", "closed": closed,
                                             "due": (self.today + timedelta(days=22)).isoformat(), "balance": 640.5, "minimum": 25.0})
        self.assertEqual(len(card["statements"]), 1)   # what you entered is kept, but not used


if __name__ == "__main__":
    unittest.main()
