import os
import sys
import tempfile
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runway import db, portfolio, prices, sfinvest, simplefin  # noqa: E402

TODAY = date(2026, 9, 23)


def account(acct_id="wf1", name="Wealthfront Automated Investing", balance="3500.00", holdings=None, transactions=None):
    a = {"id": acct_id, "name": name, "org": {"name": "Wealthfront"}, "currency": "USD", "balance": balance,
         "balance-date": 1790000000, "transactions": transactions or []}
    if holdings is not None:
        a["holdings"] = holdings
    return a


VTI = {"id": "h1", "symbol": "VTI", "description": "Vanguard Total Stock Market ETF", "shares": "10",
       "market_value": "3000.00", "cost_basis": "2500.00", "purchase_price": "250", "currency": "USD"}
MMF = {"id": "h2", "symbol": "SPAXX", "description": "Fidelity Government Money Market", "shares": "200", "market_value": "200"}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "t.db")
        db.init(self.path)
        self.c = db.connect(self.path)

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def price(self, ticker, d, close):
        self.c.execute("INSERT INTO prices(ticker, date, close, adjclose) VALUES (?,?,?,?) "
                       "ON CONFLICT(ticker, date) DO UPDATE SET close=excluded.close, adjclose=excluded.adjclose", (ticker, d, close, close))


class CaptureTests(Base):
    def test_positions_cash_and_leftover(self):
        simplefin.store_payload(self.c, {"accounts": [account(holdings=[VTI, MMF])]}, TODAY)
        acct = self.c.execute("SELECT * FROM inv_accounts").fetchone()
        self.assertEqual((acct["id"], acct["source"], acct["institution"], acct["balance"]), ("sf:wf1", "simplefin", "Wealthfront", 3500.0))
        h = {r["security_id"]: dict(r) for r in self.c.execute("SELECT * FROM holdings")}
        self.assertEqual(h["sf:VTI"]["quantity"], 10)
        self.assertEqual(h["sf:VTI"]["cost_basis"], 2500)
        self.assertEqual(h["sf:SPAXX"]["value"], 200)
        self.assertEqual(h["sf:cash"]["value"], 300)   # balance not explained by positions
        cash = {r["id"]: r["is_cash"] for r in self.c.execute("SELECT id, is_cash FROM securities")}
        self.assertEqual((cash["sf:VTI"], cash["sf:SPAXX"], cash["sf:cash"]), (0, 1, 1))
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM holding_snapshots").fetchone()[0], 3)
        ov = portfolio.overview(self.c, "1Y", TODAY)
        self.assertEqual(ov["total"], 3500.0)
        self.assertEqual(ov["unrealized_gain"], 500.0)
        self.assertEqual(ov["accounts"][0]["institution_name"], "Wealthfront")

    def test_cost_from_per_share_purchase_price(self):
        h = dict(VTI); h.pop("cost_basis")
        simplefin.store_payload(self.c, {"accounts": [account(balance="3000", holdings=[h])]}, TODAY)
        self.assertEqual(self.c.execute("SELECT cost_basis FROM holdings").fetchone()[0], 2500)

    def test_regular_accounts_are_ignored_and_balance_only_investment_accounts_kept(self):
        simplefin.store_payload(self.c, {"accounts": [account("chk", "Checking", "900")]}, TODAY)
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM inv_accounts").fetchone()[0], 0)
        # Vestwell-style: marked as an investment account but SimpleFIN only sends a balance
        simplefin.store_payload(self.c, {"accounts": [account("vw", "Vestwell 401k", "12000")]}, TODAY)
        self.assertEqual(self.c.execute("SELECT kind FROM accounts WHERE id='vw'").fetchone()[0], "investment")
        rows = self.c.execute("SELECT security_id, value FROM holdings WHERE account_id='sf:vw'").fetchall()
        self.assertEqual([tuple(r) for r in rows], [("sf:balance", 12000.0)])
        # switching it to another type removes it from Investments
        self.c.execute("UPDATE accounts SET kind='savings' WHERE id='vw'")
        simplefin.store_payload(self.c, {"accounts": [account("vw", "Vestwell 401k", "12000")]}, TODAY)
        self.assertIsNone(self.c.execute("SELECT 1 FROM inv_accounts WHERE id='sf:vw'").fetchone())

    def test_activity_and_income_from_transactions(self):
        txs = [{"id": "d1", "posted": 1788000000, "amount": "12.34", "description": "DIVIDEND RECEIVED VTI"},
               {"id": "d2", "posted": 1788100000, "amount": "-1000", "description": "YOU BOUGHT VTI"},
               {"id": "d3", "posted": 1788200000, "amount": "-2.50", "description": "Advisory fee"}]
        simplefin.store_payload(self.c, {"accounts": [account(holdings=[VTI], transactions=txs)]}, date(2026, 8, 1))
        act = {a["name"]: a for a in portfolio.activity(self.c)}
        self.assertEqual((act["DIVIDEND RECEIVED VTI"]["subtype"], act["DIVIDEND RECEIVED VTI"]["amount"]), ("dividend", -12.34))
        self.assertEqual(act["YOU BOUGHT VTI"]["type"], "buy")
        self.assertEqual(act["Advisory fee"]["type"], "fee")
        inc = portfolio.income(self.c, TODAY)
        self.assertEqual((inc["income_12m"], inc["fees_12m"]), (12.34, 2.5))

    def test_security_type_from_price_service(self):
        simplefin.store_payload(self.c, {"accounts": [account(holdings=[VTI])]}, TODAY)
        self.c.execute("INSERT INTO price_meta(ticker, fetched_at, ok, splits, instrument_type) VALUES ('VTI','2026-09-23',1,'[]','ETF')")
        prices.fill_security_types(self.c)
        self.assertEqual(portfolio.holdings(self.c)[0]["asset_class"], "ETFs")


