"""Categorizing transactions: payees, the AI categorizer, categories and their budgets, and reports."""
import json
import unittest
from datetime import date, timedelta

from sqlalchemy import delete, func, insert, select, update

from runway import categories, categorize, db, payees, server, simplefin
from runway import settings_keys as sk
from runway.models import Budget, Category, Merchant, MerchantLogo, Recurring, RetailItem, RetailItemMemory, RetailOrder, Rule, Transaction
from tests.shared import TODAY, LedgerCase, freeze_today, ts


class PayeeTests(unittest.TestCase):
    def test_clean(self):
        cases = {
            "TST*XIAN FAMOUS FOODS - ": "Xian Famous Foods",
            "SQ *WILLIAM GREENBERG DES": "William Greenberg Des",
            "DD *DIMSUMSAM": "Dimsumsam",
            "LYFT   *SCHD AIR 09-20": "Lyft",
            "UBER *EATS PENDING": "Uber Eats",
            "JIMMY JOHNS - 1234 - ECOM": "Jimmy Johns",
            "CVS/PHARMACY ##02671": "Cvs/pharmacy",
            "AMZN Mktp US*2K3AB1": "Amzn Mktp Us",
            "": "",
        }
        for raw, want in cases.items():
            self.assertEqual(categorize.bank_payee(raw), want, raw)

    def test_the_banks_transfer_words_come_off_the_merchant(self):
        cases = {
            # What banks send through SimpleFIN for a Target debit card or a loan paid on the bank's website
            "DIRECT DEBIT TARGET DEBIT CACH TRAN (Cash)": "Target",
            "DIRECT DEBIT TARGET DEBIT CPURCHASE (Cash)": "Target",
            "DIRECT DEPOSIT TARGET DEBITACH TRAN (Cash)": "Target",
            "DIRECT DEBIT LAKESIDE BANK BAWEB PAY (Cash)": "Lakeside Bank",
            "GEICO ACH PMT": "Geico",
            "COMCAST WEB PAY": "Comcast",
            "ACME CORP PAYROLL PPD ID: 1234567": "Acme Corp Payroll",
        }
        for raw, want in cases.items():
            self.assertEqual(categorize.clean_payee(raw), want, raw)
        # And payees synced before, as they were stored
        for long in ("Target Cach Tran Cash", "Target C Cash", "Direct Deposit Target Debitach Tran Cash", "Target Debit Cach Tran"):
            self.assertEqual(payees.shorten(long), "Target", long)
        self.assertEqual(payees.shorten("Lakeside Bank Baweb Pay Cash"), "Lakeside Bank")

    def test_a_merchants_own_name_stays_as_it_is(self):
        # Transfer words alone aren't enough: a merchant's name can end with them. Only an ACH code (or "web pay", or a
        # cut-off letter before one) says the tail is the bank's.
        for name in ("Apple Cash", "Apple Pay", "Charlotte's Web", "Vitamin C", "Target C", "Discover Credit", "Ach Payment",
                     "Direct Deposit Acme Payroll", "Cash App", "Chase Bill Pay", "Bach Pay", "Coach", "Ppd", "Target"):
            self.assertEqual(payees.shorten(name), name, name)
            self.assertEqual(categorize.bank_payee(name.upper()), name, name)

    def test_a_payee_from_the_banks_text_or_from_you(self):
        self.assertTrue(payees.from_bank("Target Cach Tran Cash", "DIRECT DEBIT TARGET DEBIT CACH TRAN (Cash)"))
        self.assertTrue(payees.from_bank("Target C Cash", "DIRECT DEBIT TARGET DEBIT CPURCHASE (Cash)"))
        self.assertFalse(payees.from_bank("Groceries Run", "DIRECT DEBIT TARGET DEBIT CACH TRAN (Cash)"))
        self.assertFalse(payees.from_bank("Target", None))
        # punctuation is plain on both sides
        self.assertTrue(payees.from_bank("Trader Joe's", "TRADER JOE'S #123"))
        self.assertTrue(payees.from_bank("At&t", "AT&T BILL PAYMENT"))
        self.assertTrue(payees.from_bank("Amazon.com", "AMAZON.COM*2K3AB1"))


