"""The pieces forecast.build is made of: a card's statement and billing cycle (bank_statement), its statements cycle by
cycle (statement_cycles, month_end_cycles, simulate_statements), what's paid toward one (paid_toward), the chart's
balances, and moving card payment edits saved under their old keys, which the Overview does and build doesn't."""
import itertools
import unittest
from datetime import date
from unittest import mock

from sqlalchemy import insert, select

from runway.domain import forecast
from runway.storage.models import Account, Override
from runway.server.api import state
from tests.shared import TODAY, LedgerCase

FULL = {"pay_mode": "full", "pay_amount": None, "apr": None}
MINIMUM = {"pay_mode": "minimum", "pay_amount": None, "apr": 24.0}
FIXED = {"pay_mode": "fixed", "pay_amount": 100.0, "apr": 12.0}


def fee(on, amount=-95.0, category="Fees & Interest", name="Voyager annual fee"):
    return {"date": on, "_on": on, "amount": amount, "category": category, "name": name}


def charge(on, amount, name="Streaming", category="Subscriptions"):
    return {"date": on, "amount": amount, "name": name, "category": category, "account_id": "cc", "kind": "recurring"}


class BankStatementTests(LedgerCase):
    def setUp(self):
        super().setUp()
        self.acct("cc", "credit", -900.0)

    def card(self):
        return dict(self.conn.execute(select(Account).where(Account.id == "cc")).fetchone())

    def test_the_billing_cycle_is_returned_and_the_card_left_alone(self):
        self.stmt("cc", 800.0, "2026-09-10", "2026-10-05", minimum=40.0)
        card = self.card()
        before = dict(card)
        st = forecast.bank_statement(self.conn, card, TODAY)
        self.assertEqual((st["closing_day"], st["due_day"], st["source"]), (10, 5, "plaid"))
        self.assertEqual(card, before)

    def test_without_a_due_date_after_the_close_it_is_due_a_while_after(self):
        self.stmt("cc", 800.0, "2026-09-10", None)
        self.assertEqual(forecast.bank_statement(self.conn, self.card(), TODAY)["due_day"],
                         (date(2026, 9, 10) + forecast.timedelta(days=forecast.NO_STATEMENT_DUE_DAYS)).day)
        self.stmt("cc", 800.0, "2026-09-10", "2026-09-10")
        self.assertEqual(forecast.bank_statement(self.conn, self.card(), TODAY)["due_day"], 5)

    def test_none_without_a_statement(self):
        self.assertIsNone(forecast.bank_statement(self.conn, self.card(), TODAY))

    def test_a_statement_you_entered_has_no_issuer_apr_and_only_it_goes_stale(self):
        self.manual("cc", 800.0, "2026-07-10", "2026-08-05")
        info = self.cycle("cc")
        self.assertEqual((info["statement_source"], info["statement_stale"], info["apr"], info["apr_source"]),
                         ("manual", True, None, None))
        self.stmt("cc", 800.0, "2026-07-10", "2026-08-05")
        info = self.cycle("cc")
        self.assertEqual((info["statement_source"], info["statement_stale"]), ("plaid", False))


