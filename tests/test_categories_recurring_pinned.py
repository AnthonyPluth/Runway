"""Pins behavior of categories, rules, categorization and recurring items that other tests don't reach: what a
removal or rename cascades to, the recurring list and its edits, auto-matching edge cases, and bulk edits."""
import json
import unittest
from datetime import date

from sqlalchemy import delete, func, insert, literal, select, update

from runway import categories, categorize, db, recurring, rules
from runway.server.api import categories as api_categories
from runway.server.api import recurring as api_recurring
from runway.server.common import ApiError
from runway.models import (Account, AiLog, Budget, Category, Override, Recurring, RetailItem, RetailItemMemory,
                           RetailOrder, Rule, Transaction, TxSplit)
from tests.shared import DbCase


class Base(DbCase):
    def setUp(self):
        super().setUp()
        self.c.execute(insert(Account), [{"id": "chk", "name": "Checking", "kind": "checking", "balance": 0,
                                          "owner": "Sara"},
                                         {"id": "cc", "name": "Card", "kind": "credit", "balance": 0, "owner": None}])
        self.n = 0

    def tx(self, amount, desc, acct="chk", posted=None, category=None, source=None, review=0, payee=None):
        self.n += 1
        tid = f"{acct}|{self.n}"
        self.c.execute(insert(Transaction).values(id=tid, account_id=acct, posted=posted or f"2026-09-{self.n:02d}",
                                                  amount=amount, description=desc,
                                                  payee=categorize.clean_payee(desc) if payee is None else payee,
                                                  category=category, category_source=source, needs_review=review))
        return tid

    def one(self, stmt):
        return self.c.execute(stmt).fetchone()

    def col(self, stmt):
        return [r[0] for r in self.c.execute(stmt)]