class BadFeedTests(Base):
    """E*Trade via SimpleFIN: some positions carry the day's change instead of the market value, a zero cost basis
    and scraped page text as the name."""

    def test_wrong_values_are_fixed_from_market_prices(self):
        self.price("PAYX", "2026-09-22", 104.49)
        self.price("MSFT", "2026-09-22", 500.59)
        holdings = [
            {"symbol": "PAYX", "description": "Paychex Inc", "shares": "29", "market_value": "-291.16", "cost_basis": "0"},
            {"symbol": "MSFT", "description": "keyboard_arrow_right MSFT info_outline Trade keyboard_arrow_down",
             "shares": "25", "market_value": "12514.75", "cost_basis": "0", "purchase_price": "0"},
        ]
        # balance = 29 x 104.49 + 12514.75 + $14 of real cash
        simplefin.store_payload(self.c, {"accounts": [account("et", "Individual Brokerage", "15558.96", holdings)]}, TODAY)
        h = {r["security_id"]: dict(r) for r in self.c.execute("SELECT * FROM holdings")}
        self.assertAlmostEqual(h["sf:PAYX"]["value"], 3030.21, places=2)
        self.assertAlmostEqual(h["sf:cash"]["value"], 14.0, places=2)       # not $3,000+ of phantom cash
        self.assertIsNone(h["sf:PAYX"]["cost_basis"])                      # 0 means "not reported"
        self.assertIsNone(self.c.execute("SELECT name FROM securities WHERE id='sf:MSFT'").fetchone()[0])
        self.c.execute("INSERT INTO price_meta(ticker, fetched_at, ok, splits, long_name) VALUES ('MSFT','x',1,'[]','Microsoft Corporation')")
        prices.fill_security_types(self.c)
        self.assertEqual(self.c.execute("SELECT name FROM securities WHERE id='sf:MSFT'").fetchone()[0], "Microsoft Corporation")
        # today's value matches what the past days are computed from, so there's no cliff at the end of the chart
        hist = portfolio.history(self.c, TODAY, days=5)
        self.assertAlmostEqual(hist["value"][-1], hist["value"][-2], delta=1.0)

    def test_values_rechecked_when_prices_arrive_later(self):
        holdings = [{"symbol": "PAYX", "shares": "29", "market_value": "-291.16", "description": "Paychex Inc"}]
        simplefin.store_payload(self.c, {"accounts": [account("et", "Individual Brokerage", "3044.21", holdings)]}, TODAY)
        self.price("PAYX", "2026-09-22", 104.49)   # first sync had no price yet
        sfinvest.recapture_all(self.c, TODAY)
        h = {r["security_id"]: r["value"] for r in self.c.execute("SELECT * FROM holdings")}
        self.assertAlmostEqual(h["sf:PAYX"], 3030.21, places=2)
        self.assertAlmostEqual(h["sf:cash"], 14.0, places=2)

    def test_checking_account_with_money_market_sweep_is_not_an_investment(self):
        sweep = [{"symbol": "SPAXX", "description": "Fidelity Government Money Market", "shares": "4279.88", "market_value": "4279.88"}]
        simplefin.store_payload(self.c, {"accounts": [account("fid", "Joint Checking", "4279.88", sweep)]}, TODAY)
        self.assertIsNone(self.c.execute("SELECT 1 FROM inv_accounts").fetchone())
        # while a new account holding real securities becomes an investment account even with a plain name
        simplefin.store_payload(self.c, {"accounts": [account("wf2", "Individual (7J4V)", "3000", [VTI])]}, TODAY)
        self.assertEqual(self.c.execute("SELECT kind FROM accounts WHERE id='wf2'").fetchone()[0], "investment")


