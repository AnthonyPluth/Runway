import json
import os
import tempfile
import threading
import unittest
from unittest import mock
from datetime import date, datetime, timedelta, UTC
from http.server import BaseHTTPRequestHandler, HTTPServer

from sqlalchemy import func, insert, select, update

from runway import db, plaid, planner, portfolio, prices
from runway.models import (Account, Asset, Holding, InvAccount, InvTransaction, PlaidItem, Price, PriceMeta, Security,
                           Transaction)
from tests.shared import DbCase

TODAY = date(2026, 9, 23)


class Base(DbCase):
    def setUp(self):
        super().setUp()
        self.c.execute(insert(PlaidItem).values(item_id="it1", access_token="tok", institution_name="Fidelity"))
        self.c.execute(insert(InvAccount).values(id="A", item_id="it1", name="Brokerage", type="investment",
                                                 subtype="brokerage", balance=0))
        self.c.execute(insert(Security), [{"id": "VTI", "ticker": "VTI", "name": "Vanguard Total Stock Market ETF",
                                           "type": "etf", "is_cash": 0},
                                          {"id": "SPAXX", "ticker": "SPAXX",
                                           "name": "Fidelity Government Money Market", "type": "mutual fund",
                                           "is_cash": 1},
                                          {"id": "XYZ", "ticker": "XYZ", "name": "XYZ Corp", "type": "equity",
                                           "is_cash": 0}])

    def tx(self, id, d, type_, sub, amount, sec=None, qty=0, price=None, fees=0):
        self.c.execute(insert(InvTransaction).values(id=id, account_id="A", security_id=sec, date=d, name=id,
                                                     type=type_, subtype=sub, quantity=qty, amount=amount, price=price,
                                                     fees=fees))

    def price(self, ticker, d, close, adj=None):
        db.upsert(self.c, Price, {"ticker": ticker, "date": d, "close": close, "adjclose": adj or close}, key=["ticker", "date"])


class HistoryTests(Base):
    def setUp(self):
        super().setUp()
        # Deposit $3,000, buy 10 VTI at $250, get a $20 dividend. Today: 10 VTI worth $3,000 + $520 cash.
        self.tx("t1", "2026-06-01", "cash", "deposit", -3000)
        self.tx("t2", "2026-06-02", "buy", "buy", 2500, "VTI", 10, 250)
        self.tx("t3", "2026-08-15", "cash", "dividend", -20, "VTI")
        self.c.execute(insert(Holding).values(account_id="A", security_id="VTI", quantity=10, price=300, value=3000,
                                              cost_basis=2500))
        self.c.execute(insert(Holding).values(account_id="A", security_id="SPAXX", quantity=520, price=1, value=520,
                                              cost_basis=520))
        self.price("VTI", "2026-06-01", 250)
        self.price("VTI", "2026-07-01", 275)
        self.price("VTI", "2026-08-01", 300)
        self.price("SPY", "2026-05-29", 500)
        self.price("SPY", "2026-09-22", 550)

    def at(self, h, d):
        return h["value"][h["dates"].index(d)]

    def test_values_rebuilt_from_activity(self):
        h = portfolio.history(self.c, TODAY)
        self.assertEqual(h["dates"][0], "2026-05-31")
        self.assertEqual(self.at(h, "2026-05-31"), 0.0)
        self.assertEqual(self.at(h, "2026-06-01"), 3000.0)            # cash only
        self.assertEqual(self.at(h, "2026-06-02"), 3000.0)            # 10 x 250 + 500 cash
        self.assertEqual(self.at(h, "2026-07-15"), 3250.0)            # 10 x 275 + 500
        self.assertEqual(self.at(h, "2026-08-14"), 3500.0)
        self.assertEqual(self.at(h, "2026-08-15"), 3520.0)            # dividend lands in cash
        self.assertEqual(h["value"][-1], 3520.0)
        self.assertEqual(h["flows"][h["dates"].index("2026-06-01")], 3000.0)
        self.assertEqual(h["invested"][-1], 3000.0)
        # Time-weighted: the deposit isn't a gain. 3000 -> 3520 with no other flows.
        self.assertAlmostEqual(h["twr"][-1], 3520 / 3000 - 1, places=6)

    def test_cash_rows_with_a_share_count_dont_change_shares(self):
        # Some institutions tag a small cash deposit with a security and a share count; it mustn't move shares.
        self.tx("t4", "2026-09-10", "cash", "deposit", -2, "VTI", -8.4)
        h = portfolio.history(self.c, TODAY)
        self.assertEqual(self.at(h, "2026-09-09"), 3520.0 - 2)       # the $2 deposit came in after; shares unchanged

    def test_performance_and_benchmark(self):
        ov = portfolio.overview(self.c, "2Y", TODAY)
        p = ov["performance"]
        self.assertEqual((p["net_deposits"], p["gain"]), (3000.0, 520.0))
        self.assertAlmostEqual(p["return"], 3520 / 3000 - 1, places=5)
        self.assertAlmostEqual(p["benchmark_return"], 550 / 500 - 1, places=5)
        self.assertEqual(ov["total"], 3520.0)
        self.assertEqual(ov["unrealized_gain"], 500.0)
        vti = next(x for x in ov["holdings"] if x["ticker"] == "VTI")
        self.assertAlmostEqual(vti["allocation"], 3000 / 3520, places=5)
        self.assertEqual(ov["income"]["income_12m"], 20.0)
        classes = {a["name"]: a["value"] for a in ov["allocation"]["asset_class"]}
        self.assertEqual(classes, {"ETFs": 3000.0, "Cash": 520.0})

    def test_withdrawal_not_a_loss(self):
        self.tx("t4", "2026-09-01", "cash", "withdrawal", 400)
        self.c.execute(update(Holding).where(Holding.security_id == "SPAXX").values(value=120, quantity=120))
        h = portfolio.history(self.c, TODAY)
        self.assertEqual(self.at(h, "2026-08-31"), 3520.0)
        self.assertEqual(self.at(h, "2026-09-01"), 3120.0)
        self.assertAlmostEqual(h["twr"][-1], 3520 / 3000 - 1, places=6)  # unchanged by taking money out

    def test_accounts_hidden_in_settings_are_excluded_everywhere(self):
        # Settings -> Accounts is the only way to leave an account out of Investments
        self.assertEqual(portfolio.overview(self.c, "1Y", TODAY)["total"], 3520.0)
        self.c.execute(insert(Account).values(id="pl:A", name="Brokerage", kind="investment", balance=3520, hidden=1))
        self.c.execute(update(InvAccount).where(InvAccount.id == "A").values(account_id="pl:A"))
        ov = portfolio.overview(self.c, "1Y", TODAY)
        self.assertEqual(ov["total"], 0)
        self.assertEqual(ov["holdings"], [])
        self.c.execute(update(Account).where(Account.id == "pl:A").values(hidden=0))
        self.assertEqual(portfolio.overview(self.c, "1Y", TODAY)["total"], 3520.0)

    def test_the_old_per_account_flag_no_longer_has_an_endpoint(self):
        from runway.server import routes
        self.assertFalse([r for r in routes.ROUTES if r[1].startswith("/api/plaid/accounts/")])


