"""Loans paid down on their terms, and what the retirement planner can sell: a home less what will still be owed on its
loan, and company equity as it will have vested."""
import unittest
from datetime import date

from sqlalchemy import insert, update

from runway import equity, loans, planner
from runway.models import Account, Asset, LoanTerms
from tests.shared import TODAY, LedgerCase


class AmortizationTests(unittest.TestCase):
    def test_a_30_year_mortgage_as_a_calculator_has_it(self):
        # $200,000 at 6.5% over 30 years: $1,264.14 a month, $187,221 left after 5 years, $0 after 30.
        payment = loans.payment_to_pay_off(200_000, 6.5, 360)
        self.assertAlmostEqual(payment, 1264.14, places=2)
        owed, short = loans.project(200_000, 6.5, 1264.14)
        self.assertFalse(short)
        self.assertEqual(len(owed), 31)                       # today, then each year until it's paid off
        self.assertEqual(owed[0], 200_000)
        self.assertAlmostEqual(owed[1], 197_764.50, places=2)
        self.assertAlmostEqual(owed[5], 187_221.68, places=2)  # B(1+r)^60 − P((1+r)^60 − 1)/r with P = 1,264.14
        self.assertEqual(owed[-1], 0)

    def test_paid_off_early_floors_at_zero(self):
        owed, short = loans.project(10_000, 5, 5_000)
        self.assertEqual(owed, [10_000, 0])
        self.assertFalse(short)
        self.assertEqual(loans.project(12_000, 0, 1_000)[0], [12_000, 0])   # interest-free: a year of payments
        self.assertEqual(loans.project(-50, 5, 100), ([-50], False))         # overpaid: nothing to project

    def test_a_payment_that_doesnt_cover_the_interest_is_flagged_not_grown(self):
        # 6% on $300,000 is $1,500 a month: paying $1,400 (or exactly $1,500) never pays it down.
        for payment in (1_400, 1_500, 0):
            self.assertEqual(loans.project(300_000, 6, payment), ([300_000], True))

    def test_the_year_of_the_last_payment(self):
        today, t = date(2026, 10, 1), lambda rate, payment: {"rate": rate, "payment": payment}
        # a payment a month from November: 2 is December 2026, 14 December 2027, 15 January 2028
        self.assertEqual(loans.payoff_year(2_000, t(0, 1_000), today), 2026)
        self.assertEqual(loans.payoff_year(14_000, t(0, 1_000), today), 2027)
        self.assertEqual(loans.payoff_year(14_001, t(0, 1_000), today), 2028)
        self.assertEqual(loans.payoff_year(50_000, t(6, 1_000), today), 2031)   # 58 payments: the last in August 2031
        self.assertEqual(loans.payoff_year(0, t(6, 1_000), today), 2026)        # already paid off
        for terms in (None, t(None, 1_000), t(6, None), t(6, 250), t(6, 0)):    # not projected, or never paid down
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
        # Sept is this month (left out); a double payment one month and a refund-like debit don't sway the median.
        self.payments("2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09")
        self.payments("2026-07", amount=1_840.0)
        self.tx("mtg", "2026-06-15", -45.0, "LATE FEE")
        self.tx("mtg", "2026-08-20", 300.0, "EXTRA", pending=1)          # pending: not yet
        self.tx("mtg", "2026-02-03", 9_999.0, "LONG AGO")                # before the last six whole months
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
        self.plaid(interest_rate=6.0, monthly_payment=None, maturity_date="2036-09-01")   # ten years from TODAY's month
        t = loans.terms(self.conn, TODAY)["mtg"]
        # Plaid's rate wins over yours; it sent no payment, so the one you set is used (and can be changed)
        self.assertEqual((t["rate"], t["payment"], t["source"], t["plaid"], t["plaid_payment"], t["maturity"]),
                         (6.0, 900, "manual", True, False, "2036-09-01"))
        self.assertEqual((t["set_rate"], t["set_payment"]), (3, 900))   # your rate is kept, just not used
        # With no payment set either, Plaid's payoff date gives the payment that pays it off by then
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
            self.plaid(interest_rate=4.0, monthly_payment=0, maturity_date=maturity)   # 0: in deferment
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
        # 360 payments from October 2026: the last in September 2056; no money out like it, so it isn't in spending
        self.assertEqual(h["loan"], {"rate": 6.5, "payment": 1264.14, "source": "manual", "note": None, "account_id": "mtg",
                                     "payment_counted": False, "payoff_year": 2056})

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
        self.acct("mtg", "credit", -5_000, interest_rate=6.5, monthly_payment=1_000)   # its type changed since
        self.home()
        h = self.sellable("asset:")
        self.assertEqual((h["owed"], h["owed_by_year"], h["loan"]), (5_000, [5_000], None))

    def test_equity_counts_what_will_have_vested(self):
        # 4,800 RSUs from 2025-09-15 over 4 years with a 1-year cliff: 1,200 vested on TODAY (2026-09-23), all by 2029.
        cid = equity.save_company(self.conn, {"name": "Acme", "share_price": 10})
        equity.save_grant(self.conn, cid, {"kind": "rsu", "quantity": 4800, "vest_start": "2025-09-15", "vest_months": 48,
                                           "cliff_months": 12})
        e = self.sellable("equity:")
        self.assertEqual(e["value_by_year"], [12_000, 24_000, 36_000, 48_000])
        self.assertEqual((e["value"], e["owed"], e["yearly_change"]), (12_000, 0, None))   # keeps pace with inflation

    def test_equity_with_nothing_vested_yet_is_offered_if_it_will_vest(self):
        cid = equity.save_company(self.conn, {"name": "Startup", "share_price": 2})
        equity.save_grant(self.conn, cid, {"kind": "iso", "quantity": 1000, "strike": 0.5, "vest_start": "2026-09-01",
                                           "vest_months": 24, "cliff_months": 12})
        e = self.sellable("equity:")
        self.assertEqual(e["value_by_year"], [0, 750, 1_500])   # half at the cliff, then the rest; $1.50 a share over the strike
        # Left out of net worth, or never worth anything (no share price): not offered.
        equity.save_company(self.conn, {"in_networth": False}, cid)
        self.assertFalse(any(a["key"].startswith("equity:") for a in planner.sellable(self.conn, TODAY)))
        equity.save_company(self.conn, {"name": "Unpriced"})
        self.assertFalse(any(a["key"].startswith("equity:") for a in planner.sellable(self.conn, TODAY)))

    def test_options_past_their_expiration_count_only_what_was_exercised(self):
        # 1,000 options at $0.50, all vested by TODAY; 200 exercised; they expire in 2028.
        cid = equity.save_company(self.conn, {"name": "Startup", "share_price": 2})
        equity.save_grant(self.conn, cid, {"kind": "iso", "quantity": 1000, "strike": 0.5, "vest_start": "2022-01-01",
                                           "vest_months": 48, "exercised": 200, "expires_on": "2028-06-30"})
        e = self.sellable("equity:")
        # Today and next year: 800 × $1.50 spread + 200 shares × $2; from 2028-09 on, only the 200 exercised shares.
        self.assertEqual(e["value_by_year"], [1_600, 1_600, 400])

    def test_a_grant_whose_schedule_cant_be_worked_out_stays_at_today(self):
        cid = equity.save_company(self.conn, {"name": "Acme", "share_price": 10})
        gid = equity.save_grant(self.conn, cid, {"kind": "rsu", "quantity": 10, "vest_start": "2024-01-01", "vest_months": 12})
        from runway.models import EquityGrant
        self.conn.execute(update(EquityGrant).where(EquityGrant.id == gid).values(vest_months=200000))
        self.assertEqual(equity.value_by_year(equity.overview(self.conn, TODAY)["companies"][0], date(2026, 9, 23)), [0])
