"""Amazon and Target orders: reading what the browser extension sends, matching charges to transactions, and splitting."""
import json
from datetime import UTC, date, datetime, timedelta
from unittest import mock

from sqlalchemy import delete, func, insert, select, update

from runway import db, retail, splits
from runway.retail import store
from runway.models import Account, AiLog, RetailCharge, RetailItem, RetailItemMemory, RetailOrder, Transaction
from runway.server.api import retail as api_retail
from runway.server.api import transactions as api_tx
from tests.shared import freeze_today
from tests.retail_support import AI, ORDER, Base, fake_ai, fixture


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
        recent = (freeze_today(self) - timedelta(days=20)).isoformat()
        store.save_charge(self.c, "target|7777", store.save_order(self.c, "target", "7777"), recent, -5.0, None)
        self.assertEqual([retail.unmatched_count(self.c), retail.unmatched_count(self.c, "target"),
                          retail.unmatched_count(self.c, "amazon")], [1, 1, 0])
        self.assertEqual(retail.status(self.c)["recent"][0]["id"], "target:7777")   # no date yet: first
