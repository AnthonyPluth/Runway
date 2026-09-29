"""The Churning page's API handlers (runway/server/api/churning.py), on the sample data plus a few cards."""
import os
import tempfile
import unittest
from datetime import date, timedelta
from unittest import mock

from runway import db, demo
from runway.server.api import churning as api
from runway.server.common import ApiError

TODAY = date.today()


def q(**kw):
    return {k: [str(v)] for k, v in kw.items()}


class ChurningApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        env = mock.patch.dict(os.environ, {"RUNWAY_DATA": self.tmp.name})
        env.start()
        self.addCleanup(env.stop)
        path = os.path.join(self.tmp.name, "runway.db")
        db.init(path)
        self.c = db.connect(path)
        demo.seed(self.c, TODAY)
        self.c.execute("INSERT INTO users(sub, first_name, last_seen) VALUES ('u1', 'Alex', 1)")

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

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
        self.assertEqual(out["people"], ["Alex", "Sam"])   # the signed-in person, then the partner from the cards
        self.assertEqual([c["product"] for c in out["cards"]], ["Gold", "Freedom Unlimited"])
        g = out["cards"][0]
        self.assertGreater(g["spent"], 0)   # the sample card's purchases since it was opened
        self.assertEqual((g["bonus_state"], g["rates"]), ("active", [{"category": "Groceries", "multiplier": 4.0}]))
        self.assertEqual(out["five24"]["Sam"]["count"], 0)   # an authorized user card doesn't count
        self.assertEqual(out["five24"]["Alex"]["count"], 1)
        self.assertEqual(out["upcoming"][0]["kind"], "task")
        self.assertIn("demo-card", [a["id"] for a in out["accounts"]])
        self.assertIn("chase", [i["key"] for i in out["issuers"]])
        self.assertIn("ur", [c["key"] for c in out["currencies"]])
        api.api_churn_card_update(self.c, {}, {"status": "closed", "closed_on": TODAY.isoformat()}, str(gold))
        self.assertEqual(api.api_churning(self.c, {}, {})["cards"][0]["status"], "closed")
        api.api_churn_card_remove(self.c, {}, {}, str(gold))
        self.assertEqual(len(api.api_churning(self.c, {}, {})["cards"]), 1)

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
        self.assertEqual(len(api.api_churning_best(self.c, {}, {})["cards"]), 3)   # no category: base rates
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

    def test_bank_bonuses(self):
        opened = (TODAY - timedelta(days=10)).isoformat()
        bid = api.api_bank_bonus_add(self.c, {}, {"owner": "Sam", "bank": "Chase", "opened_on": opened, "bonus": 300,
                                                  "account_id": "demo-checking", "dd_total": 500})["id"]
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
        key = api.api_churn_currency(self.c, {}, {"name": "Bilt", "cents": 1.7})["key"]
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
