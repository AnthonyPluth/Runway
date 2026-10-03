"""Transactions: the list's date, amount and kind filters, its search by amount and category, what it adds up to, and
editing, adding and deleting a transaction (with what Undo needs, and a sync that keeps what you changed)."""
import unittest
from datetime import date

from sqlalchemy import insert, select, update

from runway import plaidbank, simplefin, splits
from runway.models import Account, PlaidAccount, RetailCharge, RetailOrder, Transaction, TxSplit
from runway.server.api import transactions as tx
from runway.server.common import ApiError
from tests.shared import TODAY, LedgerCase, ts


def q(**kw):
    return {k: [str(v)] for k, v in kw.items()}


class Ledger(LedgerCase):
    def setUp(self):
        super().setUp()
        self.acct("chk", "checking", 1000.0)
        self.acct("card", "credit", -200.0)
        self.acct("brk", "investment", 5000.0)
        for tid, acct, posted, amount, payee, cat in [
            ("chk|1", "chk", "2026-09-01", 2500.0, "Acme Payroll", "Paycheck"),
            ("chk|2", "chk", "2026-09-05", -59.28, "Green Grocer", "Groceries"),
            ("chk|3", "chk", "2026-09-10", -500.0, "Transfer to Savings", "Transfer"),
            ("card|4", "card", "2026-09-12", -12.5, "Corner Coffee", "Coffee & Snacks"),
            ("card|5", "card", "2026-09-20", -80.0, "Market", None),
            ("card|6", "card", "2026-08-30", -40.0, "Old Thing", "Ignore"),
        ]:
            self.conn.execute(insert(Transaction).values(id=tid, account_id=acct, posted=posted, amount=amount, payee=payee,
                                                         description=payee.upper(), category=cat, pending=0))
        # A split one: part groceries, part a card payment (a transfer).
        splits.set_splits(self.conn, "card|5", [{"amount": -50, "category": "Groceries"}, {"amount": -30, "category": "Credit Card Payment"}])

    def ids(self, **kw):
        return sorted(t["id"] for t in tx.api_transactions(self.conn, q(limit=1000, **kw), {})["items"])

    def row(self, tid):
        return self.conn.execute(select(Transaction).where(Transaction.id == tid)).fetchone()


