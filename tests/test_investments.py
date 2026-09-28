import json
import os
import sys
import tempfile
import threading
import unittest
from unittest import mock
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runway import db, plaid, portfolio, prices  # noqa: E402

TODAY = date(2026, 9, 23)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "t.db")
        db.init(self.path)
        self.c = db.connect(self.path)
        self.c.execute("INSERT INTO plaid_items(item_id, access_token, institution_name) VALUES ('it1','tok','Fidelity')")
        self.c.execute("INSERT INTO inv_accounts(id, item_id, name, type, subtype, balance) VALUES ('A','it1','Brokerage','investment','brokerage',0)")
        self.c.executemany("INSERT INTO securities(id, ticker, name, type, is_cash) VALUES (?,?,?,?,?)", [
            ("VTI", "VTI", "Vanguard Total Stock Market ETF", "etf", 0),
            ("SPAXX", "SPAXX", "Fidelity Government Money Market", "mutual fund", 1),
            ("XYZ", "XYZ", "XYZ Corp", "equity", 0),
        ])

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def tx(self, id, d, type_, sub, amount, sec=None, qty=0, price=None, fees=0):
        self.c.execute("INSERT INTO inv_transactions(id, account_id, security_id, date, name, type, subtype, quantity, amount, price, fees) "
                       "VALUES (?,?,?,?,?,?,?,?,?,?,?)", (id, "A", sec, d, id, type_, sub, qty, amount, price, fees))

    def price(self, ticker, d, close, adj=None):
        self.c.execute("INSERT INTO prices(ticker, date, close, adjclose) VALUES (?,?,?,?) "
                       "ON CONFLICT(ticker, date) DO UPDATE SET close=excluded.close, adjclose=excluded.adjclose", (ticker, d, close, adj or close))


class HistoryTests(Base):
    def setUp(self):
        super().setUp()
        # Deposit $3,000, buy 10 VTI at $250, get a $20 dividend. Today: 10 VTI worth $3,000 + $520 cash.
        self.tx("t1", "2026-06-01", "cash", "deposit", -3000)
        self.tx("t2", "2026-06-02", "buy", "buy", 2500, "VTI", 10, 250)
        self.tx("t3", "2026-08-15", "cash", "dividend", -20, "VTI")
        self.c.execute("INSERT INTO holdings(account_id, security_id, quantity, price, value, cost_basis) VALUES ('A','VTI',10,300,3000,2500)")
        self.c.execute("INSERT INTO holdings(account_id, security_id, quantity, price, value, cost_basis) VALUES ('A','SPAXX',520,1,520,520)")
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
        self.c.execute("UPDATE holdings SET value=120, quantity=120 WHERE security_id='SPAXX'")
        h = portfolio.history(self.c, TODAY)
        self.assertEqual(self.at(h, "2026-08-31"), 3520.0)
        self.assertEqual(self.at(h, "2026-09-01"), 3120.0)
        self.assertAlmostEqual(h["twr"][-1], 3520 / 3000 - 1, places=6)  # unchanged by taking money out

    def test_hidden_accounts_excluded(self):
        self.c.execute("UPDATE inv_accounts SET hidden=1")
        self.assertEqual(portfolio.overview(self.c, "1Y", TODAY)["total"], 0)


class SplitTests(Base):
    def test_split_does_not_jump(self):
        # 10 XYZ at $200, 2-for-1 split on Jul 1, now 20 at $100. Yahoo's closes are split-adjusted (100 throughout).
        self.tx("s0", "2026-06-10", "buy", "buy", 2000, "XYZ", 10, 200)
        self.tx("s1", "2026-07-01", "transfer", "split", 0, "XYZ", 10)
        self.c.execute("INSERT INTO holdings(account_id, security_id, quantity, price, value, cost_basis) VALUES ('A','XYZ',20,100,2000,2000)")
        self.c.execute("INSERT INTO holdings(account_id, security_id, quantity, price, value) VALUES ('A','SPAXX',0,1,0)")
        self.tx("s_dep", "2026-06-09", "cash", "deposit", -2000)
        for d in ("2026-06-09", "2026-06-30", "2026-07-01", "2026-09-22"):
            self.price("XYZ", d, 100)
        self.c.execute("INSERT INTO price_meta(ticker, fetched_at, ok, splits) VALUES ('XYZ', ?, 1, ?)",
                       (datetime.now().isoformat(), json.dumps([["2026-07-01", 2.0]])))
        h = portfolio.history(self.c, TODAY)
        vals = {d: v for d, v in zip(h["dates"], h["value"])}
        self.assertEqual(vals["2026-06-30"], 2000.0)
        self.assertEqual(vals["2026-07-01"], 2000.0)
        self.assertEqual(h["flows"][h["dates"].index("2026-07-01")], 0.0)
        self.assertAlmostEqual(h["twr"][-1], 0.0, places=6)


