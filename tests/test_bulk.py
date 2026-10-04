"""Changing many transactions at once."""
import unittest

from sqlalchemy import insert, select, update

from runway import categorize, rules, splits
from runway.server.api import categories as api_categories
from runway.server.api import transactions as api_tx
from runway.models import Account, Transaction
from tests.shared import DbCase


class Fixture(DbCase):
    """Four transactions on a card, one of them split."""

    def setUp(self):
        super().setUp()
        self.c.execute(insert(Account).values(id="cc", name="Card", kind="credit", balance=0))
        for i, (amt, desc, cat, review) in enumerate([(-10, "SQ *JOES 1", None, 1), (-12, "SQ *JOES 2", "Shopping", 1),
                                                      (-100, "TARGET", "Shopping", 0), (-5, "OTHER", "Other", 1)]):
            self.c.execute(insert(Transaction).values(id=f"t{i}", account_id="cc", posted="2026-09-01", amount=amt,
                                                      description=desc, payee=desc, category=cat,
                                                      category_source="ai" if cat else None, needs_review=review))
        splits.set_splits(self.c, "t2", [{"amount": -60, "category": "Groceries"}, {"amount": -40, "category": "Shopping"}])

    def rows(self):
        return {r["id"]: tuple(r)[1:] for r in self.c.execute(
            select(Transaction.id, Transaction.payee, Transaction.category, Transaction.category_source,
                   Transaction.needs_review, Transaction.is_split)
            .order_by(Transaction.id))}


class BulkTests(Fixture):
    def test_category_rename_and_reviewed(self):
        self.assertEqual(categorize.bulk_update(self.c, ["t0", "t1", "t2", "nope"], category="Coffee & Snacks"), 3)
        r = self.rows()
        self.assertEqual(r["t0"][1:], ("Coffee & Snacks", "manual", 0, 0))
        self.assertEqual(r["t2"][1:], ("Coffee & Snacks", "manual", 0, 0))
        self.assertEqual(splits.get(self.c, "t2"), [])
        categorize.bulk_update(self.c, ["t0", "t1"], payee="Joe's Coffee")
        self.assertEqual({self.rows()[t][0] for t in ("t0", "t1")}, {"Joe's Coffee"})
        categorize.bulk_update(self.c, ["t3"], reviewed=True)
        self.assertEqual(self.rows()["t3"][1:4], ("Other", "manual", 0))

    def test_checks(self):
        for args, kw in [([], {"category": "Shopping"}), (["t0"], {}), (["t0"], {"category": "Nope"}),
                         ([str(i) for i in range(categorize.MAX_BULK + 1)], {"reviewed": True})]:
            with self.assertRaises(ValueError):
                categorize.bulk_update(self.c, args, **kw)

    def test_bulk_sends_back_what_was_and_restore_undoes_it(self):
        before = self.rows()
        r = api_tx.api_tx_bulk(self.c, None, {"ids": ["t0", "t1", "t2"], "category": "Coffee & Snacks", "payee": "Joe"})
        self.assertEqual(r["updated"], 3)
        was = {w["id"]: w for w in r["was"]}
        self.assertEqual((was["t0"]["category"], was["t0"]["needs_review"], was["t0"]["payee"]), (None, 1, "SQ *JOES 1"))
        self.assertEqual((was["t1"]["category"], was["t1"]["category_source"]), ("Shopping", "ai"))
        self.assertEqual([p["category"] for p in was["t2"]["splits"]], ["Groceries", "Shopping"])
        self.assertNotEqual(self.rows(), before)
        self.assertEqual(api_tx.api_tx_bulk(self.c, None, {"restore": r["was"]})["updated"], 3)
        self.assertEqual(self.rows(), before)
        self.assertEqual([(p["category"], p["amount"]) for p in splits.get(self.c, "t2")], [("Groceries", -60), ("Shopping", -40)])

    def test_restore_puts_the_payee_back_exactly(self):
        long = "ACH WEB SINGLE CO NAME " * 5
        for tid, payee in (("t0", "SQ *JOES  COFFEE"), ("t1", long)):
            self.c.execute(update(Transaction).where(Transaction.id == tid).values(payee=payee))
        was = api_tx.snapshot(self.c, ["t0", "t1"])
        categorize.bulk_update(self.c, ["t0", "t1"], payee="Joe")
        self.assertEqual(api_tx.restore(self.c, was), 2)
        self.assertEqual(self.rows()["t0"][0], "SQ *JOES  COFFEE")
        self.assertEqual(self.rows()["t1"][0], long)
        self.assertEqual(api_tx.restore(self.c, [{"id": "t0", "payee": 7}]), 1)
        self.assertIsNone(self.rows()["t0"][0])

    def test_restore_skips_what_is_gone_and_unknown_categories(self):
        was = api_tx.snapshot(self.c, ["t1"])
        was[0]["category"] = "No such category"
        was.append({"id": "gone", "category": "Other"})
        self.assertEqual(api_tx.restore(self.c, [*was, "junk"]), 1)
        self.assertEqual(self.rows()["t1"][1], None)

    def test_single_and_ai_changes_send_back_what_was(self):
        r = api_tx.api_tx_category(self.c, None, {"category": "Coffee & Snacks"}, "t1")
        self.assertEqual([(w["id"], w["category"], w["needs_review"]) for w in r["was"]], [("t1", "Shopping", 1)])
        r = api_tx.api_ai_apply(self.c, None, {"tx_ids": ["t0", "t3"], "category": "Shopping"})
        self.assertEqual({w["id"]: w["category"] for w in r["was"]}, {"t0": None, "t3": "Other"})


