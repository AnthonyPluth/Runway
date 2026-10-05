import json
import os
import threading
import unittest
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from sqlalchemy import func, insert, select, update

from runway.storage import db
from runway.domain import loans, networth
from runway.providers import prices, realie
from runway.storage.models import Account, Asset, AssetValue, LoanTerms, NetworthSnapshot, Transaction
from tests.shared import DbCase, TODAY, own_database



class Base(DbCase):
    def setUp(self):
        super().setUp()
        rows = [("chk", "Checking", "checking", 4561.10, 0, 0), ("brk", "Brokerage", "investment", 98250.35, 0, 0),
                ("cc", "Sapphire", "credit", -1275.40, 0, 0), ("mtg", "Mortgage", "loan", -250000.0, 0, 0),
                ("auto", "Auto loan", "loan", 12000.0, 1, 0), ("old", "Closed card", "credit", -99.0, 0, 1)]
        self.c.execute(insert(Account), [dict(zip(("id", "name", "kind", "balance", "owed_positive", "hidden"), r, strict=True)) for r in rows])


class NetWorthTests(Base):
    def test_totals_groups_equity_and_snapshots(self):
        home = networth.save_asset(self.c, {"name": "House", "kind": "home", "value": "$425,000", "loan_account_id": "mtg"}, today=TODAY)
        car = networth.save_asset(self.c, {"name": "Model Y", "kind": "vehicle", "value": 30000, "yearly_change": "-15",
                                           "loan_account_id": "auto"}, today=date(2025, 9, 23))
        s = networth.summary(self.c, TODAY)
        g = {x["key"]: x for x in s["groups"]}
        self.assertEqual(g["cash"]["total"], 4561.10)
        self.assertEqual(g["investments"]["total"], 98250.35)
        self.assertEqual(g["credit"]["total"], 1275.40)
        self.assertEqual(g["loan"]["total"], 262000.0)
        self.assertAlmostEqual(g["vehicle"]["total"], 25500.0, delta=15)
        self.assertAlmostEqual(s["net"], 4561.10 + 98250.35 + 425000 + g["vehicle"]["total"] - 1275.40 - 262000, places=2)
        house = g["home"]["items"][0]
        self.assertEqual((house["equity"], house["loan"]["name"]), (175000.0, "Mortgage"))
        self.assertIsNone(s["change"]["30d"])
        self.c.execute(insert(NetworthSnapshot).values(date="2026-08-20", assets=0, liabilities=0, net=300000))
        s2 = networth.summary(self.c, TODAY)
        self.assertEqual(s2["change"]["30d"], round(s2["net"] - 300000, 2))
        self.assertEqual(len(s2["history"]), 2)
        networth.save_asset(self.c, {"value": 440000}, home, today=date(2026, 10, 1))
        vals = self.c.execute(select(AssetValue.date, AssetValue.value)
                              .where(AssetValue.asset_id == home).order_by(AssetValue.date)).fetchall()
        self.assertEqual([tuple(v) for v in vals], [("2026-09-23", 425000.0), ("2026-10-01", 440000.0)])
        networth.remove_asset(self.c, car)
        self.assertEqual(len(networth.assets(self.c, TODAY)), 1)

    def test_an_account_can_be_left_out_of_net_worth_only(self):
        from runway.server.api.accounts import api_account_update
        before = networth.summary(self.c, TODAY, save=False)
        api_account_update(self.c, {}, {"networth_hidden": 1}, "brk")
        after = networth.summary(self.c, TODAY, save=False)
        self.assertEqual(round(before["net"] - after["net"], 2), 98250.35)
        self.assertNotIn("investments", {g["key"] for g in after["groups"]})
        self.assertEqual(after["excluded"], [{"id": "brk", "name": "Brokerage", "org": None, "kind": "investment", "balance": 98250.35}])
        self.assertEqual(before["excluded"], [])
        api_account_update(self.c, {}, {"networth_hidden": 1}, "cc")
        api_account_update(self.c, {}, {"networth_hidden": 1}, "auto")
        api_account_update(self.c, {}, {"networth_hidden": 1}, "chk")
        bal = {a["id"]: a["balance"] for a in networth.summary(self.c, TODAY, save=False)["excluded"]}
        self.assertEqual(bal, {"brk": 98250.35, "cc": 1275.40, "auto": 12000.0, "chk": 4561.10})
        api_account_update(self.c, {}, {"networth_hidden": 0}, "auto")
        api_account_update(self.c, {}, {"networth_hidden": 0}, "chk")
        api_account_update(self.c, {}, {"networth_hidden": 0}, "cc")
        after = networth.summary(self.c, TODAY, save=False)
        api_account_update(self.c, {}, {"networth_hidden": 1}, "cc")
        self.assertEqual(round(networth.summary(self.c, TODAY, save=False)["net"] - after["net"], 2), 1275.40)
        self.assertEqual(self.c.execute(select(Account.hidden).where(Account.id == "cc")).fetchone()[0], 0)
        api_account_update(self.c, {}, {"networth_hidden": 1}, "old")
        self.assertEqual({a["id"] for a in networth.summary(self.c, TODAY, save=False)["excluded"]}, {"brk", "cc"})
        api_account_update(self.c, {}, {"networth_hidden": 0}, "brk")
        api_account_update(self.c, {}, {"networth_hidden": 0}, "cc")
        self.assertEqual(networth.summary(self.c, TODAY, save=False)["net"], before["net"])

    def test_change_says_which_snapshot_it_is_measured_from(self):
        self.c.execute(insert(NetworthSnapshot).values(date="2026-08-10", assets=0, liabilities=0, net=100000))
        s = networth.summary(self.c, TODAY, save=False)
        self.assertEqual(s["change"]["30d"], round(s["net"] - 100000, 2))
        self.assertEqual(s["change_since"], {"30d": "2026-08-10", "90d": None, "1y": None})
        self.assertEqual(s["change"]["90d"], None)

    def test_validation(self):
        for bad in ({"name": "", "kind": "home", "value": 1}, {"name": "X", "kind": "boat", "value": 1},
                    {"name": "X", "kind": "home", "value": "abc"}, {"name": "X", "kind": "home", "value": -5},
                    {"name": "X", "kind": "home", "value": 1, "loan_account_id": "chk"}):
            with self.assertRaises(ValueError):
                networth.save_asset(self.c, bad, today=TODAY)


