"""Rules with conditions (text, amount, direction, account) and actions (category, rename, split, review)."""
import os
import tempfile
import unittest

from sqlalchemy import insert, select, update

from runway import categories, categorize, db, rules, splits
from runway.models import Account, Rule, Transaction
from tests.shared import DbCase


class Base(DbCase):
    def setUp(self):
        super().setUp()
        for aid, kind in (("chk", "checking"), ("cc", "credit")):
            self.c.execute(insert(Account).values(id=aid, name=aid.upper(), kind=kind, balance=0))
        self.n = 0

    def tx(self, amount, desc, acct="chk", category=None, source=None):
        self.n += 1
        tid = f"{acct}|{self.n}"
        self.c.execute(insert(Transaction).values(id=tid, account_id=acct, posted=f"2026-09-{self.n:02d}",
                                                  amount=amount, description=desc, payee=categorize.clean_payee(desc),
                                                  category=category, category_source=source))
        return tid

    def rule(self, **kw):
        return rules.save(self.c, kw)

    def row(self, tid):
        return self.c.execute(select(Transaction).where(Transaction.id == tid)).fetchone()


class MatchingTests(Base):
    def test_most_specific_rule_wins_each_action(self):
        self.rule(match="venmo", category="Transfer")
        self.rule(match="venmo", amount_min=1000, direction="out", category="Mortgage")
        self.rule(match="venmo", rename="Venmo")
        rent = self.tx(-1850, "VENMO *PAYMENT 1234")
        lunch = self.tx(-18, "VENMO *PAYMENT 5678")
        refund = self.tx(1850, "VENMO *CASHOUT")
        categorize.categorize(self.c, use_ai=False)
        self.assertEqual((self.row(rent)["category"], self.row(rent)["payee"]), ("Mortgage", "Venmo"))
        self.assertEqual((self.row(lunch)["category"], self.row(lunch)["payee"]), ("Transfer", "Venmo"))
        self.assertEqual(self.row(refund)["category"], "Transfer")

    def test_account_and_text_modes(self):
        self.rule(match="interest", match_mode="starts", account_id="chk", category="Income")
        self.rule(match="apple.com/bill", match_mode="exact", category="Subscriptions")
        a = self.tx(1.25, "INTEREST PAYMENT")
        b = self.tx(-5, "INTEREST PAYMENT", acct="cc")
        c = self.tx(3, "MONTHLY INTEREST")
        d = self.tx(-2.99, "APPLE.COM/BILL")
        e = self.tx(-2.99, "APPLE.COM/BILL ITUNES")
        categorize.categorize(self.c, use_ai=False)
        self.assertEqual([self.row(t)["category"] for t in (a, b, c, d, e)], ["Income", None, None, "Subscriptions", None])

    def test_a_rule_made_from_a_long_payee_matches_its_shorter_name(self):
        self.rule(match="target cach tran cash", category="Shopping")
        self.rule(match="lakeside bank baweb pay cash", match_mode="exact", category="Loans")
        a = self.tx(-35.91, "DIRECT DEBIT TARGET DEBIT CACH TRAN (Cash)")
        b = self.tx(-47.02, "DIRECT DEBIT TARGET DEBIT CPURCHASE (Cash)")
        c = self.tx(-512.40, "LAKESIDE BANK BAWEB PAY CASH 20251001")
        d = self.tx(-12, "TARGETED ADS LLC")
        e = self.tx(-20, "LAKESIDE BANK MORTGAGE")
        self.rule(match="paypal ach transfer", category="Transfer")
        f = self.tx(-100, "PAYPAL ACH TRANSFER 20251001")
        g = self.tx(-25, "PAYPAL")
        self.assertEqual((self.row(a)["payee"], self.row(g)["payee"]), ("Target", "Paypal"))
        categorize.categorize(self.c, use_ai=False)
        self.assertEqual([self.row(t)["category"] for t in (a, b, c, d, e, f, g)],
                         ["Shopping", "Shopping", "Loans", None, None, "Transfer", None])

    def test_rename_feeds_history(self):
        self.rule(match="sq *joes", rename="Joe's Coffee")
        old = self.tx(-4, "SQ *JOES 123", category="Coffee & Snacks", source="manual")
        self.c.execute(update(Transaction).where(Transaction.id == old).values(payee="Joe's Coffee"))
        new = self.tx(-5, "SQ *JOES 456")
        categorize.categorize(self.c, [new], use_ai=False)
        self.assertEqual((self.row(new)["payee"], self.row(new)["category"], self.row(new)["category_source"]),
                         ("Joe's Coffee", "Coffee & Snacks", "history"))

    def test_split_and_review(self):
        self.rule(match="costco", split=[{"category": "Groceries", "percent": 70}, {"category": "Shopping", "percent": 30}])
        self.rule(match="cash app", category="Transfer", review=True)
        big = self.tx(-100.01, "COSTCO WHSE #123")
        cash = self.tx(-40, "CASH APP*FRIEND")
        categorize.categorize(self.c, use_ai=False)
        self.assertEqual([(p["category"], p["amount"]) for p in splits.get(self.c, big)], [("Groceries", -70.01), ("Shopping", -30.0)])
        self.assertEqual((self.row(cash)["category"], self.row(cash)["needs_review"]), ("Transfer", 1))


    def test_a_split_a_cent_over_100_percent_still_adds_up(self):
        with self.assertRaisesRegex(rules.RuleError, "100%"):
            self.rule(match="costco", split=[{"category": "Groceries", "percent": 60.01}, {"category": "Shopping", "percent": 40}])
        parts = rules.split_parts(-250.0, [{"category": "Groceries", "percent": 60.01}, {"category": "Shopping", "percent": 40}])
        self.assertEqual(round(sum(p["amount"] for p in parts), 2), -250.0)
        import json
        self.c.execute(insert(Rule).values(match="costco",
                                           split=json.dumps([{"category": "Groceries", "percent": 60.01},
                                                             {"category": "Shopping", "percent": 40}])))
        t = self.tx(-250, "COSTCO WHSE")
        categorize.categorize(self.c, use_ai=False)
        self.assertEqual(round(sum(p["amount"] for p in splits.get(self.c, t)), 2), -250.0)