class FilterTests(Ledger):
    def test_dates_both_ends_included(self):
        self.assertEqual(self.ids(**{"from": "2026-09-05", "to": "2026-09-12"}), ["card|4", "chk|2", "chk|3"])
        self.assertEqual(self.ids(**{"from": "2026-09-20"}), ["card|5"])
        self.assertEqual(self.ids(to="2026-09-01"), ["card|6", "chk|1"])
        with self.assertRaises(ApiError):
            self.ids(**{"from": "2026-13-01"})

    def test_amounts_either_way(self):
        self.assertEqual(self.ids(min=50, max=100), ["card|5", "chk|2"])
        self.assertEqual(self.ids(min="59.28", max="59.28"), ["chk|2"])
        self.assertEqual(self.ids(max=-15), ["card|4"])   # the size of it, money in or out
        with self.assertRaises(ApiError):
            self.ids(min="lots")
        with self.assertRaises(ApiError):
            self.ids(max="nan")

    def test_kind(self):
        self.assertEqual(self.ids(kind="in"), ["chk|1"])
        self.assertEqual(self.ids(kind="out"), ["card|4", "card|5", "chk|2"])   # not the transfer, nor what's ignored
        self.assertEqual(self.ids(kind="transfer"), ["card|5", "chk|3"])   # a split with a transfer part too
        with self.assertRaises(ApiError):
            self.ids(kind="sideways")

    def test_search_by_amount_category_and_note(self):
        self.assertEqual(self.ids(q="59.28"), ["chk|2"])
        self.assertEqual(self.ids(q="$59.28"), ["chk|2"])
        self.assertEqual(self.ids(q="−2,500"), ["chk|1"])
        self.assertEqual(self.ids(q="coffee"), ["card|4"])
        self.assertEqual(self.ids(q="groc"), ["card|5", "chk|2"])   # the name, and a split's part
        self.assertEqual(self.ids(q="paycheck"), ["chk|1"])
        self.conn.execute(update(Transaction).where(Transaction.id == "chk|3").values(notes="For the trip"))
        self.assertEqual(self.ids(q="trip"), ["chk|3"])

    def test_sum_counts_as_the_day_totals_do(self):
        r = tx.api_transactions(self.conn, {}, {})
        self.assertEqual(r["total"], 6)
        # payroll, groceries, coffee and the split's groceries part; not the transfer, the card payment part or Ignore
        self.assertEqual(r["sum"], round(2500 - 59.28 - 12.5 - 50, 2))
        self.assertEqual(tx.api_transactions(self.conn, q(category="Groceries"), {})["sum"], round(-59.28 - 50, 2))
        self.assertEqual(tx.api_transactions(self.conn, q(kind="transfer"), {})["sum"], -530.0)
        self.assertEqual(tx.api_transactions(self.conn, q(q="zzz"), {})["sum"], 0)

    def test_source(self):
        self.conn.execute(insert(Transaction).values(id="chk|pl:x", account_id="chk", posted="2026-09-02", amount=-1.0, pending=0))
        got = {t["id"]: t["source"] for t in tx.api_transactions(self.conn, {}, {})["items"]}
        self.assertEqual((got["chk|1"], got["chk|pl:x"]), ("simplefin", "plaid"))

    def test_bulk_by_filter(self):
        r = tx.api_tx_bulk(self.conn, {}, {"filter": {"account": "card", "ignored": "0"}, "reviewed": True})
        self.assertEqual(r["updated"], 2)
        self.assertEqual(sorted(w["id"] for w in r["was"]), ["card|4", "card|5"])
        with self.assertRaises(ApiError):   # matching nothing: nothing to change
            tx.api_tx_bulk(self.conn, {}, {"filter": {"q": "nothing matches this"}, "reviewed": True})