class LoanTests(Base):
    """A loan with a rate and payment (Plaid's, yours, or inferred: loans.terms) is paid down month by month from its
    last balance, synced or not."""

    @staticmethod
    def by_hand(owed: float, rate: float, payment: float, months: int) -> float:
        for _ in range(months):
            owed = max(0.0, owed * (1 + rate / 1200) - payment)
        return owed

    def loan(self, **kw):
        return {"kind": "loan", "balance": -200000.0, "owed_positive": 0, "balance_date": "2025-09-23", **kw}

    @staticmethod
    def terms(rate, payment):
        return {"rate": rate, "payment": payment}

    def test_amortizes_from_the_balance_date(self):
        a, t = self.loan(), self.terms(6.0, 1199.10)
        self.assertAlmostEqual(loans.owed_on(a, t, TODAY), self.by_hand(200000, 6.0, 1199.10, 12), delta=0.01)
        self.assertEqual(loans.owed_on(a, t, date(2025, 9, 23)), 200000.0)
        self.assertEqual(loans.owed_on(a, t, date(2025, 10, 22)), 200000.0)
        self.assertAlmostEqual(loans.owed_on(a, t, date(2025, 10, 23)), self.by_hand(200000, 6.0, 1199.10, 1), delta=0.01)
        self.assertEqual(loans.owed_on(a, t, date(2056, 1, 1)), 0.0)
        self.assertEqual(loans.owed_on(a, t, date(2025, 1, 1)), 200000.0)
        self.assertAlmostEqual(loans.amortize(200000, 6.0, 1199.10, 12), loans.project(200000, 6.0, 1199.10)[0][1], delta=0.01)

    def test_a_payment_on_the_31st_is_made_at_the_end_of_a_shorter_month(self):
        self.assertEqual(loans.months_between(date(2026, 1, 31), date(2026, 2, 27)), 0)
        self.assertEqual(loans.months_between(date(2026, 1, 31), date(2026, 2, 28)), 1)
        self.assertEqual(loans.months_between(date(2026, 1, 31), date(2026, 3, 30)), 1)
        self.assertEqual(loans.months_between(date(2026, 1, 31), date(2026, 3, 31)), 2)
        self.assertEqual(loans.months_between(date(2026, 3, 15), date(2026, 1, 15)), 0)

    def test_without_a_rate_or_payment(self):
        self.assertEqual(loans.owed_on(self.loan(), self.terms(None, 1000), TODAY), 200000.0)
        self.assertEqual(loans.owed_on(self.loan(), self.terms(0, 1000), TODAY), 188000.0)
        self.assertEqual(loans.owed_on(self.loan(), self.terms(6.0, None), TODAY), 200000.0)
        self.assertEqual(loans.owed_on(self.loan(), None, TODAY), 200000.0)
        self.assertEqual(loans.owed_on(self.loan(balance_date=None), self.terms(6.0, 1000), TODAY), 200000.0)
        self.assertEqual(loans.owed_on(self.loan(balance_date="garbled"), self.terms(6.0, 1000), TODAY), 200000.0)
        self.assertEqual(loans.owed_on(self.loan(), self.terms(6.0, 900), TODAY), 200000.0)
        self.assertEqual(loans.owed_on(self.loan(balance=12000.0, owed_positive=1), self.terms(0, 500), TODAY), 6000.0)
        self.assertEqual(loans.owed_on({**self.loan(), "kind": "credit"}, self.terms(0, 500), TODAY), 200000.0)

    def test_net_worth_and_equity_use_the_paid_down_balance(self):
        self.c.execute(update(Account).where(Account.id == "mtg").values(balance_date="2026-03-23", interest_rate=6.0,
                                                                         monthly_payment=1500))
        networth.save_asset(self.c, {"name": "House", "kind": "home", "value": 425000, "loan_account_id": "mtg"}, today=TODAY)
        s = networth.summary(self.c, TODAY, save=False)
        g = {x["key"]: x for x in s["groups"]}
        owed = round(self.by_hand(250000, 6.0, 1500, 6), 2)
        mtg = next(i for i in g["loan"]["items"] if i["id"] == "mtg")
        self.assertEqual((mtg["value"], mtg["synced"]), (owed, 250000.0))
        self.assertNotIn("synced", next(i for i in g["loan"]["items"] if i["id"] == "auto"))
        self.assertEqual(g["home"]["items"][0]["equity"], round(425000 - owed, 2))
        self.assertEqual(g["loan"]["total"], round(owed + 12000, 2))
        from runway.server.api.accounts import api_account_update
        api_account_update(self.c, {}, {"networth_hidden": 1}, "mtg")
        left_out = networth.summary(self.c, TODAY, save=False)["excluded"]
        self.assertEqual(next(a["balance"] for a in left_out if a["id"] == "mtg"), owed)

    def test_plaids_terms_and_inferred_payments_count_too(self):
        self.c.execute(update(Account).where(Account.id == "mtg").values(
            balance_date="2026-06-23", plaid_account_id="p-mtg", interest_rate=3.0, monthly_payment=900))
        self.c.execute(insert(LoanTerms).values(plaid_account_id="p-mtg", item_id="item", kind="mortgage", interest_rate=6.0,
                                                monthly_payment=1500))
        loan = lambda: next(i for i in {x["key"]: x for x in networth.summary(self.c, TODAY, save=False)["groups"]}["loan"]["items"]
                            if i["id"] == "auto")
        mtg = next(i for i in networth.summary(self.c, TODAY, save=False)["groups"] if i["key"] == "loan")["items"]
        self.assertEqual(next(i for i in mtg if i["id"] == "mtg")["value"], round(self.by_hand(250000, 6.0, 1500, 3), 2))
        self.c.execute(update(Account).where(Account.id == "auto").values(balance_date="2026-07-23", interest_rate=0))
        self.assertNotIn("synced", loan())
        self.c.execute(insert(Transaction), [{"id": f"pay{m}", "account_id": "auto", "posted": f"2026-0{m}-05", "amount": 400}
                                             for m in (6, 7, 8)])
        self.assertEqual((loan()["value"], loan()["synced"]), (11200.0, 12000.0))


