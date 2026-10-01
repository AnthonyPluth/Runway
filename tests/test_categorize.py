"""Categorizing transactions: payees, the AI categorizer, categories and their budgets, and reports."""
import json
import unittest
from datetime import date, timedelta

from runway import categories, categorize, db, server
from tests.shared import LedgerCase


class PayeeTests(unittest.TestCase):
    def test_clean(self):
        cases = {
            "TST*XIAN FAMOUS FOODS - ": "Xian Famous Foods",
            "SQ *WILLIAM GREENBERG DES": "William Greenberg Des",
            "DD *DIMSUMSAM": "Dimsumsam",
            "LYFT   *SCHD AIR 09-20": "Lyft",
            "UBER *EATS PENDING": "Uber Eats",
            "JIMMY JOHNS - 3956 - ECOM": "Jimmy Johns",
            "CVS/PHARMACY ##02671": "Cvs/pharmacy",
            "AMZN Mktp US*2K3AB1": "Amzn Mktp Us",
            "": "",
        }
        for raw, want in cases.items():
            self.assertEqual(categorize.clean_payee(raw), want, raw)


class CategorizeTests(LedgerCase):
    def setUp(self):
        super().setUp()
        self.acct("chk", "checking", 100.0)
        self.acct("cc", "credit", -50.0)

    def test_pipeline_with_fake_ai(self):
        self.tx("chk", "2026-09-01", 0.0, "REDEMPTION FROM CORE ACCOUNT SPAXX")
        self.tx("chk", "2026-09-01", -300.0, "DIRECT DEBIT CITI AUTOPAY PAYMENT")
        self.tx("cc", "2026-09-02", 300.0, "AUTOPAY PAYMENT THANK YOU")
        self.tx("cc", "2026-09-03", -12.0, "TST*XIAN FAMOUS FOODS")
        self.tx("cc", "2026-09-04", -40.0, "MYSTERY MERCHANT 123")
        self.conn.execute("INSERT INTO rules(match, category) VALUES ('xian famous', 'Restaurants')")
        db.set_setting(self.conn, "openrouter_api_key", "k")
        seen = {}

        def fake(key, model, prompt):
            seen["prompt"], seen["model"] = prompt, model
            return 'Sure! [{"i": 0, "category": "shopping", "confidence": 0.6}]'

        counts = categorize.categorize(self.conn, None, caller=fake)
        self.assertEqual(counts, {"auto": 3, "rule": 1, "history": 0, "ai": 0, "review": 1})
        self.assertIn("MYSTERY MERCHANT", seen["prompt"])
        self.assertEqual(seen["model"], categorize.DEFAULT_MODEL)
        row = self.conn.execute("SELECT * FROM transactions WHERE description LIKE 'MYSTERY%'").fetchone()
        self.assertEqual((row["category"], row["needs_review"], row["category_source"]), ("Shopping", 1, "ai"))
        cats = dict(self.conn.execute("SELECT description, category FROM transactions").fetchall())
        self.assertEqual(cats["DIRECT DEBIT CITI AUTOPAY PAYMENT"], "Credit Card Payment")
        self.assertEqual(cats["AUTOPAY PAYMENT THANK YOU"], "Credit Card Payment")

    def test_one_question_per_merchant_and_history_reuse(self):
        for d in range(5):
            self.tx("cc", f"2026-09-0{d + 1}", -3.0, "MTA*NYCT PAYGO")
        self.tx("cc", "2026-09-06", 5.0, "MTA*NYCT PAYGO")  # a refund is asked about separately
        db.set_setting(self.conn, "openrouter_api_key", "k")
        asked = []

        def fake(key, model, prompt):
            items = json.loads(prompt.split("Transactions (JSON):\n")[1].split("\n\nReply")[0])
            asked.extend(items)
            return json.dumps([{"i": it["i"], "category": "Public Transit" if it["amount"] < 0 else "Refunds", "confidence": 0.97} for it in items])

        counts = categorize.categorize(self.conn, None, caller=fake)
        self.assertEqual(len(asked), 2)
        self.assertEqual(counts["ai"], 6)
        # Next sync: same merchant is settled from history without asking again.
        self.tx("cc", "2026-09-09", -3.0, "MTA*NYCT PAYGO")
        asked.clear()
        counts = categorize.categorize(self.conn, None, caller=fake)
        self.assertEqual((len(asked), counts["history"]), (0, 1))

    def test_edits_during_slow_ai_call_are_not_blocked_or_overwritten(self):
        self.tx("cc", "2026-09-04", -40.0, "MYSTERY")
        self.conn.commit()
        db.set_setting(self.conn, "openrouter_api_key", "k")
        other = db.connect(self.path)
        other.execute("PRAGMA busy_timeout=1000")  # fail fast if the lock were still held
        edited = {}

        def slow(key, model, prompt):
            # While "waiting on the model", the user changes the same transaction from the UI.
            categorize.set_category(other, "cc|0", "Groceries")
            other.commit()
            edited["ok"] = True
            return '[{"i": 0, "category": "Shopping", "confidence": 0.99}]'

        categorize.categorize(self.conn, None, caller=slow)
        self.conn.commit()
        other.close()
        self.assertTrue(edited.get("ok"))
        row = self.conn.execute("SELECT category, category_source FROM transactions WHERE id='cc|0'").fetchone()
        self.assertEqual(tuple(row), ("Groceries", "manual"))

    def test_review_suggestions_need_confirmation(self):
        for d in range(3):
            self.tx("cc", f"2026-09-0{d + 1}", -9.0, "SQ *BLUE BOTTLE")
        self.tx("cc", "2026-09-05", -30.0, "Z & H GRILL CORP")
        self.conn.execute("UPDATE transactions SET needs_review=1")
        db.set_setting(self.conn, "openrouter_api_key", "k")

        def fake(key, model, prompt):
            items = json.loads(prompt.split("Transactions (JSON):\n")[1].split("\n\nReply")[0])
            return json.dumps([{"i": it["i"], "category": "Coffee & Snacks" if "Blue" in it["payee"] else "Restaurants",
                                "confidence": 0.9} for it in items])

        sug = categorize.suggest_for_review(self.conn, caller=fake)
        self.assertEqual([(s["merchant"], s["count"], s["category"]) for s in sug],
                         [("Blue Bottle", 3, "Coffee & Snacks"), ("Z & H Grill Corp", 1, "Restaurants")])
        # nothing applied yet
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM transactions WHERE needs_review=1").fetchone()[0], 4)
        n = categorize.apply_to_group(self.conn, sug[0]["tx_ids"], "Coffee & Snacks", remember=True)
        self.assertEqual(n, 3)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM transactions WHERE needs_review=1").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT category FROM rules WHERE match='blue bottle'").fetchone()[0], "Coffee & Snacks")

    def test_sync_can_skip_ai(self):
        self.tx("cc", "2026-09-04", -40.0, "MYSTERY")
        db.set_setting(self.conn, "openrouter_api_key", "k")
        db.set_setting(self.conn, "auto_ai_on_sync", "0")
        called = []
        counts = categorize.categorize(self.conn, None, caller=lambda *a: called.append(1) or "[]")
        self.assertEqual((called, counts["review"]), ([], 1))

    def test_ai_failure_goes_to_review(self):
        self.tx("cc", "2026-09-04", -40.0, "MYSTERY")
        db.set_setting(self.conn, "openrouter_api_key", "k")

        def boom(*a):
            raise RuntimeError("OpenRouter HTTP 401: bad key")

        counts = categorize.categorize(self.conn, None, caller=boom)
        self.assertEqual(counts["review"], 1)
        self.assertIn("401", db.get_setting(self.conn, "last_llm_error"))

    def test_bad_category_rejected(self):
        parsed = categorize.parse_ai_reply('[{"i":0,"category":"Snacks","confidence":0.99},{"i":1,"category":"Groceries","confidence":"0.9"}]',
                                           ["Groceries"])
        self.assertEqual(parsed, {0: (None, 0.0), 1: ("Groceries", 0.9)})
        self.assertEqual(categorize.parse_ai_reply("no json here", ["Groceries"]), {})

    def test_remember_creates_rule_and_propagates(self):
        self.tx("cc", "2026-09-03", -12.0, "SQ *BLUE BOTTLE 123")
        self.tx("cc", "2026-09-10", -9.0, "SQ *BLUE BOTTLE 456")
        self.conn.execute("UPDATE transactions SET needs_review=1")
        n = categorize.set_category(self.conn, "cc|0", "Coffee & Snacks", remember=True)
        self.assertEqual(n, 1)
        self.assertEqual(self.conn.execute("SELECT match FROM rules").fetchone()[0], "blue bottle")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM transactions WHERE needs_review=1").fetchone()[0], 0)
        with self.assertRaises(ValueError):
            categorize.set_category(self.conn, "cc|0", "Nope")


