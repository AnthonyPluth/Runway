"""Loans paid down on their terms, and what the retirement planner can sell: a home less what will still be owed on its
loan, and company equity as it will have vested."""
import unittest
from datetime import date

from sqlalchemy import insert, update

from runway import equity, loans, planner
from runway.models import Account, Asset, EquityCompany, EquityGrant, LoanTerms, Transaction
from tests.shared import TODAY, LedgerCase


class AmortizationTests(unittest.TestCase):
    def test_a_30_year_mortgage_as_a_calculator_has_it(self):
        payment = loans.payment_to_pay_off(200_000, 6.5, 360)
        self.assertAlmostEqual(payment, 1264.14, places=2)
        owed, short = loans.project(200_000, 6.5, 1264.14)
        self.assertFalse(short)
        self.assertEqual(len(owed), 31)
        self.assertEqual(owed[0], 200_000)
        self.assertAlmostEqual(owed[1], 197_764.50, places=2)
        self.assertAlmostEqual(owed[5], 187_221.68, places=2)
        self.assertEqual(owed[-1], 0)

    def test_paid_off_early_floors_at_zero(self):
        owed, short = loans.project(10_000, 5, 5_000)
        self.assertEqual(owed, [10_000, 0])
        self.assertFalse(short)
        self.assertEqual(loans.project(12_000, 0, 1_000)[0], [12_000, 0])
        self.assertEqual(loans.project(-50, 5, 100), ([-50], False))

    def test_a_payment_that_doesnt_cover_the_interest_is_flagged_not_grown(self):
        for payment in (1_400, 1_500, 0):
            self.assertEqual(loans.project(300_000, 6, payment), ([300_000], True))

    def test_the_year_of_the_last_payment(self):
        today, t = date(2026, 10, 1), lambda rate, payment: {"rate": rate, "payment": payment}
        self.assertEqual(loans.payoff_year(2_000, t(0, 1_000), today), 2026)
        self.assertEqual(loans.payoff_year(14_000, t(0, 1_000), today), 2027)
        self.assertEqual(loans.payoff_year(14_001, t(0, 1_000), today), 2028)
        self.assertEqual(loans.payoff_year(50_000, t(6, 1_000), today), 2031)
        self.assertEqual(loans.payoff_year(0, t(6, 1_000), today), 2026)
        for terms in (None, t(None, 1_000), t(6, None), t(6, 250), t(6, 0)):
            self.assertIsNone(loans.payoff_year(50_000, terms, today))

    def test_payment_that_pays_it_off_by_a_date(self):
        self.assertAlmostEqual(loans.payment_to_pay_off(12_000, 0, 12), 1_000)
        self.assertAlmostEqual(loans.payment_to_pay_off(100_000, 6, 120), 1110.21, places=2)
        self.assertEqual(loans.payment_to_pay_off(500, 6, 0), 500)

    def test_owed_by_year_says_what_its_based_on(self):
        self.assertEqual(loans.owed_by_year(1000, None), ([1000], {"rate": None, "payment": None, "source": None, "note": "no_rate"}))
        t = {"rate": 5, "payment": None, "source": None}
        self.assertEqual(loans.owed_by_year(1000, t)[1]["note"], "no_payment")
        years, basis = loans.owed_by_year(10_000, {"rate": 5, "payment": 5_000, "source": "manual"})
        self.assertEqual((years, basis), ([10_000, 0], {"rate": 5, "payment": 5_000, "source": "manual", "note": None}))
        self.assertEqual(loans.owed_by_year(300_000, {"rate": 6, "payment": 100, "source": "inferred"})[1]["note"],
                         "payment_below_interest")