class TransferHistoryTests(Base):
    """Pins how history treats transfers, corporate actions, prices from trades alone, and balance-only accounts."""

    def test_transfers_trades_and_a_balance_only_account(self):
        self.c.execute(insert(Security).values(id="NOPX", ticker="NOPX", name="No Price Inc", type="equity", is_cash=0))
        self.c.execute(insert(InvAccount).values(id="B", item_id="it1", name="Old 401k", type="investment",
                                                 subtype="401k", balance=2500))
        self.tx("d1", "2026-08-01", "cash", "deposit", -1000)
        self.tx("x1", "2026-08-05", "transfer", "transfer", 0, "XYZ", 5)            # in-kind: 5 XYZ, priced from history
        self.tx("n1", "2026-08-10", "buy", "buy", 400, "NOPX", 4, 100)             # no price history: valued at trades
        self.tx("n2", "2026-09-01", "sell", "sell", -220, "NOPX", -2, 110)
        self.tx("w1", "2026-09-05", "transfer", "transfer", 150)                    # cash sent out
        self.tx("s1", "2026-09-10", "transfer", "spin off", 0, "XYZ", 1)            # corporate action: shares, no flow
        self.tx("f1", "2026-09-30", "cash", "deposit", -50)                         # after today: ignored
        self.c.execute(insert(Holding).values(account_id="A", security_id="XYZ", quantity=6, price=50, value=300))
        self.c.execute(insert(Holding).values(account_id="A", security_id="NOPX", quantity=2, price=110, value=220))
        self.c.execute(insert(Holding).values(account_id="A", security_id="SPAXX", quantity=670, price=1, value=670))
        self.price("XYZ", "2026-08-01", 40)
        self.price("XYZ", "2026-09-01", 45)
        self.price("XYZ", "2026-09-22", 50)
        h = portfolio.history(self.c, TODAY, days=60)
        got = {d: (h["value"][i], h["flows"][i], h["invested"][i], h["twr"][i]) for i, d in enumerate(h["dates"])}
        self.assertEqual(got["2026-07-31"], (2500.0, 0.0, 2500.0, 0.0))            # the 401k's balance, held flat
        self.assertEqual(got["2026-08-01"], (3500.0, 1000.0, 3500.0, 0.0))
        self.assertEqual(got["2026-08-05"], (3700.0, 200.0, 3700.0, 0.0))          # 5 x $40 moved in counts as added
        self.assertEqual(got["2026-08-10"], (3700.0, 0.0, 3700.0, 0.0))
        self.assertEqual(got["2026-09-01"], (3765.0, 0.0, 3700.0, 0.017568))
        self.assertEqual(got["2026-09-05"], (3615.0, -150.0, 3550.0, 0.017568))
        self.assertEqual(got["2026-09-10"], (3660.0, 0.0, 3550.0, 0.030234))
        self.assertEqual(got["2026-09-23"], (3690.0, 0.0, 3550.0, 0.038679))
        self.assertEqual((h["dates"][0], h["missing_prices"], h["estimated_before"]), ("2026-07-31", ["NOPX"], None))


class SplitTests(Base):
    def test_split_does_not_jump(self):
        # 10 XYZ at $200, 2-for-1 split on Jul 1, now 20 at $100. Yahoo's closes are split-adjusted (100 throughout).
        self.tx("s0", "2026-06-10", "buy", "buy", 2000, "XYZ", 10, 200)
        self.tx("s1", "2026-07-01", "transfer", "split", 0, "XYZ", 10)
        self.c.execute(insert(Holding).values(account_id="A", security_id="XYZ", quantity=20, price=100, value=2000,
                                              cost_basis=2000))
        self.c.execute(insert(Holding).values(account_id="A", security_id="SPAXX", quantity=0, price=1, value=0))
        self.tx("s_dep", "2026-06-09", "cash", "deposit", -2000)
        for d in ("2026-06-09", "2026-06-30", "2026-07-01", "2026-09-22"):
            self.price("XYZ", d, 100)
        self.c.execute(insert(PriceMeta).values(ticker="XYZ", fetched_at=datetime.now().isoformat(), ok=1,
                                                splits=json.dumps([["2026-07-01", 2.0]])))
        h = portfolio.history(self.c, TODAY)
        vals = dict(zip(h["dates"], h["value"], strict=True))
        self.assertEqual(vals["2026-06-30"], 2000.0)
        self.assertEqual(vals["2026-07-01"], 2000.0)
        self.assertEqual(h["flows"][h["dates"].index("2026-07-01")], 0.0)
        self.assertAlmostEqual(h["twr"][-1], 0.0, places=6)


class XrayTests(Base):
    def test_rules(self):
        self.c.execute(insert(Holding).values(account_id="A", security_id="XYZ", quantity=10, price=100, value=6000,
                                              cost_basis=4000))
        self.c.execute(insert(Holding).values(account_id="A", security_id="SPAXX", quantity=4000, price=1, value=4000))
        self.tx("f1", "2026-09-01", "fee", "management fee", 80)
        ov = portfolio.overview(self.c, "1Y", TODAY)
        rules = {r["name"]: r for r in ov["xray"]}
        self.assertFalse(rules["Largest single holding"]["ok"])   # 60% in one stock
        self.assertFalse(rules["Uninvested cash"]["ok"])          # 40% cash
        self.assertFalse(rules["Fees paid"]["ok"])                # 0.8%
        self.assertEqual(ov["income"]["fees_12m"], 80.0)