class BrandNameTests(unittest.TestCase):
    def test_big_merchants_get_the_brands_name(self):
        cases = {
            "amzn mktp us*2k3": "Amazon", "AMZN Mktp US*2K3AB1": "Amazon", "amazon.com*xyz": "Amazon",
            "AMZN Digital*AB12CD": "Amazon", "WM SUPERCENTER #123": "Walmart", "Wal-Mart Super Center": "Walmart",
            "Walmart.com 8009256278": "Walmart", "SQ *STARBUCKS": "Starbucks", "STARBUCKS STORE 12345": "Starbucks",
            "TARGET T-1234": "Target", "COSTCO WHSE #0123": "Costco", "NETFLIX.COM": "Netflix", "SPOTIFY USA": "Spotify",
            "MCDONALD'S F1234": "McDonald’s", "APPLE.COM/BILL 866-712-7753 CA": "Apple", "Disney Plus 888-905-7888": "Disney+",
            "Delta Air Lines 0062": "Delta Air Lines",
            # The sub-brands stay apart
            "PRIME VIDEO*2K3AB1": "Prime Video", "Amazon Prime*2K3AB1": "Amazon Prime", "AUDIBLE*2K3AB1": "Audible",
            "UBER *EATS PENDING": "Uber Eats", "UBER *TRIP": "Uber", "UBER *ONE": "Uber One",
        }
        for raw, want in cases.items():
            self.assertEqual(categorize.clean_payee(raw), want, raw)

    def test_not_every_mention_of_a_brand_is_the_brand(self):
        # A wrong brand is worse than a long name: the payee has to start with the brand, as a whole word, and not be a
        # payment, a part of the business worth telling apart, or a word other businesses use too.
        for raw in ("PAYMENT TO AMAZON", "AMAZONIA CAFE", "AMAZON CORP SYF PAYMNT", "AMAZON.COM SVCS PAYROLL PPD ID: 123",
                    "COSTCO GAS #123", "TARGET CARD SRVC", "APPLE CASH", "APPLE PAY", "APPLE MUSIC", "UBER *PASS",
                    "PEACOCK CAFE", "HILTON HEAD PIZZA", "SOUTHWEST GAS", "DD *DOORDASH MCDONALDS", "VENMO *JOHN",
                    "CLAUDE'S BARBER", "TARGETED MARKETING", "GOOGLE *YOUTUBE", "COMCAST WEB PAY", "KINDLE SVCS*2K3AB1"):
            self.assertEqual(categorize.clean_payee(raw), categorize.bank_payee(raw), raw)

    def test_brands_you_chose_keep_the_banks_name(self):
        self.assertEqual(categorize.clean_payee("AMZN Mktp US*2K3AB1", keep_bank={"Amazon"}), "Amzn Mktp Us")
        self.assertEqual(categorize.clean_payee("WM SUPERCENTER #123", keep_bank={"Amazon"}), "Walmart")

    def test_what_the_details_offer(self):
        choice = categorize.brand_choice
        mktp = "AMZN Mktp US*2K3AB1"
        self.assertEqual(choice({"payee": "Amazon", "description": mktp}),
                         {"brand": "Amazon", "bank_name": "Amzn Mktp Us", "using": "brand"})
        self.assertEqual(choice({"payee": "Amzn Mktp Us", "description": mktp}),
                         {"brand": "Amazon", "bank_name": "Amzn Mktp Us", "using": "bank"})
        self.assertIsNone(choice({"payee": "Birthday Gift", "description": mktp}))             # a name you gave it
        self.assertIsNone(choice({"payee": "Amazon", "description": mktp, "merchant_id": "m"}))  # Plaid's
        self.assertIsNone(choice({"payee": "Uber Eats", "description": "UBER *EATS"}))          # the bank's says it already
        self.assertIsNone(choice({"payee": "Costco Gas", "description": "COSTCO GAS #1"}))
        self.assertIsNone(choice({"payee": "Amazon", "description": None}))

    def test_a_rule_made_from_the_banks_name_still_matches(self):
        from runway import rules
        mktp = {"payee": "Amazon", "description": "AMZN Mktp US*2K3AB1", "amount": -20}
        other = {"payee": "Amazon", "description": "AMAZON.COM*ZZ9QW1", "amount": -20}
        for mode in ("exact", "starts", "contains"):
            r = {"match": "amzn mktp us", "match_mode": mode}
            self.assertTrue(rules.matches(r, mktp), mode)
            self.assertFalse(rules.matches(r, other), mode)   # not every Amazon order
        # Not one you renamed "Amazon" by hand from someone else's text, nor one whose text names another brand.
        r = {"match": "amzn mktp us", "match_mode": "exact"}
        self.assertFalse(rules.matches(r, {"payee": "Amazon", "description": "WM SUPERCENTER #1", "amount": -5}))
        self.assertFalse(rules.matches(r, {"payee": "Walmart", "description": "AMZN Mktp US*2K3AB1 X", "amount": -5}))
        # A rule for a text that isn't a brand's name learns nothing new.
        r = {"match": "costco gas", "match_mode": "exact"}
        self.assertFalse(rules.matches(r, {"payee": "Costco", "description": "COSTCO WHSE #1", "amount": -5}))