class CategoryTests(LedgerCase):
    def setUp(self):
        super().setUp()
        self.acct("cc", "credit", -10.0)
        self.tx("cc", "2026-09-01", -12.0, "CHIPOTLE", "Restaurants")
        self.conn.execute("INSERT INTO rules(match, category) VALUES ('chipotle', 'Restaurants')")
        self.conn.execute("INSERT INTO budgets(category, amount) VALUES ('Restaurants', 300)")

    def test_budget_rollover(self):
        # Restaurants: $300 a month, rolling over from July. The setUp's $12 CHIPOTLE is on Sep 1.
        categories.add(self.conn, "Fast food", parent="Restaurants")
        self.conn.execute("UPDATE budgets SET rollover_from='2026-07' WHERE category='Restaurants'")
        self.tx("cc", "2026-06-10", -50.0, "BEFORE", "Restaurants")        # before it rolled over: not counted
        self.tx("cc", "2026-07-10", -200.0, "JULY", "Restaurants")         # $100 left
        self.tx("cc", "2026-08-10", -350.0, "AUGUST", "Fast food")         # a subcategory counts: $50 of $400 left
        cats = [c for c in categories.all_categories(self.conn) if not c["is_transfer"] and not c["is_income"]]
        rows = {r["category"]: dict(r) for r in self.conn.execute("SELECT * FROM budgets")}
        carry = lambda m: server.budget_carry(self.conn, cats, rows, date.fromisoformat(m))["Restaurants"]
        self.assertEqual((carry("2026-07-01"), carry("2026-08-01"), carry("2026-09-01")), (0.0, 100.0, 50.0))
        self.tx("cc", "2026-08-20", -500.0, "BIG NIGHT", "Restaurants")     # going over isn't carried
        self.assertEqual(carry("2026-09-01"), 0.0)
        self.assertEqual(carry("2026-10-01"), 288.0)                        # September: $300 - $12
        server.api_budget_set(self.conn, {}, {"category": "Restaurants", "rollover": False})
        self.assertIsNone(self.conn.execute("SELECT rollover_from FROM budgets").fetchone()[0])
        with self.assertRaises(server.ApiError):
            server.api_budget_set(self.conn, {}, {"category": "Groceries", "rollover": True})   # no budget to roll over

    def test_investment_accounts_stay_out_of_transactions(self):
        self.acct("brk", "investment", 5000.0)
        self.tx("brk", "2026-09-02", -250.0, "BUY VTI")
        self.conn.execute("UPDATE transactions SET needs_review=1")
        got = server.api_transactions(self.conn, {}, None)
        self.assertEqual([t["description"] for t in got["items"]], ["CHIPOTLE"])
        self.assertEqual(got["total"], 1)
        self.assertEqual(server.api_state(self.conn, {}, None)["review_count"], 1)   # the card's, not the buy

    def test_coming_up_wears_its_merchants_logo(self):
        self.acct("chk", "checking", 1000.0)
        last = (date.today() - timedelta(days=20)).isoformat()
        self.conn.execute("INSERT INTO recurring(name, account_id, amount, frequency, anchor_date, active) "
                          "VALUES ('Netflix', 'chk', -15.49, 'monthly', ?, 1)", (last,))
        rid = self.conn.execute("SELECT id FROM recurring").fetchone()[0]
        self.tx("chk", last, -15.49, "NETFLIX.COM")
        self.conn.execute("UPDATE transactions SET recurring_id=?, merchant_id='m-netflix' WHERE description='NETFLIX.COM'", (rid,))
        self.conn.execute("INSERT INTO merchants(id, name, logo, logo_type) VALUES ('m-netflix', 'Netflix', 'cG5n', 'image/png')")
        fc = server.api_overview(self.conn, {"days": ["60"]}, None)
        ev = [e for e in fc["events"] if e.get("recurring_id") == rid]
        self.assertTrue(ev)
        self.assertEqual({e["logo"] for e in ev}, {"/api/merchants/m-netflix/logo"})

    def test_setup_steps(self):
        steps = server.setup_steps(self.conn)
        self.assertEqual((steps["primary"], steps["recurring"], steps["budgets"], steps["dismissed"]), (False, False, True, False))
        self.acct("chk", "checking", 10.0)        # the only checking account is the primary one
        self.assertTrue(server.setup_steps(self.conn)["primary"])
        server.api_settings(self.conn, {}, {"setup_dismissed": True})
        self.assertTrue(server.setup_steps(self.conn)["dismissed"])

    def test_looks(self):
        categories.add(self.conn, "Fast food", parent="Restaurants")
        categories.add(self.conn, "Zebra Rides")
        by = {c["name"]: c for c in categories.all_categories(self.conn)}
        self.assertEqual((by["Groceries"]["icon"], by["Groceries"]["custom_icon"]), ("🛒", None))
        self.assertEqual(by["Fast food"]["color"], by["Restaurants"]["color"])   # a subcategory wears its parent's color
        self.assertIn(by["Zebra Rides"]["color"], categories.PALETTE)            # an unknown name still gets one
        categories.set_look(self.conn, "Restaurants", "🍔", "#1C9AA8")
        by = {c["name"]: c for c in categories.all_categories(self.conn)}
        self.assertEqual((by["Restaurants"]["icon"], by["Restaurants"]["color"]), ("🍔", "#1c9aa8"))
        self.assertEqual(by["Fast food"]["color"], "#1c9aa8")
        categories.rename(self.conn, "Restaurants", "Eating out")                 # the look goes with the name
        self.assertEqual(self.conn.execute("SELECT icon FROM categories WHERE name='Eating out'").fetchone()[0], "🍔")
        categories.set_look(self.conn, "Eating out", "", "")                      # back to the default
        self.assertEqual(self.conn.execute("SELECT icon FROM categories WHERE name='Eating out'").fetchone()[0], None)
        for icon in ("1️⃣", "#️⃣", "🇯🇵", "👍🏽", "👨‍👩‍👧‍👦", "❤️", "↩️", "▶️", "ℹ️", "‼️", "〰️", "↔️"):             # anything the emoji keyboard types
            categories.set_look(self.conn, "Eating out", icon, None)
            self.assertEqual(self.conn.execute("SELECT icon FROM categories WHERE name='Eating out'").fetchone()[0], icon)
        for icon, color in (("abc", None), ("1", None), ("#", None), ("!?", None), ("é", None), ("!\ufe0f", None), ("🍔" * 17, None),
                            (None, "red"), (None, "#12345g")):
            with self.assertRaises(categories.CategoryError):
                categories.set_look(self.conn, "Eating out", icon, color)
        with self.assertRaises(categories.CategoryError):
            categories.set_look(self.conn, "Nope", "🍔", None)

    def test_add_sub_rename_remove(self):
        categories.add(self.conn, "Fast food", parent="Restaurants")
        sub = self.conn.execute("SELECT * FROM categories WHERE name='Fast food'").fetchone()
        self.assertEqual((sub["parent"], sub["is_transfer"], sub["is_income"]), ("Restaurants", 0, 0))
        with self.assertRaises(categories.CategoryError):
            categories.add(self.conn, "Burgers", parent="Fast food")  # one level only
        with self.assertRaises(categories.CategoryError):
            categories.add(self.conn, "restaurants")  # duplicate, any case
        tree = [c["name"] for c in categories.all_categories(self.conn)]
        self.assertEqual(tree[tree.index("Restaurants") + 1], "Fast food")
        # rename carries transactions, rules, budgets and children along
        categories.rename(self.conn, "Restaurants", "Dining")
        self.assertEqual(self.conn.execute("SELECT category FROM transactions").fetchone()[0], "Dining")
        self.assertEqual(self.conn.execute("SELECT category FROM rules").fetchone()[0], "Dining")
        self.assertEqual(self.conn.execute("SELECT category FROM budgets").fetchone()[0], "Dining")
        self.assertEqual(self.conn.execute("SELECT parent FROM categories WHERE name='Fast food'").fetchone()[0], "Dining")
        # can't remove a parent that still has subcategories, or a built-in
        with self.assertRaises(categories.CategoryError):
            categories.remove(self.conn, "Dining")
        with self.assertRaises(categories.CategoryError):
            categories.remove(self.conn, "Credit Card Payment")
        categories.remove(self.conn, "Fast food")
        moved = categories.remove(self.conn, "Dining", move_to="Other")
        self.assertEqual(moved, 1)
        self.assertEqual(self.conn.execute("SELECT category FROM transactions").fetchone()[0], "Other")
        self.assertEqual(self.conn.execute("SELECT category FROM rules").fetchone()[0], "Other")
        self.assertIsNone(self.conn.execute("SELECT 1 FROM budgets").fetchone())

    def test_move_rollups_and_flatten(self):
        categories.add(self.conn, "Food")
        categories.move(self.conn, "Restaurants", "Food")
        categories.add(self.conn, "Groceries & more", parent="Food")
        with self.assertRaises(categories.CategoryError):
            categories.add(self.conn, "Burgers", parent="Restaurants")      # no sub-subcategories
        with self.assertRaises(categories.CategoryError):
            categories.move(self.conn, "Food", "Other")                     # Food has subcategories
        with self.assertRaises(categories.CategoryError):
            categories.move(self.conn, "Other", "Restaurants")              # under a subcategory
        tree = {c["name"]: c for c in categories.all_categories(self.conn)}
        self.assertEqual((tree["Restaurants"]["path"], tree["Restaurants"]["depth"], tree["Restaurants"]["top"]), (["Food", "Restaurants"], 1, "Food"))
        # budget and reports roll subcategories into the parent
        self.tx("cc", "2026-09-02", -8.0, "FIVE GUYS", "Groceries & more")
        self.conn.commit()
        from runway import server
        b = {c["name"]: c for c in server.api_budget(self.conn, {"month": ["2026-09"]}, None)["categories"]}
        self.assertEqual((b["Food"]["spent"], b["Restaurants"]["spent"]), (20.0, 12.0))
        cf = server.api_cashflow(self.conn, {"month": ["2026-09"]}, None)
        food = next(n for n in cf["spending"] if n["name"] == "Food")
        self.assertEqual((food["value"], sorted(k["name"] for k in food["children"])), (20.0, ["Groceries & more", "Restaurants"]))
        self.assertEqual(len(server.api_transactions(self.conn, {"category": ["Food"]}, None)["items"]), 2)
        # anything nested deeper by an earlier version moves up under its top-level category
        self.conn.execute("INSERT INTO categories(name, is_transfer, is_income, parent) VALUES ('Burgers', 0, 0, 'Restaurants')")
        self.assertEqual(categories.flatten(self.conn), 1)
        self.assertEqual(self.conn.execute("SELECT parent FROM categories WHERE name='Burgers'").fetchone()[0], "Food")
        categories.move(self.conn, "Burgers", None)
        categories.move(self.conn, "Burgers", "Income")
        self.assertEqual(self.conn.execute("SELECT is_income FROM categories WHERE name='Burgers'").fetchone()[0], 1)
        with self.assertRaises(categories.CategoryError):
            categories.move(self.conn, "Transfer", "Food")

    def test_spent_drilldown_matches_budget(self):
        from runway import server
        categories.add(self.conn, "Food")
        categories.move(self.conn, "Restaurants", "Food")
        self.acct("loan", "loan", -5000.0)
        self.tx("cc", "2026-09-05", -30.0, "SUSHI", "Restaurants")
        self.tx("cc", "2026-09-06", 5.0, "SUSHI REFUND", "Restaurants")      # refunds count against spending
        self.tx("cc", "2026-08-30", -99.0, "LAST MONTH", "Restaurants")       # other month
        self.tx("loan", "2026-09-07", -40.0, "ODD LOAN ITEM", "Food")         # not an account the budget counts
        self.conn.commit()
        spent = {c["name"]: c["spent"] for c in server.api_budget(self.conn, {"month": ["2026-09"]}, None)["categories"]}
        items = server.api_transactions(self.conn, {"category": ["Food"], "month": ["2026-09"], "scope": ["budget"]}, None)["items"]
        self.assertEqual(round(-sum(t["amount"] for t in items), 2), spent["Food"])
        self.assertEqual(sorted(t["description"] for t in items), ["CHIPOTLE", "SUSHI", "SUSHI REFUND"])

    def test_remove_without_target_sends_to_review(self):
        categories.remove(self.conn, "Restaurants")
        row = self.conn.execute("SELECT category, needs_review FROM transactions").fetchone()
        self.assertEqual(tuple(row), (None, 1))
        self.assertIsNone(self.conn.execute("SELECT 1 FROM rules").fetchone())