class FireTests(Base):
    """The financial-independence assumptions you type are kept, so the page comes back the way you left it."""

    def setUp(self):
        super().setUp()
        self.c.execute(insert(Holding).values(account_id="A", security_id="VTI", quantity=10, price=300, value=3000,
                                              cost_basis=2500))

    def plan(self):
        return portfolio.overview(self.c, "1Y", TODAY)["plan"]

    def test_plan_starts_from_runways_figures(self):
        db.set_setting(self.c, "fire_annual_spending", "62000")   # changed on the old financial-independence card
        p = self.plan()
        self.assertTrue(p["is_default"])
        self.assertEqual(p["plan"]["spending"], 62000.0)
        self.assertEqual(p["plan"]["people"][0]["savings"], p["computed"]["yearly_savings"])
        self.assertEqual(p["current"], portfolio.overview(self.c, "1Y", TODAY)["history"]["value"][-1])

    def test_a_saved_plan_is_kept_and_can_be_forgotten(self):
        plan = {**self.plan()["plan"], "people": [
            {"name": "Alex", "birth_year": 1985, "retire_age": 60, "savings": 30000},
            {"name": "Sam", "birth_year": 1987, "retire_age": 62, "savings": "12000"}],
            "income": [{"name": "Social Security", "amount": 28000, "person": 1, "start_age": 67}],
            "events": [{"name": "College", "year": TODAY.year + 12, "amount": -80000}]}
        planner.save(self.c, plan, TODAY)
        p = self.plan()
        self.assertFalse(p["is_default"])
        self.assertEqual([x["retire_age"] for x in p["plan"]["people"]], [60, 62])
        self.assertEqual(p["plan"]["people"][1]["savings"], 12000.0)
        self.assertEqual(p["plan"]["income"][0], {"name": "Social Security", "amount": 28000.0, "person": 1, "start_age": 67, "end_age": None})
        planner.save(self.c, None)
        self.assertTrue(self.plan()["is_default"])

    def test_nonsense_plans_are_refused(self):
        good = self.plan()["plan"]
        for bad in ({**good, "people": []}, {**good, "people": good["people"] * 3}, {**good, "volatility": 3},
                    {**good, "spending": "lots"}, {**good, "plan_to_age": 500},
                    {**good, "income": [{"name": "SS", "amount": 1, "person": 1, "start_age": 67}]},   # no second person
                    {**good, "events": [{"name": "x", "year": 1990, "amount": 5}]},
                    {**good, "assets": [{"key": "account:chk", "sell_year": TODAY.year + 1}]},
                    {**good, "events": [{}] * (planner.MAX_ROWS + 1)}):
            with self.assertRaises(planner.PlanError):
                planner.save(self.c, bad, TODAY)
        self.assertIsNone(planner.saved(self.c))

    def test_homes_and_equity_can_be_sold_into_the_plan(self):
        self.c.execute(insert(Account).values(id="mtg", name="Mortgage", kind="loan", balance=-200000))
        self.c.execute(insert(Asset).values(name="House", kind="home", value=450000, as_of=TODAY.isoformat(),
                                            yearly_change=3, loan_account_id="mtg"))
        house = next(a for a in self.plan()["assets"] if a["name"] == "House")
        self.assertEqual((house["value"], house["owed"], house["yearly_change"]), (450000.0, 200000.0, 0.03))
        self.assertEqual(house["loan"], {"rate": None, "payment": None, "source": None, "note": "no_rate", "account_id": "mtg",
                                         "payment_counted": None, "payoff_year": None})   # no terms: kept as it is
        # with its terms set, what's owed is paid down to today (as on Net worth), projected on from there
        self.c.execute(update(Account).where(Account.id == "mtg").values(balance_date="2026-07-23", interest_rate=6, monthly_payment=1500))
        house = next(a for a in self.plan()["assets"] if a["name"] == "House")
        owed = 200000.0
        for _ in range(2):
            owed = owed * 1.005 - 1500
        self.assertAlmostEqual(house["owed"], owed, delta=0.01)
        self.assertEqual(house["owed_by_year"][0], house["owed"])
        self.assertEqual({k: house["loan"][k] for k in ("rate", "payment", "source", "note", "account_id")},
                         {"rate": 6, "payment": 1500, "source": "manual", "note": None, "account_id": "mtg"})
        self.assertEqual(house["loan"]["payoff_year"], 2044)   # 219 more payments from October 2026: the last in December 2044
        # a payment and no rate: nothing is guessed (loans.py), so neither paid down nor projected
        self.c.execute(update(Account).where(Account.id == "mtg").values(interest_rate=None))
        house = next(a for a in self.plan()["assets"] if a["name"] == "House")
        self.assertEqual((house["owed"], house["loan"]["note"], house["loan"]["payoff_year"]), (200000.0, "no_rate", None))

    def test_says_whether_a_loans_payment_is_in_spending(self):
        self.c.execute(insert(Account), [{"id": "mtg", "name": "Mortgage", "kind": "loan", "balance": -200000, "interest_rate": 6,
                                          "monthly_payment": 1850},
                                         {"id": "chk", "name": "Checking", "kind": "checking", "balance": 0, "interest_rate": None,
                                          "monthly_payment": None}])
        self.c.execute(insert(Asset).values(name="House", kind="home", value=450000, as_of=TODAY.isoformat(), loan_account_id="mtg"))
        counted = lambda: next(a for a in self.plan()["assets"] if a["name"] == "House")["loan"]["payment_counted"]
        pay = lambda id, posted, amount, category, desc=None: self.c.execute(insert(Transaction).values(
            id=id, account_id="chk", posted=posted, amount=amount, category=category, description=desc))
        for m in range(3, 9):   # a steady move to savings about the payment's size: not taken for the payment
            pay(f"s{m}", f"2026-{m:02}-20", -2000, "Transfer", "TO SAVINGS")
        self.assertIsNone(counted())
        for m in range(3, 9):   # paid every month, but as a transfer naming it: not in spending
            pay(f"t{m}", f"2026-{m:02}-01", -1850, "Transfer", "MORTGAGE PMT")
        pay("p2", "2026-06-01", -1500, "Mortgage")   # spending, but not the payment
        pay("p3", "2026-09-01", -1850, "Mortgage")   # this month: outside the 6 full months spending counts
        pay("p4", "2026-07-15", -1900, "Groceries")  # within 10% of it once: a one-off, not a payment
        self.assertFalse(counted())
        for m in (3, 4, 5):   # with July, four months of it: a payment that repeats
            pay(f"m{m}", f"2026-{m:02}-02", -1850, "Mortgage")
        self.assertTrue(counted())
        self.c.execute(update(Transaction).where(Transaction.id == "p3").values(category=None))   # this month's still doesn't
        self.c.execute(update(Transaction).where(Transaction.id == "m5").values(category=None))   # uncategorized counts too
        self.assertTrue(counted())
        self.c.execute(update(Transaction).where(Transaction.id == "p4").values(category="Transfer"))   # three months left
        self.assertFalse(counted())
        # found in neither: Runway can't tell, so it's left as it is (None), not added
        self.c.execute(update(Transaction).where(Transaction.id.like("t%")).values(amount=-925, category="Gifts & Donations"))   # in halves
        self.assertIsNone(counted())
        self.c.execute(update(Account).where(Account.id == "mtg").values(monthly_payment=None))   # no payment: nothing to match
        self.assertIsNone(counted())

    def test_a_payment_that_names_the_lender_wins_over_lookalikes(self):
        out = lambda month, amount, text: {"month": f"2026-{month:02}", "amount": amount, "text": text}
        lookalikes = [out(m, 352, "whole foods") for m in (3, 4, 5, 6)]
        self.assertTrue(planner.payment_counted(lookalikes, 350, ["Ally", "Car loan"]))   # nothing names it: amounts alone
        named = [out(m, 350, "ally auto payment") for m in (3, 4)]
        self.assertFalse(planner.payment_counted(lookalikes + named, 350, ["Ally", "Car loan"]))   # only two months of it
        named += [out(m, 350, "ally auto payment") for m in (5, 6)]
        self.assertTrue(planner.payment_counted(lookalikes + named, 350, ["Ally", "Car loan"]))
        self.assertFalse(planner.payment_counted([out(3, 350, "ally")] * 4, 350, []))   # four in one month is still one month

    def test_a_transfer_is_the_loans_payment_only_by_name_size_and_not_a_cards(self):
        out = lambda month, amount, text: {"month": f"2026-{month:02}", "amount": amount, "text": text}
        months = (3, 4, 5, 6)
        # a Chase card's autopay names the auto loan's lender, at the card bill's size: not the loan's payment
        card = [out(m, 520, "chase credit crd autopay") for m in months]
        self.assertFalse(planner.payment_counted(card, 500, ["Chase", "Auto loan"], named_only=True))
        # past 10% of it (an escrow allowance is for spending only)
        self.assertFalse(planner.payment_counted([out(m, 600, "chase auto loan pmt") for m in months], 500, ["Chase"], named_only=True))
        self.assertTrue(planner.payment_counted([out(m, 500, "chase auto loan pmt") for m in months], 500, ["Chase"], named_only=True))
        # a loan from a card issuer, paid by transfer, is still the loan's payment
        self.assertTrue(planner.payment_counted([out(m, 500, "capital one auto pmt") for m in months], 500, ["Capital One"], named_only=True))

    def test_a_mortgage_paid_with_its_escrow_is_still_its_payment(self):
        # The lender reports $1,850 of principal and interest; the bank shows $2,450 going out, taxes and insurance in
        out = lambda month, amount, text: {"month": f"2026-{month:02}", "amount": amount, "text": text}
        escrowed = [out(m, 2450, "rocket mortgage payment") for m in (3, 4, 5, 6)]
        self.assertTrue(planner.payment_counted(escrowed, 1850, ["Rocket Mortgage", "Home loan"]))
        self.assertFalse(planner.payment_counted(escrowed, 1850, ["Other Bank"]))   # not named: the amount alone is too far off
        self.assertFalse(planner.payment_counted([out(m, 2900, "rocket mortgage") for m in (3, 4, 5, 6)], 1850, ["Rocket Mortgage"]))
        self.assertFalse(planner.payment_counted([out(m, 1500, "rocket mortgage") for m in (3, 4, 5, 6)], 1850, ["Rocket Mortgage"]))

    def test_a_sale_cant_be_in_the_past(self):
        good = self.plan()["plan"]
        with self.assertRaisesRegex(planner.PlanError, r"^A sale can’t be in the past: sell in 2026 or later$"):
            planner.clean({**good, "assets": [{"key": "asset:1", "sell_year": 2025}]}, TODAY)
        with self.assertRaisesRegex(planner.PlanError, r"at most 100 years out, in 2126"):
            planner.clean({**good, "assets": [{"key": "asset:1", "sell_year": 2127}]}, TODAY)
        with self.assertRaisesRegex(planner.PlanError, r"^The year it's sold must be a number$"):
            planner.clean({**good, "assets": [{"key": "asset:1", "sell_year": "soon"}]}, TODAY)
        self.assertEqual(planner.clean({**good, "assets": [{"key": "asset:1", "sell_year": 2026}]}, TODAY)["assets"],
                         [{"key": "asset:1", "sell_year": 2026}])

    def test_a_sale_kept_for_a_year_now_past_counts_this_year(self):
        self.c.execute(insert(Asset).values(id=7, name="House", kind="home", value=450000, as_of=TODAY.isoformat()))
        raw = json.dumps({**planner.clean(self.plan()["plan"], date(2024, 1, 1)), "assets": [{"key": "asset:7", "sell_year": 2025}]})
        db.set_setting(self.c, "retirement_plan", raw)
        self.assertEqual(self.plan()["plan"]["assets"], [{"key": "asset:7", "sell_year": 2026, "was": 2025}])
        self.assertEqual(db.get_setting(self.c, "retirement_plan"), raw)   # shown, not written back
        planner.save(self.c, self.plan()["plan"], TODAY)   # the next change keeps this year (and drops `was`)
        self.assertEqual(self.plan()["plan"]["assets"], [{"key": "asset:7", "sell_year": 2026}])

    def test_vehicles_are_listed_for_their_loan_but_never_sold_into_the_plan(self):
        self.c.execute(insert(Account).values(id="auto", name="Car loan", kind="loan", balance=-20000, interest_rate=5,
                                              monthly_payment=600))
        self.c.execute(insert(Asset), [{"id": 8, "name": "Car", "kind": "vehicle", "value": 30000, "as_of": TODAY.isoformat(),
                                        "yearly_change": -15, "loan_account_id": "auto"},
                                       {"id": 9, "name": "Cabin", "kind": "other", "value": 90000, "as_of": TODAY.isoformat(),
                                        "yearly_change": None, "loan_account_id": None}])
        assets = {a["name"]: a for a in self.plan()["assets"]}
        self.assertEqual((assets["Car"]["kind"], assets["Car"]["loan"]["payment"]), ("vehicle", 600))
        self.assertIsNone(assets["Cabin"]["yearly_change"])   # not set: the page keeps it level with inflation
        good = self.plan()["plan"]
        with self.assertRaisesRegex(planner.PlanError, "Vehicles aren’t sold into the plan"):
            planner.save(self.c, {**good, "assets": [{"key": "asset:8", "sell_year": 2030}]}, TODAY)
        # one kept from before vehicles were left out isn't counted
        db.set_setting(self.c, "retirement_plan", json.dumps({**planner.clean(good, TODAY), "assets": [
            {"key": "asset:8", "sell_year": 2030}, {"key": "asset:9", "sell_year": 2031}]}))
        self.assertEqual(self.plan()["plan"]["assets"], [{"key": "asset:9", "sell_year": 2031}])

    def test_the_plan_knows_whether_spending_is_runways_figure_or_yours(self):
        p = self.plan()
        self.assertFalse(p["plan"]["spending_own"])   # the default: Runway's figure
        planner.save(self.c, {**p["plan"], "spending": 48000, "spending_own": True}, TODAY)
        self.assertTrue(self.plan()["plan"]["spending_own"])
        planner.save(self.c, {**p["plan"], "spending_own": "0"}, TODAY)
        self.assertFalse(self.plan()["plan"]["spending_own"])
        self.assertFalse(planner.clean({**p["plan"], "spending_own": None}, TODAY)["spending_own"])   # left out: Runway's

    def test_a_plan_kept_before_the_flag_is_runways_figure(self):
        # Saved whole on every change, its spending can't tell a typed figure from Runway's: taken as Runway's,
        # however far it is from today's figure, and nothing is written back on reading it
        computed = self.plan()["computed"]
        old = {k: v for k, v in planner.clean(self.plan()["plan"], TODAY).items() if k != "spending_own"}
        for spending in (computed["annual_spending"], computed["annual_spending"] + 2400):
            raw = json.dumps({**old, "spending": spending})
            db.set_setting(self.c, "retirement_plan", raw)
            self.assertFalse(self.plan()["plan"]["spending_own"])
            self.assertEqual(db.get_setting(self.c, "retirement_plan"), raw)

    def test_yearly_savings_says_what_it_is(self):
        p = self.plan()["computed"]
        self.assertEqual((p["savings_measured"], p["savings_since"]), (True, None))   # a year of history: the last 12 months
        self.tx("dep", "2026-06-01", "cash", "deposit", -3000)
        p = self.plan()["computed"]
        self.assertEqual((p["yearly_savings"], p["savings_since"]), (3000.0, "2026-05-31"))   # history starts then
        db.set_setting(self.c, "fire_yearly_savings", "20000")   # typed on the old card: not a measurement
        p = self.plan()["computed"]
        self.assertEqual((p["yearly_savings"], p["savings_measured"], p["savings_since"]), (20000.0, False, None))