class TermsTests(LedgerCase):
    """Where a loan's rate and payment come from: Plaid's, yours, its payoff date, or recent payments."""

    def setUp(self):
        super().setUp()
        self.acct("mtg", "loan", -250_000)

    def plaid(self, **kw):
        self.conn.execute(update(Account).where(Account.id == "mtg").values(plaid_account_id="p-mtg"))
        self.conn.execute(insert(LoanTerms).values(plaid_account_id="p-mtg", item_id="item", kind="mortgage", **kw))

    def payments(self, *months, amount=1_840.0):
        for m in months:
            self.tx("mtg", f"{m}-03", amount, "PAYMENT")

    def test_payment_inferred_from_recent_months(self):
        self.payments("2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09")
        self.payments("2026-07", amount=1_840.0)
        self.tx("mtg", "2026-06-15", -45.0, "LATE FEE")
        self.tx("mtg", "2026-08-20", 300.0, "EXTRA", pending=1)
        self.tx("mtg", "2026-02-03", 9_999.0, "LONG AGO")
        self.assertEqual(loans.inferred_payments(self.conn, ["mtg"], TODAY), {"mtg": 1_840.0})

    def test_no_inference_without_enough_history(self):
        self.payments("2026-08")
        self.assertEqual(loans.inferred_payments(self.conn, ["mtg"], TODAY), {})
        self.assertEqual(loans.inferred_payments(self.conn, [], TODAY), {})

    def test_your_terms_then_recent_payments(self):
        self.payments("2026-07", "2026-08")
        t = loans.terms(self.conn, TODAY)["mtg"]
        self.assertEqual((t["rate"], t["payment"], t["source"], t["plaid"], t["inferred_payment"]), (None, None, None, False, 1_840.0))
        self.conn.execute(update(Account).where(Account.id == "mtg").values(interest_rate=6.25))
        t = loans.terms(self.conn, TODAY)["mtg"]
        self.assertEqual((t["rate"], t["payment"], t["source"]), (6.25, 1_840.0, "inferred"))
        self.conn.execute(update(Account).where(Account.id == "mtg").values(monthly_payment=2_000))
        t = loans.terms(self.conn, TODAY)["mtg"]
        self.assertEqual((t["rate"], t["payment"], t["source"], t["set_rate"], t["set_payment"]), (6.25, 2_000, "manual", 6.25, 2_000))

    def test_plaids_terms_win_and_a_payoff_date_gives_the_payment(self):
        self.conn.execute(update(Account).where(Account.id == "mtg").values(interest_rate=3, monthly_payment=900))
        self.plaid(interest_rate=6.0, monthly_payment=None, maturity_date="2036-09-01")
        t = loans.terms(self.conn, TODAY)["mtg"]
        self.assertEqual((t["rate"], t["payment"], t["source"], t["plaid"], t["plaid_payment"], t["maturity"]),
                         (6.0, 900, "manual", True, False, "2036-09-01"))
        self.assertEqual((t["set_rate"], t["set_payment"]), (3, 900))
        self.conn.execute(update(Account).where(Account.id == "mtg").values(monthly_payment=None))
        t = loans.terms(self.conn, TODAY)["mtg"]
        self.assertEqual(t["source"], "plaid")
        self.assertAlmostEqual(t["payment"], loans.payment_to_pay_off(250_000, 6, 120), places=2)

    def test_plaids_payment_when_it_has_one(self):
        self.plaid(interest_rate=6.0, monthly_payment=2_140.5, maturity_date="2052-05-01")
        t = loans.terms(self.conn, TODAY)["mtg"]
        self.assertEqual((t["payment"], t["source"]), (2_140.5, "plaid"))

    def test_a_maturity_already_past_or_garbled_falls_back_to_payments(self):
        self.payments("2026-07", "2026-08")
        for maturity in ("2020-01-01", "someday"):
            self.conn.execute(LoanTerms.__table__.delete())
            self.plaid(interest_rate=4.0, monthly_payment=0, maturity_date=maturity)
            t = loans.terms(self.conn, TODAY)["mtg"]
            self.assertEqual((t["payment"], t["source"]), (1_840.0, "inferred"))


