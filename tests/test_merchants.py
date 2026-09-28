"""Merchant logos: noted from Plaid, downloaded from Plaid only, served by Runway; bundled ones for big names."""
import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runway import db, merchants, server  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40


class FakeResponse(io.BytesIO):
    def __init__(self, data, ctype):
        super().__init__(data)
        self.headers = {"Content-Type": ctype}


class MerchantTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "m.db")
        db.init(self.path)
        self.c = db.connect(self.path)
        self.asked = []

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def opener(self, replies):
        def open_(req):
            self.asked.append(req.full_url)
            data, ctype = replies[req.full_url]
            return FakeResponse(data, ctype)
        return open_

    def test_note_uses_the_counterparty_too(self):
        mid = merchants.note(self.c, {"merchant_name": None, "counterparties": [
            {"type": "payment_app", "name": "Venmo"},
            {"type": "merchant", "name": "Blue Bottle", "entity_id": "ent-bb", "logo_url": "https://plaid.com/bb.png"}]})
        self.assertEqual(mid, "ent-bb")
        self.assertIsNone(merchants.note(self.c, {"merchant_name": "Nobody"}))   # nothing to show for it
        self.assertEqual(merchants.note(self.c, {"merchant_name": "Corner Shop", "logo_url": "https://plaid.com/c.png"}),
                         "name:corner shop")

    def test_logos_come_only_from_plaid_as_images(self):
        good, other, svg = "https://plaid-merchant-logos.plaid.com/a.png", "https://evil.example.com/b.png", "https://plaid.com/c.svg"
        for i, url in enumerate((good, other, svg, "http://plaid.com/d.png")):
            merchants.note(self.c, {"merchant_name": f"M{i}", "merchant_entity_id": f"e{i}", "logo_url": url})
        got = merchants.fetch_logos(self.c, opener=self.opener({good: (PNG, "image/png"), svg: (b"<svg/>", "image/svg+xml")}))
        self.assertEqual(got, 1)
        self.assertEqual(self.asked, [good, svg])          # never the other host, never plain http
        self.assertEqual(merchants.logo(self.c, "e0"), (PNG, "image/png"))
        self.assertIsNone(merchants.logo(self.c, "e2"))    # SVG refused
        self.assertEqual(merchants.fetch_logos(self.c, opener=self.opener({})), 0)   # the misses wait a month

    def test_transactions_find_their_logo_by_id_or_name(self):
        merchants.note(self.c, {"merchant_name": "Blue Bottle", "merchant_entity_id": "ent-bb", "logo_url": "https://plaid.com/bb.png"})
        merchants.fetch_logos(self.c, opener=self.opener({"https://plaid.com/bb.png": (PNG, "image/png")}))
        txs = [{"id": "a", "merchant_id": "ent-bb", "payee": "BB"},       # from Plaid
               {"id": "b", "merchant_id": None, "payee": "blue  bottle"},  # from SimpleFIN, same name
               {"id": "c", "merchant_id": None, "payee": "Starbucks"},
               {"id": "d", "merchant_id": None, "payee": "Blue Bottle Coffee 12"},   # starts with the name
               {"id": "e", "merchant_id": None, "payee": "Blue Bottles Inc"}]        # not as whole words
        self.assertEqual(merchants.for_transactions(self.c, txs), {"a": "ent-bb", "b": "ent-bb", "d": "ent-bb"})


    def test_plaid_logo_first_then_a_bundled_one(self):
        merchants.note(self.c, {"merchant_name": "Target", "merchant_entity_id": "ent-t", "logo_url": "https://plaid.com/t.png"})
        merchants.fetch_logos(self.c, opener=self.opener({"https://plaid.com/t.png": (PNG, "image/png")}))
        self.c.execute("INSERT INTO accounts(id, name, kind) VALUES ('a', 'Card', 'credit')")
        self.c.executemany("INSERT INTO transactions(id, account_id, posted, amount, description, payee, merchant_id) "
                           "VALUES (?, 'a', '2026-09-20', -5, ?, ?, ?)", [
                               ("t1", "TARGET 0001", "Target", "ent-t"),          # Plaid's logo
                               ("t2", "WAL-MART #12", "Walmart", None),          # bundled
                               ("t3", "SQ *JOES COFFEE", "Joe's Coffee", None)])  # neither: its initial
        self.c.commit()
        got = {t["id"]: t["logo"] for t in server.api_transactions(self.c, {}, None)["items"]}
        self.assertEqual(got, {"t1": "/api/merchants/ent-t/logo", "t2": "/merchants/walmart.svg", "t3": None})


if __name__ == "__main__":
    unittest.main()