class EditTests(Ledger):
    def test_name_note_and_undo(self):
        r = tx.api_tx_update(self.conn, {}, {"payee": "  Green   Grocer Co ", "notes": " weekly shop "}, "chk|2")
        t = self.row("chk|2")
        self.assertEqual((t["payee"], t["notes"]), ("Green Grocer Co", "weekly shop"))
        self.assertEqual((r["tx"]["payee"], r["tx"]["notes"]), ("Green Grocer Co", "weekly shop"))
        tx.api_tx_update(self.conn, {}, {"restore": r["was"]}, "chk|2")
        t = self.row("chk|2")
        self.assertEqual((t["payee"], t["notes"], t["category"]), ("Green Grocer", None, "Groceries"))

    def test_a_synced_ones_date_and_amount_keep_the_banks_beside_them(self):
        r = tx.api_tx_update(self.conn, {}, {"amount": "-61.00", "posted": "2026-09-06"}, "chk|2")
        t = self.row("chk|2")
        self.assertEqual((t["amount"], t["posted"], t["bank_amount"], t["bank_posted"]), (-61.0, "2026-09-06", -59.28, "2026-09-05"))
        tx.api_tx_update(self.conn, {}, {"amount": -62}, "chk|2")   # changed again: the bank's is still the bank's
        self.assertEqual(self.row("chk|2")["bank_amount"], -59.28)
        tx.api_tx_update(self.conn, {}, {"amount": -59.28}, "chk|2")   # back to the bank's: not yours any more
        self.assertIsNone(self.row("chk|2")["bank_amount"])
        tx.api_tx_update(self.conn, {}, {"restore": r["was"]}, "chk|2")
        t = self.row("chk|2")
        self.assertEqual((t["amount"], t["posted"], t["bank_amount"], t["bank_posted"]), (-59.28, "2026-09-05", None, None))

    def test_checks(self):
        for body in ({"amount": "lots"}, {"amount": True}, {"amount": float("inf")}, {"amount": 1e12}, {"posted": "2026-02-30"},
                     {"notes": 5}, {}, {"category": "Groceries"}):
            with self.subTest(body=body), self.assertRaises(ApiError):
                tx.api_tx_update(self.conn, {}, body, "chk|2")
        with self.assertRaises(ApiError) as e:
            tx.api_tx_update(self.conn, {}, {"payee": "x"}, "nope")
        self.assertEqual(e.exception.status, 404)
        self.conn.execute(update(Transaction).where(Transaction.id == "chk|2").values(pending=1))
        with self.assertRaises(ApiError):   # the bank still decides a pending one's
            tx.api_tx_update(self.conn, {}, {"amount": -1}, "chk|2")
        tx.api_tx_update(self.conn, {}, {"notes": "fine"}, "chk|2")   # but a note is yours

    def test_a_split_ones_parts_follow_its_amount_and_come_back_with_undo(self):
        r = tx.api_tx_update(self.conn, {}, {"amount": -100}, "card|5")
        self.assertEqual(sorted(p["amount"] for p in splits.get(self.conn, "card|5")), [-62.5, -37.5])
        self.assertEqual(self.row("card|5")["needs_review"], 0)   # you changed it: nothing to check
        tx.api_tx_update(self.conn, {}, {"restore": r["was"]}, "card|5")
        self.assertEqual(sorted(p["amount"] for p in splits.get(self.conn, "card|5")), [-50.0, -30.0])

    def test_taking_the_category_away(self):
        r = tx.api_tx_update(self.conn, {}, {"category": None}, "card|5")
        t = self.row("card|5")
        self.assertEqual((t["category"], t["is_split"], t["needs_review"]), (None, 0, 1))
        tx.api_tx_update(self.conn, {}, {"restore": r["was"]}, "card|5")
        self.assertEqual(self.row("card|5")["is_split"], 1)

    def test_simplefin_sync_keeps_your_date_and_amount(self):
        payload = lambda amount: {"errors": [], "accounts": [{
            "org": {"name": "Bank"}, "id": "A1", "name": "Checking", "currency": "USD", "balance": "10", "balance-date": ts(TODAY),
            "transactions": [{"id": "t1", "posted": ts(date(2026, 9, 20)), "amount": amount, "description": "SQ *CAFE"}]}]}
        simplefin.store_payload(self.conn, payload("-10.00"), date(2026, 9, 1))
        tx.api_tx_update(self.conn, {}, {"amount": -12.0, "posted": "2026-09-21", "notes": "tip"}, "A1|t1")
        simplefin.store_payload(self.conn, payload("-10.50"), date(2026, 9, 1))
        t = self.row("A1|t1")
        self.assertEqual((t["amount"], t["posted"], t["notes"]), (-12.0, "2026-09-21", "tip"))
        self.assertEqual((t["bank_amount"], t["bank_posted"]), (-10.5, "2026-09-20"))   # the bank's latest, beside yours

    def test_plaid_sync_keeps_your_date_and_amount(self):
        self.conn.execute(update(Account).where(Account.id == "chk").values(provider="plaid", plaid_account_id="pa"))
        self.conn.execute(insert(PlaidAccount).values(plaid_account_id="pa", item_id="it"))
        item = {"item_id": "it", "cursor": "c"}
        t1 = {"transaction_id": "x1", "account_id": "pa", "date": "2026-09-20", "amount": 10.0, "name": "CAFE"}
        plaidbank.sync_transactions(self.conn, item, TODAY, ([t1], [], [], "c1"))
        tx.api_tx_update(self.conn, {}, {"amount": -12.0}, "chk|pl:x1")
        plaidbank.sync_transactions(self.conn, item, TODAY, ([], [{**t1, "amount": 11.0, "date": "2026-09-21"}], [], "c2"))
        t = self.row("chk|pl:x1")
        self.assertEqual((t["amount"], t["bank_amount"], t["posted"]), (-12.0, -11.0, "2026-09-21"))   # the date wasn't yours

    def test_a_note_follows_a_pending_one_when_it_posts(self):
        self.conn.execute(update(Account).where(Account.id == "chk").values(provider="plaid", plaid_account_id="pa"))
        self.conn.execute(insert(PlaidAccount).values(plaid_account_id="pa", item_id="it"))
        item = {"item_id": "it", "cursor": "c"}
        p = {"transaction_id": "p1", "account_id": "pa", "date": "2026-09-20", "amount": 10.0, "name": "CAFE", "pending": True}
        plaidbank.sync_transactions(self.conn, item, TODAY, ([p], [], [], "c1"))
        tx.api_tx_update(self.conn, {}, {"notes": "with Sam"}, "chk|pl:p1")
        posted = {**p, "transaction_id": "t1", "pending": False, "pending_transaction_id": "p1"}
        plaidbank.sync_transactions(self.conn, item, TODAY, ([posted], [], [], "c2"))
        self.assertEqual(self.row("chk|pl:t1")["notes"], "with Sam")