class CategoryCascadeTests(Base):
    def setUp(self):
        super().setUp()
        self.whole = self.tx(-10, "PHARMACY ONE", category="Pharmacy", source="manual")
        self.split = self.tx(-100, "COSTCO", category="Groceries", source="manual")
        self.c.execute(update(Transaction).where(Transaction.id == self.split).values(is_split=1))
        self.c.execute(insert(TxSplit), [{"tx_id": self.split, "amount": -60, "category": "Groceries"},
                                         {"tx_id": self.split, "amount": -40, "category": "Pharmacy"}])
        self.c.execute(insert(RetailOrder).values(id="amazon:1", retailer="amazon", order_number="1"))
        self.c.execute(insert(RetailItem).values(order_id="amazon:1", title="Aspirin", category="Pharmacy",
                                                 category_source="ai", confidence=0.9))
        self.c.execute(insert(RetailItemMemory).values(key="aspirin", category="Pharmacy"))
        self.c.execute(insert(Budget).values(category="Pharmacy", amount=40))
        self.c.execute(insert(Rule).values(match="pharm", category="Pharmacy"))
        self.c.execute(insert(Rule).values(match="costco", split=json.dumps([{"category": "Groceries", "percent": 50},
                                                                             {"category": "Pharmacy", "percent": 50}])))
        self.c.execute(insert(Rule).values(match="rx", category="Pharmacy", rename="Rx"))

    def test_remove_without_a_replacement(self):
        self.assertEqual(categories.remove(self.c, "Pharmacy"), 2)
        w = self.one(select(Transaction.category, Transaction.category_source, Transaction.confidence,
                            Transaction.needs_review)
                     .where(Transaction.id == self.whole))
        s = self.one(select(Transaction.is_split, Transaction.category, Transaction.category_source,
                            Transaction.confidence, Transaction.needs_review)
                     .where(Transaction.id == self.split))
        self.assertEqual(tuple(w), (None, None, None, 1))
        self.assertEqual(tuple(s), (0, None, None, None, 1))
        self.assertEqual(self.col(select(func.count()).select_from(TxSplit)), [0])
        self.assertEqual(tuple(self.one(select(RetailItem.category, RetailItem.category_source,
                                               RetailItem.confidence))), (None, None, None))
        self.assertEqual(self.col(select(RetailItemMemory.key)), [])
        self.assertEqual(self.col(select(Budget.category)), [])
        self.assertEqual([(r["match"], r["category"], r["split"], r["rename"])
                          for r in self.c.execute(select(Rule).order_by(Rule.id))],
                         [("rx", None, None, "Rx")])
        self.assertIsNone(self.one(select(Category.name).where(Category.name == "Pharmacy")))

    def test_remove_into_another_category(self):
        self.assertEqual(categories.remove(self.c, "Pharmacy", "Medical"), 2)
        self.assertEqual(self.col(select(Transaction.category).where(Transaction.id == self.whole)), ["Medical"])
        self.assertEqual(self.col(select(TxSplit.category).order_by(TxSplit.id)), ["Groceries", "Medical"])
        self.assertEqual(self.col(select(RetailItem.category)), ["Medical"])
        self.assertEqual(self.col(select(RetailItemMemory.category)), ["Medical"])
        self.assertEqual(self.col(select(Budget.category)), [])   # its budget goes; Medical keeps its own
        self.assertEqual(self.col(select(Rule.category).order_by(Rule.id)), ["Medical", None, "Medical"])
        self.assertEqual(json.loads(self.col(select(Rule.split)
                                             .where(Rule.match == "costco"))[0])[1]["category"], "Medical")

    def test_rename_follows_everywhere(self):
        categories.add(self.c, "Vitamins", "Pharmacy")
        categories.rename(self.c, "Pharmacy", "Health")
        self.assertEqual(self.col(select(Category.parent).where(Category.name == "Vitamins")), ["Health"])
        self.assertEqual(self.col(select(Transaction.category).where(Transaction.id == self.whole)), ["Health"])
        self.assertEqual(self.col(select(TxSplit.category).order_by(TxSplit.id)), ["Groceries", "Health"])
        self.assertEqual(self.col(select(RetailItem.category)), ["Health"])
        self.assertEqual(self.col(select(RetailItemMemory.category)), ["Health"])
        self.assertEqual(self.col(select(Budget.category)), ["Health"])
        self.assertEqual(self.col(select(Rule.category).order_by(Rule.id)), ["Health", None, "Health"])
        categories.rename(self.c, "Health", "health")   # only the case changes: allowed
        self.assertEqual(self.col(select(Budget.category)), ["health"])

    def test_errors(self):
        cases = [
            (lambda: categories.remove(self.c, "Transfer"), "Transfer is used by Runway itself and can't be removed"),
            (lambda: categories.remove(self.c, "Nope"), "Category not found"),
            (lambda: categories.remove(self.c, "Pharmacy", "Pharmacy"), "Pick a different category to move things to"),
            (lambda: categories.remove(self.c, "Pharmacy", "Nope"), "Pick a different category to move things to"),
            (lambda: categories.rename(self.c, "Nope", "x"), "Category not found"),
            (lambda: categories.rename(self.c, "Pharmacy", " "), "Give the category a name"),
            (lambda: categories.rename(self.c, "Pharmacy", "GROCERIES"), "There's already a category called GROCERIES"),
            (lambda: categories.add(self.c, " "), "Give the category a name"),
            (lambda: categories.add(self.c, "x" * 61), "That name is too long"),
            (lambda: categories.add(self.c, "x", "Nope"), "Parent category not found"),
            (lambda: categories.move(self.c, "Nope", None), "Category not found"),
            (lambda: categories.move(self.c, "Pharmacy", "Nope"), "Parent category not found"),
            (lambda: categories.set_look(self.c, "Nope", None, None), "Category not found"),
            (lambda: categories.set_look(self.c, "Pharmacy", "ab", None), "Pick an emoji for the icon"),
            (lambda: categories.set_look(self.c, "Pharmacy", None, "red"), "Pick a color"),
        ]
        for fn, msg in cases:
            with self.assertRaises(categories.CategoryError) as e:
                fn()
            self.assertEqual(str(e.exception), msg)
        categories.add(self.c, "Vitamins", "Pharmacy")
        with self.assertRaises(categories.CategoryError) as e:
            categories.remove(self.c, "Pharmacy")
        self.assertEqual(str(e.exception), "Remove or move the subcategories of Pharmacy first")

    def test_move_takes_the_new_parents_kind_and_looks(self):
        categories.add(self.c, "Pets")
        categories.add(self.c, "Vet", "Pets")
        categories.add(self.c, "Side", "Income")
        self.assertEqual(tuple(self.one(select(Category.is_transfer, Category.is_income)
                                        .where(Category.name == "Side"))), (0, 1))
        categories.move(self.c, "Vet", "Transfer")
        self.assertEqual(tuple(self.one(select(Category.parent, Category.is_transfer, Category.is_income)
                                        .where(Category.name == "Vet"))), ("Transfer", 1, 0))
        categories.move(self.c, "Vet", None)
        self.assertEqual(tuple(self.one(select(Category.parent, Category.is_transfer)
                                        .where(Category.name == "Vet"))), (None, 1))
        categories.set_look(self.c, "Pets", " 🐶 ", "#AABBCC")
        self.assertEqual(tuple(self.one(select(Category.icon, Category.color)
                                        .where(Category.name == "Pets"))), ("🐶", "#aabbcc"))
        categories.set_look(self.c, "Pets", "", "")
        self.assertEqual(tuple(self.one(select(Category.icon, Category.color)
                                        .where(Category.name == "Pets"))), (None, None))
        # an orphan (its parent removed by hand) shows at the top level; deeper nesting gets flattened
        self.c.execute(insert(Category), [{"name": "Lost", "is_transfer": 0, "is_income": 0, "parent": "Gone"},
                                          {"name": "Deep", "is_transfer": 0, "is_income": 0, "parent": "Vitamins"}])
        categories.add(self.c, "Vitamins", "Pharmacy")
        lost = next(c for c in categories.all_categories(self.c) if c["name"] == "Lost")
        self.assertEqual((lost["parent"], lost["depth"], lost["top"]), (None, 0, "Lost"))
        self.assertEqual(categories.flatten(self.c), 1)
        self.assertEqual(self.col(select(Category.parent).where(Category.name == "Deep")), ["Pharmacy"])

    def test_category_list_counts_split_parts(self):
        cats = {c["name"]: c for c in api_categories.api_categories(self.c, None, None)}
        self.assertEqual((cats["Pharmacy"]["transactions"], cats["Groceries"]["transactions"], cats["Travel"]["transactions"]), (2, 1, 0))

    def test_rule_list_and_delete(self):
        self.c.execute(insert(Rule).values(match="", account_id="chk", review=1))
        out = api_categories.api_rules(self.c, None, None)
        self.assertEqual([r["match"] for r in out], ["costco", "pharm", "rx", ""])
        self.assertEqual(out[-1]["summary"], "in Checking (Sara)")
        api_categories.api_rule_delete(self.c, None, None, str(out[0]["id"]))
        self.assertEqual(self.col(select(Rule.match).order_by(Rule.id)), ["pharm", "rx", ""])


