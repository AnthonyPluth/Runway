"""The Churning page's API handlers (runway/server/api/churning.py), on the sample data plus a few cards."""
import unittest
from datetime import date, timedelta

from sqlalchemy import insert

from runway.domain import demo
from runway.server.api import churning as api
from runway.server.common import ApiError
from runway.storage.models import User
from tests.shared import TODAY, DbCase, freeze_today

# (date.today() is frozen at shared.TODAY in each test's setUp: the handlers read the clock too)


def q(**kw):
    return {k: [str(v)] for k, v in kw.items()}


class ChurningApiTests(DbCase):
    def setUp(self):
        super().setUp()
        freeze_today(self)
        demo.seed(self.c, TODAY)
        self.c.execute(insert(User).values(sub="u1", first_name="Alex", last_seen=1))

    def add(self, **body):
        return api.api_churn_card_add(self.c, {}, {"owner": "Alex", "issuer": "chase", "opened_on": TODAY.isoformat(), **body})["id"]

    def test_overview_and_cards(self):
        opened = (TODAY - timedelta(days=20)).isoformat()
        gold = self.add(issuer="amex", product="Gold", opened_on=opened, account_id="demo-card", currency="mr",
                        bonus=60000, bonus_spend=6000, annual_fee=325)
        self.add(owner="Sam", product="Freedom Unlimited", authorized_user=True)
        api.api_churn_rate(self.c, {}, {"category": "Groceries", "multiplier": 4}, str(gold))
        api.api_churn_task_add(self.c, {}, {"card_id": gold, "due_on": TODAY.isoformat(), "action": "Downgrade"})
        out = api.api_churning(self.c, {}, {})
        self.assertEqual(out["people"], ["Alex", "Sam"])
        self.assertEqual([c["product"] for c in out["cards"]], ["Gold", "Freedom Unlimited"])
        g = out["cards"][0]
        self.assertGreater(g["spent"], 0)
        self.assertEqual((g["bonus_state"], g["rates"]), ("active", [{"category": "Groceries", "multiplier": 4.0, "portal_only": False}]))
        self.assertEqual(out["five24"]["Sam"]["count"], 0)
        self.assertEqual(out["five24"]["Alex"]["count"], 1)
        self.assertEqual(out["upcoming"][0]["kind"], "task")
        self.assertIn("demo-card", [a["id"] for a in out["accounts"]])
        self.assertIn("chase", [i["key"] for i in out["issuers"]])
        self.assertIn("ur", [c["key"] for c in out["currencies"]])
        api.api_churn_card_update(self.c, {}, {"status": "closed", "closed_on": TODAY.isoformat()}, str(gold))
        self.assertEqual(api.api_churning(self.c, {}, {})["cards"][0]["status"], "closed")
        api.api_churn_card_remove(self.c, {}, {}, str(gold))
        self.assertEqual(len(api.api_churning(self.c, {}, {})["cards"]), 1)

    def test_card_added_with_its_benefits(self):
        cid = self.add(product="Sapphire Reserve", annual_fee=795, benefits=[
            {"name": "Travel credit", "kind": "credit", "amount": 300, "period": "annual"}, {"preset": "priority_pass"}])
        card = next(c for c in api.api_churning(self.c, {}, {})["cards"] if c["id"] == cid)
        self.assertEqual(len(card["benefits"]), 2)
        self.assertIn(("Travel credit", 300.0), [(b["name"], b["amount"]) for b in card["benefits"]])

    def test_a_refused_benefit_refuses_the_whole_card(self):
        before = len(api.api_churning(self.c, {}, {})["cards"])
        with self.assertRaisesRegex(ApiError, "name"), self.c.sa.begin_nested():
            self.add(product="Gone", benefits=[{"name": "Fine"}, {"name": " "}])
        self.assertEqual(len(api.api_churning(self.c, {}, {})["cards"]), before)

    def test_fee_month_follows_the_opening_month(self):
        opened = date(TODAY.year - 2, 3, 10).isoformat()
        cid = self.add(product="Old", opened_on=opened, annual_fee=95, fee_month=11)
        api.api_churn_card_update(self.c, {}, {"fee_month": 12}, str(cid))
        fees = [u for u in api.api_churning(self.c, {}, {})["upcoming"] if u["kind"] == "fee"]
        self.assertTrue(fees)
        self.assertTrue(all(u["date"][5:] == "03-10" for u in fees))

    def test_best_card(self):
        self.add(product="Sapphire Preferred", currency="ur", base_rate=1)
        cid = self.add(owner="Sam", issuer="citi", product="Double Cash", currency="cash", base_rate=2)
        s = self.add(product="Freedom Flex", currency="ur")
        api.api_churn_rate(self.c, {}, {"category": "Restaurants", "multiplier": 3}, str(s))
        out = api.api_churning_best(self.c, q(category="Restaurants", amount=40), {})
        self.assertEqual([c["product"] for c in out["cards"]], ["Freedom Flex", "Double Cash", "Sapphire Preferred"])
        self.assertEqual(out["cards"][0]["value"], 1.8)
        sam = api.api_churning_best(self.c, q(category="Restaurants", owner="Sam"), {})["cards"]
        self.assertEqual([c["id"] for c in sam], [cid])
        self.assertEqual(len(api.api_churning_best(self.c, {}, {})["cards"]), 3)
        for bad in (q(category="Nope"), q(category="Groceries", amount="lots"), q(amount=-5)):
            with self.subTest(bad=bad), self.assertRaises(ApiError):
                api.api_churning_best(self.c, bad, {})

    def test_validation_errors_are_api_errors(self):
        with self.assertRaisesRegex(ApiError, "bank"):
            api.api_churn_card_add(self.c, {}, {"owner": "Alex", "issuer": "?", "product": "X", "opened_on": "2026-01-01"})
        with self.assertRaises(ApiError) as e:
            api.api_churn_card_update(self.c, {}, {}, "abc")
        self.assertEqual(e.exception.status, 404)
        with self.assertRaisesRegex(ApiError, "not found"):
            api.api_churn_card_update(self.c, {}, {"product": "X"}, "123")
        with self.assertRaisesRegex(ApiError, "category"):
            api.api_churn_rate(self.c, {}, {"category": "", "multiplier": 2}, str(self.add(product="X")))
        with self.assertRaisesRegex(ApiError, "cents"):
            api.api_churn_currency(self.c, {}, {"key": "ur"})
        with self.assertRaisesRegex(ApiError, "person"):
            api.api_churn_balance(self.c, {}, {"owner": "", "currency": "ur", "points": 1})
        with self.assertRaisesRegex(ApiError, "card"):
            api.api_churn_task_add(self.c, {}, {"due_on": "2026-01-01", "action": "x"})

    def test_rates_plans_benefits_and_portal(self):
        vx = self.add(issuer="capital_one", product="Venture X", currency="c1", portal_name="Capital One Travel",
                      plan="product_change", plan_target="VentureOne", annual_fee=395,
                      rates=[{"category": "*", "multiplier": 2}, {"category": "Travel", "multiplier": 10, "portal_only": True}])
        out = api.api_churning(self.c, {}, {})
        card = out["cards"][0]
        self.assertEqual((card["base_rate"], card["plan"], card["rates"][0]["portal_only"]), (2.0, "product_change", True))
        self.assertIn("Alex", out["owners"])
        self.assertIn("Travel", [c["name"] for c in out["categories"]])
        self.assertNotIn("Transfer", [c["name"] for c in out["categories"]])
        self.assertEqual(out["base_marker"], "*")
        self.assertEqual([a["key"] for a in out["alert_prefs"]], ["churn_fee", "churn_bonus", "churn_plan", "churn_benefit",
                                                               "churn_apply"])
        self.assertTrue(all(a["on"] and a["label"] for a in out["alert_prefs"]))
        self.assertIn("lounge", [p["key"] for p in out["benefit_presets"]])
        self.assertIn("airline", [g["kind"] for g in out["currency_groups"]])
        self.assertTrue(out["values_as_of"])

        best = api.api_churning_best(self.c, q(category="Travel", amount=100), {})
        self.assertFalse(best["portal"])
        self.assertEqual((best["cards"][0]["multiplier"], best["cards"][0]["portal_option"]["note"]),
                         (2.0, "10x if booked through Capital One Travel"))
        best = api.api_churning_best(self.c, q(category="Travel", portal=1), {})
        self.assertEqual((best["portal"], best["cards"][0]["multiplier"], best["cards"][0]["needs_portal"]), (True, 10.0, True))

        with self.assertRaisesRegex(ApiError, "listed twice"):
            api.api_churn_card_update(self.c, {}, {"rates": [{"category": "Travel", "multiplier": 1}] * 2}, str(vx))
        api.api_churn_rate(self.c, {}, {"category": "Travel", "multiplier": "", "portal_only": True}, str(vx))
        self.assertEqual(api.api_churning(self.c, {}, {})["cards"][0]["rates"], [])

        b = api.api_churn_benefit_add(self.c, {}, {"preset": "travel_credit", "amount": 300}, str(vx))["id"]
        api.api_churn_benefit_update(self.c, {}, {"name": "Capital One Travel credit"}, str(b))
        api.api_churn_benefit_use(self.c, {}, {"amount": 100}, str(b))
        got = api.api_churning(self.c, {}, {})["cards"][0]
        self.assertEqual((got["benefits"][0]["name"], got["benefits"][0]["remaining"], got["net_fee"]),
                         ("Capital One Travel credit", 200.0, 95.0))
        api.api_churn_benefit_unuse(self.c, {}, {}, str(b))
        self.assertEqual(api.api_churning(self.c, {}, {})["cards"][0]["benefits"][0]["remaining"], 300.0)
        with self.assertRaisesRegex(ApiError, "left this period"):
            api.api_churn_benefit_use(self.c, {}, {"amount": 301}, str(b))
        with self.assertRaisesRegex(ApiError, "resets"):
            api.api_churn_benefit_add(self.c, {}, {"name": "X", "period": "daily"}, str(vx))
        with self.assertRaises(ApiError) as e:
            api.api_churn_benefit_use(self.c, {}, {}, "x")
        self.assertEqual(e.exception.status, 404)
        api.api_churn_benefit_remove(self.c, {}, {}, str(b))
        self.assertEqual(api.api_churning(self.c, {}, {})["cards"][0]["benefits"], [])

        done = api.api_churn_plan_done(self.c, {}, {}, str(vx))
        self.assertEqual((done["status"], done["plan_done_on"]), ("product_changed", TODAY.isoformat()))
        self.assertEqual([c["product"] for c in api.api_churning(self.c, {}, {})["cards"]], ["Venture X", "VentureOne"])
        undone = api.api_churn_plan_undo(self.c, {}, {}, str(vx))
        self.assertEqual((undone["status"], undone["new_card_id"]), ("open", None))
        with self.assertRaisesRegex(ApiError, "isn't checked off"):
            api.api_churn_plan_undo(self.c, {}, {}, str(vx))

        t = api.api_churn_task_add(self.c, {}, {"card_id": vx, "due_on": TODAY.isoformat(), "action": "Call"})["id"]
        until = api.api_churn_task_snooze(self.c, {}, {"days": 3}, str(t))["snooze_until"]
        self.assertEqual(until, (TODAY + timedelta(days=3)).isoformat())
        self.assertNotIn("task", [u["kind"] for u in api.api_churning(self.c, {}, {})["upcoming"]])
        with self.assertRaisesRegex(ApiError, "date"):
            api.api_churn_task_snooze(self.c, {}, {"until": "soon"}, str(t))

    def test_wishlist_and_scores(self):
        w = api.api_churn_wish_add(self.c, {}, {"owner": "Alex", "issuer": "chase", "product": "Sapphire Preferred",
                                               "min_score": 760, "bonus": 75000, "currency": "ur"})["id"]
        api.api_churn_score(self.c, {}, {"owner": "Alex", "score": 720, "source": "Experian FICO 8"})
        out = api.api_churning(self.c, {}, {})
        item = out["wishlist"][0]
        self.assertEqual((item["id"], item["ready"], item["blockers"][0]["kind"]), (w, False, "score"))
        self.assertEqual((out["scores"]["Alex"]["score"], out["scores"]["Alex"]["source"]), (720, "Experian FICO 8"))
        api.api_churn_wish_update(self.c, {}, {"min_score": ""}, str(w))
        self.assertTrue(api.api_churning(self.c, {}, {})["wishlist"][0]["ready"])
        r = api.api_churn_wish_applied(self.c, {}, {}, str(w))
        self.assertEqual(r["kind"], "card")
        self.assertIn(r["id"], [c["id"] for c in api.api_churning(self.c, {}, {})["cards"]])
        with self.assertRaisesRegex(ApiError, "already"):
            api.api_churn_wish_applied(self.c, {}, {}, str(w))
        with self.assertRaisesRegex(ApiError, "Pick the bank"):
            api.api_churn_wish_add(self.c, {}, {"owner": "Alex", "product": "X"})
        with self.assertRaisesRegex(ApiError, "credit score"):
            api.api_churn_score(self.c, {}, {"owner": "Alex", "score": "great"})
        with self.assertRaises(ApiError) as e:
            api.api_churn_wish_update(self.c, {}, {}, "nope")
        self.assertEqual(e.exception.status, 404)
        api.api_churn_wish_remove(self.c, {}, {}, str(w))
        self.assertEqual(api.api_churning(self.c, {}, {})["wishlist"], [])

    def test_bank_bonuses(self):
        opened = (TODAY - timedelta(days=10)).isoformat()
        bid = api.api_bank_bonus_add(self.c, {}, {"owner": "Sam", "bank": "Chase", "opened_on": opened, "bonus": 300,
                                                  "account_id": "demo-checking", "dd_total": 1_000_000})["id"]
        out = api.api_churning(self.c, {}, {})
        self.assertEqual(out["people"], ["Alex", "Sam"])
        b = out["bank"][0]
        self.assertEqual((b["bank"], b["progress"]["source"]), ("Chase", "account"))
        self.assertIn("bank_due", [u["kind"] for u in out["upcoming"]])
        api.api_bank_bonus_update(self.c, {}, {"received_on": TODAY.isoformat(), "received_amount": 300}, str(bid))
        out = api.api_churning(self.c, {}, {})
        self.assertEqual(out["bank"][0]["state"], "received")
        self.assertEqual(out["bank_income"], {"Sam": {str(TODAY.year): 300.0}})
        with self.assertRaisesRegex(ApiError, "checking or savings"):
            api.api_bank_bonus_update(self.c, {}, {"account_id": "demo-card"}, str(bid))
        with self.assertRaises(ApiError):
            api.api_bank_bonus_add(self.c, {}, {"owner": "Sam", "bank": "", "opened_on": opened, "bonus": 1})
        api.api_bank_bonus_remove(self.c, {}, {}, str(bid))
        self.assertEqual(api.api_churning(self.c, {}, {})["bank"], [])

    def test_currencies_balances_and_tasks(self):
        key = api.api_churn_currency(self.c, {}, {"name": "Wyndham", "cents": 1.7})["key"]
        api.api_churn_currency(self.c, {}, {"key": "mr", "cents": 2})
        api.api_churn_balance(self.c, {}, {"owner": "Alex", "currency": "mr", "points": 50000})
        out = api.api_churning(self.c, {}, {})
        self.assertEqual(next(c for c in out["currencies"] if c["key"] == key)["cents"], 1.7)
        mr = next(r for r in out["rewards"]["Alex"]["currencies"] if r["currency"] == "mr")
        self.assertEqual(mr["balance_value"], 1000.0)
        api.api_churn_currency_remove(self.c, {}, {}, key)
        api.api_churn_currency_remove(self.c, {}, {}, "mr")
        cur = {c["key"]: c for c in api.api_churning(self.c, {}, {})["currencies"]}
        self.assertNotIn(key, cur)
        self.assertEqual(cur["mr"]["cents"], 1.5)
        card = self.add(product="Freedom")
        t = api.api_churn_task_add(self.c, {}, {"card_id": card, "due_on": "2026-12-01", "action": "Close"})["id"]
        api.api_churn_task_update(self.c, {}, {"done": True}, str(t))
        self.assertEqual(api.api_churning(self.c, {}, {})["tasks"][0]["done"], 1)
        api.api_churn_task_remove(self.c, {}, {}, str(t))
        self.assertEqual(api.api_churning(self.c, {}, {})["tasks"], [])


if __name__ == "__main__":
    unittest.main()
