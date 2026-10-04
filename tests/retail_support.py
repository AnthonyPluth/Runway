"""What the retail tests share: the Amazon page fixtures, a stand-in for the model, and the base case with a card account."""
import json
import os
from unittest import mock

from sqlalchemy import insert, select

from runway import retail, splits
from runway.retail import store
from runway.models import Account, Transaction
from tests.shared import DbCase, add_tx


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
        add_tx(self.c, "card", posted, amount, id=tid, description=desc, payee=desc, category=category,
               category_source=source)

    def row(self, tid):
        return self.c.execute(select(Transaction).where(Transaction.id == tid)).fetchone()

    def parts(self, tid):
        return [(p["category"], p["amount"]) for p in splits.get(self.c, tid)]

    def amazon_order_with_charge(self, amount=-60.88, day="2024-09-09"):
        retail.amazon_order(self.c, ORDER, fixture(f"order-details-{ORDER}.html"))
        store.save_charge(self.c, f"amazon|{ORDER}|x", retail.order_key("amazon", ORDER), day, amount, None)