class XrayTests(Base):
    def test_rules(self):
        self.c.execute("INSERT INTO holdings(account_id, security_id, quantity, price, value, cost_basis) VALUES ('A','XYZ',10,100,6000,4000)")
        self.c.execute("INSERT INTO holdings(account_id, security_id, quantity, price, value) VALUES ('A','SPAXX',4000,1,4000)")
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
        self.c.execute("INSERT INTO holdings(account_id, security_id, quantity, price, value, cost_basis) VALUES ('A','VTI',10,300,3000,2500)")

    def fire(self):
        return portfolio.overview(self.c, "1Y", TODAY)["fire"]

    def test_saved_assumptions_win_and_can_be_reset(self):
        computed = self.fire()["computed"]
        portfolio.save_fire(self.c, {"annual_spending": 62000, "withdrawal_rate": 0.035})
        f = self.fire()
        self.assertEqual((f["annual_spending"], f["withdrawal_rate"]), (62000.0, 0.035))
        self.assertEqual(f["saved"], ["annual_spending", "withdrawal_rate"])
        self.assertEqual(f["expected_return"], computed["expected_return"])   # untouched ones stay Runway's
        self.assertEqual(f["computed"], computed)
        portfolio.save_fire(self.c, {"annual_spending": None, "withdrawal_rate": None})
        back = self.fire()
        self.assertEqual(back["saved"], [])
        self.assertEqual(back["annual_spending"], computed["annual_spending"])

    def test_nonsense_assumptions_are_refused(self):
        for bad in ({"withdrawal_rate": 4.0}, {"annual_spending": -1}, {"expected_return": "soon"}, {"nope": 1}):
            with self.assertRaises(ValueError):
                portfolio.save_fire(self.c, bad)
        self.assertEqual(portfolio.fire_saved(self.c), {})


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
        day = lambda d: int(datetime(d.year, d.month, d.day, 14, tzinfo=timezone.utc).timestamp())
        ts = [day(date(2026, 9, 21)), day(date(2026, 9, 22))]
        body = json.dumps({"chart": {"result": [{
            "meta": {"gmtoffset": -14400}, "timestamp": ts,
            "indicators": {"quote": [{"close": [100.0, 101.0]}], "adjclose": [{"adjclose": [99.0, 100.0]}]},
            "events": {"splits": {"1": {"date": day(date(2026, 3, 2)), "numerator": 4, "denominator": 1}}}}]}}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)


class SyncTests(unittest.TestCase):
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
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "t.db")
        db.init(self.path)
        self.c = db.connect(self.path)
        MockPlaid.calls.clear(); MockPlaid.login_required = False

    def tearDown(self):
        self.c.close(); self.tmp.cleanup()

    def test_link_exchange_sync_remove(self):
        with self.assertRaises(plaid.PlaidError):
            plaid.link_token(self.c)  # no keys yet
        db.set_setting(self.c, "plaid_client_id", "cid"); db.set_setting(self.c, "plaid_secret", "sec")
        self.assertEqual(plaid.link_token(self.c), "link-sandbox-123")
        self.assertEqual(MockPlaid.calls[-1][1]["products"], ["investments"])
        item = plaid.exchange(self.c, "public-1", {"name": "Fidelity", "institution_id": "ins_12"})
        res = plaid.sync_item(self.c, item, TODAY)
        self.assertEqual(res, {"accounts": 1, "holdings": 2, "transactions": 7})
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM inv_transactions").fetchone()[0], 7)
        tx_calls = [c for p, c in MockPlaid.calls if p == "/investments/transactions/get"]
        self.assertEqual([c["options"]["offset"] for c in tx_calls], [0, 3, 6])
        self.assertEqual(tx_calls[0]["start_date"], (TODAY - timedelta(days=plaid.HISTORY_DAYS)).isoformat())
        cash = self.c.execute("SELECT is_cash FROM securities WHERE id='s-cash'").fetchone()[0]
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
        self.assertEqual(self.c.execute("SELECT error FROM plaid_items").fetchone()[0], "ITEM_LOGIN_REQUIRED")
        plaid.remove_item(self.c, item)
        for table in ("plaid_items", "inv_accounts", "holdings", "inv_transactions"):
            self.assertEqual(self.c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0, table)

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
        import io, urllib.error
        from unittest import mock
        asked = []

        def limited(t, *_a):
            asked.append(t)
            raise urllib.error.HTTPError("https://prices", 429, "Too Many Requests", {}, io.BytesIO(b""))
        with mock.patch.object(prices, "fetch", side_effect=limited):
            res = prices.refresh(self.c, ["VTI", "VXUS", "BND"], date(2026, 1, 1))
        self.assertEqual((asked, res["failed"]), (["BND"], ["BND"]))   # stops at the first refusal
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM price_meta").fetchone()[0], 0)   # nothing held against them
        with mock.patch.object(prices, "fetch", side_effect=TimeoutError("timed out")):
            prices.refresh(self.c, ["VTI"], date(2026, 1, 1))
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM price_meta").fetchone()[0], 0)
        self.assertEqual(prices.refresh(self.c, ["VTI"], date(2026, 1, 1))["fetched"], ["VTI"])   # tried again next time


if __name__ == "__main__":
    unittest.main()