class MonthlySpendingTests(DbCase):
    """Retirement spending and the emergency-fund rule count spending the way Reports does."""

    def test_uncategorized_counts_and_a_month_of_money_back_is_zero(self):
        self.c.execute(insert(Account), [{"id": "chk", "name": "Checking", "kind": "checking", "balance": 0},
                                         {"id": "brk", "name": "Brokerage", "kind": "investment", "balance": 0}])
        rows = [("2026-03-05", -1000, "Groceries"), ("2026-03-06", -500, None),   # uncategorized money out is spending
                ("2026-03-07", -2000, "Transfer"), ("2026-03-08", 5000, "Income"), ("2026-03-09", 300, None),
                ("2026-04-05", -400, "Groceries"), ("2026-04-06", -100, "No such category"),   # unknown: uncategorized
                ("2026-05-05", -200, "Shopping"), ("2026-05-06", 700, "Shopping"),   # more back than out: a month of none
                ("2026-02-27", -9999, "Groceries"), ("2026-09-01", -9999, "Groceries")]   # outside the 6 full months
        self.c.execute(insert(Transaction), [{"id": f"t{i}", "account_id": "chk", "posted": d, "amount": a, "category": c}
                                             for i, (d, a, c) in enumerate(rows)])
        self.c.execute(insert(Transaction).values(id="inv", account_id="brk", posted="2026-03-10", amount=-750))
        self.assertEqual(portfolio.monthly_spending(self.c, TODAY), round((1500 + 500 + 0) / 3, 2))