class CostBasisTests(Base):
    def test_manual_cost_basis_survives_syncs_and_feeds_gains(self):
        h = dict(VTI); h["cost_basis"] = "0"
        simplefin.store_payload(self.c, {"accounts": [account(balance="3000", holdings=[h])]}, TODAY)
        from runway import server
        self.assertEqual(portfolio.overview(self.c, "1Y", TODAY)["cost_missing"], 1)
        server.api_cost_basis(self.c, None, {"account_id": "sf:wf1", "security_id": "sf:VTI", "per_share": "240"})
        simplefin.store_payload(self.c, {"accounts": [account(balance="3000", holdings=[h])]}, TODAY)   # next sync
        ov = portfolio.overview(self.c, "1Y", TODAY)
        vti = ov["holdings"][0]
        self.assertEqual((vti["cost_basis"], vti["gain"], vti["cost_manual"]), (2400.0, 600.0, True))
        self.assertEqual(vti["lots"][0]["reported_cost_basis"], None)
        self.assertEqual((ov["unrealized_gain"], ov["cost_missing"]), (600.0, 0))
        # per-share price scales with the shares held: 12 shares after buying 2 more
        h12 = dict(h); h12["shares"] = "12"; h12["market_value"] = "3600"
        simplefin.store_payload(self.c, {"accounts": [account(balance="3600", holdings=[h12])]}, TODAY)
        self.assertEqual(portfolio.overview(self.c, "1Y", TODAY)["holdings"][0]["cost_basis"], 2880.0)
        # older total-dollar entries still work
        server.api_cost_basis(self.c, None, {"account_id": "sf:wf1", "security_id": "sf:VTI", "cost_basis": "3000"})
        self.assertEqual(portfolio.overview(self.c, "1Y", TODAY)["holdings"][0]["cost_basis"], 3000.0)
        server.api_cost_basis(self.c, None, {"account_id": "sf:wf1", "security_id": "sf:VTI", "per_share": ""})
        self.assertIsNone(portfolio.overview(self.c, "1Y", TODAY)["holdings"][0]["gain"])

    def test_day_change_percent(self):
        simplefin.store_payload(self.c, {"accounts": [account(balance="3000", holdings=[VTI])]}, TODAY)
        self.price("VTI", "2026-09-22", 300)
        self.price("VTI", "2026-09-23", 303)
        ov = portfolio.overview(self.c, "1Y", TODAY)
        self.assertEqual(ov["day_change"], 30.0)
        self.assertAlmostEqual(ov["day_change_pct"], 30 / 2970, places=6)


