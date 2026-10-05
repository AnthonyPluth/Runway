"""Splitting an order's charge across its items by store: the allocation, Target and Costco."""
import json
import random
import unittest
from datetime import date, datetime, timedelta

from sqlalchemy import func, select, update

from runway.storage import db
from runway.domain import retail
from runway.domain.retail import split as retail_split
from runway.domain.retail import store
from runway.storage.models import Category, RetailCharge, RetailItem, RetailOrder
from tests.retail_support import Base


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