class BrandNameApiTests(LedgerCase):
    def setUp(self):
        super().setUp()
        self.acct("chk", "checking", 100.0)
        for desc in ("AMZN Mktp US*2K3AB1", "AMAZON.COM*ZZ9QW1", "AMZN Digital*RT4QW2", "WM SUPERCENTER #1"):
            self.tx("chk", "2026-09-01", -10.0, desc)
        self.tx("chk", "2026-09-02", -10.0, "AMZN Mktp US*9P8O7I")
        self.tx("chk", "2026-09-02", -10.0, "AMZN Mktp US*1A2S3D")
        self.conn.execute(update(Transaction).where(Transaction.id == "chk|5").values(payee="Gift For Sam"))   # yours

    def payees(self):
        return dict(self.conn.execute(select(Transaction.id, Transaction.payee)).fetchall())

    def call(self, tx_id, **body):
        return server.api_tx_brand_name(self.conn, {}, body, tx_id)

    def test_the_list_says_which_name_it_uses(self):
        items = {t["id"]: t["brand"] for t in server.api_transactions(self.conn, {}, None)["items"]}
        self.assertEqual(items["chk|0"], {"brand": "Amazon", "bank_name": "Amzn Mktp Us", "using": "brand"})
        self.assertIsNone(items["chk|5"])

    def test_rules_and_recurring_items_made_from_the_brands_name_keep_working_with_the_banks_name(self):
        from runway import recurring, rules
        self.call("chk|0", use="bank", all=True)   # every Amazon transaction back to the bank's name, from now on
        self.assertEqual(self.payees()["chk|0"], "Amzn Mktp Us")
        rule = {"match": "amazon", "match_mode": "exact"}
        self.assertTrue(rules._text_matches(rule, {"payee": "Amzn Mktp Us", "description": "AMZN Mktp US*2K3AB1"}))
        self.assertFalse(rules._text_matches(rule, {"payee": "Walmart", "description": "WM SUPERCENTER #1"}))
        self.assertFalse(rules._text_matches(rule, {"payee": "Gift For Sam", "description": "AMZN Mktp US*2K3AB1"}))   # yours
        self.assertFalse(rules._text_matches({"match": "walmart", "match_mode": "exact"},
                                             {"payee": "Amzn Mktp Us", "description": "AMZN Mktp US*2K3AB1"}))
        self.conn.execute(insert(Recurring).values(name="Amazon", account_id="chk", amount=-10.0, frequency="monthly",
                                                   anchor_date="2026-09-01", active=1, match="amazon"))
        rid = self.conn.execute(select(Recurring.id)).scalar()
        recurring.auto_match(self.conn, [rid])
        linked = {r[0] for r in self.conn.execute(select(Transaction.id).where(Transaction.recurring_id == rid))}
        self.assertEqual(linked, {"chk|0", "chk|1", "chk|2", "chk|4"})   # Amazon's: not Walmart's, nor one you named

    def test_the_banks_name_for_one_and_undo(self):
        before = self.payees()
        r = self.call("chk|0", use="bank")
        self.assertEqual((r["updated"], r["payee"], r["brand"]), (1, "Amzn Mktp Us", "Amazon"))
        self.assertEqual(self.payees(), {**before, "chk|0": "Amzn Mktp Us"})
        self.assertEqual(categorize.kept_bank_names(self.conn), set())   # just this one
        server.api_tx_bulk(self.conn, {}, {"restore": r["was"], "keep_bank": r["keep_bank"]})
        self.assertEqual(self.payees(), before)
        # And back to the brand's
        self.call("chk|0", use="bank")
        self.assertEqual(self.call("chk|0", use="brand")["payee"], "Amazon")

    def test_the_banks_name_for_all_and_from_now_on(self):
        before = self.payees()
        r = self.call("chk|0", use="bank", all=True)
        self.assertEqual(r["updated"], 4)
        self.assertEqual(self.payees(), {"chk|0": "Amzn Mktp Us", "chk|1": "Amazon.com", "chk|2": "Amzn Digital",
                                         "chk|3": "Walmart", "chk|4": "Amzn Mktp Us", "chk|5": "Gift For Sam"})
        self.assertEqual(categorize.kept_bank_names(self.conn), {"Amazon"})
        # The next sync keeps the bank's name for Amazon, and only Amazon.
        simplefin.store_payload(self.conn, {"errors": [], "accounts": [{
            "org": {"name": "Chase"}, "id": "chk", "name": "Checking", "currency": "USD", "balance": "100",
            "balance-date": ts(TODAY), "transactions": [
                {"id": "n1", "posted": ts(TODAY), "amount": "-3.00", "description": "AMZN Mktp US*1Q2W3E"},
                {"id": "n2", "posted": ts(TODAY), "amount": "-3.00", "description": "WM SUPERCENTER #9"}]}]}, TODAY)
        self.assertEqual((self.payees()["chk|n1"], self.payees()["chk|n2"]), ("Amzn Mktp Us", "Walmart"))
        # a payee SimpleFIN named itself, other than the bank's text, stays as it was: a rule made from it still matches
        db.set_setting(self.conn, sk.BRAND_NAMES_OFF, "[]")
        self.conn.execute(insert(Rule).values(match="walmart supercenter", match_mode="exact", category="Groceries"))
        simplefin.store_payload(self.conn, {"errors": [], "accounts": [{
            "org": {"name": "Chase"}, "id": "chk", "name": "Checking", "currency": "USD", "balance": "100",
            "balance-date": ts(TODAY), "transactions": [
                {"id": "n3", "posted": ts(TODAY), "amount": "-40.00", "payee": "Walmart Supercenter",
                 "description": "WAL-MART SUPERCENTER #1234"},
                {"id": "n4", "posted": ts(TODAY), "amount": "-5.00", "payee": "AMZN Mktp US", "description": "AMZN Mktp US*9Z8Y7X"}]}]}, TODAY)
        self.assertEqual((self.payees()["chk|n3"], self.payees()["chk|n4"]), ("Walmart Supercenter", "Amazon"))
        categorize.categorize(self.conn, ["chk|n3"], use_ai=False)
        self.assertEqual(self.conn.execute(select(Transaction.category).where(Transaction.id == "chk|n3")).scalar(), "Groceries")
        self.conn.execute(delete(Rule))
        self.conn.execute(delete(Transaction).where(Transaction.id.in_(["chk|n3", "chk|n4"])))
        db.set_setting(self.conn, sk.BRAND_NAMES_OFF, '["Amazon"]')
        # Undo puts back the names and the brand's own
        self.conn.execute(delete(Transaction).where(Transaction.id.in_(["chk|n1", "chk|n2"])))
        server.api_tx_bulk(self.conn, {}, {"restore": r["was"], "keep_bank": r["keep_bank"]})
        self.assertEqual(self.payees(), before)
        self.assertEqual(categorize.kept_bank_names(self.conn), set())
        # "Use Amazon" for all goes back to the brand's name, from now on too
        self.call("chk|0", use="bank", all=True)
        r = self.call("chk|4", use="brand", all=True)
        self.assertEqual((r["updated"], r["keep_bank"]), (4, {"brand": "Amazon", "keep": True}))
        self.assertEqual(self.payees(), before)
        self.assertEqual(categorize.kept_bank_names(self.conn), set())

    def test_what_cant_be_renamed(self):
        for tx_id, body in (("chk|5", {"use": "bank"}),        # a name you gave it
                            ("nope", {"use": "bank"}), ("chk|0", {"use": "sideways"})):
            with self.assertRaises(server.ApiError):
                self.call(tx_id, **body)
        self.assertEqual(self.payees()["chk|5"], "Gift For Sam")
        # A broken setting reads as none, and is replaced the next time
        db.set_setting(self.conn, "brand_names_off", "{oops")
        self.assertEqual(categorize.kept_bank_names(self.conn), set())
        self.call("chk|0", use="bank", all=True)
        self.assertEqual(categorize.kept_bank_names(self.conn), {"Amazon"})


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
        self.conn.execute(insert(Rule).values(match="xian famous", category="Restaurants"))
        db.set_setting(self.conn, "openrouter_api_key", "k")
        seen = {}

        def fake(key, model, prompt):
            seen["prompt"], seen["model"] = prompt, model
            return 'Sure! [{"i": 0, "category": "shopping", "confidence": 0.6}]'

        counts = categorize.categorize(self.conn, None, caller=fake)
        self.assertEqual(counts, {"auto": 3, "rule": 1, "history": 0, "ai": 0, "review": 1})
        self.assertIn("MYSTERY MERCHANT", seen["prompt"])
        self.assertEqual(seen["model"], "openrouter/free")
        self.assertEqual(categorize.DEFAULT_MODEL, "openrouter/free")
        row = self.conn.execute(select(Transaction).where(Transaction.description.like("MYSTERY%"))).fetchone()
        self.assertEqual((row["category"], row["needs_review"], row["category_source"]), ("Shopping", 1, "ai"))
        cats = dict(self.conn.execute(select(Transaction.description, Transaction.category)).fetchall())
        self.assertEqual(cats["DIRECT DEBIT CITI AUTOPAY PAYMENT"], "Credit Card Payment")
        self.assertEqual(cats["AUTOPAY PAYMENT THANK YOU"], "Credit Card Payment")

    def test_a_model_you_set_is_used_for_categorizing(self):
        self.tx("cc", "2026-09-04", -40.0, "MYSTERY MERCHANT 123")
        db.set_setting(self.conn, "openrouter_api_key", "k")
        db.set_setting(self.conn, "llm_model", "openai/gpt-4o-mini")
        db.set_setting(self.conn, "card_ai_model", "some/card-model")   # not used for categorizing
        seen = []
        categorize.categorize(self.conn, None, caller=lambda k, m, p: seen.append(m) or "[]")
        self.assertEqual(seen, ["openai/gpt-4o-mini"])

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
        if not db.using_postgres():
            other.sa.exec_driver_sql("PRAGMA busy_timeout=1000")  # fail fast if the lock were still held
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
        row = self.conn.execute(select(Transaction.category, Transaction.category_source)
                                .where(Transaction.id == "cc|0")).fetchone()
        self.assertEqual(tuple(row), ("Groceries", "manual"))

    def test_review_suggestions_need_confirmation(self):
        for d in range(3):
            self.tx("cc", f"2026-09-0{d + 1}", -9.0, "SQ *BLUE BOTTLE")
        self.tx("cc", "2026-09-05", -30.0, "Z & H GRILL CORP")
        self.conn.execute(update(Transaction).values(needs_review=1))
        db.set_setting(self.conn, "openrouter_api_key", "k")

        def fake(key, model, prompt):
            items = json.loads(prompt.split("Transactions (JSON):\n")[1].split("\n\nReply")[0])
            return json.dumps([{"i": it["i"], "category": "Coffee & Snacks" if "Blue" in it["payee"] else "Restaurants",
                                "confidence": 0.9} for it in items])

        sug = categorize.suggest_for_review(self.conn, caller=fake)
        self.assertEqual([(s["merchant"], s["count"], s["category"]) for s in sug],
                         [("Blue Bottle", 3, "Coffee & Snacks"), ("Z & H Grill Corp", 1, "Restaurants")])
        # nothing applied yet
        self.assertEqual(self.conn.execute(select(func.count())
                                           .select_from(Transaction)
                                           .where(Transaction.needs_review == 1)).fetchone()[0], 4)
        n = categorize.apply_to_group(self.conn, sug[0]["tx_ids"], "Coffee & Snacks", remember=True)
        self.assertEqual(n, 3)
        self.assertEqual(self.conn.execute(select(func.count())
                                           .select_from(Transaction)
                                           .where(Transaction.needs_review == 1)).fetchone()[0], 1)
        self.assertEqual(self.conn.execute(select(Rule.category)
                                           .where(Rule.match == "blue bottle")).fetchone()[0], "Coffee & Snacks")

    def test_skipped_merchants_are_left_out_of_the_suggestions(self):
        self.tx("cc", "2026-09-01", -9.0, "SQ *BLUE BOTTLE")
        self.tx("cc", "2026-09-05", -30.0, "Z & H GRILL CORP")
        self.conn.execute(update(Transaction).values(needs_review=1))
        asked = []

        def fake(key, model, prompt):
            items = json.loads(prompt.split("Transactions (JSON):\n")[1].split("\n\nReply")[0])
            asked.extend(it["payee"] for it in items)
            return json.dumps([{"i": it["i"], "category": "Restaurants", "confidence": 0.9} for it in items])

        sug = categorize.suggest_for_review(self.conn, caller=fake, skip=["blue  bottle"])   # as typed, any case or spacing
        self.assertEqual([s["merchant"] for s in sug], ["Z & H Grill Corp"])
        self.assertNotIn("Blue Bottle", asked)                                     # not even asked about

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
        self.conn.execute(update(Transaction).values(needs_review=1))
        # Last month's, already categorized by Runway (reviewed, not by hand): "always" corrects it too. One you
        # categorized yourself stays yours.
        self.tx("cc", "2026-08-03", -12.0, "SQ *BLUE BOTTLE 123", "Restaurants")
        self.tx("cc", "2026-07-03", -12.0, "SQ *BLUE BOTTLE 123", "Restaurants")
        self.conn.execute(update(Transaction).where(Transaction.id == "cc|2").values(category_source="ai", needs_review=0))
        self.conn.execute(update(Transaction).where(Transaction.id == "cc|3").values(category_source="manual", needs_review=0))
        n = categorize.set_category(self.conn, "cc|0", "Coffee & Snacks", remember=True)
        self.assertEqual(n, 2)
        self.assertEqual(self.conn.execute(select(Rule.match)).fetchone()[0], "blue bottle")
        self.assertEqual(self.conn.execute(select(func.count())
                                           .select_from(Transaction)
                                           .where(Transaction.needs_review == 1)).fetchone()[0], 0)
        got = dict(self.conn.execute(select(Transaction.id, Transaction.category)).fetchall())
        self.assertEqual((got["cc|2"], got["cc|3"]), ("Coffee & Snacks", "Restaurants"))
        with self.assertRaises(ValueError):
            categorize.set_category(self.conn, "cc|0", "Nope")