class AddTests(Ledger):
    def test_add_list_and_delete(self):
        r = tx.api_tx_create(self.conn, {}, {"account": "chk", "posted": "2026-09-15", "payee": "Farmers Market", "amount": "-23.40",
                                             "category": "Groceries", "notes": "cash"})
        t = self.row(r["id"])
        self.assertTrue(r["id"].startswith("chk|manual:"))
        self.assertEqual((t["amount"], t["category"], t["category_source"], t["needs_review"], t["notes"], t["pending"]),
                         (-23.4, "Groceries", "manual", 0, "cash", 0))
        listed = {x["id"]: x for x in tx.api_transactions(self.conn, q(account="chk"), {})["items"]}
        self.assertEqual(listed[r["id"]]["source"], "manual")
        # Its date and amount are just yours: nothing kept beside them.
        tx.api_tx_update(self.conn, {}, {"amount": -25, "posted": "2026-09-16"}, r["id"])
        t = self.row(r["id"])
        self.assertEqual((t["amount"], t["bank_amount"], t["bank_posted"]), (-25.0, None, None))
        with self.assertRaises(ApiError):   # and it keeps a name
            tx.api_tx_update(self.conn, {}, {"payee": " "}, r["id"])
        tx.api_tx_delete(self.conn, {}, {}, r["id"])
        self.assertIsNone(self.row(r["id"]))

    def test_without_a_category_it_waits_in_review(self):
        r = tx.api_tx_create(self.conn, {}, {"account": "card", "posted": "2026-09-15", "payee": "Cash", "amount": 5})
        self.assertEqual((self.row(r["id"])["category"], self.row(r["id"])["needs_review"]), (None, 1))

    def test_checks(self):
        good = {"account": "chk", "posted": "2026-09-15", "payee": "X", "amount": -1}
        for bad in ({"account": "nope"}, {"account": "brk"}, {"posted": "soon"}, {"amount": ""}, {"amount": "x"}, {"payee": ""},
                    {"category": "Not a category"}):
            with self.subTest(bad=bad), self.assertRaises(ApiError):
                tx.api_tx_create(self.conn, {}, {**good, **bad})

    def test_only_yours_can_be_deleted(self):
        with self.assertRaises(ApiError):
            tx.api_tx_delete(self.conn, {}, {}, "chk|2")
        with self.assertRaises(ApiError):
            tx.api_tx_delete(self.conn, {}, {}, "chk|manual:nope")

    def test_deleting_one_lets_go_of_its_parts_and_order(self):
        r = tx.api_tx_create(self.conn, {}, {"account": "chk", "posted": "2026-09-15", "payee": "Shop", "amount": -10})
        splits.set_splits(self.conn, r["id"], [{"amount": -6, "category": "Groceries"}, {"amount": -4, "category": "Shopping"}])
        self.conn.execute(insert(RetailOrder).values(id="o1", retailer="amazon", order_number="1"))
        self.conn.execute(insert(RetailCharge).values(id="c1", order_id="o1", date="2026-09-15", amount=-10, tx_id=r["id"]))
        tx.api_tx_delete(self.conn, {}, {}, r["id"])
        self.assertIsNone(self.conn.execute(select(TxSplit.id).where(TxSplit.tx_id == r["id"])).fetchone())
        self.assertIsNone(self.conn.execute(select(RetailCharge.tx_id).where(RetailCharge.id == "c1")).scalar())


if __name__ == "__main__":
    unittest.main()
