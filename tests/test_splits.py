"""Splitting a transaction across categories."""
import unittest
from datetime import date

from sqlalchemy import delete, func, insert, select, update

from runway.domain import categories, categorize, forecast, splits
from runway import server
from runway.providers import simplefin
from runway.storage.models import Budget, Category, Transaction, TxSplit
from runway.server.api import transactions as api_tx
from tests.shared import TODAY, LedgerCase, ts


class SplitTests(LedgerCase):
    """One transaction spread across categories: the parts, not the transaction, are what gets counted."""

    def setUp(self):
        super().setUp()
        self.acct("cc", "credit", -100.0)
        self.tx("cc", "2026-09-10", -100.0, "TARGET", "Shopping")
        self.tx_id = self.conn.execute(select(Transaction.id)).fetchone()[0]

    def split(self, *parts):
        return splits.set_splits(self.conn, self.tx_id, [{"amount": a, "category": c} for a, c in parts])

    def test_parts_must_add_up_to_the_transaction(self):
        with self.assertRaises(splits.SplitError):
            self.split((-60.0, "Groceries"), (-30.0, "Shopping"))
        with self.assertRaises(splits.SplitError):
            self.split((-100.0, "Groceries"))
        with self.assertRaises(splits.SplitError):
            self.split((-60.0, "Groceries"), (-40.0, "Nonsense"))
        self.assertEqual(self.conn.execute(select(func.count()).select_from(TxSplit)).fetchone()[0], 0)

    def test_budget_and_reports_count_the_parts(self):
        self.conn.execute(insert(Budget).values(category="Groceries", amount=500))
        self.split((-60.0, "Groceries"), (-40.0, "Shopping"))
        self.conn.commit()
        spent = {c["name"]: c["spent"] for c in server.api_budget(self.conn, {"month": ["2026-09"]}, None)["categories"]}
        self.assertEqual((spent["Groceries"], spent["Shopping"]), (60.0, 40.0))
        self.assertEqual(forecast.budget_plan(self.conn, date(2026, 9, 20))[0]["spent"], 60.0)
        cf = server.api_cashflow(self.conn, {"month": ["2026-09"]}, None)
        self.assertEqual({n["name"]: n["value"] for n in cf["spending"]}, {"Groceries": 60.0, "Shopping": 40.0})

    def test_the_list_shows_and_filters_by_the_parts(self):
        self.split((-60.0, "Groceries"), (-40.0, "Shopping"))
        self.conn.commit()
        item = server.api_transactions(self.conn, {}, None)["items"][0]
        self.assertEqual(item["is_split"], 1)
        self.assertEqual([(s["category"], s["amount"]) for s in item["splits"]], [("Groceries", -60.0), ("Shopping", -40.0)])
        for cat in ("Groceries", "Shopping"):
            self.assertEqual(len(server.api_transactions(self.conn, {"category": [cat]}, None)["items"]), 1)
        self.assertEqual(server.api_transactions(self.conn, {"category": ["Travel"]}, None)["items"], [])

    def test_under_a_category_filter_a_pick_changes_only_that_part(self):
        self.conn.execute(insert(Category).values(name="Produce", parent="Groceries"))
        self.split((-50.0, "Groceries"), (-10.0, "Produce"), (-40.0, "Shopping"))
        got = server.api_transactions(self.conn, {"category": ["Groceries"]}, None)
        self.assertEqual(got["items"][0]["match"], {"amount": -60.0, "categories": ["Groceries", "Produce"]})
        r = api_tx.api_tx_category(self.conn, None, {"category": "Travel", "only": "Groceries"}, self.tx_id)
        self.assertEqual([(p["category"], p["amount"]) for p in splits.get(self.conn, self.tx_id)],
                         [("Travel", -50.0), ("Travel", -10.0), ("Shopping", -40.0)])
        self.assertIsNone(r["offer_rule"])
        api_tx.api_tx_bulk(self.conn, None, {"ids": [self.tx_id], "category": "Travel", "only": "Shopping"})
        row = self.conn.execute(select(Transaction.category, Transaction.category_source, Transaction.is_split)).fetchone()
        self.assertEqual(tuple(row), ("Travel", "manual", 0))
        api_tx.api_tx_bulk(self.conn, None, {"restore": r["was"]})
        self.assertEqual([(p["category"], p["amount"]) for p in splits.get(self.conn, self.tx_id)],
                         [("Groceries", -50.0), ("Produce", -10.0), ("Shopping", -40.0)])

    def test_a_pick_without_a_filter_or_outside_it_changes_the_whole_transaction(self):
        self.split((-60.0, "Groceries"), (-40.0, "Shopping"))
        api_tx.api_tx_category(self.conn, None, {"category": "Travel", "only": "Gifts & Donations"}, self.tx_id)
        row = self.conn.execute(select(Transaction.category, Transaction.is_split)).fetchone()
        self.assertEqual(tuple(row), ("Travel", 0))

    def test_categorizing_a_split_transaction_puts_it_back_together(self):
        self.split((-60.0, "Groceries"), (-40.0, "Shopping"))
        categorize.set_category(self.conn, self.tx_id, "Travel")
        row = self.conn.execute(select(Transaction.category, Transaction.is_split)).fetchone()
        self.assertEqual((row["category"], row["is_split"]), ("Travel", 0))
        self.assertEqual(self.conn.execute(select(func.count()).select_from(TxSplit)).fetchone()[0], 0)

    def test_rules_and_review_leave_a_split_alone(self):
        self.split((-60.0, "Groceries"), (-40.0, "Shopping"))
        self.conn.execute(update(Transaction).values(category=None, category_source=None))
        server.api_rule_add(self.conn, None, {"match": "target", "category": "Travel", "apply": True})
        categorize.categorize(self.conn, use_ai=False)
        row = self.conn.execute(select(Transaction.category, Transaction.is_split, Transaction.needs_review)).fetchone()
        self.assertEqual((row["category"], row["is_split"], row["needs_review"]), (None, 1, 0))

    def test_renaming_and_removing_a_category_follow_the_parts(self):
        self.split((-60.0, "Groceries"), (-40.0, "Shopping"))
        categories.rename(self.conn, "Groceries", "Food shopping")
        self.assertEqual([s["category"] for s in splits.get(self.conn, self.tx_id)], ["Food shopping", "Shopping"])
        categories.remove(self.conn, "Food shopping")
        row = self.conn.execute(select(Transaction.category, Transaction.is_split, Transaction.needs_review)).fetchone()
        self.assertEqual((row["category"], row["is_split"], row["needs_review"]), (None, 0, 1))
        self.assertEqual(self.conn.execute(select(func.count()).select_from(TxSplit)).fetchone()[0], 0)

    def test_a_split_pending_transaction_keeps_its_parts_when_it_posts(self):
        self.split((-60.0, "Groceries"), (-40.0, "Shopping"))
        self.tx("cc", "2026-09-11", -100.0, "TARGET")
        posted = self.conn.execute(select(Transaction.id).where(Transaction.id != self.tx_id)).fetchone()[0]
        splits.carry_over(self.conn, self.tx_id, posted, -100.0)
        self.assertEqual(len(splits.get(self.conn, posted)), 2)
        self.assertEqual(self.conn.execute(select(Transaction.is_split)
                                           .where(Transaction.id == posted)).fetchone()[0], 1)
        self.split((-60.0, "Groceries"), (-40.0, "Shopping"))
        splits.carry_over(self.conn, self.tx_id, posted, -120.0)
        self.assertEqual(splits.get(self.conn, self.tx_id), [])

    def test_parts_of_a_deleted_transaction_are_pruned(self):
        self.split((-60.0, "Groceries"), (-40.0, "Shopping"))
        self.conn.execute(delete(Transaction).where(Transaction.id == self.tx_id))
        splits.prune(self.conn)
        self.assertEqual(self.conn.execute(select(func.count()).select_from(TxSplit)).fetchone()[0], 0)