class CategorizePinnedTests(Base):
    def test_bulk_update(self):
        a = self.tx(-5, "A", category="Shopping", source="ai", review=1)
        b = self.tx(-5, "B", review=1)
        s = self.tx(-10, "S", category="Groceries", source="manual")
        self.c.execute(update(Transaction).where(Transaction.id == s).values(is_split=1))
        self.c.execute(insert(TxSplit), [{"tx_id": s, "amount": -5, "category": "Groceries"},
                                         {"tx_id": s, "amount": -5, "category": "Shopping"}])
        self.assertEqual(categorize.bulk_update(self.c, [a, b, a, "nope"], reviewed=True), 2)
        self.assertEqual([tuple(r) for r in self.c.execute(select(Transaction.category_source, Transaction.needs_review)
                                                           .where(Transaction.id.in_([a, b]))
                                                           .order_by(Transaction.id))],
                         [("manual", 0), (None, 0)])
        self.assertEqual(categorize.bulk_update(self.c, [s, b], category="Travel", payee="  New   Name "), 2)
        self.assertEqual([tuple(r) for r in self.c.execute(select(Transaction.category, Transaction.payee,
                                                                  Transaction.is_split, Transaction.category_source)
                                                           .where(Transaction.id.in_([b, s]))
                                                           .order_by(Transaction.id))],
                         [("Travel", "New Name", 0, "manual"), ("Travel", "New Name", 0, "manual")])
        self.assertEqual(self.col(select(func.count()).select_from(TxSplit)), [0])
        ids = [f"x{i}" for i in range(700)] + [a]
        self.assertEqual(categorize.bulk_update(self.c, ids, payee="Z"), 1)   # more than one chunk

    def test_remember_updates_matching_open_transactions(self):
        a = self.tx(-5, "BLUE BOTTLE 1", review=1, payee="Blue Bottle")
        b = self.tx(-6, "x", payee="Cafe", review=0, category="Coffee & Snacks", source="ai")
        self.c.execute(update(Transaction).where(Transaction.id == b).values(description="SQ *BLUE BOTTLE 2"))
        c = self.tx(-7, "BLUE BOTTLE 3", category="Shopping", source="manual")
        d = self.tx(-8, "BLUE_BOTTLE 4", review=1)   # '_' isn't a wildcard
        e = self.tx(-9, "BLUE BOTTLE 5", review=1)
        self.c.execute(update(Transaction).where(Transaction.id == e).values(category="Shopping", category_source="ai"))
        f = self.tx(-9, "PAID blue bottle xx", review=1, payee="Other")
        self.assertEqual(categorize.set_category(self.c, a, "Coffee & Snacks", remember=True), 2)
        self.assertEqual([tuple(r) for r in self.c.execute(select(Transaction.id, Transaction.category,
                                                                  Transaction.category_source)
                                                           .order_by(Transaction.id))],
                         [(a, "Coffee & Snacks", "manual"), (b, "Coffee & Snacks", "ai"), (c, "Shopping", "manual"),
                          (d, None, None), (e, "Coffee & Snacks", "rule"), (f, "Coffee & Snacks", "rule")])
        self.assertEqual([tuple(r) for r in self.c.execute(select(Rule.match, Rule.category))],
                         [("blue bottle", "Coffee & Snacks")])

    def test_ai_log_keeps_the_last_200(self):
        for i in range(205):
            self.c.execute(insert(AiLog).values(purpose="x", model="m", merchants=i))
        db.set_setting(self.c, "openrouter_api_key", "k")
        t = self.tx(-5, "SOMEWHERE")
        group = [[dict(self.one(select(Transaction, literal("checking").label("kind")).where(Transaction.id == t)))]]
        out = categorize.ask_model(self.c, group, lambda *a: '[{"i": 0, "category": "Travel", "confidence": 0.8}]')
        self.assertEqual(out, [("Travel", 0.8)])
        self.assertEqual(self.col(select(func.count()).select_from(AiLog)), [200])
        last = self.one(select(AiLog.purpose, AiLog.merchants, AiLog.answered, AiLog.new_cats, AiLog.ok, AiLog.message)
                        .order_by(AiLog.id.desc()))
        self.assertEqual(tuple(last), ("sync", 1, 1, 0, 1, "Suggested a category for 1 of 1 merchants"))

    def test_ai_answers_and_what_waits_in_review(self):
        db.set_setting(self.c, "openrouter_api_key", "k")
        t1 = self.tx(-5, "SHOP ONE")
        t2 = self.tx(-5, "MOVE MONEY")
        t3 = self.tx(-5, "MYSTERY")
        t4 = self.tx(-5, "PAYDAY")
        answer = {"Shop One": ("Shopping", 0.95), "Move Money": ("Transfer", 0.99), "Mystery": (None, 0), "Payday": ("Income", 0.99)}

        def caller(_k, _m, prompt):
            txs = json.loads(prompt.split("Transactions (JSON):\n")[1].split("\n")[0])
            return json.dumps([{"i": t["i"], "category": answer[t["payee"]][0], "confidence": answer[t["payee"]][1]} for t in txs])
        counts = categorize.categorize(self.c, caller=caller)
        self.assertEqual(counts, {"auto": 0, "rule": 0, "history": 0, "ai": 1, "review": 3})
        self.assertEqual([tuple(r) for r in self.c.execute(select(Transaction.id, Transaction.category,
                                                                  Transaction.category_source, Transaction.needs_review)
                                                           .order_by(Transaction.id))],
                         [(t1, "Shopping", "ai", 0), (t2, "Transfer", "ai", 1), (t3, None, None, 1), (t4, "Income", "ai", 1)])