class RuleApplyTests(Fixture):
    def test_apply_says_what_changed_so_it_can_be_undone(self):
        self.c.execute(update(Transaction).where(Transaction.id == "t1").values(category_source="manual"))
        rid = rules.save(self.c, {"match": "joes", "category": "Coffee & Snacks", "rename": "Joe's"})
        before = self.rows()
        r = api_categories.api_rule_apply(self.c, None, None, str(rid))
        self.assertEqual(r["updated"], 2)
        self.assertTrue(r["undoable"])
        by = {c["id"]: c for c in r["changed"]}
        self.assertEqual(set(by), {"t0", "t1"})
        self.assertEqual((by["t0"]["was_category"], by["t0"]["was_payee"], by["t0"]["was_needs_review"]), (None, "SQ *JOES 1", 1))
        self.assertEqual((by["t1"]["was_category"], by["t1"]["was_source"]), ("Shopping", "manual"))
        rows = [{"id": c["id"], "category": c["was_category"], "category_source": c["was_source"], "confidence": c["was_confidence"],
                 "needs_review": c["was_needs_review"], "payee": c["was_payee"], "is_split": c["was_split"]} for c in r["changed"]]
        api_tx.restore(self.c, rows)
        self.assertEqual(self.rows(), before)

    def test_apply_keeps_a_split_when_it_only_renames(self):
        rid = rules.save(self.c, {"match": "target", "rename": "Target Co"})
        r = api_categories.api_rule_apply(self.c, None, None, str(rid))
        self.assertEqual([(c["id"], c["was_split"]) for c in r["changed"]], [("t2", 1)])
        api_tx.restore(self.c, [{"id": "t2", "category": "Shopping", "category_source": "ai", "needs_review": 0, "payee": "TARGET", "is_split": 1}])
        self.assertEqual(len(splits.get(self.c, "t2")), 2)

    def test_apply_with_nothing_to_change(self):
        rid = rules.save(self.c, {"match": "nothing like this", "category": "Shopping"})
        self.assertEqual(api_categories.api_rule_apply(self.c, None, None, str(rid))["changed"], [])


if __name__ == "__main__":
    unittest.main()
