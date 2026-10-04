"""Amazon and Target orders: reading what the browser extension sends, matching charges to transactions, and splitting."""
import hashlib
import hmac
import json
import os
import random
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import UTC, date, datetime, timedelta
from unittest import mock

from sqlalchemy import delete, func, insert, select, update

from runway import categorize, db, monitoring, oidc, retail, splits
from runway.retail import split as retail_split
from runway.retail import store
from runway.models import Account, AiLog, Category, RetailCharge, RetailItem, RetailItemMemory, RetailOrder, Transaction
from runway.server.api import retail as api_retail
from runway.server.api import transactions as api_tx
from tests.shared import DbCase

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "amazon")
ORDER = "111-6778632-7354601"   # 4 items, $57.69 + $2.99 shipping + $3.19 tax - $2.99 free shipping = $60.88


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


def fake_ai(answers):
    """A stand-in for the model: picks the category of the first keyword found in each item's title."""
    def caller(_key, _model, prompt):
        items = json.loads(prompt.split("Items (JSON):\n", 1)[1].split("\n", 1)[0])
        out = []
        for it in items:
            cat = next((c for word, c in answers.items() if word in it["item"].lower()), None)
            out.append({"i": it["i"], "category": cat, "confidence": 0.9})
        return json.dumps(out)
    return caller


AI = fake_ai({"sash": "Shopping", "tea": "Groceries", "crucible": "Entertainment", "ziploc": "Groceries"})


class Base(DbCase):
    def setUp(self):
        super().setUp()
        self.c.execute(insert(Account).values(id="card", name="Card", kind="credit", balance=0))
        self.since = mock.patch.object(store, "since", return_value="2024-01-01")
        self.since.start()

    def tearDown(self):
        self.since.stop()

    def tx(self, tid, posted, amount, desc, category=None, source=None):
        self.c.execute(insert(Transaction).values(id=tid, account_id="card", posted=posted, amount=amount,
                                                  description=desc, payee=desc, category=category,
                                                  category_source=source))

    def row(self, tid):
        return self.c.execute(select(Transaction).where(Transaction.id == tid)).fetchone()

    def parts(self, tid):
        return [(p["category"], p["amount"]) for p in splits.get(self.c, tid)]

    def amazon_order_with_charge(self, amount=-60.88, day="2024-09-09"):
        retail.amazon_order(self.c, ORDER, fixture(f"order-details-{ORDER}.html"))
        store.save_charge(self.c, f"amazon|{ORDER}|x", retail.order_key("amazon", ORDER), day, amount, None)