class CategoryTests(LedgerCase):
    def setUp(self):
        super().setUp()
        self.acct("cc", "credit", -10.0)
        self.tx("cc", "2026-09-01", -12.0, "CHIPOTLE", "Restaurants")
        self.conn.execute(insert(Rule).values(match="chipotle", category="Restaurants"))
        self.conn.execute(insert(Budget).values(category="Restaurants", amount=300))

    def test_budget_rollover(self):
        # Restaurants: $300 a month, rolling over from July. The setUp's $12 CHIPOTLE is on Sep 1.
        categories.add(self.conn, "Fast food", parent="Restaurants")
        self.conn.execute(update(Budget).where(Budget.category == "Restaurants").values(rollover_from="2026-07"))
        self.tx("cc", "2026-06-10", -50.0, "BEFORE", "Restaurants")        # before it rolled over: not counted
        self.tx("cc", "2026-07-10", -200.0, "JULY", "Restaurants")         # $100 left
        self.tx("cc", "2026-08-10", -350.0, "AUGUST", "Fast food")         # a subcategory counts: $50 of $400 left
        cats = [c for c in categories.all_categories(self.conn) if not c["is_transfer"] and not c["is_income"]]
        rows = {r["category"]: dict(r) for r in self.conn.execute(select(Budget))}
        carry = lambda m: server.budget_carry(self.conn, cats, rows, date.fromisoformat(m))["Restaurants"]
        self.assertEqual((carry("2026-07-01"), carry("2026-08-01"), carry("2026-09-01")), (0.0, 100.0, 50.0))
        self.tx("cc", "2026-08-20", -500.0, "BIG NIGHT", "Restaurants")     # going over isn't carried
        self.assertEqual(carry("2026-09-01"), 0.0)
        self.assertEqual(carry("2026-10-01"), 288.0)                        # September: $300 - $12
        server.api_budget_set(self.conn, {}, {"category": "Restaurants", "rollover": False})
        self.assertIsNone(self.conn.execute(select(Budget.rollover_from)).fetchone()[0])
        with self.assertRaises(server.ApiError):
            server.api_budget_set(self.conn, {}, {"category": "Groceries", "rollover": True})   # no budget to roll over

    def test_investment_accounts_stay_out_of_transactions(self):
        self.acct("brk", "investment", 5000.0)
        self.tx("brk", "2026-09-02", -250.0, "BUY VTI")
        self.conn.execute(update(Transaction).values(needs_review=1))
        got = server.api_transactions(self.conn, {}, None)
        self.assertEqual([t["description"] for t in got["items"]], ["CHIPOTLE"])
        self.assertEqual(got["total"], 1)
        self.assertEqual(server.api_state(self.conn, {}, None)["review_count"], 1)   # the card's, not the buy

    def test_coming_up_wears_its_merchants_logo(self):
        self.acct("chk", "checking", 1000.0)
        last = (freeze_today(self) - timedelta(days=20)).isoformat()
        self.conn.execute(insert(Recurring).values(name="Netflix", account_id="chk", amount=-15.49,
                                                   frequency="monthly", anchor_date=last, active=1))
        rid = self.conn.execute(select(Recurring.id)).fetchone()[0]
        self.tx("chk", last, -15.49, "NETFLIX.COM")
        self.conn.execute(update(Transaction).where(Transaction.description == "NETFLIX.COM")
                          .values(recurring_id=rid, merchant_id="m-netflix"))
        self.conn.execute(insert(Merchant).values(id="m-netflix", name="Netflix", logo="cG5n", logo_type="image/png"))
        fc = server.api_overview(self.conn, {"days": ["60"]}, None)
        ev = [e for e in fc["events"] if e.get("recurring_id") == rid]
        self.assertTrue(ev)
        self.assertEqual({e["logo"] for e in ev}, {"/api/merchants/m-netflix/logo"})

    def test_a_logo_chosen_for_a_recurring_item_shows_on_it_and_its_coming_up_entries(self):
        self.acct("chk", "checking", 1000.0)
        last = (freeze_today(self) - timedelta(days=20)).isoformat()
        for name, amount in (("Netflix", -15.49), ("Rent", -900)):
            self.conn.execute(insert(Recurring).values(name=name, account_id="chk", amount=amount,
                                                       frequency="monthly", anchor_date=last, active=1))
        rid, rent = (r[0] for r in self.conn.execute(select(Recurring.id).order_by(Recurring.id)))
        self.tx("chk", last, -15.49, "NETFLIX.COM")
        self.conn.execute(update(Transaction).where(Transaction.description == "NETFLIX.COM")
                          .values(recurring_id=rid, merchant_id="m-netflix"))
        self.conn.execute(insert(Merchant).values(id="m-netflix", name="Netflix", logo="cG5n", logo_type="image/png"))
        self.conn.execute(insert(Merchant).values(id="site:landlord.com", name="landlord.com", logo="cG5n", logo_type="image/png"))

        def logos():
            items = {i["id"]: i["logo"] for i in server.api_recurring(self.conn, {}, None)}
            ev = server.api_overview(self.conn, {"days": ["60"]}, None)["events"]
            return items, {e["recurring_id"]: e["logo"] for e in ev if e.get("recurring_id")}
        netflix = "/api/merchants/m-netflix/logo"
        self.assertEqual(logos(), ({rid: netflix, rent: None}, {rid: netflix, rent: None}))
        # chosen by the item's name: it works for an item with nothing matched, and wins over its matched transaction's logo
        self.conn.execute(insert(MerchantLogo).values(key="rent", website="landlord.com", hidden=0))
        self.conn.execute(insert(MerchantLogo).values(key="netflix", website=None, hidden=1))
        site = "/api/merchants/site%3Alandlord.com/logo"
        self.assertEqual(logos(), ({rid: None, rent: site}, {rid: None, rent: site}))

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
        self.assertEqual(self.conn.execute(select(Category.icon)
                                           .where(Category.name == "Eating out")).fetchone()[0], "🍔")
        categories.set_look(self.conn, "Eating out", "", "")                      # back to the default
        self.assertEqual(self.conn.execute(select(Category.icon)
                                           .where(Category.name == "Eating out")).fetchone()[0], None)
        for icon in ("1️⃣", "#️⃣", "🇯🇵", "👍🏽", "👨‍👩‍👧‍👦", "❤️", "↩️", "▶️", "ℹ️", "‼️", "〰️", "↔️"):             # anything the emoji keyboard types
            categories.set_look(self.conn, "Eating out", icon, None)
            self.assertEqual(self.conn.execute(select(Category.icon)
                                               .where(Category.name == "Eating out")).fetchone()[0], icon)
        for icon, color in (("abc", None), ("1", None), ("#", None), ("!?", None), ("é", None), ("!\ufe0f", None), ("🍔" * 17, None),
                            (None, "red"), (None, "#12345g")):
            with self.assertRaises(categories.CategoryError):
                categories.set_look(self.conn, "Eating out", icon, color)
        with self.assertRaises(categories.CategoryError):
            categories.set_look(self.conn, "Nope", "🍔", None)

    def test_add_sub_rename_remove(self):
        categories.add(self.conn, "Fast food", parent="Restaurants")
        sub = self.conn.execute(select(Category).where(Category.name == "Fast food")).fetchone()
        self.assertEqual((sub["parent"], sub["is_transfer"], sub["is_income"]), ("Restaurants", 0, 0))
        with self.assertRaises(categories.CategoryError):
            categories.add(self.conn, "Burgers", parent="Fast food")  # one level only
        with self.assertRaises(categories.CategoryError):
            categories.add(self.conn, "restaurants")  # duplicate, any case
        tree = [c["name"] for c in categories.all_categories(self.conn)]
        self.assertEqual(tree[tree.index("Restaurants") + 1], "Fast food")
        # rename carries transactions, rules, budgets and children along
        categories.rename(self.conn, "Restaurants", "Dining")
        self.assertEqual(self.conn.execute(select(Transaction.category)).fetchone()[0], "Dining")
        self.assertEqual(self.conn.execute(select(Rule.category)).fetchone()[0], "Dining")
        self.assertEqual(self.conn.execute(select(Budget.category)).fetchone()[0], "Dining")
        self.assertEqual(self.conn.execute(select(Category.parent)
                                           .where(Category.name == "Fast food")).fetchone()[0], "Dining")
        # can't remove a parent that still has subcategories, or a built-in
        with self.assertRaises(categories.CategoryError):
            categories.remove(self.conn, "Dining")
        with self.assertRaises(categories.CategoryError):
            categories.remove(self.conn, "Credit Card Payment")
        categories.remove(self.conn, "Fast food")
        moved = categories.remove(self.conn, "Dining", move_to="Other")
        self.assertEqual(moved, 1)
        self.assertEqual(self.conn.execute(select(Transaction.category)).fetchone()[0], "Other")
        self.assertEqual(self.conn.execute(select(Rule.category)).fetchone()[0], "Other")
        self.assertIsNone(self.conn.execute(select(Budget.category)).fetchone())

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
        # (anything nested deeper by an earlier version was moved up by migration 0039: tests/test_migrations.py)
        categories.add(self.conn, "Burgers", parent="Food")
        categories.move(self.conn, "Burgers", None)
        categories.move(self.conn, "Burgers", "Income")
        self.assertEqual(self.conn.execute(select(Category.is_income)
                                           .where(Category.name == "Burgers")).fetchone()[0], 1)
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
        row = self.conn.execute(select(Transaction.category, Transaction.needs_review)).fetchone()
        self.assertEqual(tuple(row), (None, 1))
        self.assertIsNone(self.conn.execute(select(Rule.id)).fetchone())


