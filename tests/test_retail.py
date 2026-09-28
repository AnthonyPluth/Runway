"""Amazon and Target orders: reading what the browser extension sends, matching charges to transactions, and splitting."""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runway import db, retail, splits  # noqa: E402

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


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "r.db")
        db.init(self.path)
        self.c = db.connect(self.path)
        self.c.execute("INSERT INTO accounts(id, name, kind, balance) VALUES ('card', 'Card', 'credit', 0)")
        self.since = mock.patch.object(retail, "since", return_value="2024-01-01")
        self.since.start()

    def tearDown(self):
        self.since.stop()
        self.c.close()
        self.tmp.cleanup()

    def tx(self, tid, posted, amount, desc, category=None, source=None):
        self.c.execute("INSERT INTO transactions(id, account_id, posted, amount, description, payee, category, category_source) "
                       "VALUES (?,?,?,?,?,?,?,?)", (tid, "card", posted, amount, desc, desc, category, source))

    def row(self, tid):
        return self.c.execute("SELECT * FROM transactions WHERE id=?", (tid,)).fetchone()

    def parts(self, tid):
        return [(p["category"], p["amount"]) for p in splits.get(self.c, tid)]

    def amazon_order_with_charge(self, amount=-60.88, day="2024-09-09"):
        retail.amazon_order(self.c, ORDER, fixture(f"order-details-{ORDER}.html"))
        retail._save_charge(self.c, f"amazon|{ORDER}|x", retail.order_key("amazon", ORDER), day, amount, None)