class EditingTests(Base):
    def test_checks(self):
        for bad, msg in [({"match": "x", "category": "Shopping"}, "two letters"),
                         ({"category": "Shopping"}, "some text"),
                         ({"match": "shop"}, "what the rule should do"),
                         ({"match": "shop", "category": "Nope"}, "Unknown category"),
                         ({"match": "shop", "amount_min": 50, "amount_max": 10, "category": "Shopping"}, "bigger"),
                         ({"match": "shop", "split": [{"category": "Groceries", "percent": 50}, {"category": "Shopping", "percent": 40}]}, "100%")]:
            with self.assertRaisesRegex(rules.RuleError, msg):
                rules.clean(self.c, bad)
        self.assertEqual(rules.clean(self.c, {"amount_min": 5000, "review": True})["review"], 1)

    def test_every_check_and_what_a_clean_rule_looks_like(self):
        for bad, msg in [({"match": "shop", "match_mode": "regex", "category": "Shopping"}, "Pick how the text should match"),
                         ({"match": "shop", "amount_min": "lots", "category": "Shopping"}, "Amounts must be numbers"),
                         ({"match": "shop", "amount_min": "nan", "category": "Shopping"}, "Amounts must be numbers"),
                         ({"match": "shop", "amount_max": "inf", "category": "Shopping"}, "Amounts must be numbers"),
                         ({"match": "shop", "amount_max": "1e13", "category": "Shopping"}, "Amounts must be numbers"),
                         ({"match": "shop", "direction": "sideways", "category": "Shopping"}, "Direction is money out or money in"),
                         ({"match": "shop", "account_id": "nope", "category": "Shopping"}, "Unknown account"),
                         ({"match": "shop", "split": [{"category": "Groceries", "percent": 100}]}, "at least two parts"),
                         ({"match": "shop", "split": "Groceries"}, "at least two parts"),
                         ({"match": "shop", "split": [{"category": "Groceries", "percent": "x"}, {"category": "Shopping", "percent": 50}]}, "a percentage"),
                         ({"match": "shop", "split": [{"category": "Nope", "percent": 50}, {"category": "Shopping", "percent": 50}]}, "a category"),
                         ({"match": "shop", "split": [None, {"category": "Shopping", "percent": 50}]}, "a percentage"),
                         ({"match": "shop", "split": [{"category": "Groceries", "percent": 0}, {"category": "Shopping", "percent": 100}]}, "a percentage")]:
            with self.assertRaisesRegex(rules.RuleError, msg):
                rules.clean(self.c, bad)
        self.assertEqual(rules.clean(self.c, {
            "match": "  Venmo   RENT ", "match_mode": "starts", "amount_min": "-1000.504", "amount_max": "", "direction": "out",
            "account_id": "chk", "category": "Shopping", "rename": "  Landlord   Co  ", "review": "yes"}), {
            "match": "venmo rent", "match_mode": "starts", "amount_min": 1000.5, "amount_max": None, "direction": "out",
            "account_id": "chk", "category": "Shopping", "rename": "Landlord Co", "review": 1, "split": None})
        split = rules.clean(self.c, {"match": "costco", "category": "Groceries",
                                     "split": [{"category": "Groceries", "percent": "60"}, {"category": "Shopping", "percent": 40}]})
        self.assertEqual((split["category"], split["split"]),
                         (None, '[{"category": "Groceries", "percent": 60.0}, {"category": "Shopping", "percent": 40.0}]'))
        self.assertEqual(rules.clean(self.c, {"direction": "in", "rename": "x" * 100})["rename"], "x" * 80)
        self.assertIsNone(rules.clean(self.c, {"account_id": "chk", "amount_max": 5, "review": 1, "split": []})["split"])

    def test_preview_and_apply_leave_your_choices(self):
        mine = self.tx(-12, "CHIPOTLE 0123", category="Groceries", source="manual")
        other = self.tx(-14, "CHIPOTLE 0456", category="Shopping", source="ai")
        self.tx(-9, "PANERA")
        body = {"match": "chipotle", "category": "Restaurants", "rename": "Chipotle Mexican Grill"}
        p = rules.preview(self.c, body)
        self.assertEqual((p["matches"], p["changes"]), (2, 2))
        rid = self.rule(**body)
        self.assertEqual(rules.apply_rule(self.c, rid), 2)
        self.assertEqual((self.row(mine)["category"], self.row(mine)["payee"]), ("Groceries", "Chipotle Mexican Grill"))
        self.assertEqual((self.row(other)["category"], self.row(other)["category_source"]), ("Restaurants", "rule"))
        self.assertEqual(rules.apply_rule(self.c, rid), 0)
        self.assertIn("error", rules.preview(self.c, {"match": "c"}))

    def test_remember_updates_only_the_plain_rule(self):
        self.rule(match="shell", amount_min=100, category="Travel")
        self.rule(match="shell", category="Shopping")
        rules.remember(self.c, "shell", "Auto & Gas")
        got = sorted((r["amount_min"] or 0, r["category"]) for r in rules.load(self.c))
        self.assertEqual(got, [(0, "Auto & Gas"), (100, "Travel")])
        rules.remember(self.c, "bp", "Auto & Gas")
        self.assertEqual(len(rules.load(self.c)), 3)

    def test_categories_follow_renames_and_removals(self):
        self.rule(match="costco", split=[{"category": "Groceries", "percent": 60}, {"category": "Shopping", "percent": 40}])
        self.rule(match="kroger", category="Groceries", rename="Kroger")
        self.rule(match="trader joe", category="Groceries")
        categories.rename(self.c, "Groceries", "Food")
        split = next(r for r in rules.load(self.c) if r["match"] == "costco")["split"]
        self.assertEqual([p["category"] for p in split], ["Food", "Shopping"])
        categories.remove(self.c, "Food")
        left = {r["match"]: (r["category"], r["rename"], r["split"]) for r in rules.load(self.c)}
        self.assertEqual(left, {"kroger": (None, "Kroger", None)})

    def test_offer_to_remember(self):
        tid = self.tx(-5, "SQ *BLUE BOTTLE 123")
        offer = categorize.rule_offer(self.c, tid, "Coffee & Snacks")
        self.assertEqual((offer["merchant"], offer["match"], offer["replaces"]), ("Blue Bottle", "blue bottle", None))
        rules.remember(self.c, "blue bottle", "Coffee & Snacks")
        self.assertIsNone(categorize.rule_offer(self.c, tid, "Coffee & Snacks"))
        self.assertEqual(categorize.rule_offer(self.c, tid, "Restaurants")["replaces"], "Coffee & Snacks")
        self.assertIsNone(categorize.rule_offer(self.c, self.tx(-5, "BP"), "Auto & Gas"))

    def test_applying_an_ai_suggestion_asks_instead_of_making_a_rule(self):
        from runway import server
        ids = [self.tx(-5, "SQ *BLUE BOTTLE 123"), self.tx(-6, "SQ *BLUE BOTTLE 456")]
        r = server.api_ai_apply(self.c, None, {"tx_ids": ids, "category": "Coffee & Snacks"})
        self.assertEqual(r["updated"], 2)
        self.assertEqual(rules.load(self.c), [])
        self.assertEqual(r["offer_rule"]["match"], "blue bottle")
        self.assertEqual(self.row(ids[1])["category"], "Coffee & Snacks")

    def test_the_offer_says_how_many_more_it_would_categorize(self):
        tid = self.tx(-5, "SQ *BLUE BOTTLE 123")
        self.tx(-6, "SQ *BLUE BOTTLE 456")
        self.tx(-7, "SQ *BLUE BOTTLE 789", category="Restaurants", source="manual")
        reviewed = self.tx(-8, "SQ *BLUE BOTTLE 000", category="Restaurants", source="rule")
        self.c.execute(update(Transaction).where(Transaction.id == reviewed).values(needs_review=0))
        same = self.tx(-9, "SQ *BLUE BOTTLE 111", category="Coffee & Snacks", source="ai")
        self.c.execute(update(Transaction).where(Transaction.id == same).values(needs_review=0))
        self.assertEqual(categorize.rule_offer(self.c, tid, "Coffee & Snacks")["also_updated"], 2)

    def test_setting_one_merchants_transactions_together_offers_a_rule(self):
        from runway import server
        ids = [self.tx(-5, "SQ *BLUE BOTTLE 123"), self.tx(-6, "SQ *BLUE BOTTLE 456")]
        other = self.tx(-7, "SQ *BLUE BOTTLE 789")
        r = server.api_tx_bulk(self.c, None, {"ids": ids, "category": "Coffee & Snacks"})
        self.assertEqual((r["offer_rule"]["match"], r["offer_rule"]["also_updated"]), ("blue bottle", 1))
        self.assertEqual(rules.load(self.c), [])
        self.assertIsNone(self.row(other)["category"])
        mixed = server.api_tx_bulk(self.c, None, {"ids": [ids[0], self.tx(-9, "KROGER #12")], "category": "Groceries"})
        self.assertIsNone(mixed["offer_rule"])
        renamed = server.api_tx_bulk(self.c, None, {"ids": ids, "payee": "Blue Bottle Coffee"})
        self.assertIsNone(renamed["offer_rule"])

    def test_setting_every_match_of_a_filter_offers_a_rule_too(self):
        from runway import server
        self.tx(-5, "SQ *BLUE BOTTLE 123"); self.tx(-6, "SQ *BLUE BOTTLE 456")
        r = server.api_tx_bulk(self.c, None, {"filter": {"q": "blue bottle"}, "category": "Coffee & Snacks"})
        self.assertEqual((r["updated"], r["offer_rule"]["merchant"]), (2, "Blue Bottle"))

    def test_accepting_keeps_the_category_and_can_be_undone(self):
        from runway import server
        tid = self.tx(-5, "SQ *BLUE BOTTLE 123", category="Restaurants", source="history")
        self.c.execute(update(Transaction).where(Transaction.id == tid).values(needs_review=1, confidence=0.4))
        r = server.api_tx_accept(self.c, None, None, tid)
        row = self.row(tid)
        self.assertEqual((row["category"], row["needs_review"], row["category_source"]), ("Restaurants", 0, "manual"))
        server.api_tx_bulk(self.c, None, {"restore": r["was"]})
        row = self.row(tid)
        self.assertEqual((row["category"], row["needs_review"], row["category_source"], row["confidence"]), ("Restaurants", 1, "history", 0.4))

    def test_nothing_to_accept_without_a_category(self):
        from runway import server
        from runway.server.common import ApiError
        tid = self.tx(-5, "SQ *BLUE BOTTLE 123")
        with self.assertRaisesRegex(ApiError, "Choose a category"):
            server.api_tx_accept(self.c, None, None, tid)
        with self.assertRaises(ApiError) as e:
            server.api_tx_accept(self.c, None, None, "nope")
        self.assertEqual(e.exception.status, 404)

    def test_describe(self):
        rid = self.rule(match="venmo", match_mode="starts", amount_min=1000, amount_max=2500, direction="out",
                        account_id="chk", category="Mortgage")
        r = next(x for x in rules.load(self.c) if x["id"] == rid)
        self.assertEqual(rules.describe(r, {"chk": "Checking"}),
                         "merchant starts with 'venmo' · $1,000.00–$2,500.00 · money out · in Checking")


class UpgradeTests(unittest.TestCase):
    def test_existing_rules_survive_and_texts_may_repeat(self):
        from alembic import command
        path = os.path.join(tempfile.mkdtemp(), "old.db")
        with db.engine(path).begin() as sa_conn:
            command.upgrade(db.alembic_config(sa_conn), "0006")
            sa_conn.exec_driver_sql("INSERT INTO rules(match, category) VALUES ('venmo', 'Transfer')")
        db.init(path)
        with db.session(path) as c:
            rules.save(c, {"match": "venmo", "amount_min": 1000, "category": "Mortgage"})
            self.assertEqual(sorted((r["match"], r["category"]) for r in rules.load(c)),
                             [("venmo", "Mortgage"), ("venmo", "Transfer")])


if __name__ == "__main__":
    unittest.main()
