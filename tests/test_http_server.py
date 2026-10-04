"""The HTTP server end to end: requests through server.Handler."""
import json
import unittest
import urllib.error
import urllib.request
from datetime import date, datetime

from sqlalchemy import insert

from runway import categorize, db
from runway.models import Account, Transaction
from tests.shared import ServerCase, freeze_today


class ServerTests(ServerCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        freeze_today(cls)       # the tests add transactions dated today

    def test_sync_schedule(self):
        from runway import server
        at = lambda s: datetime.fromisoformat(s)
        self.assertTrue(server.daily_due(None))
        self.assertFalse(server.daily_due("2026-09-25T07:30:00", at("2026-09-25T23:00")))   # already synced today
        self.assertTrue(server.daily_due("2026-09-25T06:30:00", at("2026-09-25T08:00")))    # an early sync doesn't skip 7am
        self.assertFalse(server.daily_due("2026-09-24T22:00:00", at("2026-09-25T06:45")))   # a new day, but before 7am
        self.assertTrue(server.daily_due("2026-09-24T22:00:00", at("2026-09-25T07:15")))
        self.assertTrue(server.daily_due("2026-09-24T04:00:00", at("2026-09-25T05:00")))    # missed yesterday's 7am
        self.assertFalse(server.daily_due("2026-09-25T07:02:00", at("2026-09-25T13:00")))   # the hour's sync counts

    def test_plaid_once_a_day_at_7(self):
        from runway import server
        at = lambda s: datetime.fromisoformat(s)
        self.assertTrue(server.plaid_due(None))
        self.assertTrue(server.plaid_due("2026-09-24T07:05:00", at("2026-09-25T07:00")))    # the 7am sync
        self.assertFalse(server.plaid_due("2026-09-25T07:01:00", at("2026-09-25T23:00")))   # already asked today
        self.assertFalse(server.plaid_due("2026-09-24T07:05:00", at("2026-09-25T06:59")))   # not 7am yet
        self.assertFalse(server.plaid_due("2026-09-24T22:00:00", at("2026-09-25T02:00")))   # asked after yesterday's 7am
        self.assertTrue(server.plaid_due("2026-09-24T06:00:00", at("2026-09-25T02:00")))    # missed yesterday's
        self.assertTrue(server.plaid_due("2026-09-20T09:00:00", at("2026-09-25T12:00")))

    def test_plaid_refresh_at_630(self):
        from runway import server
        at = lambda s: datetime.fromisoformat(s)
        self.assertTrue(server.plaid_refresh_due(None, at("2026-09-25T06:30")))
        self.assertTrue(server.plaid_refresh_due("2026-09-24T06:31:00", at("2026-09-25T06:45")))
        self.assertFalse(server.plaid_refresh_due("2026-09-25T06:31:00", at("2026-09-25T06:45")))   # already told today
        self.assertFalse(server.plaid_refresh_due(None, at("2026-09-25T06:29")))                    # too early
        self.assertFalse(server.plaid_refresh_due(None, at("2026-09-25T07:00")))                    # the sync's due: too late

    def test_sync_on_visit_needs_a_connection(self):
        self.assertEqual(self.req("POST", "/api/sync/auto"), (200, {"started": False}))

    def test_state_and_static(self):
        code, st = self.req("GET", "/api/state")
        self.assertEqual(code, 200)
        self.assertFalse(st["connected"])
        with urllib.request.urlopen(self.base + "/manifest.webmanifest") as resp:
            self.assertIn(b"Runway", resp.read())
            self.assertEqual(resp.headers["Content-Type"].split(";")[0], "application/manifest+json")
        with urllib.request.urlopen(self.base + "/sw.js") as resp:
            self.assertEqual(resp.headers["Content-Type"].split(";")[0], "text/javascript")

    def test_csrf_header_required(self):
        code, _ = self.req("POST", "/api/settings", {"horizon_days": 60}, headers={"X-Runway": "0"})
        self.assertEqual(code, 403)

    def test_foreign_host_rejected(self):
        r = urllib.request.Request(self.base + "/api/state", headers={"Host": "evil.example"})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(r)
        self.assertEqual(cm.exception.code, 403)

    def test_settings_and_overview(self):
        self.assertEqual(self.req("POST", "/api/settings", {"openrouter_api_key": "sk-or-x", "llm_model": "openai/gpt-4o-mini"})[0], 200)
        _, st = self.req("GET", "/api/state")
        self.assertTrue(st["has_api_key"])
        self.assertEqual(st["llm_model"], "openai/gpt-4o-mini")
        self.assertNotIn("sk-or-x", json.dumps(st))  # key never sent back to the page
        self.assertEqual(st["card_ai_model"], "anthropic/claude-haiku-4.5")   # each model has its own default
        self.assertEqual(self.req("POST", "/api/settings", {"card_ai_model": " google/gemini-2.5-flash "})[0], 200)
        _, st = self.req("GET", "/api/state")
        self.assertEqual((st["llm_model"], st["card_ai_model"]), ("openai/gpt-4o-mini", "google/gemini-2.5-flash"))
        self.req("POST", "/api/settings", {"llm_model": "", "card_ai_model": ""})
        _, st = self.req("GET", "/api/state")
        self.assertEqual((st["llm_model"], st["card_ai_model"]), ("openrouter/free", "anthropic/claude-haiku-4.5"))
        # the defaults, for the empty fields' placeholders
        self.assertEqual((st["llm_model_default"], st["card_ai_model_default"]), (categorize.DEFAULT_MODEL, categorize.DEFAULT_CARD_MODEL))
        code, fc = self.req("GET", "/api/overview?days=30")
        self.assertEqual(code, 200)
        self.assertEqual(len(fc["dates"]), 31)

    def test_budget_and_rules_api(self):
        with db.session() as c:
            db.insert_ignore(c, Account, {"id": "b1", "name": "Card", "kind": "credit", "balance": -10}, key=["id"])
            today = date.today()
            c.execute(insert(Transaction), [{"id": "b1|1", "account_id": "b1", "posted": today.isoformat(),
                                             "amount": -80, "description": "WHOLE FOODS", "payee": "Whole Foods",
                                             "category": "Groceries"},
                                            {"id": "b1|2", "account_id": "b1", "posted": today.isoformat(),
                                             "amount": -45, "description": "SHELL", "payee": "Shell",
                                             "category": "Auto & Gas"},
                                            {"id": "b1|3", "account_id": "b1", "posted": today.isoformat(),
                                             "amount": 900, "description": "PAYMENT", "payee": "Payment",
                                             "category": "Credit Card Payment"}])
        self.assertEqual(self.req("POST", "/api/budget", {"category": "Groceries", "amount": 500})[0], 200)
        self.assertEqual(self.req("POST", "/api/budget", {"category": "Transfer", "amount": 5})[0], 400)
        _, b = self.req("GET", "/api/budget")
        g = next(c for c in b["categories"] if c["name"] == "Groceries")
        self.assertEqual((g["budget"], g["spent"], g["left"]), (500.0, 80.0, 420.0))
        gas = next(c for c in b["categories"] if c["name"] == "Auto & Gas")
        self.assertIsNone(gas["budget"])
        self.assertFalse(any(c["name"] == "Credit Card Payment" for c in b["categories"]))
        # rules: add, edit, apply
        self.req("POST", "/api/rules", {"match": "whole", "category": "Shopping"})
        _, rules = self.req("GET", "/api/rules")
        rid = next(r["id"] for r in rules if r["match"] == "whole")
        self.assertEqual(self.req("POST", f"/api/rules/{rid}", {"match": "whole foods", "category": "Shopping"})[0], 200)
        _, res = self.req("POST", f"/api/rules/{rid}/apply", {})
        self.assertEqual(res["updated"], 1)
        _, res = self.req("POST", f"/api/rules/{rid}/apply", {})
        self.assertEqual(res["updated"], 0)   # counts only what changed

    def test_subcategory_rollup_and_cashflow(self):
        today = date.today()
        d = today.isoformat()
        self.assertEqual(self.req("POST", "/api/categories", {"name": "Fast food", "parent": "Restaurants"})[0], 200)
        with db.session() as c:
            db.insert_ignore(c, Account, {"id": "cf", "name": "Checking", "kind": "checking", "balance": 100}, key=["id"])
            c.execute(insert(Transaction), [{"id": "cf|1", "account_id": "cf", "posted": d, "amount": -20,
                                             "description": "SHAKE SHACK", "payee": "Shake Shack",
                                             "category": "Fast food"},
                                            {"id": "cf|2", "account_id": "cf", "posted": d, "amount": -50,
                                             "description": "NICE PLACE", "payee": "Nice Place",
                                             "category": "Restaurants"},
                                            {"id": "cf|3", "account_id": "cf", "posted": d, "amount": 3000,
                                             "description": "PAYROLL", "payee": "Payroll", "category": "Income"},
                                            {"id": "cf|4", "account_id": "cf", "posted": d, "amount": -700,
                                             "description": "CARD PAYMENT", "payee": "Card Payment",
                                             "category": "Credit Card Payment"},
                                            {"id": "cf|5", "account_id": "cf", "posted": d, "amount": -30,
                                             "description": "MYSTERY", "payee": "Mystery", "category": None}])
        self.req("POST", "/api/budget", {"category": "Restaurants", "amount": 100})
        _, b = self.req("GET", "/api/budget")
        r = next(c for c in b["categories"] if c["name"] == "Restaurants")
        ff = next(c for c in b["categories"] if c["name"] == "Fast food")
        self.assertEqual((r["spent"], r["own_spent"], r["has_children"]), (70.0, 50.0, True))
        self.assertEqual((ff["spent"], ff["parent"]), (20.0, "Restaurants"))
        _, cf = self.req("GET", "/api/cashflow")
        rest = next(n for n in cf["spending"] if n["name"] == "Restaurants")
        self.assertEqual(rest["value"], 70.0)
        self.assertEqual(sorted((k["name"], k["value"]) for k in rest["children"]), [("Fast food", 20.0), ("Restaurants (general)", 50.0)])
        self.assertIn({"name": "Uncategorized", "value": 30.0, "children": []}, cf["spending"])
        self.assertFalse(any(n["name"] == "Credit Card Payment" for n in cf["spending"]))  # transfers never count
        self.assertEqual(next(n for n in cf["income"] if n["name"] == "Income")["value"], 3000.0)
        # removing via the API
        code, res = self.req("POST", "/api/categories/remove", {"name": "Fast food", "move_to": "Restaurants"})
        self.assertEqual((code, res["moved"]), (200, 1))
        self.assertEqual(self.req("POST", "/api/categories/remove", {"name": "Transfer"})[0], 400)

    def test_recurring_validation(self):
        code, err = self.req("POST", "/api/recurring", {"name": "x", "account_id": "nope", "amount": "1", "anchor_date": "2026-09-01"})
        self.assertEqual(code, 400)
        self.assertIn("error", err)


if __name__ == "__main__":
    unittest.main()