# ---------------------------------------------------------------------------------------------- mock servers

class MockPlaid(BaseHTTPRequestHandler):
    calls: list = []
    login_required = False

    def log_message(self, *a):
        pass

    def reply(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        MockPlaid.calls.append((self.path, req))
        if req.get("client_id") != "cid" or req.get("secret") != "sec":
            return self.reply(400, {"error_code": "INVALID_API_KEYS", "error_message": "invalid client_id or secret"})
        if self.path == "/link/token/create":
            return self.reply(200, {"link_token": "link-sandbox-123"})
        if self.path == "/item/public_token/exchange":
            return self.reply(200, {"access_token": "access-1", "item_id": "item-1"})
        if self.path == "/item/remove":
            return self.reply(200, {})
        if MockPlaid.login_required:
            return self.reply(400, {"error_code": "ITEM_LOGIN_REQUIRED", "error_message": "login required",
                                    "display_message": "Your credentials have changed."})
        secs = [{"security_id": "s-vti", "ticker_symbol": "VTI", "name": "Vanguard Total Stock", "type": "etf", "close_price": 300},
                {"security_id": "s-cash", "ticker_symbol": "CUR:USD", "name": "U S Dollar", "type": "cash", "is_cash_equivalent": True}]
        if self.path == "/investments/holdings/get":
            return self.reply(200, {
                "accounts": [{"account_id": "acc-1", "name": "Brokerage", "type": "investment", "subtype": "brokerage",
                              "balances": {"current": 3100, "iso_currency_code": "USD"}}],
                "holdings": [{"account_id": "acc-1", "security_id": "s-vti", "quantity": 10, "institution_price": 300,
                              "institution_value": 3000, "cost_basis": 2500},
                             {"account_id": "acc-1", "security_id": "s-cash", "quantity": 100, "institution_price": 1, "institution_value": 100}],
                "securities": secs, "item": {"item_id": "item-1", "institution_name": "Fidelity"}})
        if self.path == "/investments/transactions/get":
            all_tx = [{"investment_transaction_id": f"t{i}", "account_id": "acc-1", "security_id": "s-vti", "date": f"2026-0{1 + i % 8}-15",
                       "name": "BUY VTI", "type": "buy", "subtype": "buy", "quantity": 1, "amount": 250, "price": 250, "fees": 0}
                      for i in range(7)]
            off, cnt = req["options"]["offset"], req["options"]["count"]
            page = all_tx[off:off + min(cnt, 3)]  # serve small pages to exercise pagination
            return self.reply(200, {"investment_transactions": page, "total_investment_transactions": len(all_tx), "securities": secs})
        self.reply(404, {"error_code": "NOT_FOUND"})


class MockYahoo(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        day = lambda d: int(datetime(d.year, d.month, d.day, 14, tzinfo=UTC).timestamp())
        ts = [day(date(2026, 9, 21)), day(date(2026, 9, 22))]
        body = json.dumps({"chart": {"result": [{
            "meta": {"gmtoffset": -14400}, "timestamp": ts,
            "indicators": {"quote": [{"close": [100.0, 101.0]}], "adjclose": [{"adjclose": [99.0, 100.0]}]},
            "events": {"splits": {"1": {"date": day(date(2026, 3, 2)), "numerator": 4, "denominator": 1}}}}]}}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)


class SyncTests(DbCase):
    @classmethod
    def setUpClass(cls):
        cls.plaid = HTTPServer(("127.0.0.1", 0), MockPlaid)
        cls.yahoo = HTTPServer(("127.0.0.1", 0), MockYahoo)
        for s in (cls.plaid, cls.yahoo):
            threading.Thread(target=s.serve_forever, daemon=True).start()
        os.environ["RUNWAY_PLAID_URL"] = f"http://127.0.0.1:{cls.plaid.server_port}"
        os.environ["RUNWAY_PRICES_URL"] = f"http://127.0.0.1:{cls.yahoo.server_port}/chart"

    @classmethod
    def tearDownClass(cls):
        cls.plaid.shutdown(); cls.yahoo.shutdown()
        os.environ.pop("RUNWAY_PLAID_URL", None); os.environ.pop("RUNWAY_PRICES_URL", None)

    def setUp(self):
        super().setUp()
        MockPlaid.calls.clear(); MockPlaid.login_required = False

    def test_link_exchange_sync_remove(self):
        with self.assertRaises(plaid.PlaidError):
            plaid.link_token(self.c)  # no keys yet
        db.set_setting(self.c, "plaid_client_id", "cid"); db.set_setting(self.c, "plaid_secret", "sec")
        self.assertEqual(plaid.link_token(self.c), "link-sandbox-123")
        self.assertEqual(MockPlaid.calls[-1][1]["products"], ["investments"])
        item = plaid.exchange(self.c, "public-1", {"name": "Fidelity", "institution_id": "ins_12"})
        res = plaid.sync_item(self.c, item, TODAY)
        self.assertEqual(res, {"accounts": 1, "holdings": 2, "transactions": 7})
        self.assertEqual(self.c.execute(select(func.count()).select_from(InvTransaction)).fetchone()[0], 7)
        tx_calls = [c for p, c in MockPlaid.calls if p == "/investments/transactions/get"]
        self.assertEqual([c["options"]["offset"] for c in tx_calls], [0, 3, 6])
        self.assertEqual(tx_calls[0]["start_date"], (TODAY - timedelta(days=plaid.HISTORY_DAYS)).isoformat())
        cash = self.c.execute(select(Security.is_cash).where(Security.id == "s-cash")).fetchone()[0]
        self.assertEqual(cash, 1)
        # second sync only re-reads a recent window
        plaid.sync_item(self.c, item, TODAY)
        self.assertEqual([c for p, c in MockPlaid.calls if p == "/investments/transactions/get"][-1]["start_date"],
                         (TODAY - timedelta(days=plaid.REFRESH_DAYS)).isoformat())
        # update mode link token for reconnecting
        plaid.link_token(self.c, item)
        self.assertEqual(MockPlaid.calls[-1][1]["access_token"], "access-1")
        self.assertNotIn("products", MockPlaid.calls[-1][1])
        # expired login is recorded, not fatal for other items
        MockPlaid.login_required = True
        out = plaid.sync_all(self.c)
        self.assertEqual(out["items"], 0)
        self.assertIn("credentials have changed", out["errors"][0])
        self.assertEqual(self.c.execute(select(PlaidItem.error)).fetchone()[0], "ITEM_LOGIN_REQUIRED")
        plaid.remove_item(self.c, item)
        for model in (PlaidItem, InvAccount, Holding, InvTransaction):
            self.assertEqual(self.c.execute(select(func.count()).select_from(model)).fetchone()[0], 0, model.__table__.name)

    def test_price_fetch_with_splits(self):
        res = prices.refresh(self.c, ["VTI", "CUR:USD"], date(2026, 1, 1))
        self.assertEqual(res, {"fetched": ["VTI"], "failed": []})
        real, adj, splits = prices.history(self.c, "VTI", date(2026, 1, 1))
        self.assertEqual(splits, [("2026-03-02", 4.0)])
        self.assertEqual(real["2026-09-22"], 101.0)       # after the split: unchanged
        self.assertEqual(adj["2026-09-22"], 100.0)
        # a second refresh within the day is skipped
        self.assertEqual(prices.refresh(self.c, ["VTI"], date(2026, 1, 1)), {"fetched": [], "failed": []})

    def test_a_rate_limit_doesnt_mark_tickers_bad(self):
        import io
        import urllib.error
        from unittest import mock
        asked = []

        def limited(t, *_a):
            asked.append(t)
            raise urllib.error.HTTPError("https://prices", 429, "Too Many Requests", {}, io.BytesIO(b""))
        with mock.patch.object(prices, "fetch", side_effect=limited):
            res = prices.refresh(self.c, ["VTI", "VXUS", "BND"], date(2026, 1, 1))
        self.assertEqual((asked, res["failed"]), (["BND"], ["BND"]))   # stops at the first refusal
        self.assertEqual(self.c.execute(select(func.count()).select_from(PriceMeta)).fetchone()[0], 0)   # nothing held against them
        with mock.patch.object(prices, "fetch", side_effect=TimeoutError("timed out")):
            prices.refresh(self.c, ["VTI"], date(2026, 1, 1))
        self.assertEqual(self.c.execute(select(func.count()).select_from(PriceMeta)).fetchone()[0], 0)
        self.assertEqual(prices.refresh(self.c, ["VTI"], date(2026, 1, 1))["fetched"], ["VTI"])   # tried again next time

    def test_a_ticker_that_stops_answering_keeps_what_was_known(self):
        from unittest import mock
        good = ([("2026-09-21", 10.0, 9.5), ("2026-09-22", 11.0, 10.5)], [("2026-03-02", 2.0)], {"type": "ETF", "name": "Fund"})
        with mock.patch.object(prices, "fetch", return_value=good):
            prices.refresh(self.c, ["ABC"], date(2026, 1, 1))
        again = ([("2026-09-22", 12.0, 11.5)], [("2026-03-02", 2.0)], {"type": None, "name": None})
        with mock.patch.object(prices, "fetch", return_value=again):   # new closes replace old ones; no name this time
            prices.refresh(self.c, ["ABC"], date(2026, 1, 1), force=True)
        meta = dict(self.c.execute(select(PriceMeta.ok, PriceMeta.splits, PriceMeta.instrument_type,
                                          PriceMeta.long_name)
                                   .where(PriceMeta.ticker == "ABC")).fetchone())
        self.assertEqual(meta, {"ok": 1, "splits": "[[\"2026-03-02\", 2.0]]", "instrument_type": "ETF", "long_name": ""})
        with mock.patch.object(prices, "fetch", return_value=([], [], {})):   # nothing back: splits and type are kept
            self.assertEqual(prices.refresh(self.c, ["ABC"], date(2026, 1, 1), force=True)["failed"], ["ABC"])
        meta = dict(self.c.execute(select(PriceMeta.ok, PriceMeta.splits, PriceMeta.instrument_type,
                                          PriceMeta.long_name)
                                   .where(PriceMeta.ticker == "ABC")).fetchone())
        self.assertEqual(meta, {"ok": 0, "splits": "[[\"2026-03-02\", 2.0]]", "instrument_type": "ETF", "long_name": ""})
        rows = [tuple(r) for r in self.c.execute(select(Price.date, Price.close, Price.adjclose)
                                                 .where(Price.ticker == "ABC").order_by(Price.date))]
        self.assertEqual(rows, [("2026-09-21", 10.0, 9.5), ("2026-09-22", 12.0, 11.5)])


def q(price, t=1000, open_=True):
    return {"price": price, "prev_close": 100.0, "time": t, "type": "EQUITY",
            "open_start": 1 if open_ else None, "open_end": 2 ** 40 if open_ else None}


class QuoteStreamTests(unittest.TestCase):
    def run_stream(self, rounds, lifetime=60):
        """quote_stream against a scripted list of quotes() answers, on a fake clock (5s per sleep)."""
        clock = [0.0]
        answers = iter(rounds)
        with mock.patch.object(prices, "quotes", side_effect=lambda *_a, **_k: next(answers)):
            return list(prices.quote_stream(["VTI", "AAPL"], lifetime=lifetime, clock=lambda: clock[0],
                                            sleep=lambda s: clock.__setitem__(0, clock[0] + s)))

    def test_sends_everything_then_only_what_moved(self):
        out = self.run_stream([
            {"SPY": q(500), "VTI": q(250), "AAPL": q(200)},
            {"SPY": q(500), "VTI": q(250), "AAPL": q(200)},          # nothing moved: a keep-alive
            {"SPY": q(500), "VTI": q(251, 1005), "AAPL": q(200)},
        ] + [{"SPY": q(500)}] * 20, lifetime=10)
        self.assertEqual(set(out[0]["quotes"]), {"SPY", "VTI", "AAPL"})
        self.assertEqual(out[0]["market"], "open")
        self.assertIsNone(out[1])
        self.assertEqual(out[2]["quotes"], {"VTI": q(251, 1005)})
        self.assertEqual(len(out), 3)                                 # stops after `lifetime` seconds

    def test_closed_market_sends_one_update_and_ends(self):
        out = self.run_stream([{"SPY": q(500, open_=False), "VTI": q(250, open_=False)}])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["market"], "closed")

    def test_many_tickers_slow_down(self):
        self.assertEqual(prices.stream_interval(3), prices.STREAM_SECONDS)
        self.assertEqual(prices.stream_interval(80), 20)


class QuoteStreamEndpointTests(unittest.TestCase):
    def test_streams_server_sent_events(self):
        from http.server import ThreadingHTTPServer
        import urllib.request
        from runway import server
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"RUNWAY_DATA": tmp}):
            db.init()
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            try:
                update = {"quotes": {"VTI": q(250)}, "market": "closed", "as_of": "2026-09-28T17:00:00"}
                with mock.patch.object(prices, "quote_stream", side_effect=lambda *_a, **_k: (u for u in [update])):
                    resp = urllib.request.urlopen(f"http://127.0.0.1:{httpd.server_port}/api/investments/stream", timeout=10)
                    self.assertEqual(resp.headers["Content-Type"], "text/event-stream")
                    body = resp.read().decode()
            finally:
                httpd.shutdown(); httpd.server_close()
        self.assertIn("event: quotes\ndata: " + json.dumps(update) + "\n\n", body)
        self.assertTrue(body.rstrip().endswith(f"retry: {prices.CLOSED_RETRY * 1000}"))   # closed: come back later