class RepairTests(Base):
    def test_startup_repair_of_old_data(self):
        # what the previous version stored: junk name, day's change as value, zero cost basis, phantom cash
        self.c.execute("INSERT INTO accounts(id, name, kind, balance) VALUES ('et', 'Individual Brokerage', 'investment', 3044.21)")
        self.c.execute("INSERT INTO inv_accounts(id, item_id, name, balance, source, institution) VALUES ('sf:et','simplefin','Individual Brokerage',3044.21,'simplefin','E*Trade')")
        self.c.execute("INSERT INTO securities(id, ticker, name, is_cash) VALUES ('sf:PAYX','PAYX','keyboard_arrow_right PAYX info_outline Trade keyboard_arrow_down',0), ('sf:cash',NULL,'Cash',1)")
        self.c.execute("INSERT INTO holdings(account_id, security_id, quantity, price, value, cost_basis) VALUES ('sf:et','sf:PAYX',29,-10.04,-291.16,0), ('sf:et','sf:cash',3335.37,1,3335.37,0)")
        self.price("PAYX", "2026-09-22", 104.49)
        self.assertEqual(sfinvest.repair_stored(self.c, TODAY), 1)
        h = {r["security_id"]: dict(r) for r in self.c.execute("SELECT * FROM holdings")}
        self.assertAlmostEqual(h["sf:PAYX"]["value"], 3030.21, places=2)
        self.assertAlmostEqual(h["sf:cash"]["value"], 14.0, places=2)
        self.assertIsNone(h["sf:PAYX"]["cost_basis"])
        self.assertIsNone(self.c.execute("SELECT name FROM securities WHERE id='sf:PAYX'").fetchone()[0])
        self.assertEqual(sfinvest.repair_stored(self.c, TODAY), 0)   # only once: the feed is kept from now on


class SnapshotHistoryTests(Base):
    def snap(self, d, qty, cash):
        self.c.execute("INSERT INTO holding_snapshots VALUES (?, 'sf:wf1', 'sf:VTI', ?, ?) "
                       "ON CONFLICT(date, account_id, security_id) DO UPDATE SET quantity=excluded.quantity, value=excluded.value", (d, qty, qty * 100))
        self.c.execute("INSERT INTO holding_snapshots VALUES (?, 'sf:wf1', 'sf:cash', ?, ?) "
                       "ON CONFLICT(date, account_id, security_id) DO UPDATE SET quantity=excluded.quantity, value=excluded.value", (d, cash, cash))

    def test_returns_ignore_deposits_and_estimate_before_first_snapshot(self):
        self.c.execute("INSERT INTO accounts(id, name, kind) VALUES ('wf1', 'Wealthfront', 'investment')")
        sfinvest.capture(self.c, account(holdings=[VTI]), "wf1", "Wealthfront", 3000.0, TODAY)
        self.c.execute("DELETE FROM holding_snapshots")
        # Sep 1: 10 VTI + $0. Sep 10: deposited $1,210 and bought 10 more at $121. Prices: 100 -> 110 -> 121.
        self.snap("2026-09-01", 10, 0)
        self.snap("2026-09-10", 20, 0)
        self.price("VTI", "2026-08-25", 100)
        self.price("VTI", "2026-09-05", 110)
        self.price("VTI", "2026-09-10", 121)
        self.c.execute("UPDATE holding_snapshots SET value=quantity*121 WHERE date='2026-09-10' AND security_id='sf:VTI'")
        h = portfolio.history(self.c, TODAY, days=40)
        at = lambda key, d: h[key][h["dates"].index(d)]
        self.assertEqual(h["estimated_before"], "2026-09-01")
        self.assertEqual(at("value", "2026-08-25"), 1000.0)          # estimate: Sep 1 positions at the older price
        self.assertEqual(at("value", "2026-09-05"), 1100.0)
        self.assertAlmostEqual(at("flows", "2026-09-10"), 1210.0)     # the 10 new shares count as money added
        self.assertAlmostEqual(h["twr"][-1], 0.21, places=6)          # 100 -> 121 is +21%, deposits excluded
        self.assertAlmostEqual(h["invested"][-1] - h["invested"][0], 1210.0)


    def test_balance_only_snapshots_give_way_to_real_positions(self):
        # Snapshots from before you entered a 401(k)'s funds hold only its balance; the later positions carry history.
        self.c.execute("INSERT INTO accounts(id, name, kind) VALUES ('wf1', 'Wealthfront', 'investment')")
        sfinvest.capture(self.c, account(holdings=[VTI]), "wf1", "Wealthfront", 3000.0, TODAY)
        self.c.execute("DELETE FROM holding_snapshots")
        self.c.execute("INSERT INTO holding_snapshots VALUES ('2026-09-01', 'sf:wf1', 'sf:balance', 0, 2000)")
        self.snap("2026-09-10", 20, 0)
        self.price("VTI", "2026-08-25", 100)
        self.price("VTI", "2026-09-10", 121)
        h = portfolio.history(self.c, TODAY, days=40)
        at = lambda key, d: h[key][h["dates"].index(d)]
        self.assertEqual(h["estimated_before"], "2026-09-10")
        self.assertEqual(at("value", "2026-09-01"), 2000.0)          # 20 VTI at $100, not a flat balance
        self.assertAlmostEqual(h["twr"][-1], 0.21, places=6)