class MockRealie(BaseHTTPRequestHandler):
    calls = []
    nested = False

    def log_message(self, *a):
        pass

    def do_GET(self):
        MockRealie.calls.append((self.path, self.headers.get("Authorization")))
        q = parse_qs(urlparse(self.path).query)
        addr = q["address"][0]
        if self.headers.get("Authorization") != "rl-key":
            code, body = 401, {"message": "bad key"}
        elif "Nowhere" in addr:
            code, body = 404, {"message": "not found"}
        elif "No Estimate" in addr:
            code, body = 200, {"property": {"modelValue": 0, "city": "SPRINGFIELD", "zipCode": "62701"}}
        elif MockRealie.nested:
            code, body = 200, {"property": {"realieValuation": {"ml": {"value": 445000}},
                                            "propertyLocation": {"city": "SPRINGFIELD", "zipCode": "62701"}}}
        else:
            code, body = 200, {"property": [{"modelValue": 199000, "city": "CHICAGO", "zipCode": "60601"},
                                            {"modelValue": 431000, "city": "SPRINGFIELD", "zipCode": "62701-1234"}]}
        b = json.dumps(body).encode()
        self.send_response(code); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)


class RealieTests(Base):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), MockRealie)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        os.environ["RUNWAY_REALIE_URL"] = f"http://127.0.0.1:{cls.srv.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown(); os.environ.pop("RUNWAY_REALIE_URL", None)

    def setUp(self):
        super().setUp()
        MockRealie.nested = False

    def test_split_address(self):
        self.assertEqual(realie.split_address("1 Main St, Springfield, IL 62701"),
                         {"street": "1 Main St", "city": "Springfield", "state": "IL", "zip": "62701"})
        self.assertEqual(realie.split_address("1 Main St, Springfield IL"), {"street": "1 Main St", "city": "Springfield", "state": "IL", "zip": ""})
        self.assertEqual(realie.split_address("1 Main St Apt 2, Springfield, il 62701-1234")["state"], "IL")
        for bad in ("1 Main St", "1 Main St, Springfield"):
            with self.assertRaises(realie.RealieError):
                realie.split_address(bad)

    def test_lookup_refresh_and_limit(self):
        home = networth.save_asset(self.c, {"name": "House", "kind": "home", "value": 425000,
                                            "address": "1 Main St, Springfield, IL 62701", "auto_update": True}, today=date(2026, 8, 1))
        with self.assertRaises(realie.RealieError):
            realie.refresh_asset(self.c, home, TODAY)
        db.set_setting(self.c, "realie_api_key", "rl-key")
        est = realie.refresh_asset(self.c, home, TODAY)
        self.assertEqual((est["value"], est["low"], est["high"]), (431000.0, None, None))
        a = self.c.execute(select(Asset.value, Asset.source, Asset.last_lookup).where(Asset.id == home)).fetchone()
        self.assertEqual(tuple(a), (431000.0, "realie", "2026-09-23"))
        self.assertIn("address=1+Main+St&state=IL", MockRealie.calls[-1][0])
        self.assertEqual(MockRealie.calls[-1][1], "rl-key")
        self.assertEqual(realie.used_this_month(self.c, TODAY), 1)
        n = len(MockRealie.calls)
        with self.assertRaises(realie.RealieError) as cm:
            realie.refresh_asset(self.c, home, date(2026, 9, 29))
        self.assertIn("next lookup is Sep 30", str(cm.exception))
        self.assertEqual(realie.refresh_due(self.c, date(2026, 9, 29)), 0)
        self.assertEqual(len(MockRealie.calls), n)
        MockRealie.nested = True
        self.assertEqual(realie.refresh_due(self.c, date(2026, 9, 30)), 1)
        self.assertEqual(self.c.execute(select(Asset.value).where(Asset.id == home)).fetchone()[0], 445000.0)
        TODAY2 = date(2026, 9, 30)
        self.c.execute(update(Asset).where(Asset.id == home).values(last_lookup=None))
        networth.save_asset(self.c, {"address": "Nowhere, Springfield, IL"}, home, today=TODAY)
        with self.assertRaises(realie.RealieError) as cm:
            realie.refresh_asset(self.c, home, TODAY2)
        self.assertIn("couldn't find", str(cm.exception))
        MockRealie.nested = False
        networth.save_asset(self.c, {"address": "1 Main St, Peoria, IL 61602"}, home, today=TODAY)
        with self.assertRaises(realie.RealieError) as cm:
            realie.refresh_asset(self.c, home, TODAY2)
        self.assertIn("not in Peoria", str(cm.exception))
        networth.save_asset(self.c, {"address": "1 No Estimate Rd, Springfield, IL 62701"}, home, today=TODAY)
        with self.assertRaises(realie.RealieError) as cm:
            realie.refresh_asset(self.c, home, TODAY2)
        self.assertIn("doesn't have a value estimate", str(cm.exception))
        db.set_setting(self.c, "realie_calls:2026-09", "25")
        n = len(MockRealie.calls)
        with self.assertRaises(realie.RealieError) as cm:
            realie.refresh_asset(self.c, home, TODAY2)
        self.assertIn("25 free Realie lookups", str(cm.exception))
        self.assertEqual(len(MockRealie.calls), n)

    def test_a_home_realie_values_isnt_valued_by_hand(self):
        from runway.server.api.networth import api_asset_update, api_networth
        from runway.server.common import ApiError
        address = "1 Main St, Springfield, IL 62701"
        home = networth.save_asset(self.c, {"name": "House", "kind": "home", "value": 425000, "address": address,
                                            "auto_update": True}, today=TODAY)
        car = networth.save_asset(self.c, {"name": "Car", "kind": "vehicle", "value": 30000}, today=TODAY)
        valued = lambda: {a["name"]: a["realie_valued"] for a in api_networth(self.c, {}, {})["assets_list"]}
        self.assertEqual(valued(), {"House": False, "Car": False})
        db.set_setting(self.c, "realie_api_key", "rl-key")
        self.assertEqual(valued(), {"House": False, "Car": False})
        realie.refresh_asset(self.c, home, TODAY)
        self.assertEqual(valued(), {"House": True, "Car": False})
        with self.assertRaisesRegex(ApiError, "Realie values this home"):
            api_asset_update(self.c, {}, {"value": 500000}, str(home))
        self.assertEqual(self.c.execute(select(Asset.value).where(Asset.id == home)).scalar(), 431000.0)
        api_asset_update(self.c, {}, {"name": "Our house"}, str(home))
        api_asset_update(self.c, {}, {"auto_update": False, "value": 433000}, str(home))
        self.assertEqual((valued()["Our house"], self.c.execute(select(Asset.value).where(Asset.id == home)).scalar()),
                         (False, 433000.0))
        api_asset_update(self.c, {}, {"auto_update": True}, str(home))
        self.assertFalse(valued()["Our house"])
        realie.refresh_asset(self.c, home, TODAY + timedelta(days=7))
        self.assertTrue(valued()["Our house"])
        api_asset_update(self.c, {}, {"value": 28000}, str(car))
        api_asset_update(self.c, {}, {"address": "1 Main St"}, str(home))
        self.assertFalse(valued()["Our house"])
        api_asset_update(self.c, {}, {"value": 440000}, str(home))
        self.assertEqual(self.c.execute(select(Asset.value).where(Asset.id == home)).scalar(), 440000.0)
        self.assertFalse(realie.values_home(False, {"kind": "home", "source": "realie", "address": address}))
        self.assertTrue(realie.values_home(True, {"kind": "home", "source": "realie", "address": address, "auto_update": 1}))
        self.assertFalse(realie.values_home(True, {"kind": "home", "source": "realie", "address": address, "auto_update": 0}))
        self.assertFalse(realie.values_home(True, {"kind": "other", "source": "realie", "address": address}))