class DuplicateConnectionTests(unittest.TestCase):
    """The same login linked twice (two Wealthfront connections with the same accounts) is flagged, and refused at link."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "d.db")
        db.init(self.path)
        self.c = db.connect(self.path)

    def tearDown(self):
        self.c.close(); self.tmp.cleanup()

    def add(self, item, accounts, institution="Wealthfront", products="investments"):
        self.c.execute("INSERT INTO plaid_items(item_id, access_token, institution_id, institution_name, products) VALUES (?,?,?,?,?)",
                       (item, "tok", "ins_wf", institution, products))
        for i, (name, mask) in enumerate(accounts):
            self.c.execute("INSERT INTO inv_accounts(id, item_id, name, mask) VALUES (?,?,?,?)", (f"{item}-{i}", item, name, mask))

    def test_duplicates_are_found(self):
        kids = [("Roth IRA", "3639"), ("Oliver's 529 Account", "6624")]
        self.add("a", kids)
        self.add("b", kids)
        self.add("c", [("Individual", "1111")])            # a different Wealthfront login: fine
        self.assertEqual(plaid.duplicates(self.c, "a"), [{"item_id": "b", "shared": 2, "adds_nothing": True}])
        self.assertEqual(plaid.duplicates(self.c, "c"), [])
        self.add("d", kids + [("Joint", "2222")])           # overlaps, but brings a new account too
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


class InvestmentAccountsInYourAccountsTests(unittest.TestCase):
    """Investment accounts linked through Plaid show under Settings → Accounts and count in net worth, once."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "i.db")
        db.init(self.path)
        self.c = db.connect(self.path)
        self.c.execute("INSERT INTO plaid_items(item_id, access_token, institution_name, products) VALUES ('wf', 't', 'Wealthfront', 'investments')")

    def tearDown(self):
        self.c.close(); self.tmp.cleanup()

    def inv(self, id_, name, balance):
        self.c.execute("INSERT INTO inv_accounts(id, item_id, name, mask, balance) VALUES (?, 'wf', ?, '1234', ?)", (id_, name, balance))

    def sf(self, id_, name, balance, org="Wealthfront"):
        self.c.execute("INSERT INTO accounts(id, name, org, kind, balance) VALUES (?,?,?, 'investment', ?)", (id_, name, org, balance))

    def acct(self, id_):
        return self.c.execute("SELECT account_id FROM inv_accounts WHERE id=?", (id_,)).fetchone()[0]

    def test_its_own_account_when_simplefin_has_nothing_there(self):
        self.sf("et", "E*Trade", 1000, org="E*Trade")
        self.inv("a1", "Individual", 5000)
        plaid.update_investment_accounts(self.c, "wf")
        self.assertEqual(self.acct("a1"), "pl:a1")
        row = self.c.execute("SELECT name, kind, balance FROM accounts WHERE id='pl:a1'").fetchone()
        self.assertEqual((row["name"], row["kind"], row["balance"]), ("Individual ••1234", "investment", 5000))
        from runway import networth
        self.assertAlmostEqual(networth.summary(self.c)["assets"], 6000)      # counted in net worth (with E*Trade's 1000)
        self.c.execute("UPDATE inv_accounts SET balance=5100 WHERE id='a1'")
        plaid.update_investment_accounts(self.c, "wf")                       # balances follow each sync
        self.assertEqual(self.c.execute("SELECT balance FROM accounts WHERE id='pl:a1'").fetchone()[0], 5100)

    def test_hiding_its_account_in_settings_hides_it_on_investments(self):
        self.inv("a1", "Individual", 5000)
        plaid.update_investment_accounts(self.c, "wf")
        self.c.execute("UPDATE accounts SET hidden=1 WHERE id='pl:a1'")
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
        self.assertTrue(self.c.execute("SELECT 1 FROM accounts WHERE id='pl:new'").fetchone())
        plaid.match_investment(self.c, "new", "ignore")                      # changing your mind removes its entry
        self.assertIsNone(self.c.execute("SELECT 1 FROM accounts WHERE id='pl:new'").fetchone())
        with self.assertRaises(ValueError):
            plaid.match_investment(self.c, "529", "not-an-account")

    def test_a_matched_simplefin_account_shows_once_on_investments(self):
        self.sf("sf-roth", "Roth IRA", 4943.43)
        self.c.execute("INSERT INTO inv_accounts(id, item_id, name, balance, source) VALUES ('sf:sf-roth', 'sf', 'Roth IRA', 4943.43, 'simplefin')")
        self.inv("roth", "Roth IRA", 4943.43)
        ids = lambda: {a["id"] for a in portfolio.overview(self.c, "1Y", date.today())["accounts"] if not a["hidden"]}
        self.assertEqual(ids(), {"sf:sf-roth", "roth"})                     # not matched yet: both
        plaid.match_investment(self.c, "roth", "sf-roth")
        self.assertEqual(ids(), {"roth"})

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
        self.assertIsNone(self.c.execute("SELECT 1 FROM accounts WHERE id='pl:a1'").fetchone())