class CategorizeFixTests(LedgerCase):
    def setUp(self):
        super().setUp()
        self.acct("chk", "checking", 1000.0)

    def test_autopay_needs_a_card(self):
        cat = lambda d: categorize.heuristic_category({"description": d, "amount": -100}, "checking")
        for bill in ("COMCAST XFINITY AUTOPAY", "STATE FARM AUTOPAY", "CITY WATER EPAY", "CAPITAL ONE AUTO FINANCE PMT",
                     "CHASE MORTGAGE AUTOPAY"):
            self.assertIsNone(cat(bill), bill)
        for card in ("CHASE CREDIT CRD AUTOPAY", "CAPITAL ONE MOBILE PMT", "AMEX EPAYMENT ACH PMT", "DISCOVER E-PAYMENT",
                     "CITI AUTOPAY PAYMENT", "BARCLAYCARD US AUTOPAY", "APPLECARD GSBANK PAYMENT"):
            self.assertEqual(cat(card), "Credit Card Payment", card)

    def test_your_rules_beat_the_built_in_guess(self):
        from runway import rules
        rules.save(self.conn, {"match": "chase credit", "category": "Transfer"})
        self.tx("chk", "2026-09-10", -300.0, "CHASE CREDIT CRD AUTOPAY")
        categorize.categorize(self.conn, use_ai=False)
        row = self.conn.execute("SELECT category, category_source FROM transactions").fetchone()
        self.assertEqual((row["category"], row["category_source"]), ("Transfer", "rule"))

    def test_a_split_that_cant_be_made_goes_to_review_and_the_sync_goes_on(self):
        import json
        self.conn.execute("INSERT INTO rules(match, split) VALUES ('costco', ?)",
                          (json.dumps([{"category": "Groceries", "percent": 60}, {"category": "Gone", "percent": 40}]),))
        self.tx("chk", "2026-09-10", -250.0, "COSTCO WHSE")
        self.tx("chk", "2026-09-11", -20.0, "COFFEE")
        categorize.categorize(self.conn, use_ai=False)
        row = self.conn.execute("SELECT is_split, needs_review FROM transactions WHERE description='COSTCO WHSE'").fetchone()
        self.assertEqual((row["is_split"], row["needs_review"]), (0, 1))

    def test_renaming_or_removing_a_category_follows_order_items(self):
        self.conn.execute("INSERT INTO retail_orders(id, retailer, order_number) VALUES ('amazon:1','amazon','1')")
        self.conn.execute("INSERT INTO retail_items(order_id, title, amount, category, category_source) "
                          "VALUES ('amazon:1','Oats',5,'Groceries','manual')")
        self.conn.execute("INSERT INTO retail_item_memory(key, category) VALUES ('oats','Groceries')")
        categories.rename(self.conn, "Groceries", "Food")
        self.assertEqual(self.conn.execute("SELECT category FROM retail_items").fetchone()[0], "Food")
        self.assertEqual(self.conn.execute("SELECT category FROM retail_item_memory").fetchone()[0], "Food")
        categories.remove(self.conn, "Food")
        self.assertIsNone(self.conn.execute("SELECT category_source FROM retail_items").fetchone()[0])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM retail_item_memory").fetchone()[0], 0)


class ReportRefundTests(LedgerCase):
    def test_refunds_lower_spending_and_uncategorized_money_in_isnt_income(self):
        from runway import reports
        self.acct("chk", "checking", 0.0)
        self.tx("chk", "2026-09-01", 3000.0, "ACME PAYROLL", "Income")
        self.tx("chk", "2026-09-05", -400.0, "STORE", "Shopping")
        self.tx("chk", "2026-09-06", 100.0, "STORE REFUND", "Refunds")
        self.tx("chk", "2026-09-07", 500.0, "ZELLE FROM SAM")                     # not categorized yet
        m = reports.income_vs_spending(self.conn, "2026-09", 2)["months"][-1]
        self.assertEqual((m["income"], m["spending"]), (3000.0, 300.0))
        self.conn.commit()
        cf = server.api_cashflow(self.conn, {"month": ["2026-09"]}, None)
        self.assertEqual(cf["total_in"], 3000.0)


if __name__ == "__main__":
    unittest.main()