class QuoteTests(unittest.TestCase):
    def test_market_state(self):
        q = {"open_start": 1000, "open_end": 2000}
        self.assertEqual(prices.market_state(q, 1500), "open")
        self.assertEqual(prices.market_state(q, 2500), "closed")
        self.assertEqual(prices.market_state(None, 1500), "closed")


class NewCategoryTests(Base):
    def test_ai_can_propose_and_create_a_category(self):
        from runway.domain import categorize
        from runway import server
        db.set_setting(self.c, "openrouter_api_key", "k")
        self.c.execute(insert(Transaction), [{"id": "t1", "account_id": "chk", "posted": "2026-09-10", "amount": -45,
                                              "description": "PETSMART #123", "payee": "Petsmart", "needs_review": 1},
                                             {"id": "t2", "account_id": "chk", "posted": "2026-09-11", "amount": -12,
                                              "description": "STARBUCKS", "payee": "Starbucks", "needs_review": 1},
                                             {"id": "t3", "account_id": "chk", "posted": "2026-09-12", "amount": -30,
                                              "description": "CHEWY.COM", "payee": "Chewy", "needs_review": 1}])
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
        self.assertEqual(sug["Petsmart"]["new_category"], {"name": "Pets", "parent": None})
        self.assertEqual((sug["Starbucks"]["category"], sug["Starbucks"]["new_category"]), ("Groceries", None))
        r = server.api_ai_apply(self.c, None, {"tx_ids": sug["Petsmart"]["tx_ids"], "new_category": sug["Petsmart"]["new_category"], "direction": "out"})
        self.assertEqual((r["category"], r["created"], r["updated"]), ("Pets", True, 1))
        r2 = server.api_ai_apply(self.c, None, {"tx_ids": sug["Chewy"]["tx_ids"], "new_category": sug["Chewy"]["new_category"], "direction": "out"})
        self.assertEqual((r2["category"], r2["created"]), ("Pets", False))
        self.assertEqual(self.c.execute(select(func.count())
                                        .select_from(Transaction)
                                        .where(Transaction.category == "Pets")).fetchone()[0], 2)
        self.assertNotIn("new_category", categorize.build_prompt(["A"], [], []))