class AmazonPagesTests(Base):
    def test_transactions_page(self):
        r = retail.amazon_transactions(self.c, fixture("transactions-page.html"))
        self.assertEqual(r["orders"], ["123-4567890-1234567"])            # its items are still to be read
        self.assertIn("ppw-widgetState", r["next_form"])                   # and there's another page
        charges = self.c.execute("SELECT date, amount, payment FROM retail_charges ORDER BY date").fetchall()
        self.assertEqual([tuple(c) for c in charges],
                         [("2024-10-09", -28.79, "Mastercard ****1234"), ("2024-10-11", -45.19, "Visa ****1234")])
        retail.amazon_transactions(self.c, fixture("transactions-page.html"))   # the same page again changes nothing
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM retail_charges").fetchone()[0], 2)

    def test_stops_paging_at_the_start_date(self):
        with mock.patch.object(retail, "since", return_value="2024-10-10"):
            r = retail.amazon_transactions(self.c, fixture("transactions-page.html"))
        self.assertIsNone(r["next_form"])
        self.assertEqual([tuple(c) for c in self.c.execute("SELECT date FROM retail_charges")], [("2024-10-11",)])

    def test_signed_out(self):
        with self.assertRaises(retail.RetailError):
            retail.amazon_transactions(self.c, '<html><form name="signIn" action="/ap/signin"></form></html>')

    def test_order_details(self):
        self.assertEqual(retail.amazon_order(self.c, ORDER, fixture(f"order-details-{ORDER}.html")), {"read": True, "items": 4})
        o = self.c.execute("SELECT * FROM retail_orders").fetchone()
        self.assertEqual((o["placed"], o["total"], o["subtotal"], o["tax"], o["payment"], o["details"]),
                         ("2024-09-08", 60.88, 57.69, 3.19, "Prime Visa 1111", 1))
        items = self.c.execute("SELECT title, amount FROM retail_items ORDER BY position").fetchall()
        self.assertEqual([i["amount"] for i in items], [7.49, 18.95, 9.98, 21.27])
        self.assertTrue(items[2]["title"].startswith("The Crucible"))

    def test_unreadable_order_is_retried_a_few_times(self):
        for _ in range(retail.MAX_ATTEMPTS):
            self.assertEqual(retail._need(self.c, "amazon", [ORDER]) if _ else [ORDER], [ORDER])
            self.assertEqual(retail.amazon_order(self.c, ORDER, "<html>nothing here</html>"), {"read": False})
        self.assertEqual(retail._need(self.c, "amazon", [ORDER]), [])   # given up on


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
        item = self.c.execute("SELECT id FROM retail_items WHERE title LIKE 'Organic%'").fetchone()["id"]
        retail.set_item_category(self.c, item, "Groceries")
        self.assertEqual(self.parts("t1"), [("Shopping", -40.88), ("Groceries", -20.0)])

    def test_your_own_choices_are_left_alone(self):
        self.amazon_order_with_charge()
        self.tx("t1", "2024-09-10", -60.88, "AMZN Mktp US", "Gifts & Donations", "manual")
        db.set_setting(self.c, "openrouter_api_key", "k")
        retail.finish(self.c, "amazon", caller=AI)
        self.assertEqual((self.row("t1")["category"], self.row("t1")["is_split"]), ("Gifts & Donations", 0))
        charge = self.c.execute("SELECT id, tx_id FROM retail_charges").fetchone()
        self.assertEqual(charge["tx_id"], "t1")                            # matched, just not changed
        self.assertEqual(retail.apply(self.c, charge["id"], force=True), "split")   # unless you ask

    def test_a_split_you_edited_is_left_alone(self):
        self.amazon_order_with_charge()
        self.tx("t1", "2024-09-10", -60.88, "AMZN Mktp US", "Shopping", "rule")
        db.set_setting(self.c, "openrouter_api_key", "k")
        retail.finish(self.c, "amazon", caller=AI)
        splits.set_splits(self.c, "t1", [{"amount": -30.88, "category": "Groceries"}, {"amount": -30, "category": "Shopping"}])
        item = self.c.execute("SELECT id FROM retail_items WHERE title LIKE 'The Crucible%'").fetchone()["id"]
        retail.set_item_category(self.c, item, "Shopping")
        self.assertEqual(self.parts("t1"), [("Groceries", -30.88), ("Shopping", -30.0)])

    def test_item_category_is_remembered_and_resplits(self):
        self.amazon_order_with_charge()
        self.tx("t1", "2024-09-10", -60.88, "AMZN Mktp US", "Shopping", "rule")
        db.set_setting(self.c, "openrouter_api_key", "k")
        retail.finish(self.c, "amazon", caller=AI)
        item = self.c.execute("SELECT id, title FROM retail_items WHERE title LIKE 'The Crucible%'").fetchone()
        retail.set_item_category(self.c, item["id"], "Shopping")
        self.assertEqual(self.parts("t1"), [("Groceries", -42.44), ("Shopping", -18.44)])
        # The same book in another order gets Shopping without asking the model.
        oid = retail._save_order(self.c, "amazon", "112-0000000-0000000", details=1)
        retail._save_items(self.c, oid, [{"title": item["title"], "amount": 9.98}])
        retail.categorize_items(self.c, caller=fake_ai({}))
        self.assertEqual(tuple(self.c.execute("SELECT category, category_source FROM retail_items WHERE order_id=?", (oid,)).fetchone()),
                         ("Shopping", "memory"))

    def test_unlink_restores_and_isnt_matched_again(self):
        self.amazon_order_with_charge()
        self.tx("t1", "2024-09-10", -60.88, "AMZN Mktp US", "Shopping", "rule")
        db.set_setting(self.c, "openrouter_api_key", "k")
        retail.finish(self.c, "amazon", caller=AI)
        charge = self.c.execute("SELECT id FROM retail_charges").fetchone()["id"]
        retail.unlink(self.c, charge)
        t = self.row("t1")
        self.assertEqual((t["is_split"], t["category"], t["category_source"]), (0, "Shopping", "rule"))
        retail.match_and_apply(self.c)
        self.assertIsNone(self.c.execute("SELECT tx_id FROM retail_charges").fetchone()["tx_id"])
        self.assertEqual(retail.link(self.c, charge, "t1"), "split")   # you can still pick it yourself

    def test_pending_transaction_that_posts_is_matched_again(self):
        self.amazon_order_with_charge()
        self.tx("pend", "2024-09-09", -60.88, "AMZN Mktp US", "Shopping", "rule")
        db.set_setting(self.c, "openrouter_api_key", "k")
        retail.finish(self.c, "amazon", caller=AI)
        # The bank replaces the pending transaction with a posted one (its parts carry over, as a sync does).
        self.tx("posted", "2024-09-11", -60.88, "AMZN Mktp US", "Shopping", "rule")
        splits.carry_over(self.c, "pend", "posted", -60.88)
        self.c.execute("DELETE FROM transactions WHERE id='pend'")
        retail.match_and_apply(self.c)
        self.assertEqual(self.c.execute("SELECT tx_id FROM retail_charges").fetchone()["tx_id"], "posted")
        self.assertEqual(len(self.parts("posted")), 3)

    def test_closest_date_wins(self):
        self.amazon_order_with_charge()
        self.tx("late", "2024-09-18", -60.88, "AMZN Mktp US")
        self.tx("near", "2024-09-10", -60.88, "AMZN Mktp US")
        self.tx("early", "2024-09-01", -60.88, "AMZN Mktp US")       # before the window
        retail.match(self.c)
        self.assertEqual(self.c.execute("SELECT tx_id FROM retail_charges").fetchone()["tx_id"], "near")