class PlaidDuplicateTests(Base):
    def test_linking_through_plaid_hides_the_simplefin_copy(self):
        from runway import plaid
        simplefin.store_payload(self.c, {"accounts": [account("vw", "Retirement Savings 401k", "333069.97"),
                                                      account("wf", "Roth IRA", "4935.94", [VTI]),
                                                      account("et", "Individual Brokerage", "154756.49", [VTI])]}, TODAY)
        for iid, inst in (("sf:vw", "Vestwell"), ("sf:wf", "Wealthfront Anthony"), ("sf:et", "E*Trade")):
            self.c.execute("UPDATE inv_accounts SET institution=? WHERE id=?", (inst, iid))
        self.c.execute("INSERT INTO plaid_items(item_id, access_token, institution_name) VALUES ('it', 'tok', 'Vestwell'), ('it2', 'tok2', 'E*TRADE from Morgan Stanley')")
        self.assertEqual(plaid.hide_simplefin_duplicates(self.c, "it"), ["Retirement Savings 401k"])
        self.assertEqual(plaid.hide_simplefin_duplicates(self.c, "it2"), ["Individual Brokerage"])
        hidden = dict(self.c.execute("SELECT id, hidden FROM inv_accounts").fetchall())
        self.assertEqual((hidden["sf:vw"], hidden["sf:wf"], hidden["sf:et"]), (1, 0, 1))
        self.assertEqual(self.c.execute("SELECT hidden FROM accounts WHERE id='vw'").fetchone()[0], 0)   # net worth unaffected