class SellableTests(LedgerCase):
    def home(self, loan="mtg"):
        self.conn.execute(insert(Asset).values(name="House", kind="home", value=450_000, as_of=TODAY.isoformat(),
                                               yearly_change=3, loan_account_id=loan))

    def sellable(self, key_start):
        return next(a for a in planner.sellable(self.conn, TODAY) if a["key"].startswith(key_start))

    def test_a_home_with_a_loan_paid_down_on_its_terms(self):
        self.acct("mtg", "loan", -200_000, interest_rate=6.5, monthly_payment=1264.14)
        self.home()
        h = self.sellable("asset:")
        self.assertEqual((h["value"], h["owed"], h["yearly_change"]), (450_000, 200_000, 0.03))
        self.assertEqual(len(h["owed_by_year"]), 31)
        self.assertAlmostEqual(h["owed_by_year"][5], 187_221.68, places=2)
        self.assertEqual(h["owed_by_year"][-1], 0)
        self.assertEqual(h["loan"], {"rate": 6.5, "payment": 1264.14, "source": "manual", "note": None, "account_id": "mtg",
                                     "payment_counted": None, "payoff_year": 2056})

    def test_a_home_whose_loan_has_no_rate_keeps_todays_balance(self):
        self.acct("mtg", "loan", 200_000, owed_positive=1)
        self.home()
        h = self.sellable("asset:")
        self.assertEqual((h["owed"], h["owed_by_year"], h["loan"]["note"]), (200_000, [200_000], "no_rate"))

    def test_a_home_without_a_loan(self):
        self.home(loan=None)
        h = self.sellable("asset:")
        self.assertEqual((h["owed"], h["owed_by_year"], h["loan"]), (0, [0], None))

    def test_a_home_against_an_account_no_longer_a_loan_keeps_todays_balance(self):
        self.acct("mtg", "credit", -5_000, interest_rate=6.5, monthly_payment=1_000)
        self.home()
        h = self.sellable("asset:")
        self.assertEqual((h["owed"], h["owed_by_year"], h["loan"]), (5_000, [5_000], None))

    def test_loans_against_nothing_are_in_the_plan_as_debts(self):
        self.acct("mtg", "loan", -200_000, interest_rate=6.5, monthly_payment=1264.14)
        self.home()
        self.acct("stu", "loan", -12_000, name="Student Loan", org="Nelnet", interest_rate=5, monthly_payment=400)
        self.acct("exp", "loan", -30_000, name="Car Loan", org="Lakeside Bank")
        self.acct("old", "loan", -5_000, hidden=1)
        self.acct("done", "loan", 0, interest_rate=4, monthly_payment=100)
        self.acct("chk", "checking", 0)
        for m in (4, 5, 6, 7, 8):
            self.tx("chk", f"2026-{m:02}-12", -400, "NELNET STUDENT LN PMT", "Education")
        plan = planner.sellable(self.conn, TODAY)
        self.assertEqual([a["key"] for a in plan], ["asset:1", "loan:exp", "loan:stu"])
        stu = next(a for a in plan if a["key"] == "loan:stu")
        self.assertEqual((stu["name"], stu["kind"], stu["value"], stu["owed"]), ("Student Loan", "loan", 0, 12_000))
        self.assertEqual(stu["owed_by_year"], loans.project(12_000, 5, 400)[0])
        self.assertEqual(stu["loan"], {"rate": 5, "payment": 400, "source": "manual", "note": None, "account_id": "stu",
                                       "payment_counted": True, "payoff_year": 2029})
        exp = next(a for a in plan if a["key"] == "loan:exp")
        self.assertEqual((exp["owed"], exp["owed_by_year"]), (30_000, [30_000]))
        self.assertEqual(exp["loan"], {"rate": None, "payment": None, "source": None, "note": "no_rate", "account_id": "exp",
                                       "payment_counted": None, "payoff_year": None})
        self.conn.execute(update(Transaction).where(Transaction.account_id == "chk").values(category="Transfer"))
        stu = next(a for a in planner.sellable(self.conn, TODAY) if a["key"] == "loan:stu")
        self.assertIs(stu["loan"]["payment_counted"], False)
        self.conn.execute(update(Account).where(Account.id == "stu").values(networth_hidden=1))
        self.assertNotIn("loan:stu", [a["key"] for a in planner.sellable(self.conn, TODAY)])
        with self.assertRaisesRegex(planner.PlanError, "Unknown asset"):
            planner.clean({**planner.default({"yearly_savings": 0, "annual_spending": 0, "expected_return": 0.05}, TODAY),
                           "assets": [{"key": "loan:exp", "sell_year": 2030}]}, TODAY)

    def test_equity_counts_what_will_have_vested(self):
        cid = equity.save_company(self.conn, {"name": "Acme", "share_price": 10})
        equity.save_grant(self.conn, cid, {"kind": "rsu", "quantity": 4800, "vest_start": "2025-09-15", "vest_months": 48,
                                           "cliff_months": 12})
        e = self.sellable("equity:")
        self.assertEqual(e["value_by_year"], [12_000, 24_000, 36_000, 48_000])
        self.assertEqual((e["value"], e["owed"], e["yearly_change"]), (12_000, 0, None))

    def test_equity_with_nothing_vested_yet_is_offered_if_it_will_vest(self):
        cid = equity.save_company(self.conn, {"name": "Startup", "share_price": 2})
        equity.save_grant(self.conn, cid, {"kind": "iso", "quantity": 1000, "strike": 0.5, "vest_start": "2026-09-01",
                                           "vest_months": 24, "cliff_months": 12})
        e = self.sellable("equity:")
        self.assertEqual(e["value_by_year"], [0, 750, 1_500])
        equity.save_company(self.conn, {"in_networth": False}, cid)
        self.assertFalse(any(a["key"].startswith("equity:") for a in planner.sellable(self.conn, TODAY)))
        equity.save_company(self.conn, {"name": "Unpriced"})
        self.assertFalse(any(a["key"].startswith("equity:") for a in planner.sellable(self.conn, TODAY)))

    def test_options_expiring_later_are_taken_as_exercised_first(self):
        cid = equity.save_company(self.conn, {"name": "Startup", "share_price": 2})
        equity.save_grant(self.conn, cid, {"kind": "iso", "quantity": 1000, "strike": 0.5, "vest_start": "2022-01-01",
                                           "vest_months": 48, "exercised": 200, "expires_on": "2028-06-30"})
        self.assertEqual(self.sellable("equity:")["value_by_year"], [1_600])

    def test_options_vest_only_until_they_expire_and_expired_ones_count_what_was_exercised(self):
        cid = equity.save_company(self.conn, {"name": "Startup", "share_price": 2})
        equity.save_grant(self.conn, cid, {"kind": "nso", "quantity": 1200, "strike": 1, "vest_start": "2025-10-01",
                                           "vest_months": 48, "expires_on": "2027-10-01"})
        by_year = self.sellable("equity:")["value_by_year"]
        self.assertEqual(by_year[-1], 600)
        self.assertEqual(len(by_year), 3)
        cid2 = equity.save_company(self.conn, {"name": "Oldco", "share_price": 2})
        equity.save_grant(self.conn, cid2, {"kind": "iso", "quantity": 1000, "strike": 0.5, "vest_start": "2018-01-01",
                                            "vest_months": 48, "exercised": 300, "expires_on": "2026-01-01"})
        old = next(a for a in planner.sellable(self.conn, TODAY) if a["name"] == "Oldco")
        self.assertEqual(old["value_by_year"], [600])

    def test_carta_grants_go_on_vesting_from_what_carta_reported(self):
        today = date(2026, 10, 1)
        self.conn.execute(insert(EquityCompany).values(id="c", name="Startup", share_price=12, in_networth=1, source="carta"))
        self.conn.execute(insert(EquityGrant), [
            {"id": "g1", "company_id": "c", "kind": "iso", "label": "ES-452", "granted_on": "2024-11-23", "quantity": 20619,
             "strike": 3.5, "vest_start": None, "vest_months": 48, "cliff_months": 12, "vest_every": 1, "exercised": 0,
             "vested_reported": 11168, "vested_reported_on": "2026-09-29", "expires_on": "2034-11-22", "source": "carta"},
            {"id": "g2", "company_id": "c", "kind": "iso", "label": "ES-858", "granted_on": "2026-01-23", "quantity": 3002,
             "strike": 4.78, "vest_start": "2026-01-23", "vest_months": 12, "cliff_months": 0, "vest_every": 1, "exercised": 0,
             "vested_reported": None, "vested_reported_on": None, "expires_on": "2036-01-23", "source": "carta"}])
        e = next(a for a in planner.sellable(self.conn, today) if a["key"] == "equity:c")
        by_year = e["value_by_year"]
        es858 = 3002 * (12 - 4.78)
        self.assertEqual(by_year[0], round(11168 * 8.5 + 3002 * 8 / 12 * 7.22, 2))
        self.assertEqual(len(by_year), 4)
        self.assertTrue(by_year[0] < by_year[1] < by_year[2] < by_year[3])
        self.assertEqual(by_year[3], round(20619 * 8.5 + es858, 2))
        self.assertAlmostEqual(by_year[1], (11168 + 9451 * 5154.75 / 11168.625) * 8.5 + es858, delta=0.05)
        g = equity.overview(self.conn, today)["companies"][0]["grants"][0]
        self.assertLess(equity.vested_later(g, date(2028, 11, 22), today), 20619)
        self.assertEqual(equity.vested_later(g, date(2028, 11, 23), today), 20619)
        g = {**g, "vested_reported": 5000}
        self.assertEqual(equity.vested_later(g, date(2028, 11, 23), today), 20619)
        self.assertGreaterEqual(equity.vested_later(g, date(2027, 10, 1), today), 5000)

    def test_a_grant_whose_schedule_cant_be_worked_out_stays_at_today(self):
        cid = equity.save_company(self.conn, {"name": "Acme", "share_price": 10})
        gid = equity.save_grant(self.conn, cid, {"kind": "rsu", "quantity": 10, "vest_start": "2024-01-01", "vest_months": 12})
        from runway.models import EquityGrant
        self.conn.execute(update(EquityGrant).where(EquityGrant.id == gid).values(vest_months=200000))
        self.assertEqual(equity.value_by_year(equity.overview(self.conn, TODAY)["companies"][0], date(2026, 9, 23)), [0])