class AllocateTests(unittest.TestCase):
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
        online = self.c.execute("SELECT * FROM retail_orders WHERE order_number='912001234567'").fetchone()
        self.assertEqual((online["channel"], online["placed"], online["total"], online["details"]), ("online", "2024-09-02", 31.8, 1))
        items = self.c.execute("SELECT title, quantity, amount, department FROM retail_items ORDER BY position").fetchall()
        self.assertEqual([tuple(i) for i in items], [("Good & Gather Whole Milk 1gal", 2.0, 7.98, "GROCERY"),
                                                     ("Threshold Bath Towel", 1.0, 21.99, None)])
        receipt = {"order_number": "5555-0123-4567-8901", "order_lines": [
            {"item": {"description": "Tide Pods 42ct"}, "original_quantity": 1, "total_price": {"amount": 12.49}}]}
        self.assertEqual(retail.target_order(self.c, "5555-0123-4567-8901", receipt), {"read": True})
        store = self.c.execute("SELECT channel, details FROM retail_orders WHERE order_number='5555-0123-4567-8901'").fetchone()
        self.assertEqual(tuple(store), ("store", 1))

    def test_split_with_departments_when_theres_no_ai(self):
        retail.target_history(self.c, self.HISTORY, "ONLINE")
        self.tx("t1", "2024-09-04", -31.80, "TARGET 00012345 MINNEAPOLIS MN", "Shopping", "history")
        self.tx("t2", "2024-09-06", -12.49, "TARGET T-1234", "Shopping", "history")
        out = retail.finish(self.c, "target")
        self.assertEqual(out["items"]["department"], 1)
        self.assertEqual(self.parts("t1"), [("Shopping", -23.33), ("Groceries", -8.47)])
        self.assertEqual(self.c.execute("SELECT tx_id FROM retail_charges WHERE order_id=?",
                                        (retail.order_key("target", "5555-0123-4567-8901"),)).fetchone()["tx_id"], "t2")
        self.assertEqual(self.row("t2")["is_split"], 0)                     # no items yet: left as it was


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
        self.assertEqual(self.req("POST", "/api/ext/start", {"retailer": "walmart"}, ext)[0], 400)
        code, r = self.req("POST", "/api/ext/target/history", {"purchase_type": "STORE", "data": {"orders": []}}, ext)
        self.assertEqual((code, r["more"]), (200, False))
        self.assertEqual(self.req("POST", "/api/ext/finish", {"retailer": "target"}, ext)[0], 200)
        # What's still unmatched now, for the extension's popup (matches you make in Runway count straight away).
        self.assertEqual(self.req("POST", "/api/ext/status", {}, ext), (200, {"unmatched": {"amazon": 0, "target": 0}}))
        # The key only opens the extension's calls.
        self.assertEqual(self.req("POST", "/api/retail/token", headers=ext)[0], 403)
        self.req("POST", "/api/retail/token/remove", headers={"X-Runway": "1"})
        self.assertEqual(self.req("POST", "/api/ext/ping", {}, ext)[0], 401)
        with urllib.request.urlopen(self.base + "/api/retail/extension.zip") as resp:   # to install it from Settings
            import io, zipfile
            self.assertIn("runway-orders/manifest.json", zipfile.ZipFile(io.BytesIO(resp.read())).namelist())
        code, st = self.req("GET", "/api/retail")
        self.assertEqual((code, st["token"], st["stores"]["target"]["orders"]), (200, False, 0))


if __name__ == "__main__":
    unittest.main()