class CycleTests(unittest.TestCase):
    def test_statement_cycles(self):
        self.assertEqual(list(forecast.statement_cycles(date(2026, 9, 10), 10, 5, date(2026, 12, 31))),
                         [(date(2026, 10, 10), date(2026, 11, 5)), (date(2026, 11, 10), date(2026, 12, 5))])
        self.assertEqual(list(forecast.statement_cycles(date(2026, 1, 31), 31, 28, date(2026, 5, 1))),
                         [(date(2026, 2, 28), date(2026, 3, 28)), (date(2026, 3, 31), date(2026, 4, 28))])
        self.assertEqual(list(forecast.statement_cycles(date(2026, 9, 10), 10, 5, date(2026, 11, 4))), [])

    def test_month_end_cycles(self):
        self.assertEqual(list(forecast.month_end_cycles(date(2026, 9, 23), date(2026, 12, 28))),
                         [(date(2026, 9, 30), date(2026, 10, 25)), (date(2026, 10, 31), date(2026, 11, 25)),
                          (date(2026, 11, 30), date(2026, 12, 25))])
        self.assertEqual(len(list(forecast.month_end_cycles(date(2026, 9, 23), date(2026, 12, 25)))), 2)
        self.assertEqual(list(forecast.month_end_cycles(date(2027, 1, 31), date(2027, 3, 31))),
                         [(date(2027, 1, 31), date(2027, 2, 25)), (date(2027, 2, 28), date(2027, 3, 25))])

    def test_paid_toward(self):
        self.assertEqual(forecast.paid_toward(MINIMUM, 40.0, True, -250.0), 250.0)
        self.assertEqual(forecast.paid_toward(MINIMUM, 40.0, False, -250.0), 40.0)
        self.assertEqual(forecast.paid_toward(MINIMUM, 40.0, True, None), 40.0)
        self.assertEqual(forecast.paid_toward(FULL, 300.0, True, -250.0), 300.0)


class SimulateTests(unittest.TestCase):
    def run_cycles(self, plan, owing, **kw):
        args = {"card_id": "cc", "start": date(2026, 9, 10), "owing": owing, "plan": plan, "today": TODAY, "budgets": {},
                "charges": [], "fees": [], "covers": set(), "spent_on": {}, **kw}
        return forecast.simulate_statements(forecast.statement_cycles(date(2026, 9, 10), 10, 5, date(2026, 12, 31)), **args)

    def test_charges_land_in_their_cycle_and_a_covered_one_is_in_its_budget(self):
        budgets = {"2026-09-30": 10.0, "2026-10-10": 5.0, "2026-10-11": 7.0}
        cycles = self.run_cycles(FULL, 0.0, budgets=budgets, charged_so_far=300.0,
                                 charges=[charge("2026-09-28", -15.0), charge("2026-10-28", -15.0),
                                          charge("2026-10-02", -60.0, "Groceries club", "Groceries")],
                                 fees=[fee("2026-10-14")], covers={"Groceries"}, spent_on={"Groceries": budgets})
        self.assertEqual([(c["close"], c["statement"], c["pay"], c["carried"]) for c in cycles],
                         [(date(2026, 10, 10), 330.0, 330.0, 0.0), (date(2026, 11, 10), 117.0, 117.0, 0.0)])
        self.assertEqual([c["key"] for c in cycles], ["cardclose:cc:2026-10-10", "cardclose:cc:2026-11-10"])
        self.assertEqual([c["old_key"] for c in cycles], ["card:cc:2026-11-05", "card:cc:2026-12-05"])
        self.assertEqual([c["pays"] for c in cycles], [date(2026, 11, 5), date(2026, 12, 7)])
        self.assertEqual(cycles[1]["fees"], [fee("2026-10-14")])
        first = cycles[0]["estimate"]
        self.assertEqual((first["charged_so_far"], first["budgets_total"], first["recurring"]),
                         (300.0, 15.0, [{"name": "Streaming", "amount": 15.0}]))
        self.assertEqual(cycles[1]["estimate"]["fees"], [{"name": "Voyager annual fee", "amount": 95.0}])

    def test_what_isnt_paid_carries_with_interest(self):
        cycles = self.run_cycles(MINIMUM, 1000.0, budgets={"2026-10-01": 100.0}, spent_on={"Dining": {"2026-10-01": 100.0}})
        first, second = cycles
        self.assertAlmostEqual(first["interest"], (1000 + 100 / 2) * 0.24 / 12)
        self.assertAlmostEqual(first["statement"], 1121.0)
        self.assertAlmostEqual(first["pay"], 25.0 if 1121 * 0.01 + 21 < 25 else 1121 * 0.01 + 21)
        self.assertAlmostEqual(second["statement"], first["carried"] + second["interest"])
        self.assertEqual(first["estimate"]["carried"], 1000.0)

    def test_owed_now_is_charges_not_a_carried_balance(self):
        cycles = forecast.simulate_statements(
            forecast.month_end_cycles(TODAY, date(2026, 12, 1)), card_id="cc", start=TODAY, owing=250.0, plan=FIXED,
            today=TODAY, budgets={}, charges=[], fees=[fee("2026-09-23")], covers=set(), spent_on={}, owed_now=True)
        first, second = cycles
        self.assertEqual((first["interest"], first["statement"], first["pay"], first["fees"]), (0.0, 345.0, 100.0, [fee("2026-09-23")]))
        est = first["estimate"]
        self.assertEqual((est["owed_now"], est.get("carried"), est["assumed_cycle"], est["total"]), (250.0, None, True, 100.0))
        self.assertAlmostEqual(second["interest"], 245.0 * 0.12 / 12)
        self.assertEqual(second["estimate"]["carried"], 245.0)

    def test_an_edit_is_whats_paid_and_carries_the_rest(self):
        cycles = self.run_cycles(MINIMUM, 1000.0, edits={"card:cc:2026-11-05": -600.0})
        self.assertEqual(cycles[0]["pay"], 600.0)
        self.assertAlmostEqual(cycles[0]["carried"], cycles[0]["statement"] - 600.0)
        self.assertEqual(cycles[1]["pay"], cycles[1]["planned"])

    def test_a_cycle_paid_before_today_has_no_estimate(self):
        cycles = self.run_cycles(FULL, 0.0, today=date(2026, 11, 20), charged_so_far=50.0, budgets={"2026-11-01": 20.0})
        self.assertEqual(["estimate" in c for c in cycles], [False, True])

    def test_paying_the_estimated_minimum_never_lets_the_balance_grow(self):
        cycles = forecast.simulate_statements(
            forecast.statement_cycles(date(2026, 9, 10), 10, 5, date(2027, 12, 31)), card_id="cc", start=date(2026, 9, 10),
            owing=5000.0, plan={**MINIMUM, "apr": 29.99}, today=TODAY, budgets={}, charges=[], fees=[], covers=set(),
            spent_on={})
        statements = [c["statement"] for c in cycles]
        self.assertGreater(len(statements), 12)
        self.assertTrue(all(b < a for a, b in itertools.pairwise(statements)), statements)