class CategorizeFixTests(LedgerCase):
    def setUp(self):
        super().setUp()
        self.acct("chk", "checking", 1000.0)

    def test_autopay_needs_a_card(self):
        cat = lambda d: categorize.heuristic_category({"description": d, "amount": -100}, "checking")
        for bill in ("COMCAST XFINITY AUTOPAY", "STATE FARM AUTOPAY", "CITY WATER EPAY", "CAPITAL ONE AUTO FINANCE PMT",
                     "CHASE MORTGAGE AUTOPAY", "CAPITAL ONE AUTO PMT", "NORTHWIND MORTG OLB MTGPMT"):
            self.assertIsNone(cat(bill), bill)
        for card in ("CHASE CREDIT CRD AUTOPAY", "CAPITAL ONE MOBILE PMT", "AMEX EPAYMENT ACH PMT", "DISCOVER E-PAYMENT",
                     "CITI AUTOPAY PAYMENT", "BARCLAYCARD US AUTOPAY", "APPLECARD GSBANK PAYMENT"):
            self.assertEqual(cat(card), "Credit Card Payment", card)

    def test_your_rules_beat_the_built_in_guess(self):
        from runway import rules
        rules.save(self.conn, {"match": "chase credit", "category": "Transfer"})
        self.tx("chk", "2026-09-10", -300.0, "CHASE CREDIT CRD AUTOPAY")
        categorize.categorize(self.conn, use_ai=False)
        row = self.conn.execute(select(Transaction.category, Transaction.category_source)).fetchone()
        self.assertEqual((row["category"], row["category_source"]), ("Transfer", "rule"))

    def test_a_split_that_cant_be_made_goes_to_review_and_the_sync_goes_on(self):
        import json
        self.conn.execute(insert(Rule).values(match="costco",
                                              split=json.dumps([{"category": "Groceries", "percent": 60},
                                                                {"category": "Gone", "percent": 40}])))
        self.tx("chk", "2026-09-10", -250.0, "COSTCO WHSE")
        self.tx("chk", "2026-09-11", -20.0, "COFFEE")
        categorize.categorize(self.conn, use_ai=False)
        row = self.conn.execute(select(Transaction.is_split, Transaction.needs_review)
                                .where(Transaction.description == "COSTCO WHSE")).fetchone()
        self.assertEqual((row["is_split"], row["needs_review"]), (0, 1))

    def test_renaming_or_removing_a_category_follows_order_items(self):
        self.conn.execute(insert(RetailOrder).values(id="amazon:1", retailer="amazon", order_number="1"))
        self.conn.execute(insert(RetailItem).values(order_id="amazon:1", title="Oats", amount=5, category="Groceries",
                                                    category_source="manual"))
        self.conn.execute(insert(RetailItemMemory).values(key="oats", category="Groceries"))
        categories.rename(self.conn, "Groceries", "Food")
        self.assertEqual(self.conn.execute(select(RetailItem.category)).fetchone()[0], "Food")
        self.assertEqual(self.conn.execute(select(RetailItemMemory.category)).fetchone()[0], "Food")
        categories.remove(self.conn, "Food")
        self.assertIsNone(self.conn.execute(select(RetailItem.category_source)).fetchone()[0])
        self.assertEqual(self.conn.execute(select(func.count()).select_from(RetailItemMemory)).fetchone()[0], 0)


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