class AmazonPagesTests(Base):
    def test_transactions_page(self):
        r = retail.amazon_transactions(self.c, fixture("transactions-page.html"))
        self.assertEqual(r["orders"], ["123-4567890-1234567"])            # its items are still to be read
        self.assertIn("ppw-widgetState", r["next_form"])                   # and there's another page
        charges = self.c.execute(select(RetailCharge.date, RetailCharge.amount, RetailCharge.payment)
                                 .order_by(RetailCharge.date)).fetchall()
        self.assertEqual([tuple(c) for c in charges],
                         [("2024-10-09", -28.79, "Mastercard ****1234"), ("2024-10-11", -45.19, "Visa ****1234")])
        retail.amazon_transactions(self.c, fixture("transactions-page.html"))   # the same page again changes nothing
        self.assertEqual(self.c.execute(select(func.count()).select_from(RetailCharge)).fetchone()[0], 2)

    def test_stops_paging_at_the_start_date(self):
        with mock.patch.object(store, "since", return_value="2024-10-10"):
            r = retail.amazon_transactions(self.c, fixture("transactions-page.html"))
        self.assertIsNone(r["next_form"])
        self.assertEqual([tuple(c) for c in self.c.execute(select(RetailCharge.date))], [("2024-10-11",)])

    def test_signed_out(self):
        with self.assertRaises(retail.RetailError):
            retail.amazon_transactions(self.c, '<html><form name="signIn" action="/ap/signin"></form></html>')

    def test_order_details(self):
        self.assertEqual(retail.amazon_order(self.c, ORDER, fixture(f"order-details-{ORDER}.html")), {"read": True, "items": 4})
        o = self.c.execute(select(RetailOrder)).fetchone()
        self.assertEqual((o["placed"], o["total"], o["subtotal"], o["tax"], o["payment"], o["details"]),
                         ("2024-09-08", 60.88, 57.69, 3.19, "Prime Visa 1111", 1))
        items = self.c.execute(select(RetailItem.title, RetailItem.amount).order_by(RetailItem.position)).fetchall()
        self.assertEqual([i["amount"] for i in items], [7.49, 18.95, 9.98, 21.27])
        self.assertTrue(items[2]["title"].startswith("The Crucible"))

    def test_unreadable_order_is_retried_a_few_times(self):
        for _ in range(retail.MAX_ATTEMPTS):
            self.assertEqual(store.need_details(self.c, "amazon", [ORDER]) if _ else [ORDER], [ORDER])
            self.assertEqual(retail.amazon_order(self.c, ORDER, "<html>nothing here</html>"), {"read": False})
        self.assertEqual(store.need_details(self.c, "amazon", [ORDER]), [])   # given up on

    def test_sign_in_and_robot_pages_dont_use_up_tries(self):
        pages = {"signin": '<html><form name="signIn" method="post" action="https://www.amazon.com/ap/signin"></form></html>',
                 "robot": '<html><title>Robot Check</title><form action="/errors/validateCaptcha">'
                          '<input id="captchacharacters"></form></html>'}
        for code, html in pages.items():
            for _ in range(retail.MAX_ATTEMPTS + 1):
                with self.assertRaises(retail.RetailError) as e:
                    retail.amazon_order(self.c, ORDER, html)
                self.assertEqual(e.exception.code, code)
            with self.assertRaises(retail.RetailError):
                retail.amazon_transactions(self.c, html)
        store.save_order(self.c, "amazon", ORDER)
        self.assertEqual(store.need_details(self.c, "amazon", [ORDER]), [ORDER])   # still to be read
        # An ordinary order page links to sign-in too: that isn't being signed out.
        self.assertEqual(retail.amazon_order(self.c, ORDER, fixture(f"order-details-{ORDER}.html"))["read"], True)

    def test_a_changed_order_notes_when_in_utc(self):
        oid = store.save_order(self.c, "amazon", ORDER)
        self.c.execute(update(RetailOrder).where(RetailOrder.id == oid).values(updated=None))
        store.save_order(self.c, "amazon", ORDER)   # nothing to change: not noted
        self.assertIsNone(self.c.execute(select(RetailOrder.updated)).fetchone()[0])
        store.save_order(self.c, "amazon", ORDER, total=12.5)
        updated = self.c.execute(select(RetailOrder.updated)).fetchone()[0]
        self.assertRegex(updated, r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$")   # as the column's own default writes it
        self.assertLess(abs((datetime.fromisoformat(updated) - datetime.now(UTC).replace(tzinfo=None)).total_seconds()), 5)

    def test_only_the_last_try_in_an_import_counts(self):
        for _ in range(retail.MAX_ATTEMPTS + 2):
            retail.amazon_order(self.c, ORDER, "<html>nothing here</html>", final=False)
        self.assertEqual(store.need_details(self.c, "amazon", [ORDER]), [ORDER])
        retail.amazon_order(self.c, ORDER, "<html>nothing here</html>", final=True)
        self.assertEqual(self.c.execute(select(RetailOrder.attempts)).fetchone()[0], 1)

    def test_same_charge_on_two_pages_is_two_charges(self):
        r = retail.amazon_transactions(self.c, fixture("transactions-page.html"))
        retail.amazon_transactions(self.c, fixture("transactions-page.html"), r["seen"])   # as if it were the next page
        self.assertEqual(self.c.execute(select(func.count()).select_from(RetailCharge)).fetchone()[0], 4)

    def test_reads_back_to_amazon_orders_whose_details_never_came(self):
        self.since.stop()
        try:
            db.set_setting(self.c, "retail_last_amazon", (datetime.now() - timedelta(days=1)).isoformat())
            usual = retail.since(self.c, "amazon")
            old = (date.fromisoformat(usual) - timedelta(days=45)).isoformat()
            oid = store.save_order(self.c, "amazon", ORDER)   # from the transactions page: no placed date yet
            store.save_charge(self.c, f"amazon|{ORDER}|1", oid, old, -60.88, None)
            self.assertEqual(retail.since(self.c, "amazon"), old)
            self.c.execute(update(RetailOrder).values(attempts=retail.MAX_ATTEMPTS))
            self.assertEqual(retail.since(self.c, "amazon"), usual)
        finally:
            self.since.start()


class SplitTests(Base):
    def test_order_splits_its_transaction_by_item(self):
        self.amazon_order_with_charge()
        self.tx("t1", "2024-09-11", -60.88, "AMAZON MKTPL*ZX81J2", "Shopping", "rule")
        self.tx("t2", "2024-09-11", -60.88, "SHELL OIL")           # same amount, not Amazon
        db.set_setting(self.c, "openrouter_api_key", "k")
        out = retail.finish(self.c, "amazon", caller=AI)
        self.assertEqual((out["matched"], out["split"]), (1, 1))
        parts = splits.get(self.c, "t1")
        self.assertEqual(round(sum(p["amount"] for p in parts), 2), -60.88)
        # Groceries: tea 18.95 + bags 21.27 = 40.22 of 57.69, so 69.7% of the $60.88 charge
        self.assertEqual(self.parts("t1"), [("Groceries", -42.44), ("Entertainment", -10.53), ("Shopping", -7.91)])
        self.assertIn("Ziploc", parts[0]["note"])
        self.assertFalse(self.row("t2")["is_split"])

    def test_one_category_just_categorizes(self):
        self.amazon_order_with_charge()
        self.tx("t1", "2024-09-10", -60.88, "Amazon.com*RT4", None)
        db.set_setting(self.c, "openrouter_api_key", "k")
        retail.finish(self.c, "amazon", caller=fake_ai({"": "Groceries"}))
        t = self.row("t1")
        self.assertEqual((t["category"], t["category_source"], t["is_split"]), ("Groceries", "retail", 0))

    def test_without_ai_items_take_the_transactions_category(self):
        self.amazon_order_with_charge()
        self.tx("t1", "2024-09-10", -60.88, "AMZN Mktp US", "Shopping", "rule")
        out = retail.finish(self.c, "amazon")
        self.assertEqual((out["items"]["left"], out["category"], out["split"]), (4, 0, 0))   # nothing to go on: left as it was
        self.assertEqual(tuple(self.row("t1"))[6:8], ("Shopping", "rule"))
        # Once you categorize one item, the rest share the transaction's category.
        item = self.c.execute(select(RetailItem.id).where(RetailItem.title.like("Organic%"))).fetchone()["id"]
        retail.set_item_category(self.c, item, "Groceries")
        self.assertEqual(self.parts("t1"), [("Shopping", -40.88), ("Groceries", -20.0)])

    def test_your_own_choices_are_left_alone(self):
        self.amazon_order_with_charge()
        self.tx("t1", "2024-09-10", -60.88, "AMZN Mktp US", "Gifts & Donations", "manual")
        db.set_setting(self.c, "openrouter_api_key", "k")
        retail.finish(self.c, "amazon", caller=AI)
        self.assertEqual((self.row("t1")["category"], self.row("t1")["is_split"]), ("Gifts & Donations", 0))
        charge = self.c.execute(select(RetailCharge.id, RetailCharge.tx_id)).fetchone()
        self.assertEqual(charge["tx_id"], "t1")                            # matched, just not changed
        self.assertEqual(retail.apply(self.c, charge["id"], force=True), "split")   # unless you ask

    def test_a_split_you_edited_is_left_alone(self):
        self.amazon_order_with_charge()
        self.tx("t1", "2024-09-10", -60.88, "AMZN Mktp US", "Shopping", "rule")
        db.set_setting(self.c, "openrouter_api_key", "k")
        retail.finish(self.c, "amazon", caller=AI)
        splits.set_splits(self.c, "t1", [{"amount": -30.88, "category": "Groceries"}, {"amount": -30, "category": "Shopping"}])
        item = self.c.execute(select(RetailItem.id).where(RetailItem.title.like("The Crucible%"))).fetchone()["id"]
        retail.set_item_category(self.c, item, "Shopping")
        self.assertEqual(self.parts("t1"), [("Groceries", -30.88), ("Shopping", -30.0)])

    def test_what_apply_says_in_each_case(self):
        self.assertEqual(retail.apply(self.c, "nope"), "unmatched")
        self.amazon_order_with_charge()
        self.tx("t1", "2024-09-10", -60.88, "AMZN Mktp US")                  # no category yet
        db.set_setting(self.c, "openrouter_api_key", "k")
        retail.finish(self.c, "amazon", caller=AI)
        charge = self.c.execute(select(RetailCharge.id)).fetchone()["id"]
        t = self.row("t1")
        self.assertEqual((t["category"], t["is_split"]), ("Groceries", 1))   # a split names its first part
        self.assertEqual(retail.apply(self.c, charge), "same")               # nothing changed since
        # Every item the same category now: the split goes and the transaction just takes it.
        self.c.execute(update(RetailItem).values(category="Shopping"))
        self.assertEqual(retail.apply(self.c, charge), "category")
        t = self.row("t1")
        self.assertEqual((t["category"], t["category_source"], t["is_split"], self.parts("t1")), ("Shopping", "retail", 0, []))
        self.assertEqual(json.loads(self.c.execute(select(RetailCharge.applied)).fetchone()[0]),
                         {"parts": [], "category": "Shopping", "prev": {"category": None, "source": None}})
        self.assertEqual(retail.apply(self.c, charge), "same")
        self.c.execute(update(RetailCharge).values(amount=5))                  # a refund keeps its category
        self.assertEqual(retail.apply(self.c, charge), "same")
        self.c.execute(delete(Transaction))
        self.assertEqual(retail.apply(self.c, charge), "unmatched")

    def test_a_model_that_fails_leaves_items_to_their_departments(self):
        oid = store.save_order(self.c, "amazon", "113-0000000-0000000", details=1)
        store.save_items(self.c, oid, [{"title": "Coffee", "amount": 30.0, "department": "Grocery & Gourmet Food"},
                                         {"title": "Mystery", "amount": 5.0}])
        db.set_setting(self.c, "openrouter_api_key", "k")

        def broken(*_a):
            raise RuntimeError("model is down")
        self.assertEqual(retail.categorize_items(self.c, caller=broken), {"memory": 0, "ai": 0, "department": 1, "left": 1})
        log = self.c.execute(select(AiLog.purpose, AiLog.ok, AiLog.message)).fetchone()
        self.assertEqual(tuple(log), ("orders", 0, "model is down"))

    LEAKY = ("OpenRouter HTTP 401: bad key for https://user:hunter2@api.example.com/v1/chat?key=sk-or-v1-abc123 "
             "while categorizing order 987654321 (card 4111111111111111)")

    def assert_scrubbed(self, text):
        for private in ("hunter2", "sk-or-v1-abc123", "987654321", "4111111111111111"):
            self.assertNotIn(private, text)
        self.assertIn("OpenRouter HTTP 401", text)   # the useful part stays, as in the transaction categorizer

    def test_a_failed_item_request_is_logged_scrubbed_like_the_transaction_categorizer(self):
        oid = store.save_order(self.c, "amazon", "113-0000000-0000000", details=1)
        store.save_items(self.c, oid, [{"title": "Coffee", "amount": 30.0, "department": "Grocery & Gourmet Food"}])
        db.set_setting(self.c, "openrouter_api_key", "k")

        def broken(*_a):
            raise RuntimeError(self.LEAKY)
        retail.categorize_items(self.c, caller=broken)
        logged = self.c.execute(select(AiLog.message)).fetchone()["message"]
        self.assert_scrubbed(logged)
        self.assertEqual(logged, monitoring.public_text(self.LEAKY)[:500])
        # the transaction categorizer logs the very same text for the same failure
        self.c.execute(delete(AiLog))
        group = [{"posted": "2024-09-10", "amount": -5.0, "kind": "checking", "payee": "Cafe", "description": "Cafe"}]
        with self.assertRaises(RuntimeError):
            categorize.ask_model(self.c, [group], caller=broken)
        self.assertEqual(self.c.execute(select(AiLog.message)).fetchone()["message"], logged)

    def test_a_failed_order_suggestion_is_scrubbed_in_the_log_and_the_error(self):
        oid = store.save_order(self.c, "amazon", "113-0000000-0000000", details=1)
        store.save_items(self.c, oid, [{"title": "Coffee", "amount": 30.0}])
        db.set_setting(self.c, "openrouter_api_key", "k")

        def broken(*_a):
            raise RuntimeError(self.LEAKY)
        with mock.patch.object(monitoring, "public_text", wraps=monitoring.public_text) as scrub:
            with self.assertRaises(retail.RetailError) as cm:
                retail.suggest_for_order(self.c, oid, caller=broken)
        scrub.assert_called_once_with(self.LEAKY)
        self.assertTrue(str(cm.exception).startswith("The AI request failed: "))
        self.assert_scrubbed(str(cm.exception))
        logged = self.c.execute(select(AiLog.message, AiLog.ok)).fetchone()
        self.assertEqual(logged["ok"], 0)
        self.assert_scrubbed(logged["message"])

    def test_item_category_is_remembered_and_resplits(self):
        self.amazon_order_with_charge()
        self.tx("t1", "2024-09-10", -60.88, "AMZN Mktp US", "Shopping", "rule")
        db.set_setting(self.c, "openrouter_api_key", "k")
        retail.finish(self.c, "amazon", caller=AI)
        item = self.c.execute(select(RetailItem.id, RetailItem.title)
                              .where(RetailItem.title.like("The Crucible%"))).fetchone()
        retail.set_item_category(self.c, item["id"], "Shopping")
        self.assertEqual(self.parts("t1"), [("Groceries", -42.44), ("Shopping", -18.44)])
        # The same book in another order gets Shopping without asking the model.
        oid = store.save_order(self.c, "amazon", "112-0000000-0000000", details=1)
        store.save_items(self.c, oid, [{"title": item["title"], "amount": 9.98}])
        retail.categorize_items(self.c, caller=fake_ai({}))
        self.assertEqual(tuple(self.c.execute(select(RetailItem.category, RetailItem.category_source)
                                              .where(RetailItem.order_id == oid)).fetchone()),
                         ("Shopping", "memory"))

    def test_unlink_restores_and_isnt_matched_again(self):
        self.amazon_order_with_charge()
        self.tx("t1", "2024-09-10", -60.88, "AMZN Mktp US", "Shopping", "rule")
        db.set_setting(self.c, "openrouter_api_key", "k")
        retail.finish(self.c, "amazon", caller=AI)
        charge = self.c.execute(select(RetailCharge.id)).fetchone()["id"]
        retail.unlink(self.c, charge)
        t = self.row("t1")
        self.assertEqual((t["is_split"], t["category"], t["category_source"]), (0, "Shopping", "rule"))
        retail.match_and_apply(self.c)
        self.assertIsNone(self.c.execute(select(RetailCharge.tx_id)).fetchone()["tx_id"])
        self.assertEqual(retail.link(self.c, charge, "t1"), "split")   # you can still pick it yourself

    def _split_order(self):
        self.amazon_order_with_charge()
        self.tx("t1", "2024-09-10", -60.88, "AMZN Mktp US", "Shopping", "rule")
        db.set_setting(self.c, "openrouter_api_key", "k")
        retail.finish(self.c, "amazon", caller=AI)
        return self.c.execute(select(RetailCharge.id)).fetchone()["id"]

    def charge(self):
        return dict(self.c.execute(select(RetailCharge.tx_id, RetailCharge.match_source, RetailCharge.not_tx, RetailCharge.applied)).fetchone())

    def test_undo_not_this_transaction(self):
        charge = self._split_order()
        before, parts = self.charge(), self.parts("t1")
        r = api_retail.api_retail_unlink(self.c, {}, {}, charge)
        self.assertEqual(self.row("t1")["is_split"], 0)
        api_retail.api_retail_charge_restore(self.c, {}, {"was": r["was"]}, charge)
        self.assertEqual(self.charge(), before)
        self.assertEqual(self.parts("t1"), parts)

    def test_undo_split_by_items(self):
        charge = self._split_order()
        api_retail.api_retail_unlink(self.c, {}, {}, charge)
        retail.link(self.c, charge, "t1")
        splits.clear(self.c, "t1")   # you categorized it yourself, as one thing
        self.c.execute(update(Transaction).where(Transaction.id == "t1").values(category="Groceries", category_source="manual"))
        before = self.charge()
        r = api_retail.api_retail_apply(self.c, {}, {}, charge)
        self.assertEqual(self.row("t1")["is_split"], 1)
        api_retail.api_retail_charge_restore(self.c, {}, {"was": r["was"]}, charge)
        t = self.row("t1")
        self.assertEqual((t["is_split"], t["category"]), (0, "Groceries"))
        self.assertEqual(self.charge(), before)

    def test_undo_an_items_category_puts_back_the_memory_items_and_split(self):
        self._split_order()
        item = self.c.execute(select(RetailItem.id).where(RetailItem.title.like("The Crucible%"))).fetchone()["id"]
        parts = self.parts("t1")
        items = [tuple(r) for r in self.c.execute(select(RetailItem.id, RetailItem.category, RetailItem.category_source).order_by(RetailItem.id))]
        r = api_retail.api_retail_item(self.c, {}, {"category": "Shopping"}, str(item))
        self.assertNotEqual(self.parts("t1"), parts)
        api_retail.api_retail_item_restore(self.c, {}, {"was": r["was"]}, str(item))
        self.assertEqual(self.parts("t1"), parts)
        self.assertEqual([tuple(r) for r in self.c.execute(select(RetailItem.id, RetailItem.category, RetailItem.category_source).order_by(RetailItem.id))], items)
        self.assertIsNone(self.c.execute(select(RetailItemMemory.category)).fetchone())   # nothing was remembered before

    def test_undo_checks_what_it_is_sent(self):
        charge = self._split_order()
        from runway.server.common import ApiError
        for body in ({}, {"was": "x"}, {"was": {"charge": {"id": "other"}}}):
            with self.subTest(body=body), self.assertRaises(ApiError):
                api_retail.api_retail_charge_restore(self.c, {}, body, charge)
        with self.assertRaises(ApiError):
            api_retail.api_retail_item_restore(self.c, {}, {}, "1")
        with self.assertRaises(ApiError):
            api_retail.api_retail_item(self.c, {}, {"category": "Shopping"}, "abc")

    def test_pending_transaction_that_posts_is_matched_again(self):
        self.amazon_order_with_charge()
        self.tx("pend", "2024-09-09", -60.88, "AMZN Mktp US", "Shopping", "rule")
        db.set_setting(self.c, "openrouter_api_key", "k")
        retail.finish(self.c, "amazon", caller=AI)
        # The bank replaces the pending transaction with a posted one (its parts carry over, as a sync does).
        self.tx("posted", "2024-09-11", -60.88, "AMZN Mktp US", "Shopping", "rule")
        splits.carry_over(self.c, "pend", "posted", -60.88)
        self.c.execute(delete(Transaction).where(Transaction.id == "pend"))
        retail.match_and_apply(self.c)
        self.assertEqual(self.c.execute(select(RetailCharge.tx_id)).fetchone()["tx_id"], "posted")
        self.assertEqual(len(self.parts("posted")), 3)

    def test_closest_date_wins(self):
        self.amazon_order_with_charge()
        self.tx("late", "2024-09-18", -60.88, "AMZN Mktp US")
        self.tx("near", "2024-09-10", -60.88, "AMZN Mktp US")
        self.tx("early", "2024-09-01", -60.88, "AMZN Mktp US")       # before the window
        retail.match(self.c)
        self.assertEqual(self.c.execute(select(RetailCharge.tx_id)).fetchone()["tx_id"], "near")

    def test_two_orders_that_could_be_either_transaction_are_left_to_you(self):
        other = "111-0000000-0000001"
        self.amazon_order_with_charge(day="2024-09-09")
        store.save_order(self.c, "amazon", other)
        store.save_charge(self.c, f"amazon|{other}|x", retail.order_key("amazon", other), "2024-09-11", -60.88, None)
        self.tx("a", "2024-09-11", -60.88, "AMZN Mktp US")
        self.tx("b", "2024-09-12", -60.88, "AMZN Mktp US")
        self.assertEqual(retail.match(self.c), 0)
        # Far enough apart that each has its own: matched.
        self.c.execute(update(RetailCharge)
                       .where(RetailCharge.order_id == retail.order_key("amazon", other)).values(date="2024-10-01"))
        self.tx("c", "2024-10-02", -60.88, "AMZN Mktp US")
        self.assertEqual(retail.match(self.c), 2)
        got = {r["order_id"]: r["tx_id"] for r in self.c.execute(select(RetailCharge.order_id, RetailCharge.tx_id))}
        self.assertEqual(got, {retail.order_key("amazon", ORDER): "a", retail.order_key("amazon", other): "c"})


class TransactionCategoryTests(Base):
    """Picking a category for a transaction sets the category of every item on its order."""

    def setUp(self):
        super().setUp()
        self.amazon_order_with_charge()
        self.tx("t1", "2024-09-10", -60.88, "AMZN Mktp US", "Shopping", "rule")
        db.set_setting(self.c, "openrouter_api_key", "k")
        retail.finish(self.c, "amazon", caller=AI)   # split three ways, items by the AI

    def items(self):
        return [tuple(r) for r in self.c.execute(
            select(RetailItem.category, RetailItem.category_source, RetailItem.confidence).order_by(RetailItem.id))]

    def test_every_item_follows_and_the_transaction_stays_whole(self):
        self.assertTrue(self.row("t1")["is_split"])
        r = api_tx.api_tx_category(self.c, None, {"category": "Gifts & Donations"}, "t1")
        self.assertEqual(self.items(), [("Gifts & Donations", "manual", 1)] * 4)
        t = self.row("t1")
        self.assertEqual((t["category"], t["category_source"], t["is_split"]), ("Gifts & Donations", "manual", 0))
        self.assertEqual(self.parts("t1"), [])
        # a later re-apply (an item recategorized elsewhere) leaves your choice alone
        charge = self.c.execute(select(RetailCharge.id)).fetchone()["id"]
        self.assertEqual(retail.apply(self.c, charge), "manual")
        # nothing was remembered for the same items in other orders
        self.assertEqual(self.c.execute(select(func.count()).select_from(RetailItemMemory)).scalar(), 0)
        self.assertIn("items", r["was"][0])

    def test_a_refunds_category_leaves_the_orders_items_alone(self):
        oid = retail.order_key("amazon", ORDER)
        before_items, before_parts = self.items(), self.parts("t1")
        self.tx("back", "2024-09-20", 20.0, "AMZN Mktp US Refund", "Shopping", "rule")
        store.save_charge(self.c, f"amazon|{ORDER}|r", oid, "2024-09-20", 20.0, None)
        self.c.execute(update(RetailCharge).where(RetailCharge.id == f"amazon|{ORDER}|r").values(tx_id="back"))
        api_tx.api_tx_category(self.c, None, {"category": "Refunds"}, "back")
        self.assertEqual(self.row("back")["category"], "Refunds")
        self.assertEqual((self.items(), self.parts("t1")), (before_items, before_parts))

    def test_undo_puts_back_the_split_and_each_items_own_category(self):
        # awkward values: an item picked by hand, one with no category at all, a fractional confidence
        ids = sorted(self.c.execute(select(RetailItem.id)).scalars())
        self.c.execute(update(RetailItem).where(RetailItem.id == ids[0]).values(category_source="manual", confidence=1))
        self.c.execute(update(RetailItem).where(RetailItem.id == ids[-1]).values(category=None, category_source=None, confidence=None))
        self.c.execute(update(RetailItem).where(RetailItem.id == ids[-2]).values(confidence=0.123))
        before_items, before_parts, before = self.items(), self.parts("t1"), tuple(self.row("t1"))
        r = api_tx.api_tx_bulk(self.c, None, {"ids": ["t1"], "category": "Gifts & Donations"})
        self.assertNotEqual(self.items(), before_items)
        self.assertEqual(api_tx.api_tx_bulk(self.c, None, {"restore": r["was"]})["updated"], 1)
        self.assertEqual(self.items(), before_items)
        self.assertEqual(self.parts("t1"), before_parts)
        self.assertEqual(tuple(self.row("t1")), before)

    def test_a_category_filter_shows_the_part_and_a_pick_changes_only_its_items(self):
        got = api_tx.api_transactions(self.c, {"category": ["Groceries"]}, None)
        self.assertEqual(got["family"], ["Groceries"])
        self.assertEqual(got["items"][0]["match"], {"amount": -42.44, "categories": ["Groceries"]})
        self.assertNotIn("match", api_tx.api_transactions(self.c, {}, None)["items"][0])   # no filter: the whole thing
        before_items, before_parts = self.items(), self.parts("t1")
        r = api_tx.api_tx_category(self.c, None, {"category": "Gifts & Donations", "only": "Groceries"}, "t1")
        self.assertTrue(r["part"])
        # the tea and the bags (the Groceries items) follow; the sash and the crucible keep theirs
        self.assertEqual(self.parts("t1"), [("Gifts & Donations", -42.44), ("Entertainment", -10.53), ("Shopping", -7.91)])
        self.assertEqual([c for c, _s, _n in self.items()].count("Gifts & Donations"), 2)
        self.assertEqual(self.c.execute(select(func.count()).select_from(RetailItemMemory)).scalar(), 0)
        api_tx.api_tx_bulk(self.c, None, {"restore": r["was"]})   # Undo puts the items and the parts back
        self.assertEqual((self.items(), self.parts("t1")), (before_items, before_parts))

    def test_when_every_part_ends_up_one_category_the_transaction_takes_it(self):
        api_tx.api_tx_bulk(self.c, None, {"ids": ["t1"], "category": "Shopping", "only": "Groceries"})
        self.assertEqual(self.parts("t1"), [("Shopping", -50.35), ("Entertainment", -10.53)])
        api_tx.api_tx_category(self.c, None, {"category": "Shopping", "only": "Entertainment"}, "t1")
        t = self.row("t1")
        self.assertEqual((t["category"], t["is_split"]), ("Shopping", 0))
        self.assertEqual(self.parts("t1"), [])

    def test_bulk_sets_items_of_each_selected_order(self):
        self.tx("t9", "2024-09-10", -5.0, "CORNER STORE", "Shopping", "rule")   # no order
        api_tx.api_tx_bulk(self.c, None, {"ids": ["t1", "t9"], "category": "Groceries"})
        self.assertEqual(self.items(), [("Groceries", "manual", 1)] * 4)
        self.assertEqual(self.row("t9")["category"], "Groceries")
        self.c.execute(update(RetailItem).values(category="Shopping", category_source="ai"))
        api_tx.api_tx_bulk(self.c, None, {"ids": ["t1"], "reviewed": True})   # no category: items stay
        self.assertEqual(self.items(), [("Shopping", "ai", 1)] * 4)

    def test_an_order_paid_twice_follows_unless_the_other_is_yours(self):
        oid = retail.order_key("amazon", ORDER)
        self.tx("t2", "2024-09-12", -10.0, "AMZN Mktp US", "Shopping", "rule")
        self.tx("t3", "2024-09-13", -10.0, "AMZN Mktp US", "Gifts & Donations", "manual")
        for cid, day, tid in (("y", "2024-09-12", "t2"), ("z", "2024-09-13", "t3")):
            store.save_charge(self.c, f"amazon|{ORDER}|{cid}", oid, day, -10.0, None)
            self.c.execute(update(RetailCharge).where(RetailCharge.id == f"amazon|{ORDER}|{cid}").values(tx_id=tid))
        retail.apply(self.c, f"amazon|{ORDER}|y")
        before_t2 = self.parts("t2")
        self.assertTrue(before_t2)   # split by the order's items
        r = api_tx.api_tx_category(self.c, None, {"category": "Groceries"}, "t1")
        self.assertEqual(self.items(), [("Groceries", "manual", 1)] * 4)
        t2, t3 = self.row("t2"), self.row("t3")
        self.assertEqual((t2["category"], t2["category_source"], t2["is_split"]), ("Groceries", "retail", 0))
        self.assertEqual((t3["category"], t3["category_source"]), ("Gifts & Donations", "manual"))
        self.assertEqual({w["id"] for w in r["was"]}, {"t1", "t2", "t3"})
        api_tx.api_tx_bulk(self.c, None, {"restore": r["was"]})
        self.assertEqual(self.parts("t2"), before_t2)
        self.assertEqual(self.row("t3")["category"], "Gifts & Donations")
        self.assertTrue(self.row("t1")["is_split"])
        # After the undo the other charge is Runway's split again: changing an item re-splits it.
        item = self.c.execute(select(RetailItem.id).where(RetailItem.order_id == oid).order_by(RetailItem.id)).fetchone()[0]
        retail.set_item_category(self.c, item, "Pharmacy", remember=False)
        self.assertIn("Pharmacy", [p[0] for p in self.parts("t2")])


class TransactionsListTests(Base):
    def test_charges_and_refunds_carry_their_order(self):
        from runway import server
        oid = retail.order_key("amazon", "111-2222222-3333333")
        self.c.execute(insert(RetailOrder).values(id=oid, retailer="amazon", order_number="111-2222222-3333333",
                                                  details=1))
        self.c.execute(insert(RetailItem).values(order_id=oid, position=0, title="Cable", quantity=1, amount=12.99))
        self.tx("buy", "2024-09-09", -12.99, "AMAZON MKTPL")
        self.tx("back", "2024-09-20", 12.99, "AMAZON REFUND")
        self.tx("other", "2024-09-10", -5.00, "COFFEE")
        store.save_charge(self.c, "c1", oid, "2024-09-09", -12.99, None)
        store.save_charge(self.c, "c2", oid, "2024-09-20", 12.99, None)
        self.c.execute(update(RetailCharge).where(RetailCharge.id == "c1").values(tx_id="buy"))
        self.c.execute(update(RetailCharge).where(RetailCharge.id == "c2").values(tx_id="back"))
        by = {t["id"]: t["retail"] for t in server.api_transactions(self.c, {}, None)["items"]}
        self.assertEqual((by["buy"]["order_id"], by["buy"]["items"], by["buy"]["retailer"]), (oid, 1, "amazon"))
        self.assertEqual(by["back"]["order_id"], oid)          # a refund shows the order it came from, too
        self.assertIsNone(by["other"])


class AppViewsTests(Base):
    """What the Orders page and Settings read: an order's details, the charges a transaction might be, the summary."""

    def setUp(self):
        super().setUp()
        self.c.execute(update(Account).where(Account.id == "card").values(display_name="My Card", owner="Sam"))
        self.amazon_order_with_charge()
        self.tx("t1", "2024-09-11", -60.88, "AMAZON MKTPL*ZX81J2", "Shopping", "rule")
        self.tx("t2", "2024-09-12", -60.88, "SHELL OIL")
        self.tx("t3", "2024-09-20", -9.99, "AMZN Mktp US")
        self.tx("t4", "2024-09-25", 4.0, "AMZN refund")
        self.oid = retail.order_key("amazon", ORDER)
        store.save_charge(self.c, "ref", self.oid, "2024-09-24", 4.0, None)
        retail.target_history(self.c, {"orders": [{"order_number": "5555", "placed_date": "2024-09-05",
                                                   "summary": {"grand_total": 12.49}}]}, "STORE")
        db.set_setting(self.c, "openrouter_api_key", "k")
        retail.finish(self.c, "amazon", caller=AI)

    def test_order_detail(self):
        o = retail.order_detail(self.c, self.oid)
        self.assertEqual(list(o), ["id", "retailer", "order_number", "channel", "placed", "total", "subtotal", "tax", "shipping",
                                   "payment", "details", "items", "charges", "url"])
        self.assertEqual(o["items"][2], {"id": 3, "title": "The Crucible: A Play in Four Acts", "quantity": 1.0, "amount": 9.98,
                                         "department": None, "category": "Entertainment", "category_source": "ai",
                                         "confidence": 0.9})
        self.assertEqual(o["charges"], [
            {"id": f"amazon|{ORDER}|x", "date": "2024-09-09", "amount": -60.88, "payment": None, "tx_id": "t1",
             "match_source": "auto", "posted": "2024-09-11", "payee": "AMAZON MKTPL*ZX81J2", "description": "AMAZON MKTPL*ZX81J2",
             "account_name": "My Card (Sam)", "applied": "split"},
            {"id": "ref", "date": "2024-09-24", "amount": 4.0, "payment": None, "tx_id": "t4", "match_source": "auto",
             "posted": "2024-09-25", "payee": "AMZN refund", "description": "AMZN refund", "account_name": "My Card (Sam)",
             "applied": None}])
        t = retail.order_detail(self.c, retail.order_key("target", "5555"))
        self.assertEqual((t["items"], t["details"], t["url"]), ([], 0, "https://www.target.com/orders"))
        self.assertEqual(t["charges"][0]["account_name"], None)   # not matched: nothing joined
        with self.assertRaises(retail.RetailError):
            retail.order_detail(self.c, "amazon:nope")

    def test_candidates(self):
        got = retail.candidates(self.c, f"amazon|{ORDER}|x")
        self.assertEqual([r["id"] for r in got], ["t1", "t2", "t3"])   # same amount first, then the store's by date
        self.assertEqual(got[0], {"id": "t1", "posted": "2024-09-11", "amount": -60.88, "payee": "AMAZON MKTPL*ZX81J2",
                                  "description": "AMAZON MKTPL*ZX81J2", "account_name": "My Card (Sam)"})
        with self.assertRaises(retail.RetailError):
            retail.candidates(self.c, "nope")

    def test_for_transactions(self):
        want = {"order_id": self.oid, "charge_id": f"amazon|{ORDER}|x", "retailer": "amazon", "order_number": ORDER,
                "channel": "online", "items": 4}
        self.assertEqual(retail.for_transactions(self.c, ["t1", "t2", "t4"]), {"t1": want, "t4": {**want, "charge_id": "ref"}})
        self.assertEqual(retail.for_transactions(self.c, []), {})

    def test_status_and_unmatched(self):
        st = retail.status(self.c)
        self.assertEqual(st["stores"]["target"], {"name": "Target", "last": None, "orders": 1, "read": 0, "matched": 0,
                                                  "unmatched": 0})
        self.assertEqual({k: v for k, v in st["stores"]["amazon"].items() if k != "last"},
                         {"name": "Amazon", "orders": 1, "read": 1, "matched": 2, "unmatched": 0})
        self.assertEqual(st["recent"], [
            {"id": self.oid, "retailer": "amazon", "order_number": ORDER, "channel": "online", "placed": "2024-09-08",
             "total": 60.88, "details": 1, "items": 4, "charges": 1, "matched": 1},
            {"id": "target:5555", "retailer": "target", "order_number": "5555", "channel": "store", "placed": "2024-09-05",
             "total": 12.49, "details": 0, "items": 0, "charges": 1, "matched": 0}])
        recent = (date.today() - timedelta(days=20)).isoformat()
        store.save_charge(self.c, "target|7777", store.save_order(self.c, "target", "7777"), recent, -5.0, None)
        self.assertEqual([retail.unmatched_count(self.c), retail.unmatched_count(self.c, "target"),
                          retail.unmatched_count(self.c, "amazon")], [1, 1, 0])
        self.assertEqual(retail.status(self.c)["recent"][0]["id"], "target:7777")   # no date yet: first


class TokenTests(Base):
    def test_the_key_is_the_persons_and_ends(self):
        token = retail.new_token(self.c, {"sub": "u1", "email": "a@example.com", "name": "A"})
        oidc.remember_user(self.c, "u1", "a@example.com", "A")
        self.assertIsNone(retail.token_check(self.c, f"Bearer {token}"))
        self.assertEqual(retail.token_check(self.c, "Bearer rwx_other"), "unknown")
        self.assertEqual(retail.token_check(self.c, None), "unknown")
        status = retail.status(self.c)
        self.assertIsNotNone(status["token_used"])                                  # noted on a working call
        self.assertEqual(status["token_expires"][:10], (date.today() + timedelta(days=retail.TOKEN_DAYS)).isoformat())
        self.assertIsNone(status["token_problem"])
        # taken off the sign-in list, the key they made stops working (like their sessions)
        with mock.patch.dict(os.environ, {"OIDC_ISSUER": "https://id.example.com", "OIDC_ALLOWED_EMAILS": "b@example.com"}):
            self.assertEqual(retail.token_check(self.c, f"Bearer {token}"), "owner_gone")
            self.assertEqual(retail.status(self.c)["token_problem"], "owner_gone")
        with mock.patch.dict(os.environ, {"OIDC_ISSUER": "https://id.example.com", "OIDC_ALLOWED_EMAILS": "a@example.com"}):
            self.assertIsNone(retail.token_check(self.c, f"Bearer {token}"))
        # and it expires
        db.set_setting(self.c, "retail_token_created", (datetime.now() - timedelta(days=retail.TOKEN_DAYS)).isoformat(timespec="seconds"))
        self.assertEqual(retail.token_check(self.c, f"Bearer {token}"), "expired")
        for reason in ("expired", "owner_gone", "unknown"):
            self.assertIn("Settings", retail.REFUSALS[reason])
        retail.remove_token(self.c)
        self.assertEqual(retail.token_check(self.c, f"Bearer {token}"), "unknown")
        self.assertEqual(retail.status(self.c)["token_expires"], None)

    def test_a_key_made_without_sign_in_only_expires(self):
        token = retail.new_token(self.c, {"name": None, "email": None, "local": True})
        self.assertIsNone(db.get_setting(self.c, "retail_token_owner"))
        with mock.patch.dict(os.environ, {"OIDC_ISSUER": "https://id.example.com", "OIDC_ALLOWED_EMAILS": "b@example.com"}):
            self.assertIsNone(retail.token_check(self.c, f"Bearer {token}"))


class TokenDetailTests(Base):
    """The extension's key exactly as it's made, kept and checked, so a change to any of it is on purpose."""

    def test_made_and_kept(self):
        token = retail.new_token(self.c, {"sub": "u1", "email": "a@example.com", "name": "A"})
        self.assertRegex(token, r"^rwx_[A-Za-z0-9_-]{43}$")
        self.assertEqual(db.get_setting(self.c, "retail_token_hash"), hashlib.sha256(token.encode()).hexdigest())
        self.assertEqual(db.get_setting(self.c, "retail_token_owner"), '{"sub": "u1", "email": "a@example.com"}')
        made = db.get_setting(self.c, "retail_token_created")
        self.assertRegex(made, r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d$")   # local time, to the second, no zone
        self.assertLess(abs((datetime.fromisoformat(made) - datetime.now()).total_seconds()), 5)
        self.assertIsNone(db.get_setting(self.c, "retail_token_used"))
        self.assertEqual(retail.token_expires(self.c), (datetime.fromisoformat(made) + timedelta(days=90)).isoformat())
        self.assertEqual(retail.TOKEN_DAYS, 90)
        # a new key replaces the old one, and starts unused
        self.assertIsNone(retail.token_check(self.c, f"Bearer {token}"))
        again = retail.new_token(self.c, {"sub": "u2"})
        self.assertEqual(db.get_setting(self.c, "retail_token_owner"), '{"sub": "u2", "email": null}')
        self.assertIsNone(db.get_setting(self.c, "retail_token_used"))
        self.assertEqual(retail.token_check(self.c, f"Bearer {token}"), "unknown")
        self.assertIsNone(retail.token_check(self.c, f"Bearer {again}"))
        retail.new_token(self.c, None)
        self.assertIsNone(db.get_setting(self.c, "retail_token_owner"))

    def test_the_header(self):
        self.assertEqual(retail.token_check(self.c, "Bearer rwx_x"), "unknown")   # no key at all
        token = retail.new_token(self.c)
        with mock.patch("hmac.compare_digest", wraps=hmac.compare_digest) as compare:
            self.assertIsNone(retail.token_check(self.c, f"Bearer {token}"))
        compare.assert_called_once_with(hashlib.sha256(token.encode()).hexdigest(),
                                        db.get_setting(self.c, "retail_token_hash"))   # in constant time
        self.assertIsNone(retail.token_check(self.c, f"  Bearer   {token}  "))
        self.assertIsNone(retail.token_check(self.c, f"Bearer\t{token}"))
        for refused in (f"bearer {token}", f"Basic {token}", f"Bearer {token} x", f"Bearer{token}", token, "",
                        f"Bearer {token[:-1]}", f"Bearer {token}x", f"Bearer {hashlib.sha256(token.encode()).hexdigest()}"):
            self.assertEqual(retail.token_check(self.c, refused), "unknown", refused)

    def test_last_use_is_noted_once_a_minute(self):
        token = retail.new_token(self.c)
        recent = (datetime.now() - timedelta(seconds=30)).isoformat(timespec="seconds")
        db.set_setting(self.c, "retail_token_used", recent)
        retail.token_check(self.c, f"Bearer {token}")
        self.assertEqual(db.get_setting(self.c, "retail_token_used"), recent)
        db.set_setting(self.c, "retail_token_used", (datetime.now() - timedelta(seconds=61)).isoformat(timespec="seconds"))
        retail.token_check(self.c, f"Bearer {token}")
        used = db.get_setting(self.c, "retail_token_used")
        self.assertRegex(used, r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d$")
        self.assertLess(abs((datetime.fromisoformat(used) - datetime.now()).total_seconds()), 5)
        db.set_setting(self.c, "retail_token_used", "not a date")   # unreadable: noted again
        retail.token_check(self.c, f"Bearer {token}")
        self.assertNotEqual(db.get_setting(self.c, "retail_token_used"), "not a date")
        retail.token_check(self.c, "Bearer rwx_other")   # a refused call isn't a use
        db.set_setting(self.c, "retail_token_used", None)
        retail.token_check(self.c, "Bearer rwx_other")
        self.assertIsNone(db.get_setting(self.c, "retail_token_used"))

    def test_when_it_ends(self):
        retail.new_token(self.c, {"sub": "u1", "email": "a@example.com"})
        db.set_setting(self.c, "retail_token_created", "2026-01-01T10:00:00")
        self.assertEqual(retail.token_expires(self.c), "2026-04-01T10:00:00")
        self.assertIsNone(retail.token_problem(self.c, datetime(2026, 4, 1, 9, 59, 59)))
        self.assertEqual(retail.token_problem(self.c, datetime(2026, 4, 1, 10, 0, 0)), "expired")
        with mock.patch.object(oidc, "access_lapsed", return_value=True) as lapsed:
            self.assertEqual(retail.token_problem(self.c, datetime(2026, 4, 2)), "expired")   # expired first
            self.assertEqual(retail.token_problem(self.c, datetime(2026, 1, 2)), "owner_gone")
        lapsed.assert_called_once_with(self.c, "u1", "a@example.com")
        for owner in ("not json", "[]", '"u1"', "null"):   # an owner that can't be read: the key only expires
            db.set_setting(self.c, "retail_token_owner", owner)
            with mock.patch.object(oidc, "access_lapsed", return_value=True) as lapsed:
                self.assertIsNone(retail.token_problem(self.c, datetime(2026, 1, 2)), owner)
            lapsed.assert_not_called()
        db.set_setting(self.c, "retail_token_created", "garbled")   # a date it can't read: no expiry
        self.assertIsNone(retail.token_expires(self.c))
        self.assertIsNone(retail.token_problem(self.c, datetime(2099, 1, 1)))

    def test_removed(self):
        retail.new_token(self.c, {"sub": "u1"})
        retail.token_check(self.c, "Bearer x")
        db.set_setting(self.c, "retail_token_used", "2026-01-01T10:00:00")
        retail.remove_token(self.c)
        for key in ("retail_token_hash", "retail_token_created", "retail_token_owner", "retail_token_used"):
            self.assertIsNone(db.get_setting(self.c, key), key)
        st = retail.status(self.c)
        self.assertEqual((st["token"], st["token_created"], st["token_used"], st["token_expires"], st["token_problem"]),
                         (False, None, None, None, None))
        db.set_setting(self.c, "retail_token_created", "2020-01-01T00:00:00")   # without a key there's no problem to show
        self.assertIsNone(retail.status(self.c)["token_problem"])


def old_allocate(amount: float, items: list[dict], fallback: str | None, order_total: float | None = None) -> list[dict]:
    """retail.allocate as it was before money.allocate_cents."""
    items = [i for i in items if (i.get("amount") or 0) > 0]
    if not items:
        return []
    items = retail_split._shipment(items, amount, order_total)
    by_cat: dict[str, dict] = {}
    for it in items:
        cat = it.get("category") or fallback
        if not cat:
            return []
        g = by_cat.setdefault(cat, {"weight": 0.0, "titles": []})
        g["weight"] += it["amount"]
        g["titles"].append(it.get("title") or "")
    total_w = sum(g["weight"] for g in by_cat.values())
    sign, cents = (-1 if amount < 0 else 1), abs(round(amount * 100))
    parts = []
    for cat, g in sorted(by_cat.items(), key=lambda kv: -kv[1]["weight"]):
        share = cents * g["weight"] / total_w
        parts.append({"category": cat, "cents": int(share), "rest": share - int(share), "titles": g["titles"]})
    for p in sorted(parts, key=lambda p: -p["rest"])[:cents - sum(p["cents"] for p in parts)]:
        p["cents"] += 1
    out = []
    for p in parts:
        if p["cents"] == 0:
            continue
        note = "; ".join(t for t in p["titles"] if t)
        out.append({"category": p["category"], "amount": sign * p["cents"] / 100,
                    "note": (note[:197] + "…") if len(note) > 200 else note})
    return out


class AllocateTests(unittest.TestCase):
    def test_as_it_worked_them_out(self):
        rng = random.Random(20261004)
        for _ in range(4000):
            n = rng.randint(1, 9)
            items = [{"amount": rng.choice([round(rng.uniform(0.01, 300), 2), 1, 1, 0.5, 3.333, 19.99, 0, -2]),
                      "category": rng.choice(["A", "B", "C", "D", None]), "title": rng.choice(["", "x", "y" * 150])}
                     for _ in range(n)]
            amount = rng.choice([round(rng.uniform(-900, 900), 2), -10.0, -0.01, -0.03, 0.07, -100.0, -1.005, 2.675])
            total = rng.choice([None, None, round(sum(max(i["amount"], 0) for i in items) * rng.uniform(1, 1.2), 2)])
            fallback = rng.choice([None, "Shopping"])
            self.assertEqual(retail.allocate(amount, items, fallback, total), old_allocate(amount, items, fallback, total),
                             (amount, items, fallback, total))

    def test_adds_up_exactly(self):
        items = [{"amount": 1, "category": "A"}, {"amount": 1, "category": "B"}, {"amount": 1, "category": "C"}]
        parts = retail.allocate(-10.00, items, None)
        self.assertEqual(round(sum(p["amount"] for p in parts), 2), -10.00)
        self.assertEqual(sorted(p["amount"] for p in parts), [-3.34, -3.33, -3.33])

    def test_uncategorized_items_use_the_fallback(self):
        parts = retail.allocate(-20, [{"amount": 5, "category": "A"}, {"amount": 15, "category": None}], "Shopping")
        self.assertEqual([(p["category"], p["amount"]) for p in parts], [("Shopping", -15.0), ("A", -5.0)])

    def test_a_shipment_is_its_own_items(self):
        items = [{"amount": 10, "category": "A", "title": "a"}, {"amount": 25, "category": "B", "title": "b"},
                 {"amount": 40, "category": "C", "title": "c"}]
        # The order came to $82.50 (10% tax); the $44.00 charge is the $40 item's shipment.
        self.assertEqual([(p["category"], p["amount"]) for p in retail.allocate(-44.0, items, None, 82.5)], [("C", -44.0)])
        # A charge no set of items explains is spread over all of them.
        self.assertEqual(len(retail.allocate(-50.0, items, None, 82.5)), 3)


class TargetTests(Base):
    HISTORY = {
        "orders": [
            {"order_number": "912001234567", "placed_date": "2024-09-02T15:04:05Z", "order_purchase_type": "ONLINE",
             "summary": {"grand_total": "31.80"},
             "order_lines": [
                 {"item": {"description": "Good & Gather Whole Milk 1gal", "product_classification": {"product_type_name": "GROCERY"}},
                  "quantity": 2, "unit_price": "3.99"},
                 {"item": {"description": "Threshold Bath Towel"}, "quantity": 1, "unit_price": 21.99},
             ]},
            {"order_number": "5555-0123-4567-8901", "placed_date": "2024-09-05", "order_purchase_type": "STORE",
             "summary": {"grand_total": 12.49}},
            {"order_number": "912000000001", "placed_date": "2023-01-01", "summary": {"grand_total": 9.99}},   # too old
        ],
        "total_pages": 3,
    }

    def test_history_and_store_receipt(self):
        r = retail.target_history(self.c, self.HISTORY, "ONLINE")
        self.assertFalse(r["more"])                                          # reached back past the start date
        self.assertEqual(r["orders"], ["5555-0123-4567-8901"])              # the receipt's items are still to come
        online = self.c.execute(select(RetailOrder).where(RetailOrder.order_number == "912001234567")).fetchone()
        self.assertEqual((online["channel"], online["placed"], online["total"], online["details"]), ("online", "2024-09-02", 31.8, 1))
        items = self.c.execute(select(RetailItem.title, RetailItem.quantity, RetailItem.amount, RetailItem.department)
                               .order_by(RetailItem.position)).fetchall()
        self.assertEqual([tuple(i) for i in items], [("Good & Gather Whole Milk 1gal", 2.0, 7.98, "GROCERY"),
                                                     ("Threshold Bath Towel", 1.0, 21.99, None)])
        receipt = {"order_number": "5555-0123-4567-8901", "order_lines": [
            {"item": {"description": "Tide Pods 42ct"}, "original_quantity": 1, "total_price": {"amount": 12.49}}]}
        self.assertEqual(retail.target_order(self.c, "5555-0123-4567-8901", receipt), {"read": True})
        store = self.c.execute(select(RetailOrder.channel, RetailOrder.details)
                               .where(RetailOrder.order_number == "5555-0123-4567-8901")).fetchone()
        self.assertEqual(tuple(store), ("store", 1))

    # Trimmed from a real /post_orders/v1/{order} reply: one line picked up, one package cancelled (out of stock).
    POST_ORDER = {
        "order_number": "102000000000001", "order_date": "2024-09-08T20:13:34.000Z",
        "payments": [{"amount": 4.65, "card_number": "0000", "payment_type": "TARGETDEBIT"}],
        "summary": {"total_items": 2, "grand_total": 4.65, "total_product_price": 4.89, "total_taxes": 0.00},
        "packages": [
            {"grouping_metadata": {"status": "STAT_ORDER_PICKED_UP", "cancellation": {}},
             "order_lines": [{"original_quantity": 1.0, "quantity": 1.0, "line_type": "GROCERY", "cancellation": {},
                              "item": {"tcin": "81642044", "description":
                                       "Garlic Parsley Mini Creamer Potatoes - 16oz - Good &#38; Gather&#8482;",
                                       "original_unit_price": 4.89, "unit_price": 4.89, "list_price": 4.89,
                                       "product_classification": {"product_type_name": "GROCERY"}}}]},
            {"grouping_metadata": {"status": "STAT_CANCELED", "cancellation": {"cancel_reason_code": "X"}},
             "order_lines": [{"original_quantity": 1.0, "quantity": 1.0,
                              "cancellation": {"cancel_reason_code_description": "Out of stock at selected store"},
                              "item": {"description": "Fresh Broccoli Florets - 12oz", "unit_price": 2.59}}]},
        ],
    }

    def test_online_order_details_leave_out_cancelled_lines(self):
        n = self.POST_ORDER["order_number"]
        retail.target_history(self.c, {"orders": [{"order_number": n, "placed_date": "2024-09-08",
                                                   "summary": {"grand_total": 4.65}}]}, "ONLINE")
        self.assertEqual(store.need_details(self.c, "target", [n]), [n])
        self.assertEqual(retail.target_order(self.c, n, self.POST_ORDER), {"read": True})
        items = self.c.execute(select(RetailItem.title, RetailItem.quantity, RetailItem.amount, RetailItem.department)
                               .join(RetailOrder, RetailOrder.id == RetailItem.order_id)
                               .where(RetailOrder.order_number == n)).fetchall()
        self.assertEqual([tuple(i) for i in items],
                         [("Garlic Parsley Mini Creamer Potatoes - 16oz - Good & Gather\u2122", 1.0, 4.89, "GROCERY")])
        order = self.c.execute(select(RetailOrder.total, RetailOrder.channel)
                               .where(RetailOrder.order_number == n)).fetchone()
        self.assertEqual(tuple(order), (4.65, "online"))

    def test_reads_back_to_orders_whose_items_are_still_to_come(self):
        self.since.stop()
        try:
            db.set_setting(self.c, "retail_last_target", (datetime.now() - timedelta(days=1)).isoformat())
            usual = retail.since(self.c, "target")
            old = (date.fromisoformat(usual) - timedelta(days=60)).isoformat()
            store.save_order(self.c, "target", "912000000009", placed=old, total=5.0)   # listed, items never read
            self.assertEqual(retail.since(self.c, "target"), old)
            self.c.execute(update(RetailOrder)
                           .where(RetailOrder.order_number == "912000000009").values(attempts=retail.MAX_ATTEMPTS))
            self.assertEqual(retail.since(self.c, "target"), usual)                        # given up on: not again
        finally:
            self.since.start()

    def test_target_order_with_nowhere_to_read_it(self):
        retail.target_history(self.c, self.HISTORY, "ONLINE")
        n = "5555-0123-4567-8901"
        for _ in range(retail.MAX_ATTEMPTS):
            self.assertEqual(store.need_details(self.c, "target", [n]), [n])
            self.assertEqual(retail.target_order(self.c, n, {}), {"read": False})
        self.assertEqual(store.need_details(self.c, "target", [n]), [])           # given up on
        row = self.c.execute(select(RetailOrder.total, RetailOrder.raw).where(RetailOrder.order_number == n)).fetchone()
        self.assertEqual(row["total"], 12.49)                                # what the history said is kept
        self.assertIn("STORE", row["raw"])

    def test_target_order_counts_only_its_last_try_and_reads_the_order_not_a_package(self):
        n = self.POST_ORDER["order_number"]
        retail.target_history(self.c, {"orders": [{"order_number": n, "placed_date": "2024-09-08",
                                                   "summary": {"grand_total": 4.65}}]}, "ONLINE")
        for _ in range(retail.MAX_ATTEMPTS + 1):
            self.assertEqual(retail.target_order(self.c, n, {"nothing": 1}, final=False), {"read": False})
        self.assertEqual(store.need_details(self.c, "target", [n]), [n])
        # Packages that name the order too: the reply itself is the order.
        reply = json.loads(json.dumps(self.POST_ORDER))
        for p in reply["packages"]:
            p["order_number"] = n
        self.assertEqual(retail.target_order(self.c, n, reply), {"read": True})
        self.assertEqual(self.c.execute(select(RetailOrder.total)
                                        .where(RetailOrder.order_number == n)).fetchone()[0], 4.65)

    def test_an_import_that_stopped_early_reads_the_same_stretch_next_time(self):
        retail.finish(self.c, "target", complete=False)
        self.assertIsNone(db.get_setting(self.c, "retail_last_target"))
        retail.finish(self.c, "target")
        self.assertIsNotNone(db.get_setting(self.c, "retail_last_target"))

    def test_split_with_departments_when_theres_no_ai(self):
        retail.target_history(self.c, self.HISTORY, "ONLINE")
        self.tx("t1", "2024-09-04", -31.80, "TARGET 00012345 MINNEAPOLIS MN", "Shopping", "history")
        self.tx("t2", "2024-09-06", -12.49, "TARGET T-1234", "Shopping", "history")
        out = retail.finish(self.c, "target")
        self.assertEqual(out["items"]["department"], 1)
        self.assertEqual(self.parts("t1"), [("Shopping", -23.33), ("Groceries", -8.47)])
        key = retail.order_key("target", "5555-0123-4567-8901")
        self.assertEqual(self.c.execute(select(RetailCharge.tx_id).where(RetailCharge.order_id == key)).fetchone()["tx_id"], "t2")
        self.assertEqual(self.row("t2")["is_split"], 0)                     # no items yet: left as it was


class CostcoTests(Base):
    RECEIPT = {"transactionBarcode": "21111500800662509301234", "transactionDate": "2025-09-30", "total": 41.98,
               "subTotal": 40.0, "taxes": 1.98, "transactionType": "Sales", "documentType": "WarehouseReceipts",
               "tenderArray": [{"tenderDescription": "VISA"}],
               "itemArray": [
                   {"itemNumber": "111", "itemDescription01": "KS PAPER TOWEL", "unit": 1, "amount": 24.0},
                   {"itemNumber": "222", "itemDescription01": "ROTISSERIE CHICKEN", "unit": 1, "amount": 6.0},
                   {"itemNumber": "333", "itemDescription01": "  /111", "unit": -1, "amount": -4.0},
                   {"itemNumber": "444", "itemDescription01": "BANANAS", "unit": 1, "amount": 14.0}]}

    def test_receipt_items_take_their_instant_savings_off(self):
        r = retail.costco_history(self.c, {"data": {"receiptsWithCounts": {"receipts": [self.RECEIPT]}}})
        self.assertEqual((r["read"], r["saved"]), (1, 1))
        rows = {x["title"]: x["amount"] for x in self.c.execute(select(RetailItem.title, RetailItem.amount))}
        self.assertEqual(rows, {"KS PAPER TOWEL": 20.0, "ROTISSERIE CHICKEN": 6.0, "BANANAS": 14.0})
        order = self.c.execute(select(RetailOrder)).fetchone()
        self.assertEqual((order["retailer"], order["channel"], order["total"], order["details"]), ("costco", "store", 41.98, 1))

    def test_costco_items_are_categorized_by_the_ai_with_the_receipt_codes_taken_off(self):
        receipt = {**self.RECEIPT, "itemArray": [
            {"itemNumber": "1", "itemDescription01": "CLOROX WAND P=120", "unit": 1, "amount": 17.78},
            {"itemNumber": "2", "itemDescription01": "WHITE QUESO 32OZ", "itemDescription02": "T6H7P504 SL60 DOM120", "unit": 1, "amount": 8.89},
            {"itemNumber": "3", "itemDescription01": "BURATTA #00123 SL24 T9H8", "unit": 1, "amount": 7.40},
            {"itemNumber": "4", "itemDescription01": "CREST PRO 5PK/5.9OZ P324 CU38", "unit": 1, "amount": 11.85}]}
        retail.costco_history(self.c, {"data": {"receiptsWithCounts": {"receipts": [receipt]}}})
        db.set_setting(self.c, "openrouter_api_key", "k")
        asked = []

        def model(key, name, prompt):
            asked.append(prompt)
            items = json.loads(prompt.split("Items (JSON):\n", 1)[1].split("\n", 1)[0])
            return json.dumps([{"i": it["i"], "category": "Groceries", "confidence": 0.8} for it in items])
        out = retail.finish(self.c, "costco", caller=model)
        self.assertEqual(out["items"]["ai"], 4)
        self.assertEqual({r["category_source"] for r in self.c.execute(select(RetailItem.category_source))}, {"ai"})
        self.assertIn("KS is Kirkland Signature", asked[0])
        sent = [it["item"] for it in json.loads(asked[0].split("Items (JSON):\n", 1)[1].split("\n", 1)[0])]
        self.assertEqual(sent, ["CLOROX WAND", "WHITE QUESO 32OZ", "BURATTA", "CREST PRO 5PK/5.9OZ"])
        # what's stored is the receipt's own wording
        self.assertIn("CLOROX WAND P=120", [r["title"] for r in self.c.execute(select(RetailItem.title))])

    def test_the_ai_suggests_categories_for_an_order_and_a_new_one_when_nothing_fits(self):
        from runway import server
        retail.costco_history(self.c, {"data": {"receiptsWithCounts": {"receipts": [self.RECEIPT]}}})
        oid = self.c.execute(select(RetailOrder.id)).fetchone()[0]
        with self.assertRaisesRegex(retail.RetailError, "OpenRouter key"):
            retail.suggest_for_order(self.c, oid)
        db.set_setting(self.c, "openrouter_api_key", "k")
        asked = []

        def model(key, name, prompt):
            asked.append(prompt)
            items = json.loads(prompt.split("Items (JSON):\n", 1)[1].split("\n", 1)[0])
            return json.dumps([{"i": it["i"], "category": "Groceries", "confidence": 0.9} if "chicken" in it["item"].lower() or "banana" in it["item"].lower()
                               else {"i": it["i"], "category": None, "new_category": "Paper Goods", "parent": "Groceries", "confidence": 0.7}
                               for it in items])
        out = retail.suggest_for_order(self.c, oid, caller=model)
        self.assertIn("propose a new one", asked[0])
        by = {self.c.execute(select(RetailItem.title)
                             .where(RetailItem.id == r["item_id"])).fetchone()[0]: r for r in out}
        self.assertEqual(by["BANANAS"]["category"], "Groceries")
        self.assertEqual(by["KS PAPER TOWEL"]["new_category"], {"name": "Paper Goods", "parent": "Groceries"})
        self.assertEqual(self.c.execute(select(func.count())
                                        .select_from(RetailItem)
                                        .where(RetailItem.category.is_not(None))).fetchone()[0], 0)   # nothing saved
        # accepting the new one creates it and uses it for the item
        item = by["KS PAPER TOWEL"]["item_id"]
        got = server.api_retail_item(self.c, {}, {"new_category": by["KS PAPER TOWEL"]["new_category"]}, str(item))
        self.assertEqual((got["category"], got["created"]), ("Paper Goods", True))
        self.assertEqual(self.c.execute(select(Category.parent)
                                        .where(Category.name == "Paper Goods")).fetchone()["parent"], "Groceries")
        self.assertEqual(self.c.execute(select(RetailItem.category, RetailItem.category_source)
                                        .where(RetailItem.id == item)).fetchone()[:], ("Paper Goods", "manual"))
        again = server.api_retail_item(self.c, {}, {"new_category": {"name": "paper goods"}}, str(by["ROTISSERIE CHICKEN"]["item_id"]))
        self.assertEqual((again["category"], again["created"]), ("Paper Goods", False))   # it exists now: used, not made twice
        # only items without a category are asked about
        self.assertEqual(len(retail.suggest_for_order(self.c, oid, caller=model)), 1)
        # a failed request is an error, not silence
        def broken(*_a):
            raise RuntimeError("down")
        with self.assertRaisesRegex(retail.RetailError, "AI request failed"):
            retail.suggest_for_order(self.c, oid, caller=broken)

    def test_only_costco_titles_are_cleaned(self):
        self.assertEqual(retail.ai_title("amazon", "Widget P=120 #12"), "Widget P=120 #12")
        self.assertEqual(retail.ai_title("costco", "P=120"), "P=120")   # never left empty

    def test_an_error_reply_asks_to_sign_in(self):
        with self.assertRaises(retail.RetailError) as cm:
            retail.costco_history(self.c, {"errors": [{"message": "Unauthorized"}]})
        self.assertEqual(cm.exception.code, "signin")

    def test_statement_text(self):
        self.assertTrue(retail.MERCHANT["costco"].search("COSTCO WHSE #0123"))
        self.assertFalse(retail.MERCHANT["costco"].search("COSTCO ANYWHERE VISA PAYMENT"))


class ExtensionApiTests(unittest.TestCase):
    """The extension's calls carry its key instead of a sign-in; everything else still needs the app's header."""

    @classmethod
    def setUpClass(cls):
        from runway import server
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["RUNWAY_DATA"] = cls.tmp.name
        db.init()
        cls.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()
        os.environ.pop("RUNWAY_DATA", None)

    def req(self, method, path, body=None, headers=None):
        h = {"Content-Type": "application/json", **(headers or {})}
        r = urllib.request.Request(self.base + path, method=method, headers=h,
                                   data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_key(self):
        self.assertEqual(self.req("POST", "/api/retail/token")[0], 403)              # the app's own calls need its header
        code, r = self.req("POST", "/api/retail/token", headers={"X-Runway": "1"})
        self.assertEqual(code, 200)
        key = r["token"]
        ext = {"Authorization": f"Bearer {key}", "Origin": "chrome-extension://abcdef"}
        self.assertEqual(self.req("POST", "/api/ext/ping", {}, {"Origin": "chrome-extension://abcdef"})[0], 401)
        self.assertEqual(self.req("POST", "/api/ext/ping", {}, {"Authorization": "Bearer nope"})[0], 401)
        self.assertEqual(self.req("POST", "/api/ext/ping", {}, ext), (200, {"ok": True}))
        code, r = self.req("POST", "/api/ext/start", {"retailer": "target"}, ext)
        self.assertEqual(code, 200)
        self.assertIn("store", r["detail_urls"])
        self.assertEqual(set(r["order_pages"]), {"store", "online"})   # where to find an order's items otherwise
        self.assertEqual(self.req("POST", "/api/ext/start", {"retailer": "walmart"}, ext)[0], 400)
        code, r = self.req("POST", "/api/ext/target/history", {"purchase_type": "STORE", "data": {"orders": []}}, ext)
        self.assertEqual((code, r["more"]), (200, False))
        self.assertEqual(self.req("POST", "/api/ext/finish", {"retailer": "target"}, ext)[0], 200)
        # What's still unmatched now, for the extension's popup (matches you make in Runway count straight away).
        self.assertEqual(self.req("POST", "/api/ext/status", {}, ext), (200, {"unmatched": {"amazon": 0, "target": 0, "costco": 0}}))
        # The key only opens the extension's calls.
        self.assertEqual(self.req("POST", "/api/retail/token", headers=ext)[0], 403)
        self.req("POST", "/api/retail/token/remove", headers={"X-Runway": "1"})
        self.assertEqual(self.req("POST", "/api/ext/ping", {}, ext)[0], 401)
        with urllib.request.urlopen(self.base + "/api/retail/extension.zip") as resp:   # to install it from Settings
            import io
            import re
            import zipfile
            z = zipfile.ZipFile(io.BytesIO(resp.read()))
        names = z.namelist()
        self.assertIn("runway-orders/manifest.json", names)
        # Every script the extension loads ships too: background.js is split into several files, which Chrome's worker
        # imports and Firefox loads from the manifest.
        background = json.loads(z.read("runway-orders/manifest.json"))["background"]
        imports = re.search(r"importScripts\(([^)]*)\)", z.read("runway-orders/background.js").decode())
        scripts = {background["service_worker"], *background["scripts"], *re.findall(r'"([^"]+\.js)"', imports.group(1))}
        self.assertGreater(len(scripts), 9)
        for script in scripts:
            self.assertIn(f"runway-orders/{script}", names)
        code, st = self.req("GET", "/api/retail")
        self.assertEqual((code, st["token"], st["stores"]["target"]["orders"]), (200, False, 0))

    def test_store_pages_that_stop_an_import(self):
        _, r = self.req("POST", "/api/retail/token", headers={"X-Runway": "1"})
        ext = {"Authorization": f"Bearer {r['token']}"}
        robot = '<html><title>Robot Check</title><form action="/errors/validateCaptcha"></form></html>'
        code, r = self.req("POST", "/api/ext/amazon/order", {"order_number": ORDER, "html": robot}, ext)
        self.assertEqual((code, r["code"]), (400, "robot"))    # the extension stops, and the order keeps its tries
        code, r = self.req("POST", "/api/ext/amazon/order", {"order_number": ORDER, "html": "<html></html>", "final": False}, ext)
        self.assertEqual((code, r), (200, {"read": False}))
        with db.session() as conn:
            self.assertEqual(conn.execute(select(RetailOrder.attempts)
                                          .where(RetailOrder.id == retail.order_key("amazon", ORDER))).fetchone()[0] or 0, 0)
        # Stopped early: the last import's date stays put. Categorizing carries on after the answer.
        with mock.patch.object(retail, "categorize_and_apply") as later:
            code, r = self.req("POST", "/api/ext/finish", {"retailer": "amazon", "complete": False}, ext)
            self.assertEqual(code, 200)
            for _ in range(50):
                if later.called:
                    break
                threading.Event().wait(0.05)
            self.assertEqual(later.call_args[0][1], "amazon")
        with db.session() as conn:
            self.assertIsNone(db.get_setting(conn, "retail_last_amazon"))
        self.req("POST", "/api/retail/token/remove", headers={"X-Runway": "1"})


if __name__ == "__main__":
    unittest.main()