class RecurringPinnedTests(Base):
    def setUp(self):
        super().setUp()
        for d in ("2026-06-03", "2026-07-03", "2026-08-03", "2026-09-03"):
            self.tx(-15.49, "STREAMFLIX 55", posted=d)
        self.tx(-15.49, "STREAMFLIX CC", acct="cc", posted="2026-09-04")
        self.tx(15.49, "STREAMFLIX REFUND", posted="2026-09-05")
        self.tx(-100, "STREAMFLIX GIFT CARDS", posted="2026-09-06")
        self.tx(-5, "A_%B", posted="2026-09-07")
        self.tx(-5, "AXXB", posted="2026-09-08")
        self.c.execute(insert(Recurring), [{"name": "Streaming", "account_id": "chk", "amount": -15.49,
                                            "frequency": "monthly", "anchor_date": "2026-06-03", "match": "streamflix",
                                            "amount_mode": "fixed"},
                                           {"name": "Odd", "account_id": "chk", "amount": -5, "frequency": "monthly",
                                            "anchor_date": "2026-06-07", "match": "a_%b", "amount_mode": "fixed"},
                                           {"name": "Short", "account_id": "chk", "amount": -5, "frequency": "monthly",
                                            "anchor_date": "2026-06-07", "match": "ax", "amount_mode": "fixed"}])
        self.c.execute(insert(Recurring).values(name="Off", account_id="chk", amount=-100, frequency="monthly",
                                                anchor_date="2026-06-07", match="streamflix gift", active=0))
        self.c.execute(update(Recurring).where(Recurring.id == 1).values(amount_min=10, amount_max=20))   # not the gift cards

    def ids(self, rid):
        return self.col(select(Transaction.id).where(Transaction.recurring_id == rid).order_by(Transaction.id))

    def test_auto_match_edges(self):
        self.assertEqual(recurring.auto_match(self.c), 5)
        self.assertEqual(self.ids(1), ["chk|1", "chk|2", "chk|3", "chk|4"])   # not the other account, the refund, the gift cards
        self.assertEqual(self.ids(2), ["chk|8"])                               # literal text, no wildcards
        self.assertEqual(self.ids(3), [])                                     # too short to match on
        self.assertEqual(self.ids(4), [])                                     # inactive
        self.assertEqual(recurring.auto_match(self.c), 0)
        # Without its amount range, any amount with its text is a payment for it: the gift cards too.
        self.c.execute(update(Recurring).where(Recurring.id == 1).values(amount_min=None, amount_max=None))
        self.assertEqual(recurring.auto_match(self.c), 1)
        self.assertEqual(self.ids(1), ["chk|1", "chk|2", "chk|3", "chk|4", "chk|7"])

    def test_list_edit_and_delete(self):
        recurring.auto_match(self.c)
        self.c.execute(insert(Override), [{"key": "rec:1:2026-10-03", "amount": -20},
                                          {"key": "rec:10:2026-10-03", "amount": -20}])
        self.c.execute(insert(Recurring).values(id=10, name="Ten", account_id="chk", amount=-1, frequency="monthly",
                                                anchor_date="2026-06-01"))
        items = {i["name"]: i for i in api_recurring.api_recurring(self.c, None, None)}
        s = items["Streaming"]
        self.assertEqual(list(s)[:13], ["id", "name", "account_id", "amount", "frequency", "anchor_date", "match", "end_date",
                                        "active", "amount_mode", "dates", "amount_min", "amount_max"])
        self.assertEqual((s["account_name"], s["matched_count"], s["last_matched"]["posted"], s["expected_amount"]),
                         ("Checking (Sara)", 4, "2026-09-03", -15.49))
        self.assertEqual([i["name"] for i in api_recurring.api_recurring(self.c, None, None)], ["Odd", "Short", "Streaming", "Ten", "Off"])
        # changing the merchant text drops links that no longer fit
        body = {"name": "Streaming", "account_id": "chk", "amount": -15.49, "anchor_date": "2026-06-03", "match": "Streamflix 55"}
        self.assertEqual(api_recurring.api_recurring_update(self.c, None, body, "1"), {"ok": True, "linked": 0, "amount_min": None, "amount_max": None})
        self.assertEqual(self.ids(1), ["chk|1", "chk|2", "chk|3", "chk|4"])
        body["match"] = "flix cc"
        self.assertEqual(api_recurring.api_recurring_update(self.c, None, body, "1"), {"ok": True, "linked": 0, "amount_min": None, "amount_max": None})
        self.assertEqual(self.ids(1), [])
        body["account_id"] = "cc"
        self.assertEqual(api_recurring.api_recurring_update(self.c, None, body, "1"), {"ok": True, "linked": 1, "amount_min": None, "amount_max": None})
        with self.assertRaises(ApiError):
            api_recurring.api_recurring_update(self.c, None, body, "99")
        api_recurring.api_recurring_delete(self.c, None, None, "1")
        self.assertEqual(self.col(select(Override.key)), ["rec:10:2026-10-03"])
        self.assertEqual(self.col(select(func.count()).select_from(Transaction)
                                  .where(Transaction.recurring_id == 1)), [0])
        self.assertIsNone(self.one(select(Recurring.id).where(Recurring.id == 1)))

    def test_missed_and_dismiss(self):
        recurring.auto_match(self.c)
        self.c.execute(delete(Transaction).where(Transaction.posted == "2026-08-03"))
        missed = recurring.missed(self.c, date(2026, 9, 20))
        self.assertEqual([(m["name"], m["date"], m["account_name"]) for m in missed],
                         [("Short", "2026-09-08", "Checking (Sara)"), ("Odd", "2026-08-07", "Checking (Sara)"),
                          ("Short", "2026-08-07", "Checking (Sara)"), ("Streaming", "2026-08-03", "Checking (Sara)")])
        with self.assertRaises(ApiError):
            api_recurring.api_recurring_dismiss(self.c, None, {"key": "card:x"})
        api_recurring.api_recurring_dismiss(self.c, None, {"key": "rec:1:2026-08-03"})
        api_recurring.api_recurring_dismiss(self.c, None, {"key": "rec:1:2026-08-03"})
        self.assertNotIn("Streaming", [m["name"] for m in recurring.missed(self.c, date(2026, 9, 20))])

    def test_add_and_link(self):
        r = api_recurring.api_recurring_add(self.c, None, {"name": "Gifts", "account_id": "chk", "amount": "-100",
                                                           "anchor_date": "2026-09-06", "match": " Gift Cards "})
        self.assertEqual(r, {"ok": True, "id": 5, "linked": 1})
        self.assertEqual(tuple(self.one(select(Recurring.match, Recurring.amount_mode, Recurring.active,
                                               Recurring.dates)
                                        .where(Recurring.id == r["id"]))),
                         ("gift cards", "fixed", 1, None))
        with self.assertRaises(ApiError):
            api_recurring.api_recurring_add(self.c, None, {"name": "X", "account_id": "nope", "amount": 1, "anchor_date": "2026-01-01"})
        self.c.execute(insert(Recurring).values(name="Blank", account_id="chk", amount=-5, frequency="monthly",
                                                anchor_date="2026-06-07"))
        blank = self.col(select(Recurring.id).where(Recurring.name == "Blank"))[0]
        recurring.link(self.c, "chk|9", blank)
        self.assertEqual(self.col(select(Recurring.match).where(Recurring.id == blank)), ["axxb"])
        self.assertEqual(api_recurring.api_tx_recurring(self.c, None, {}, "chk|9"), {"ok": True})
        self.assertEqual(self.col(select(Transaction.recurring_id).where(Transaction.id == "chk|9")), [0])
        with self.assertRaises(ApiError):
            api_recurring.api_tx_recurring(self.c, None, {"recurring_id": 999}, "chk|9")


class RulesPinnedTests(Base):
    def test_remember_and_offer(self):
        t = self.tx(-5, "BLUE BOTTLE")
        self.assertEqual(categorize.rule_offer(self.c, t, "Coffee & Snacks"),
                         {"merchant": "Blue Bottle", "match": "blue bottle", "replaces": None})
        self.c.execute(insert(Rule).values(match="blue bottle", match_mode="exact", category="Shopping"))
        rules.remember(self.c, "blue bottle", "Coffee & Snacks")   # not the exact-text rule: a new one
        self.assertEqual([tuple(r) for r in self.c.execute(select(Rule.match, Rule.match_mode, Rule.category)
                                                           .order_by(Rule.id))],
                         [("blue bottle", "exact", "Shopping"), ("blue bottle", "contains", "Coffee & Snacks")])
        self.assertIsNone(categorize.rule_offer(self.c, t, "Coffee & Snacks"))
        rules.remember(self.c, "blue bottle", "Restaurants")
        self.assertEqual(self.col(select(Rule.category).order_by(Rule.id)), ["Shopping", "Restaurants"])
        self.assertEqual(categorize.rule_offer(self.c, t, "Groceries")["replaces"], "Restaurants")


if __name__ == "__main__":
    unittest.main()