class TrackedHoldingsTests(Base):
    """A Vestwell-style 401(k): SimpleFIN only sends the balance; you enter funds and a contribution election."""

    def setUp(self):
        super().setUp()
        from runway import tracked
        self.tracked = tracked
        simplefin.store_payload(self.c, {"accounts": [account("vw", "Vestwell 401k", "20000")]}, TODAY)
        self.c.execute("UPDATE accounts SET balance_date='2026-09-22' WHERE id='vw'")
        self.price("FXAIX", "2026-09-22", 200.0)
        self.price("VTSAX", "2026-09-22", 100.0)

    def sync(self, balance, day):
        self.c.execute("UPDATE accounts SET balance=?, balance_date=? WHERE id='vw'", (balance, day))
        sfinvest.capture(self.c, account("vw", "Vestwell 401k", str(balance)), "vw", "Vestwell", balance, date.fromisoformat(day))
        return {r["security_id"]: r for r in self.c.execute("SELECT * FROM holdings WHERE account_id='sf:vw'")}

    def test_contributions_buy_shares_per_election(self):
        self.tracked.save(self.c, "sf:vw", [{"ticker": "fxaix", "shares": 60, "pct": 70}, {"ticker": "VTSAX", "shares": 80, "pct": 30}])
        h = self.sync(20000, "2026-09-22")                        # 60*200 + 80*100 = 20,000 exactly
        self.assertEqual((round(h["man:FXAIX"]["value"], 2), round(h["man:VTSAX"]["value"], 2)), (12000.0, 8000.0))
        self.assertNotIn("sf:balance", h)
        # a $1,000 paycheck contribution lands; prices unchanged
        h = self.sync(21000, "2026-09-22")
        self.assertAlmostEqual(h["man:FXAIX"]["quantity"], 60 + 700 / 200)
        self.assertAlmostEqual(h["man:VTSAX"]["quantity"], 80 + 300 / 100)
        self.assertEqual(self.c.execute("SELECT amount FROM manual_contributions").fetchone()[0], 1000.0)
        # syncing again doesn't count it twice
        self.sync(21000, "2026-09-22")
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM manual_contributions").fetchone()[0], 1)
        # market moves are market moves, not contributions
        self.price("FXAIX", "2026-09-23", 210.0)
        h = self.sync(round(63.5 * 210 + 83 * 100, 2), "2026-09-23")
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM manual_contributions").fetchone()[0], 1)
        self.assertNotIn("sf:unexplained", h)
        # the contribution shows up as money added in history, not as a gain
        hist = portfolio.history(self.c, TODAY, days=3)
        self.assertEqual(round(portfolio.overview(self.c, "1M", TODAY)["total"], 2), round(63.5 * 210 + 83 * 100, 2))

    def test_drift_and_funds_without_ticker(self):
        # A collective trust with no ticker: enter its value; it absorbs what the priced fund doesn't explain.
        self.tracked.save(self.c, "sf:vw", [{"ticker": "FXAIX", "shares": 50, "pct": 50}, {"name": "Stable Value CIT", "value": 10000, "pct": 50}])
        h = self.sync(20000, "2026-09-22")
        self.assertEqual(round(h["man:stablevaluecit"]["value"], 2), 10000.0)
        # a drop the funds don't explain (a fee, a loan) is not bought or sold; it's absorbed by the untickered fund
        h = self.sync(19800, "2026-09-22")
        self.assertEqual(round(h["man:stablevaluecit"]["value"], 2), 9800.0)
        # with only priced funds, an unexplained drop is shown as a difference and reported as drift
        self.tracked.save(self.c, "sf:vw", [{"ticker": "FXAIX", "shares": 100, "pct": 100}])
        h = self.sync(19000, "2026-09-22")
        self.assertEqual(round(h["sf:unexplained"]["value"], 2), -1000.0)
        self.assertAlmostEqual(self.c.execute("SELECT drift FROM manual_state WHERE account_id='sf:vw'").fetchone()[0], 1000 / 19000, places=4)

    def test_starting_gap_is_a_baseline_not_a_contribution(self):
        # Shares from a statement that predates the last paycheck: $1,200 short of the balance.
        self.tracked.save(self.c, "sf:vw", [{"ticker": "FXAIX", "shares": 94, "pct": 100}])   # 18,800
        h = self.sync(20000, "2026-09-22")
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM manual_contributions").fetchone()[0], 0)
        self.assertEqual(round(h["sf:unexplained"]["value"], 2), 1200.0)
        h = self.sync(21000, "2026-09-22")                        # next paycheck: only the new $1,000 is invested
        self.assertEqual(self.c.execute("SELECT amount FROM manual_contributions").fetchone()[0], 1000.0)
        self.assertAlmostEqual(h["man:FXAIX"]["quantity"], 99.0)

    def test_baseline_waits_for_prices(self):
        self.tracked.save(self.c, "sf:vw", [{"ticker": "NEWFUND", "shares": 90, "pct": 100}])
        self.sync(20000, "2026-09-22")                            # no price yet for NEWFUND
        self.price("NEWFUND", "2026-09-22", 200.0)                 # 18,000: a $2,000 gap at entry
        self.sync(20000, "2026-09-22")
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM manual_contributions").fetchone()[0], 0)

    def test_validation(self):
        for rows in ([{"ticker": "FXAIX", "shares": 10, "pct": 60}, {"ticker": "VTSAX", "shares": 5, "pct": 30}],
                     [{"name": "Trust", "shares": 5, "pct": 100}], [{"ticker": "FXAIX", "shares": -1, "pct": 100}]):
            with self.assertRaises(ValueError):
                self.tracked.save(self.c, "sf:vw", rows)


class MergeByTickerTests(Base):
    def test_same_fund_in_two_accounts_is_one_row(self):
        from runway import tracked
        simplefin.store_payload(self.c, {"accounts": [account("wf", "Roth IRA", "3000", [VTI]), account("vw", "Vestwell 401k", "5000")]}, TODAY)
        self.price("VTI", "2026-09-22", 300.0)
        self.c.execute("UPDATE accounts SET balance_date='2026-09-22' WHERE id='vw'")
        tracked.save(self.c, "sf:vw", [{"ticker": "VTI", "shares": 10, "pct": 100}])
        sfinvest.capture(self.c, account("vw", "Vestwell 401k", "5000"), "vw", "Vestwell", 5000.0, TODAY)
        vti = [h for h in portfolio.holdings(self.c) if h["ticker"] == "VTI"]
        self.assertEqual(len(vti), 1)
        self.assertEqual((vti[0]["quantity"], len(vti[0]["lots"])), (20.0, 2))
        self.assertEqual(sorted(l["security_id"] for l in vti[0]["lots"]), ["man:VTI", "sf:VTI"])