if __name__ == "__main__":
    unittest.main()


class DuplicateConnectionTests(DbCase):
    """The same login linked twice (two Wealthfront connections with the same accounts) is flagged, and refused at link."""

    def add(self, item, accounts, institution="Wealthfront", products="investments"):
        self.c.execute(insert(PlaidItem).values(item_id=item, access_token="tok", institution_id="ins_wf",
                                                institution_name=institution, products=products))
        for i, (name, mask) in enumerate(accounts):
            self.c.execute(insert(InvAccount).values(id=f"{item}-{i}", item_id=item, name=name, mask=mask))

    def test_duplicates_are_found(self):
        kids = [("Roth IRA", "3639"), ("Oliver's 529 Account", "6624")]
        self.add("a", kids)
        self.add("b", kids)
        self.add("c", [("Individual", "1111")])            # a different Wealthfront login: fine
        self.assertEqual(plaid.duplicates(self.c, "a"), [{"item_id": "b", "shared": 2, "adds_nothing": True}])
        self.assertEqual(plaid.duplicates(self.c, "c"), [])
        self.add("d", [*kids, ("Joint", "2222")])           # overlaps, but brings a new account too
        self.assertEqual([d["adds_nothing"] for d in plaid.duplicates(self.c, "d")], [False, False])

    def test_linking_the_same_login_again_is_undone(self):
        from runway import server
        kids = [("Roth IRA", "3639")]
        self.add("a", kids)

        def exchange(conn, *_):
            self.add("b", kids)
            return "b"

        removed = []
        with mock.patch.object(plaid, "exchange", side_effect=exchange), \
                mock.patch.object(plaid, "sync_item", return_value={"accounts": 1}), \
                mock.patch.object(plaid, "remove_item", side_effect=lambda conn, i: removed.append(i)):
            with self.assertRaises(server.ApiError) as e:
                server.api_plaid_exchange(self.c, {}, {"public_token": "p", "institution": {"name": "Wealthfront"}})
        self.assertEqual(e.exception.status, 409)
        self.assertIn("already connected", str(e.exception))
        self.assertEqual(removed, ["b"])

    def test_connecting_while_a_sync_runs_is_still_a_success(self):
        """The connection is made; only its first sync waits for the next one, so the answer isn't an error."""
        from runway import server
        from runway.server.api import connections
        self.add("a", [("Roth IRA", "3639")])
        lock = mock.Mock(acquire=mock.Mock(return_value=False))
        with mock.patch.object(plaid, "exchange", return_value="a"), mock.patch.object(connections, "_item_lock", return_value=lock), \
                mock.patch.object(plaid, "sync_item") as sync:
            res = server.api_plaid_exchange(self.c, {}, {"public_token": "p", "institution": {"name": "Wealthfront"}})
        self.assertEqual({k: res[k] for k in ("ok", "connected", "sync_deferred", "item_id")},
                         {"ok": True, "connected": True, "sync_deferred": True, "item_id": "a"})
        self.assertIn("come in with the next one", res["message"])
        sync.assert_not_called()
        lock.release.assert_not_called()


