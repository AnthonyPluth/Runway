import json
import os
import sys
import tempfile
import threading
import unittest
from datetime import date
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runway import db, networth, prices, rentcast  # noqa: E402

TODAY = date(2026, 9, 23)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "t.db")
        db.init(self.path)
        self.c = db.connect(self.path)
        rows = [("chk", "Checking", "checking", 4561.10, 0, 0), ("brk", "Brokerage", "investment", 154756.49, 0, 0),
                ("cc", "Sapphire", "credit", -1510.72, 0, 0), ("mtg", "Mortgage", "loan", -250000.0, 0, 0),
                ("auto", "Auto loan", "loan", 12000.0, 1, 0), ("old", "Closed card", "credit", -99.0, 0, 1)]
        self.c.executemany("INSERT INTO accounts(id, name, kind, balance, owed_positive, hidden) VALUES (?,?,?,?,?,?)", rows)

    def tearDown(self):
        self.c.close(); self.tmp.cleanup()


class NetWorthTests(Base):
    def test_totals_groups_equity_and_snapshots(self):
        home = networth.save_asset(self.c, {"name": "House", "kind": "home", "value": "$425,000", "loan_account_id": "mtg"}, today=TODAY)
        car = networth.save_asset(self.c, {"name": "Model Y", "kind": "vehicle", "value": 30000, "yearly_change": "-15",
                                           "loan_account_id": "auto"}, today=date(2025, 9, 23))
        s = networth.summary(self.c, TODAY)
        g = {x["key"]: x for x in s["groups"]}
        self.assertEqual(g["cash"]["total"], 4561.10)
        self.assertEqual(g["investments"]["total"], 154756.49)
        self.assertEqual(g["credit"]["total"], 1510.72)                  # hidden card left out
        self.assertEqual(g["loan"]["total"], 262000.0)                   # both sign conventions read as amounts owed
        self.assertAlmostEqual(g["vehicle"]["total"], 25500.0, delta=15)  # a year at -15%
        self.assertAlmostEqual(s["net"], 4561.10 + 154756.49 + 425000 + g["vehicle"]["total"] - 1510.72 - 262000, places=2)
        house = g["home"]["items"][0]
        self.assertEqual((house["equity"], house["loan"]["name"]), (175000.0, "Mortgage"))
        # a snapshot per day; change since 30 days needs an older snapshot
        self.assertIsNone(s["change"]["30d"])
        self.c.execute("INSERT INTO networth_snapshots(date, assets, liabilities, net) VALUES ('2026-08-20', 0, 0, 300000)")
        s2 = networth.summary(self.c, TODAY)
        self.assertEqual(s2["change"]["30d"], round(s2["net"] - 300000, 2))
        self.assertEqual(len(s2["history"]), 2)
        # updating the value keeps a history
        networth.save_asset(self.c, {"value": 440000}, home, today=date(2026, 10, 1))
        vals = self.c.execute("SELECT date, value FROM asset_values WHERE asset_id=? ORDER BY date", (home,)).fetchall()
        self.assertEqual([tuple(v) for v in vals], [("2026-09-23", 425000.0), ("2026-10-01", 440000.0)])
        networth.remove_asset(self.c, car)
        self.assertEqual(len(networth.assets(self.c, TODAY)), 1)

    def test_validation(self):
        for bad in ({"name": "", "kind": "home", "value": 1}, {"name": "X", "kind": "boat", "value": 1},
                    {"name": "X", "kind": "home", "value": "abc"}, {"name": "X", "kind": "home", "value": -5},
                    {"name": "X", "kind": "home", "value": 1, "loan_account_id": "chk"}):
            with self.assertRaises(ValueError):
                networth.save_asset(self.c, bad, today=TODAY)


class MockRentCast(BaseHTTPRequestHandler):
    calls = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        MockRentCast.calls.append((self.path, self.headers.get("X-Api-Key")))
        q = parse_qs(urlparse(self.path).query)
        if self.headers.get("X-Api-Key") != "rc-key":
            code, body = 401, {"message": "bad key"}
        elif "Nowhere" in q["address"][0]:
            code, body = 404, {"message": "not found"}
        else:
            code, body = 200, {"price": 431000, "priceRangeLow": 400000, "priceRangeHigh": 462000, "comparables": []}
        b = json.dumps(body).encode()
        self.send_response(code); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)


class RentCastTests(Base):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), MockRentCast)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        os.environ["RUNWAY_RENTCAST_URL"] = f"http://127.0.0.1:{cls.srv.server_port}/v1"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown(); os.environ.pop("RUNWAY_RENTCAST_URL", None)

    def test_lookup_refresh_and_limit(self):
        home = networth.save_asset(self.c, {"name": "House", "kind": "home", "value": 425000,
                                            "address": "1 Main St, Springfield, IL 62701", "auto_update": True}, today=date(2026, 8, 1))
        with self.assertRaises(rentcast.RentCastError):
            rentcast.refresh_asset(self.c, home, TODAY)           # no key yet
        db.set_setting(self.c, "rentcast_api_key", "rc-key")
        est = rentcast.refresh_asset(self.c, home, TODAY)
        self.assertEqual((est["value"], est["low"], est["high"]), (431000.0, 400000, 462000))
        a = self.c.execute("SELECT value, source, last_lookup FROM assets WHERE id=?", (home,)).fetchone()
        self.assertEqual(tuple(a), (431000.0, "rentcast", "2026-09-23"))
        self.assertIn("address=1+Main+St", MockRentCast.calls[-1][0])
        self.assertEqual(rentcast.used_this_month(self.c, TODAY), 1)
        # monthly refresh: not due yet, then due after 30 days
        self.assertEqual(rentcast.refresh_due(self.c, date(2026, 10, 1)), 0)
        self.assertEqual(rentcast.refresh_due(self.c, date(2026, 10, 24)), 1)
        # bad address and the free-plan cap
        networth.save_asset(self.c, {"address": "Nowhere"}, home, today=TODAY)
        with self.assertRaises(rentcast.RentCastError) as cm:
            rentcast.refresh_asset(self.c, home, TODAY)
        self.assertIn("couldn't find", str(cm.exception))
        db.set_setting(self.c, "rentcast_calls:2026-09", "50")
        n = len(MockRentCast.calls)
        with self.assertRaises(rentcast.RentCastError):
            rentcast.refresh_asset(self.c, home, TODAY)
        self.assertEqual(len(MockRentCast.calls), n)              # never called past the limit