class SplitAmountChangeTests(LedgerCase):
    def setUp(self):
        super().setUp()
        self.acct("cc", "credit", -50.0)
        self.tx("cc", "2026-09-10", -50.0, "BISTRO", "Restaurants")
        self.tx_id = self.conn.execute(select(Transaction.id)).fetchone()[0]

    def test_parts_follow_a_new_amount(self):
        splits.set_splits(self.conn, self.tx_id, [{"amount": -30, "category": "Restaurants"}, {"amount": -20, "category": "Gifts & Donations"}])
        self.conn.execute(update(Transaction).where(Transaction.id == self.tx_id).values(amount=-60))
        splits.follow_amount(self.conn, self.tx_id, -60.0)
        self.assertEqual([p["amount"] for p in splits.get(self.conn, self.tx_id)], [-36.0, -24.0])
        self.assertEqual(self.conn.execute(select(Transaction.needs_review)).fetchone()[0], 1)

    def test_odd_cents_still_add_up(self):
        splits.set_splits(self.conn, self.tx_id, [{"amount": -16.67, "category": "Restaurants"},
                                                  {"amount": -16.67, "category": "Groceries"},
                                                  {"amount": -16.66, "category": "Shopping"}])
        splits.follow_amount(self.conn, self.tx_id, -51.01)
        self.assertEqual(round(sum(p["amount"] for p in splits.get(self.conn, self.tx_id)), 2), -51.01)

    def test_simplefin_update_in_place_rescales(self):
        def payload(amount):
            return {"errors": [], "accounts": [{"org": {"name": "Bank"}, "id": "A1", "name": "Card", "currency": "USD",
                                                "balance": amount, "balance-date": ts(TODAY), "transactions": [
                                                    {"id": "t1", "posted": ts(date(2026, 9, 20)), "amount": amount, "description": "BISTRO"}]}]}
        simplefin.store_payload(self.conn, payload("-50.00"), date(2026, 9, 1))
        splits.set_splits(self.conn, "A1|t1", [{"amount": -30, "category": "Restaurants"}, {"amount": -20, "category": "Shopping"}])
        simplefin.store_payload(self.conn, payload("-60.00"), date(2026, 9, 1))
        self.assertEqual([p["amount"] for p in splits.get(self.conn, "A1|t1")], [-36.0, -24.0])


if __name__ == "__main__":
    unittest.main()