class ChartTests(unittest.TestCase):
    def test_balances_and_the_low(self):
        dates = ["2026-09-23", "2026-09-24", "2026-09-25"]
        events = [{"account_id": "chk", "date": "2026-09-23", "amount": -50.0},
                  {"account_id": "chk", "date": "2026-09-25", "amount": 200.0},
                  {"account_id": "sav", "date": "2026-09-24", "amount": -999.0}]
        series = forecast.balance_series({"id": "chk", "balance": 1000.0}, events, {"2026-09-24": 30.0}, dates)
        self.assertEqual(series, [950.0, 920.0, 1120.0])
        self.assertEqual(forecast.low(series, dates), {"date": "2026-09-24", "balance": 920.0})
        self.assertIsNone(forecast.low([], dates))
        spend = {"chk": {"2026-09-24": 30.004, "2026-09-25": 0.004}, "cc": {"2026-09-24": 10.0}}
        self.assertEqual(forecast.budgeted_out(spend, ["chk", "cc"], dates), {"2026-09-24": 40.0})


class OldKeyTests(LedgerCase):
    def test_the_overview_moves_old_keys_and_build_only_reads(self):
        self.conn.execute(insert(Override).values(key="card:cc:2026-11-05", amount=-77.0))
        fc = {"events": [], "charges": [], "accounts": []}
        with mock.patch.object(state.forecast, "project", return_value=(fc, {"cardclose:cc:2026-10-10": "card:cc:2026-11-05"})):
            state.api_overview(self.conn, {}, None)
        self.assertEqual(list(self.conn.execute(select(Override.key, Override.amount))), [("cardclose:cc:2026-10-10", -77.0)])

    def test_nothing_to_move(self):
        forecast.move_old_keys(self.conn, {})
        self.assertEqual(list(self.conn.execute(select(Override.key))), [])
