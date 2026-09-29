"""The API handlers for the Overview, Accounts, Budget, Transactions and push notifications, and the sync's own
queries, called directly on the sample data (runway/demo.py) plus the cases each one handles."""
import os
import tempfile
import unittest
from datetime import date, timedelta
from unittest import mock

from runway import categories, db, demo, splits
from runway.server import sync
from runway.server.api import accounts, budget, notifications, state, transactions
from runway.server.common import ApiError

TODAY = date.today()


def q(**kw):
    return {k: [str(v)] for k, v in kw.items()}


class HandlerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        env = mock.patch.dict(os.environ, {"RUNWAY_DATA": self.tmp.name})
        env.start()
        self.addCleanup(env.stop)
        path = os.path.join(self.tmp.name, "runway.db")
        db.init(path)
        self.c = db.connect(path)
        # The syncs open their own db.session(). Point it at this test's database: on SQLite RUNWAY_DATA already does,
        # but on Postgres each test's schema is named after its path, and a session without one would find no tables.
        opened = db.session
        patch = mock.patch.object(db, "session", lambda p=None: opened(p or path))
        patch.start()
        self.addCleanup(patch.stop)
        demo.seed(self.c, TODAY)

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def one(self, sql, *args):
        return self.c.execute(sql, args).fetchone()

    # ------------------------------------------------------------------------------------------ accounts

    def test_accounts_list(self):
        self.c.execute("INSERT INTO plaid_items(item_id, access_token, institution_name, products) "
                       "VALUES ('it1', 'x', 'Card Bank', 'transactions,liabilities')")
        self.c.execute("INSERT INTO plaid_accounts(plaid_account_id, item_id, mask) VALUES ('pa1', 'it1', '1234')")
        self.c.execute("INSERT INTO card_statements(plaid_account_id, item_id, last_statement_date, next_due_date) "
                       "VALUES ('pa1', 'it1', '2026-09-01', '2026-09-25')")
        self.c.execute("UPDATE accounts SET plaid_account_id='pa1', display_name='Zed Card' WHERE id='demo-card'")
        self.c.execute("INSERT INTO accounts(id, name, kind, hidden) VALUES ('gone', 'Aardvark', 'checking', 1)")
        out = accounts.api_accounts(self.c, {}, {})
        self.assertEqual([a["id"] for a in out], ["demo-checking", "demo-card", "demo-mortgage", "demo-savings", "gone"])
        cols = [c.name for c in db.schema.accounts.columns]
        self.assertEqual(list(out[0]), [*cols, "plaid_link"])
        self.assertIsNone(out[0]["plaid_link"])
        self.assertEqual(out[1]["plaid_link"], {"institution": "Card Bank", "mask": "1234", "transactions": True,
                                                "closed": "2026-09-01", "due": "2026-09-25", "statement_note": None})

    def test_account_update(self):
        with self.assertRaises(ApiError):
            accounts.api_account_update(self.c, {}, {"display_name": "x"}, "nope")
        with self.assertRaises(ApiError):
            accounts.api_account_update(self.c, {}, {"kind": "boat"}, "demo-card")
        accounts.api_account_update(self.c, {}, {"display_name": "  Daily  ", "hidden": "1", "owner": "", "name": "sneaky",
                                                 "in_forecast": 0}, "demo-checking")
        row = self.one("SELECT name, display_name, hidden, owner, in_forecast FROM accounts WHERE id='demo-checking'")
        self.assertEqual(tuple(row), ("Everyday Checking", "Daily", 1, None, 0))
        self.assertEqual(accounts.api_account_update(self.c, {}, {}, "demo-checking"), {"ok": True})

    # ------------------------------------------------------------------------------------------ push

    def test_push_recent(self):
        for i in range(10):
            self.c.execute("INSERT INTO notify_log(key, sent, title) VALUES (?,?,?)", (f"k{i}", float(i), f"t{i}"))
        out = notifications.api_push(self.c, {}, {})
        self.assertEqual(out["recent"], [{"title": f"t{i}", "sent": float(i)} for i in range(9, 1, -1)])
        self.assertEqual(out["devices"], [])

    # ------------------------------------------------------------------------------------------ state

    def test_state(self):
        self.c.execute("INSERT INTO accounts(id, name, kind) VALUES ('inv', 'Brokerage', 'investment')")
        self.c.execute("INSERT INTO transactions(id, account_id, posted, amount, needs_review) VALUES "
                       "('inv|1', 'inv', '2026-01-01', -5, 1), ('x|1', 'demo-card', '2026-01-01', -5, 1)")
        self.c.execute("INSERT INTO sync_log(ok, message) VALUES (0, 'Latest')")
        st = state.api_state(self.c, {}, {})
        self.assertEqual(st["review_count"], 1)
        self.assertEqual({k: st["last_log"][k] for k in ("ok", "message")}, {"ok": 0, "message": "Latest"})
        self.assertEqual(list(st["last_log"]), ["at", "ok", "message"])
        self.assertEqual(st["setup"], {"bank": True, "primary": True, "recurring": True, "budgets": True, "dismissed": False})

    def test_owner_choices(self):
        self.c.execute("INSERT INTO users(sub, first_name, last_seen) VALUES ('a', 'Zoe', 2), ('b', 'Adam', 1), ('c', NULL, 0), "
                       "('d', 'Joint', 3)")
        self.c.execute("UPDATE accounts SET owner='Adam' WHERE id='demo-card'")
        self.c.execute("UPDATE accounts SET owner='Pat' WHERE id='demo-savings'")
        self.c.execute("UPDATE accounts SET owner='' WHERE id='demo-checking'")
        self.assertEqual(state.owner_choices(self.c), ["Adam", "Zoe", "Pat"])

    def test_setup_steps(self):
        self.c.execute("DELETE FROM recurring")
        self.c.execute("UPDATE budgets SET amount=0")
        self.c.execute("INSERT INTO accounts(id, name, kind) VALUES ('chk2', 'Second', 'checking')")
        self.assertEqual(state.setup_steps(self.c), {"bank": True, "primary": False, "recurring": False, "budgets": False,
                                                      "dismissed": False})
        self.c.execute("UPDATE accounts SET hidden=1 WHERE id='chk2'")
        self.assertTrue(state.setup_steps(self.c)["primary"])

    def test_overview(self):
        self.c.execute("UPDATE accounts SET display_name='A Savings' WHERE id='demo-savings'")
        fc = state.api_overview(self.c, q(days=30), {})
        self.assertEqual([a["id"] for a in fc["all_accounts"]], ["demo-checking", "demo-card", "demo-mortgage", "demo-savings"])
        self.assertEqual(list(fc["all_accounts"][0]), ["id", "name", "kind", "balance", "balance_date", "owed_positive", "hidden"])
        self.assertEqual(fc["all_accounts"][3]["name"], "A Savings")
        rec = [e for e in fc["events"] if e.get("recurring_id")]
        self.assertTrue(rec)
        self.assertTrue(all("logo" in e for e in rec))
        self.assertIn("missed", fc)

    def test_overview_names_sort_by_display_name(self):
        self.c.execute("INSERT INTO accounts(id, name, display_name, kind) VALUES ('c2', 'AAA', 'ZZZ', 'checking')")
        self.c.execute("INSERT INTO accounts(id, name, kind) VALUES ('c3', 'MMM', 'checking')")
        fc = state.api_overview(self.c, q(days=14), {})
        self.assertEqual([a["id"] for a in fc["all_accounts"] if a["kind"] == "checking"], ["demo-checking", "c3", "c2"])

    def test_overrides(self):
        with self.assertRaises(ApiError):
            state.api_override_set(self.c, {}, {"key": "other:1", "amount": 1})
        with self.assertRaises(ApiError):
            state.api_override_set(self.c, {}, {"key": "rec:1:2026-09-01", "amount": "lots"})
        state.api_override_set(self.c, {}, {"key": "rec:1:2026-09-01", "amount": "12.5"})
        state.api_override_set(self.c, {}, {"key": "rec:1:2026-09-01", "amount": -20})
        self.assertEqual([tuple(r) for r in self.c.execute("SELECT key, amount FROM overrides")], [("rec:1:2026-09-01", -20.0)])
        state.api_override_delete(self.c, {}, {"key": "rec:1:2026-09-01"})
        self.assertIsNone(self.one("SELECT 1 FROM overrides"))

    def test_settings_primary_account(self):
        with self.assertRaises(ApiError):
            state.api_settings(self.c, {}, {"primary_account": "demo-card"})
        with self.assertRaises(ApiError):
            state.api_settings(self.c, {}, {"primary_account": "nope"})
        state.api_settings(self.c, {}, {"primary_account": "demo-savings"})
        self.assertEqual(db.get_setting(self.c, "primary_account"), "demo-savings")
        state.api_settings(self.c, {}, {"primary_account": ""})
        self.assertIsNone(db.get_setting(self.c, "primary_account"))

    # ------------------------------------------------------------------------------------------ budget

    def test_budget(self):
        self.c.execute("INSERT INTO accounts(id, name, display_name, kind) VALUES ('c2', 'Zeta', 'Alpha Card', 'credit')")
        self.c.execute("INSERT INTO accounts(id, name, kind, hidden) VALUES ('c3', 'Hidden Card', 'credit', 1)")
        start = TODAY.replace(day=1)
        b = budget.api_budget(self.c, {}, {})
        self.assertEqual(b["pay_accounts"], [{"id": "c2", "name": "Alpha Card", "kind": "credit"},
                                             {"id": "demo-card", "name": "Rewards Visa", "kind": "credit"},
                                             {"id": "demo-checking", "name": "Everyday Checking", "kind": "checking"},
                                             {"id": "demo-savings", "name": "High-Yield Savings", "kind": "savings"}])
        groceries = sum(r[0] for r in self.c.execute(
            "SELECT amount FROM transactions WHERE category='Groceries' AND posted>=?", (start.isoformat(),)))
        g = next(c for c in b["categories"] if c["name"] == "Groceries")
        self.assertEqual((g["budget"], g["spent"], g["pay_with"], g["usual_account"]), (600, round(-groceries, 2), "demo-card", "demo-card"))

    def test_budget_rollover_carries_unspent(self):
        last = (TODAY.replace(day=1) - timedelta(days=1)).replace(day=1)
        self.c.execute("UPDATE budgets SET rollover_from=? WHERE category='Coffee & Snacks'", (f"{last:%Y-%m}",))
        spent = sum(r[0] for r in self.c.execute("SELECT amount FROM transactions WHERE category='Coffee & Snacks' AND posted>=? "
                                                 "AND posted<?", (last.isoformat(), TODAY.replace(day=1).isoformat())))
        b = budget.api_budget(self.c, {}, {})
        c = next(c for c in b["categories"] if c["name"] == "Coffee & Snacks")
        self.assertEqual(c["carried"], max(0.0, round(60 + spent, 2)))

    def test_budget_set(self):
        with self.assertRaises(ApiError):
            budget.api_budget_set(self.c, {}, {"category": "Income", "amount": 5})
        with self.assertRaises(ApiError):   # no budget to roll over yet
            budget.api_budget_set(self.c, {}, {"category": "Travel", "rollover": True})
        with self.assertRaises(ApiError):
            budget.api_budget_set(self.c, {}, {"category": "Groceries", "pay_with": "nope"})
        budget.api_budget_set(self.c, {}, {"category": "Travel", "amount": "-250"})
        budget.api_budget_set(self.c, {}, {"category": "Travel", "rollover": True})
        budget.api_budget_set(self.c, {}, {"category": "Travel", "pay_with": "demo-checking"})
        budget.api_budget_set(self.c, {}, {"category": "Travel", "amount": 300})
        row = self.one("SELECT amount, pay_with, rollover_from FROM budgets WHERE category='Travel'")
        self.assertEqual(tuple(row), (300.0, "demo-checking", f"{TODAY:%Y-%m}"))
        budget.api_budget_set(self.c, {}, {"category": "Travel", "rollover": False})
        budget.api_budget_set(self.c, {}, {"category": "Travel", "pay_with": ""})
        self.assertEqual(tuple(self.one("SELECT pay_with, rollover_from FROM budgets WHERE category='Travel'")), (None, None))
        budget.api_budget_set(self.c, {}, {"category": "Travel", "amount": "0"})
        self.assertIsNone(self.one("SELECT 1 FROM budgets WHERE category='Travel'"))

    # ------------------------------------------------------------------------------------------ transactions

    def ids(self, **kw):
        return [t["id"] for t in transactions.api_transactions(self.c, q(**kw), {})["items"]]

    def test_transaction_filters(self):
        all_ = transactions.api_transactions(self.c, {}, {})
        self.assertEqual(all_["total"], self.one("SELECT COUNT(*) FROM transactions")[0])
        items = all_["items"]
        self.assertEqual([(t["posted"], t["id"]) for t in items],
                         sorted(((t["posted"], t["id"]) for t in items), key=lambda x: (-date.fromisoformat(x[0]).toordinal(), x[1])))
        t0 = items[0]
        for k in ("account_name", "account_kind", "recurring_name", "splits", "retail", "logo"):
            self.assertIn(k, t0)
        self.assertEqual(list(t0)[:len(db.schema.transactions.columns)], [c.name for c in db.schema.transactions.columns])
        self.c.execute("UPDATE accounts SET owner='Sam' WHERE id='demo-card'")
        card = transactions.api_transactions(self.c, q(account="demo-card", limit=3, offset=1), {})
        self.assertEqual(len(card["items"]), 3)
        self.assertEqual({t["account_name"] for t in card["items"]}, {"Rewards Visa (Sam)"})
        self.assertEqual(card["total"], self.one("SELECT COUNT(*) FROM transactions WHERE account_id='demo-card'")[0])
        rid = self.one("SELECT id FROM recurring WHERE name='Gym'")[0]
        self.c.execute("UPDATE transactions SET recurring_id=? WHERE payee='Fit Club'", (rid,))
        gym = transactions.api_transactions(self.c, q(recurring=rid), {})["items"]
        self.assertTrue(gym)
        self.assertEqual({t["recurring_name"] for t in gym}, {"Gym"})
        self.assertEqual(set(self.ids(q="PIZZA")), {r[0] for r in self.c.execute("SELECT id FROM transactions WHERE payee='Pizza Palace'")})
        month = f"{TODAY:%Y-%m}"
        self.assertEqual(set(self.ids(month=month, limit=1000)),
                         {r[0] for r in self.c.execute("SELECT id FROM transactions WHERE posted>=?", (TODAY.replace(day=1).isoformat(),))})
        self.assertEqual(set(self.ids(scope="budget", limit=1000)),
                         {r[0] for r in self.c.execute("SELECT id FROM transactions WHERE account_id<>'demo-mortgage'")})

    def test_transaction_category_filters(self):
        categories.add(self.c, "Farmers Market", "Groceries")
        tid = self.one("SELECT id FROM transactions WHERE category='Shopping' ORDER BY id")[0]
        amount = self.one("SELECT amount FROM transactions WHERE id=?", tid)[0]
        splits.set_splits(self.c, tid, [{"amount": amount / 2, "category": "Farmers Market"},
                                        {"amount": amount - amount / 2, "category": "Pharmacy"}])
        other = self.one("SELECT id FROM transactions WHERE category='Restaurants' ORDER BY id")[0]
        self.c.execute("UPDATE transactions SET category=NULL, needs_review=1 WHERE id=?", (other,))
        groceries = {r[0] for r in self.c.execute("SELECT id FROM transactions WHERE category='Groceries'")}
        self.assertEqual(set(self.ids(category="Groceries", limit=1000)), groceries | {tid})
        self.assertNotIn(tid, self.ids(category="Shopping", limit=1000))
        self.assertEqual(self.ids(category="__none__"), [other])
        self.assertEqual(self.ids(review=1), [other])

    def test_ai_log_and_apply(self):
        for i in range(30):
            self.c.execute("INSERT INTO ai_log(at, purpose) VALUES (?, 'categorize')", (f"2026-09-{i % 28 + 1:02d}",))
        log = transactions.api_ai_log(self.c, {}, {})
        self.assertEqual(len(log), 25)
        self.assertEqual([r["id"] for r in log], sorted((r["id"] for r in log), reverse=True))
        self.assertEqual(list(log[0]), [c.name for c in db.schema.ai_log.columns])
        tid = self.one("SELECT id FROM transactions WHERE category='Shopping' ORDER BY id")[0]
        out = transactions.api_ai_apply(self.c, {}, {"tx_ids": [tid], "new_category": {"name": "  groceries "}, "remember": True})
        self.assertEqual((out["category"], out["created"]), ("Groceries", False))
        out = transactions.api_ai_apply(self.c, {}, {"tx_ids": [tid], "new_category": {"name": "Pet Food", "parent": "No Such"},
                                                     "remember": True})
        self.assertEqual((out["category"], out["created"]), ("Pet Food", True))
        self.assertIsNone(self.one("SELECT parent FROM categories WHERE name='Pet Food'")[0])
        out = transactions.api_ai_apply(self.c, {}, {"tx_ids": [tid], "new_category": {"name": "Dog Food", "parent": "Groceries"},
                                                     "remember": True})
        self.assertEqual(self.one("SELECT parent FROM categories WHERE name='Dog Food'")[0], "Groceries")

    def test_recategorize(self):
        a, b, c, d = [r[0] for r in self.c.execute("SELECT id FROM transactions WHERE category='Shopping' ORDER BY id LIMIT 4")]
        self.c.execute("UPDATE transactions SET needs_review=1 WHERE id IN (?,?,?)", (a, b, c))
        self.c.execute("UPDATE transactions SET category_source='manual' WHERE id=?", (b,))
        self.c.execute("UPDATE transactions SET category=NULL, category_source=NULL WHERE id=?", (d,))
        with mock.patch.object(transactions.categorize, "categorize", return_value={"rules": 0}) as cat:
            transactions.api_recategorize(self.c, {}, {})
        self.assertEqual(sorted(cat.call_args[0][1]), sorted([a, c, d]))
        rows = {r[0]: tuple(r)[1:] for r in self.c.execute(
            "SELECT id, category, category_source, confidence FROM transactions WHERE id IN (?,?,?)", (a, b, c))}
        self.assertEqual(rows[a], (None, None, None))
        self.assertEqual(rows[b], ("Shopping", "manual", None))

    # ------------------------------------------------------------------------------------------ sync

    def test_plaid_banks(self):
        with mock.patch.object(sync.plaid, "configured", return_value=True):
            self.assertFalse(sync.plaid_banks(self.c))
            self.c.execute("INSERT INTO plaid_items(item_id, access_token, products) VALUES ('i', 'x', 'investments')")
            self.assertFalse(sync.plaid_banks(self.c))
            self.c.execute("INSERT INTO plaid_items(item_id, access_token, products) VALUES ('j', 'x', 'transactions')")
            self.assertTrue(sync.plaid_banks(self.c))

    def test_refresh_prices_asks_for_the_securities_held(self):
        self.c.execute("INSERT INTO securities(id, ticker, is_cash) VALUES ('s1','AAA',0), ('s2','BBB',0), ('s3','CCC',0), "
                       "('s4','CASH',1), ('s5',NULL,0), ('s6','ZZZ',0), ('s7','AAA',0)")
        self.c.execute("INSERT INTO holdings(account_id, security_id) VALUES ('a','s1'), ('a','s4'), ('a','s5'), ('a','s7')")
        self.c.execute("INSERT INTO inv_transactions(id, account_id, security_id, date) VALUES ('t','a','s2','2026-01-01')")
        self.c.execute("INSERT INTO manual_positions(account_id, security_id) VALUES ('a','s3')")
        with mock.patch.object(sync.prices, "refresh", return_value={}) as refresh, \
                mock.patch.object(sync.sfinvest, "recapture_all"), mock.patch.object(sync.prices, "fill_security_types"):
            sync.refresh_prices(self.c)
        self.assertEqual(sorted(refresh.call_args[0][1]), sorted(["AAA", "BBB", "CCC", sync.prices.BENCHMARK]))

    def test_investment_sync(self):
        self.c.commit()
        with mock.patch.object(sync, "refresh_prices", return_value={"AAA": 1}) as rp:
            self.assertEqual(sync.run_investment_sync(), {"items": 0, "errors": [], "prices": {}})
            rp.assert_not_called()
            self.c.execute("INSERT INTO inv_accounts(id, item_id) VALUES ('ia', 'x')")
            self.c.commit()
            self.assertEqual(sync.run_investment_sync()["prices"], {"AAA": 1})

    def test_bank_sync_logs_and_refreshes_simplefin_investments(self):
        self.c.execute("INSERT INTO inv_accounts(id, item_id, source) VALUES ('ia', 'x', 'plaid')")
        self.c.commit()
        with mock.patch.object(sync.simplefin, "sync", return_value={"new": [], "errors": ["Bank note"]}), \
                mock.patch.object(sync.merchants, "fetch_logos"), mock.patch.object(sync.realie, "refresh_due"), \
                mock.patch.object(sync, "refresh_prices") as rp:
            self.assertEqual(sync.run_sync(), {"new": 0, "categorized": mock.ANY, "bank_messages": ["Bank note"]})
            rp.assert_not_called()
            self.c.execute("UPDATE inv_accounts SET source='simplefin'")
            self.c.commit()
            sync.run_sync()
            rp.assert_called_once()
        log = self.one("SELECT ok, message FROM sync_log ORDER BY id DESC LIMIT 1")
        self.assertEqual(tuple(log), (1, "0 new transactions · bank messages: Bank note"))


if __name__ == "__main__":
    unittest.main()