class ReplyParsingTests(unittest.TestCase):
    def test_messy_replies(self):
        from runway.domain import categorize
        cats = ["Groceries", "Restaurants"]
        think = '<think>Let me consider [the list]... maybe [{"i": 0, "category": "Restaurants"}]</think>\nHere you go:\n```json\n[{"i": 0, "category": "Groceries", "confidence": 0.9}]\n```'
        self.assertEqual(categorize.parse_ai_reply(think, cats), {0: ("Groceries", 0.9)})
        prose = 'Categories [as requested]: [{"i": 0, "category": "restaurants", "confidence": 0.8}] Hope that helps [1].'
        self.assertEqual(categorize.parse_ai_reply(prose, cats), {0: ("Restaurants", 0.8)})
        self.assertIsNone(categorize.extract_json_array("I can't help with that."))

    def test_unreadable_reply_is_an_error_not_silence(self):
        from runway.domain import categorize
        path = own_database(self)
        c = db.connect(path)
        self.addCleanup(c.close)
        db.set_setting(c, "openrouter_api_key", "k"); db.set_setting(c, "llm_model", "openrouter/free")
        group = [[{"posted": "2026-09-01", "amount": -5, "kind": "credit", "payee": "X", "description": "X"}]]
        with self.assertRaises(RuntimeError) as cm:
            categorize.ask_model(c, group, caller=lambda k, m, p: "Sure! I'd categorize these as groceries.")
        self.assertIn("openrouter/free", str(cm.exception))
        self.assertIn("openrouter/free", db.get_setting(c, "last_llm_error"))
