"""Deleting a transaction and the forecast: a posted card charge the card's balance still has keeps counting in the
statement in progress; a duplicate, a pending charge or one the bank took back doesn't."""

from datetime import timedelta

from sqlalchemy import func, select, update

from runway.domain import categories, forecast
from runway.providers import plaidbank
from runway.server.api import transactions as tx
from runway.storage.models import Account, DeletedCharge, Transaction
from tests import forecast_support as fs
from tests.shared import TODAY, LedgerCase, add_tx


class DeletedChargeTests(LedgerCase):
    """card_setup: a $900 balance, which is the $800 statement less a $200 payment plus $300 charged since the close
    (COFFEE $100, GROCER $200)."""
    card_setup = fs.card_setup

    def setUp(self):
        super().setUp()
        self.card_setup()

    def tx_id(self, desc):
        return self.conn.execute(select(Transaction.id).where(Transaction.description == desc)).scalar()

    def delete(self, *descs):
        ids = [self.tx_id(d) for d in descs]
        if len(ids) == 1:
            tx.api_tx_delete(self.conn, {}, {}, ids[0])
        else:
            tx.api_tx_bulk_delete(self.conn, {}, {"ids": ids})

    def figures(self):
        """The card's figures and the forecast's: what's been charged since the close, what's left of the statement,
        the card's payments and the forecast's total day by day."""
        fc = forecast.build(self.conn, TODAY, 90)
        card = next(c for c in fc["cards"] if c["id"] == "cc")
        payments = {e["date"]: e["amount"] for e in fc["events"] if e["kind"] == "card"}
        return card["new_charges"], card["remaining"], payments, fc["total"]

    def kept(self):
        return self.conn.execute(select(func.count()).select_from(DeletedCharge)).scalar()

    def test_deleting_a_posted_charge_leaves_the_forecast_as_it_was(self):
        before = self.figures()
        self.assertEqual(before[0], 300.0)
        self.delete("COFFEE")
        self.assertIsNone(self.tx_id("COFFEE"))   # gone from the list
        self.assertEqual(self.figures(), before)

    def test_deleting_several_at_once_does_too(self):
        before = self.figures()
        self.delete("COFFEE", "GROCER")
        self.assertEqual(self.figures(), before)

    def test_a_charge_on_a_closed_statement_changes_nothing_either(self):
        before = self.figures()
        self.delete("STUFF")   # before the close: on the statement the bank reported
        self.assertEqual(self.figures(), before)

    def test_a_deleted_duplicate_stops_counting(self):
        """The balance has the charge once: deleting the second copy takes the double count out, as before."""
        self.tx("cc", "2026-09-12", -100.0, "COFFEE AGAIN", "Restaurants")
        self.assertEqual(self.figures()[0], 400.0)
        self.delete("COFFEE AGAIN")
        self.assertEqual(self.figures()[0], 300.0)

    def test_a_charge_the_bank_took_off_stops_counting(self):
        self.delete("COFFEE")
        self.conn.execute(update(Account).where(Account.id == "cc").values(balance=-800.0))   # the next sync's balance
        self.assertEqual(self.figures()[0], 200.0)

    def test_a_balance_from_before_the_close_cant_say(self):
        """A balance read before the statement closed doesn't have the charges since: the deleted one goes, as before."""
        self.conn.execute(update(Account).where(Account.id == "cc").values(balance_date="2026-09-08"))
        self.delete("COFFEE")
        self.assertEqual(self.figures()[0], 200.0)

    def test_a_deleted_pending_charge_leaves_the_forecast_as_before(self):
        """The balance doesn't have a pending charge yet: deleting it takes it out of the forecast, as before."""
        self.tx("cc", "2026-09-22", -50.0, "HOLD", "Restaurants", pending=1)
        self.assertEqual(self.figures()[0], 350.0)
        self.delete("HOLD")
        self.assertEqual((self.figures()[0], self.kept()), (300.0, 0))

    def test_a_cash_account_keeps_nothing(self):
        """A cash account's forecast starts from its balance, which a deleted transaction doesn't change."""
        self.tx("chk", "2026-09-20", -40.0, "LUNCH", "Restaurants")
        start = forecast.build(self.conn, TODAY, 90)["total"][0]
        self.delete("LUNCH")
        self.assertEqual((forecast.build(self.conn, TODAY, 90)["total"][0], self.kept()), (start, 0))

    def test_one_a_sync_brings_back_counts_once(self):
        before = self.figures()
        cid = self.tx_id("COFFEE")
        self.delete("COFFEE")
        add_tx(self.conn, "cc", "2026-09-12", -100.0, id=cid, description="COFFEE", category="Restaurants", pending=0)
        self.assertEqual(self.figures(), before)

    def test_one_plaid_removes_stops_counting(self):
        add_tx(self.conn, "cc", "2026-09-18", -60.0, id="cc|pl:t9", description="BOOKS", category="Shopping", pending=0)
        self.conn.execute(update(Account).where(Account.id == "cc").values(balance=-960.0))
        self.assertEqual(self.figures()[0], 360.0)
        self.delete("BOOKS")
        self.assertEqual((self.figures()[0], self.kept()), (360.0, 1))
        item = {"item_id": "item", "cursor": "c0"}
        plaidbank.sync_transactions(self.conn, item, TODAY, ([], [], [{"transaction_id": "t9"}], "c1"))
        self.assertEqual(self.kept(), 0)
        self.assertEqual(self.figures()[0], 300.0)

    def test_a_deleted_transfer_stays_out_whatever_happens_to_its_category(self):
        """Money out to a transfer category isn't a charge: deleting it keeps nothing, so renaming the category or
        removing it afterwards doesn't turn it into one."""
        categories.add(self.conn, "Card Transfer", is_transfer=True)
        self.tx("cc", "2026-09-16", -150.0, "MOVED OUT", "Card Transfer")
        self.conn.execute(update(Account).where(Account.id == "cc").values(balance=-1050.0))   # the balance has it
        before = self.figures()
        self.assertEqual(before[0], 300.0)
        self.delete("MOVED OUT")
        self.assertEqual((self.figures(), self.kept()), (before, 0))
        categories.rename(self.conn, "Card Transfer", "Moved")
        self.assertEqual(self.figures(), before)
        categories.remove(self.conn, "Moved")
        self.assertEqual(self.figures(), before)

    def test_kept_charges_go_after_a_year(self):
        self.delete("COFFEE")
        forecast.remember_deleted_charges(self.conn, [], TODAY + timedelta(days=400))
        self.assertEqual(self.kept(), 0)

    def test_they_go_with_the_card(self):
        self.delete("COFFEE")
        self.conn.execute(Account.__table__.delete().where(Account.__table__.c.id == "cc"))
        self.assertEqual(self.kept(), 0)