class InvestmentAccountsInYourAccountsTests(DbCase):
    """Investment accounts linked through Plaid show under Settings → Accounts and count in net worth, once."""

    def setUp(self):
        super().setUp()
        self.c.execute(insert(PlaidItem).values(item_id="wf", access_token="t", institution_name="Wealthfront",
                                                products="investments"))

    def inv(self, id_, name, balance):
        self.c.execute(insert(InvAccount).values(id=id_, item_id="wf", name=name, mask="1234", balance=balance))

    def sf(self, id_, name, balance, org="Wealthfront"):
        self.c.execute(insert(Account).values(id=id_, name=name, org=org, kind="investment", balance=balance))

    def acct(self, id_):
        return self.c.execute(select(InvAccount.account_id).where(InvAccount.id == id_)).fetchone()[0]

    def test_its_own_account_when_simplefin_has_nothing_there(self):
        self.sf("et", "E*Trade", 1000, org="E*Trade")
        self.inv("a1", "Individual", 5000)
        plaid.update_investment_accounts(self.c, "wf")
        self.assertEqual(self.acct("a1"), "pl:a1")
        row = self.c.execute(select(Account.name, Account.kind, Account.balance)
                             .where(Account.id == "pl:a1")).fetchone()
        self.assertEqual((row["name"], row["kind"], row["balance"]), ("Individual ••1234", "investment", 5000))
        from runway import networth
        self.assertAlmostEqual(networth.summary(self.c)["assets"], 6000)      # counted in net worth (with E*Trade's 1000)
        self.c.execute(update(InvAccount).where(InvAccount.id == "a1").values(balance=5100))
        plaid.update_investment_accounts(self.c, "wf")                       # balances follow each sync
        self.assertEqual(self.c.execute(select(Account.balance).where(Account.id == "pl:a1")).fetchone()[0], 5100)

    def test_hiding_its_account_in_settings_hides_it_on_investments(self):
        self.inv("a1", "Individual", 5000)
        plaid.update_investment_accounts(self.c, "wf")
        self.c.execute(update(Account).where(Account.id == "pl:a1").values(hidden=1))
        acct = next(a for a in portfolio.overview(self.c, "1Y", date.today())["accounts"] if a["id"] == "a1")
        self.assertEqual((acct["hidden"], acct["hidden_in_accounts"]), (1, 1))

    def test_same_as_simplefin_when_the_balance_says_so_and_asks_otherwise(self):
        self.sf("sf-roth", "Roth IRA", 4943.43)
        self.sf("sf-529", "529 Plan", 0.0)
        self.inv("roth", "Roth IRA", 4943.43)
        self.inv("529", "Madeleine's 529 Account", 12000)
        self.inv("new", "Joint", 700)
        plaid.update_investment_accounts(self.c, "wf")
        self.assertEqual(self.acct("roth"), "sf-roth")                       # counted once
        self.assertIsNone(self.acct("529"))                                  # not clear: waits for you
        self.assertIsNone(self.acct("new"))
        self.assertEqual(plaid.undecided_count(self.c), 2)
        self.assertEqual([c["id"] for c in plaid.investment_candidates(self.c, "wf")], ["sf-529", "sf-roth"])
        plaid.match_investment(self.c, "529", "sf-529")
        plaid.match_investment(self.c, "new", "new")
        self.assertEqual(plaid.undecided_count(self.c), 0)
        self.assertTrue(self.c.execute(select(Account.id).where(Account.id == "pl:new")).fetchone())
        plaid.match_investment(self.c, "new", "ignore")                      # changing your mind removes its entry
        self.assertIsNone(self.c.execute(select(Account.id).where(Account.id == "pl:new")).fetchone())
        with self.assertRaises(ValueError):
            plaid.match_investment(self.c, "529", "not-an-account")

    def test_a_matched_simplefin_account_shows_once_on_investments(self):
        self.sf("sf-roth", "Roth IRA", 4943.43)
        self.c.execute(insert(InvAccount).values(id="sf:sf-roth", item_id="sf", name="Roth IRA", balance=4943.43,
                                                 source="simplefin"))
        self.inv("roth", "Roth IRA", 4943.43)
        ids = lambda: {a["id"] for a in portfolio.overview(self.c, "1Y", date.today())["accounts"] if not a["hidden"]}
        self.assertEqual(ids(), {"sf:sf-roth", "roth"})                     # not matched yet: both
        plaid.match_investment(self.c, "roth", "sf-roth")
        self.assertEqual(ids(), {"roth"})

    def test_the_same_account_from_simplefin_and_plaid_is_listed_once(self):
        # E*TRADE sends ••6702 through Plaid and "Individual Brokerage (6702)" through SimpleFIN, and neither was matched
        self.c.execute(insert(PlaidItem).values(item_id="et", access_token="t",
                                                institution_name="E*TRADE from Morgan Stanley", products="investments"))
        self.c.execute(insert(InvAccount).values(id="et-6702", item_id="et", name="Individual Brokerage -6702",
                                                 mask="6702", balance=120000))
        self.c.execute(insert(InvAccount).values(id="et-1111", item_id="et", name="Roth IRA", mask="1111",
                                                 balance=30000))
        for id_, name in (("sf:et1", "Individual Brokerage (6702)"), ("sf:et2", "Rollover IRA (2222)")):
            self.c.execute(insert(InvAccount).values(id=id_, item_id="sf", name=name, balance=1, source="simplefin",
                                                     institution="E*Trade"))
        # "(6702)" at another firm is a different account
        self.c.execute(insert(InvAccount).values(id="sf:rh", item_id="sf", name="Individual (6702)", balance=1,
                                                 source="simplefin", institution="Robinhood"))
        listed = {a["id"]: a for a in portfolio.overview(self.c, "1Y", date.today())["accounts"]}
        self.assertNotIn("sf:et1", listed)                                   # the SimpleFIN copy isn't listed...
        self.assertTrue(listed["et-6702"]["also_simplefin"])                 # ...the Plaid one says so
        self.assertFalse(listed["et-1111"]["also_simplefin"])
        self.assertIn("sf:et2", listed)                                      # no Plaid account with 2222
        self.assertIn("sf:rh", listed)
        dup = next(a for a in portfolio._accounts(self.c) if a["id"] == "sf:et1")
        self.assertEqual((dup["duplicate_of"], dup["hidden"]), ("et-6702", 1))   # ...and never counted
        self.assertIn("et-6702", portfolio._visible_ids(self.c))
        self.assertNotIn("sf:et1", portfolio._visible_ids(self.c))

    def test_a_simplefin_account_links_to_one_plaid_account(self):
        self.sf("sf-roth", "Roth IRA", 4943.43)
        self.inv("roth", "Roth IRA", 4943.43)
        self.inv("roth2", "Roth IRA", 4943.43)
        plaid.match_investment(self.c, "roth", "sf-roth")
        cands = {c["id"]: c["linked_to"] for c in plaid.investment_candidates(self.c, "wf")}
        self.assertEqual(cands, {"sf-roth": "roth"})                         # the page offers it only to "roth"
        with self.assertRaises(ValueError):
            plaid.match_investment(self.c, "roth2", "sf-roth")
        self.assertIsNone(self.acct("roth2"))
        plaid.match_investment(self.c, "roth", "sf-roth")                    # choosing it again for the same one is fine
        plaid.match_investment(self.c, "roth", "")                           # unlinked: free for another
        plaid.match_investment(self.c, "roth2", "sf-roth")
        self.assertEqual(self.acct("roth2"), "sf-roth")

    def test_removing_the_connection_removes_its_accounts(self):
        self.inv("a1", "Individual", 5000)
        plaid.update_investment_accounts(self.c, "wf")
        with mock.patch.object(plaid, "call", return_value={}):
            plaid.remove_item(self.c, "wf")
        self.assertIsNone(self.c.execute(select(Account.id).where(Account.id == "pl:a1")).fetchone())