class QuoteTests(unittest.TestCase):
    def test_market_state(self):
        q = {"open_start": 1000, "open_end": 2000}
        self.assertEqual(prices.market_state(q, 1500), "open")
        self.assertEqual(prices.market_state(q, 2500), "closed")
        self.assertEqual(prices.market_state(None, 1500), "closed")


class NewCategoryTests(Base):
    def test_ai_can_propose_and_create_a_category(self):
        from runway import categorize, server
        db.set_setting(self.c, "openrouter_api_key", "k")
        self.c.execute("INSERT INTO transactions(id, account_id, posted, amount, description, payee, needs_review) VALUES "
                       "('t1','chk','2026-09-10',-45,'PETSMART #123','Petsmart',1), ('t2','chk','2026-09-11',-12,'STARBUCKS','Starbucks',1),"
                       "('t3','chk','2026-09-12',-30,'CHEWY.COM','Chewy',1)")
        seen = {}

        def fake(key, model, prompt):
            seen["prompt"] = prompt
            items = []
            for line in prompt.splitlines():
                if line.startswith("[{"):
                    for it in json.loads(line):
                        if "PET" in it["description"]:
                            items.append({"i": it["i"], "category": None, "new_category": "Pets", "parent": "Nope", "confidence": 0.8})
                        elif "CHEWY" in it["description"]:
                            items.append({"i": it["i"], "category": None, "new_category": "pets", "confidence": 0.7})
                        else:
                            items.append({"i": it["i"], "category": None, "new_category": "Groceries", "confidence": 0.6})
            return json.dumps(items)

        sug = {x["merchant"]: x for x in categorize.suggest_for_review(self.c, caller=fake)}
        self.assertIn("new_category", seen["prompt"])
        self.assertEqual(sug["Petsmart"]["new_category"], {"name": "Pets", "parent": None})   # unknown parent dropped
        self.assertEqual((sug["Starbucks"]["category"], sug["Starbucks"]["new_category"]), ("Groceries", None))  # already exists
        r = server.api_ai_apply(self.c, None, {"tx_ids": sug["Petsmart"]["tx_ids"], "new_category": sug["Petsmart"]["new_category"], "direction": "out"})
        self.assertEqual((r["category"], r["created"], r["updated"]), ("Pets", True, 1))
        r2 = server.api_ai_apply(self.c, None, {"tx_ids": sug["Chewy"]["tx_ids"], "new_category": sug["Chewy"]["new_category"], "direction": "out"})
        self.assertEqual((r2["category"], r2["created"]), ("Pets", False))                   # second proposal reuses it
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM transactions WHERE category='Pets'").fetchone()[0], 2)
        # the automatic path during sync never gets to create categories
        self.assertNotIn("new_category", categorize.build_prompt(["A"], [], []))


class ReplyParsingTests(unittest.TestCase):
    def test_messy_replies(self):
        from runway import categorize
        cats = ["Groceries", "Restaurants"]
        think = '<think>Let me consider [the list]... maybe [{"i": 0, "category": "Restaurants"}]</think>\nHere you go:\n```json\n[{"i": 0, "category": "Groceries", "confidence": 0.9}]\n```'
        self.assertEqual(categorize.parse_ai_reply(think, cats), {0: ("Groceries", 0.9)})
        prose = 'Categories [as requested]: [{"i": 0, "category": "restaurants", "confidence": 0.8}] Hope that helps [1].'
        self.assertEqual(categorize.parse_ai_reply(prose, cats), {0: ("Restaurants", 0.8)})
        self.assertIsNone(categorize.extract_json_array("I can't help with that."))

    def test_unreadable_reply_is_an_error_not_silence(self):
        from runway import categorize
        tmp = tempfile.TemporaryDirectory(); path = os.path.join(tmp.name, "t.db"); db.init(path); c = db.connect(path)
        db.set_setting(c, "openrouter_api_key", "k"); db.set_setting(c, "llm_model", "openrouter/free")
        group = [[{"posted": "2026-09-01", "amount": -5, "kind": "credit", "payee": "X", "description": "X"}]]
        with self.assertRaises(RuntimeError) as cm:
            categorize.ask_model(c, group, caller=lambda k, m, p: "Sure! I'd categorize these as groceries.")
        self.assertIn("openrouter/free", str(cm.exception))
        self.assertIn("openrouter/free", db.get_setting(c, "last_llm_error"))
        c.close(); tmp.cleanup()
