"""Deleting an account (runway/deleted_accounts.py): everything that belongs to it goes, what pointed at it lets go, a
sync (SimpleFIN's, Plaid's banks and cards, Plaid's investments) doesn't bring it back, and restoring it does."""
import json
from unittest import mock

from sqlalchemy import func, insert, select

from runway import db, deleted_accounts, plaid, plaidbank, simplefin
from runway import settings_keys as sk
from runway.models import (Account, Asset, Budget, CardStatement, ChurnBankBonus, ChurnCard, CostOverride, DeletedAccount,
                           Holding, HoldingSnapshot, InvAccount, InvSnapshot, InvTransaction, ManualContribution, ManualPosition,
                           ManualState, ManualStatement, Override, PlaidAccount, PlaidItem, Recurring, RecurringDismissed,
                           RetailCharge, RetailOrder, Rule, Transaction, TxSplit)
from runway.server.api import accounts as api
from runway.server.common import ApiError
from runway.server.sync import _sync_lock
from tests.shared import TODAY, LedgerCase, ts


class DeleteAccountTests(LedgerCase):
    def setUp(self):
        super().setUp()
        c = self.conn
        self.acct("chk", "checking", 5000.0)
        self.acct("cc", "credit", -900.0, pay_from="chk", plaid_account_id="p-cc")
        self.acct("cc2", "credit", -50.0, pay_from="chk")
        self.acct("loan", "loan", -20000.0)
        db.set_setting(c, sk.PRIMARY_ACCOUNT, "chk")
        c.execute(insert(PlaidItem).values(item_id="item", access_token="x", institution_name="Chase", products="transactions,liabilities"))
        c.execute(insert(PlaidAccount).values(plaid_account_id="p-cc", item_id="item", type="credit", name="Sapphire"))
        c.execute(insert(CardStatement).values(plaid_account_id="p-cc", item_id="item", last_statement_balance=800.0,
                                               last_statement_date="2026-09-10", next_due_date="2026-10-05"))
        self.manual("cc", 700.0, "2026-08-10", "2026-09-05")
        self.manual("cc2", 50.0, "2026-09-10", "2026-10-05")
        for i, (acct, amount) in enumerate([("cc", -100.0), ("cc", -60.0), ("chk", -20.0), ("cc2", -5.0)]):
            c.execute(insert(Transaction).values(id=f"{acct}|{i}", account_id=acct, posted="2026-09-01", amount=amount,
                                                 description="X", payee="X"))
        c.execute(insert(TxSplit).values(tx_id="cc|0", amount=-60.0, category="Groceries"))
        c.execute(insert(TxSplit).values(tx_id="chk|2", amount=-20.0, category="Groceries"))
        c.execute(insert(RetailOrder).values(id="amazon:1", retailer="amazon", order_number="1"))
        c.execute(insert(RetailCharge).values(id="amazon:1:a", order_id="amazon:1", date="2026-09-01", amount=-100.0,
                                              tx_id="cc|0", match_source="auto", applied="{}"))
        c.execute(insert(Recurring).values(id=41, name="Netflix", account_id="cc", amount=-15.0, frequency="monthly",
                                           anchor_date="2026-09-05"))
        c.execute(insert(Recurring).values(id=42, name="Rent", account_id="chk", amount=-1500.0, frequency="monthly",
                                           anchor_date="2026-09-01"))
        c.execute(insert(Override), [{"key": "rec:41:2026-10-05", "amount": -16.0}, {"key": "rec:42:2026-10-01", "amount": -1400.0},
                                     {"key": "card:cc:2026-10-05", "amount": -500.0}, {"key": "stmt:cc:2026-09-10", "amount": 750.0},
                                     {"key": "card:cc2:2026-10-05", "amount": -40.0}])
        c.execute(insert(RecurringDismissed), [{"key": "rec:41:2026-08-05"}, {"key": "rec:42:2026-08-01"}])
        c.execute(insert(Rule), [{"match": "netflix", "category": "Streaming", "account_id": "cc"},
                                 {"match": "netflix", "category": "Streaming", "account_id": None}])
        c.execute(insert(Budget).values(category="Groceries", amount=500, pay_with="cc"))
        c.execute(insert(Asset).values(id=1, name="Home", kind="home", loan_account_id="loan"))
        c.execute(insert(ChurnCard).values(id=1, owner="Sam", issuer="chase", product="Sapphire", opened_on="2026-01-01", account_id="cc"))
        c.execute(insert(ChurnBankBonus).values(id=1, owner="Sam", bank="Chase", opened_on="2026-01-01", bonus=300, account_id="chk"))

    def count(self, model, *where):
        return self.conn.execute(select(func.count()).select_from(model).where(*where)).scalar()

    def test_a_card_and_everything_that_belongs_to_it(self):
        impact = api.api_account_removal(self.conn, {}, {}, "cc")
        self.assertEqual({k: impact[k] for k in ("name", "transactions", "recurring", "rules", "statements", "plaid")},
                         {"name": "cc", "transactions": 2, "recurring": 1, "rules": 1, "statements": 1, "plaid": True})
        out = api.api_account_remove(self.conn, {}, {}, "cc")
        self.assertEqual((out["ok"], out["transactions"]), (True, 2))
        c = self.conn
        self.assertEqual(self.count(Account, Account.id == "cc"), 0)
        self.assertEqual(self.count(Transaction, Transaction.account_id == "cc"), 0)
        self.assertEqual([r[0] for r in c.execute(select(TxSplit.tx_id))], ["chk|2"])
        charge = c.execute(select(RetailCharge)).fetchone()
        self.assertEqual((charge["tx_id"], charge["match_source"], charge["applied"]), (None, None, None))   # kept, unmatched
        self.assertEqual([r[0] for r in c.execute(select(Recurring.id))], [42])
        self.assertEqual(sorted(r[0] for r in c.execute(select(Override.key))), ["card:cc2:2026-10-05", "rec:42:2026-10-01"])
        self.assertEqual([r[0] for r in c.execute(select(RecurringDismissed.key))], ["rec:42:2026-08-01"])
        self.assertEqual([r[0] for r in c.execute(select(Rule.account_id))], [None])      # not widened to every account
        self.assertEqual([r[0] for r in c.execute(select(ManualStatement.account_id))], ["cc2"])
        self.assertEqual(self.count(CardStatement), 0)
        self.assertEqual(c.execute(select(PlaidAccount.ignored)).scalar(), 1)
        self.assertIsNone(c.execute(select(Budget.pay_with)).scalar())
        self.assertIsNone(c.execute(select(ChurnCard.account_id)).scalar())
        self.assertEqual(c.execute(select(Account.pay_from).where(Account.id == "cc2")).scalar(), "chk")   # untouched
        self.assertEqual(self.count(Transaction), 2)
        self.assertEqual(api.api_accounts_deleted(c, {}, {})[0]["id"], "cc")
        tomb = c.execute(select(DeletedAccount)).fetchone()
        self.assertEqual((tomb["name"], tomb["kind"], tomb["plaid_account_id"], tomb["inv_ids"]), ("cc", "credit", "p-cc", None))
        with self.assertRaises(ApiError) as e:
            api.api_account_remove(c, {}, {}, "cc")
        self.assertEqual(e.exception.status, 404)

    def test_its_plaid_account_linked_to_another_account_leaves_it_deleted(self):
        api.api_account_remove(self.conn, {}, {}, "cc")
        plaidbank.match(self.conn, "p-cc", "cc2", TODAY)   # you chose to use the Plaid card for another account
        tomb = self.conn.execute(select(DeletedAccount.id, DeletedAccount.plaid_account_id)).fetchone()
        self.assertEqual(tuple(tomb), ("cc", None))         # still deleted, but its statements aren't held back
        self.assertEqual(deleted_accounts.plaid_ids(self.conn), set())
        self.assertEqual(self.conn.execute(select(PlaidAccount.ignored)).scalar(), 0)

    def test_what_pointed_at_it_lets_go(self):
        api.api_account_remove(self.conn, {}, {}, "chk")
        c = self.conn
        self.assertIsNone(db.get_setting(c, sk.PRIMARY_ACCOUNT))
        self.assertEqual([r[0] for r in c.execute(select(Account.pay_from).where(Account.kind == "credit"))], [None, None])
        self.assertIsNone(c.execute(select(ChurnBankBonus.account_id)).scalar())
        self.assertEqual([r[0] for r in c.execute(select(Recurring.id))], [41])
        api.api_account_remove(c, {}, {}, "loan")
        self.assertIsNone(c.execute(select(Asset.loan_account_id)).scalar())
        self.assertEqual(c.execute(select(Asset.name)).scalar(), "Home")

    def test_an_investment_account_and_its_holdings(self):
        c = self.conn
        self.acct("brk", "investment", 1500.0)
        c.execute(insert(InvAccount), [{"id": "sf:brk", "item_id": "simplefin", "name": "Brokerage", "source": "simplefin", "account_id": None},
                                       {"id": "w1", "item_id": "inv", "name": "Brokerage", "source": "plaid", "account_id": "brk"},
                                       {"id": "w2", "item_id": "inv", "name": "Roth", "source": "plaid", "account_id": "pl:w2"}])
        for iid in ("sf:brk", "w1", "w2"):
            c.execute(insert(Holding).values(account_id=iid, security_id="s1", quantity=1, value=100))
            c.execute(insert(InvSnapshot).values(date="2026-09-01", account_id=iid, value=100))
            c.execute(insert(HoldingSnapshot).values(date="2026-09-01", account_id=iid, security_id="s1", quantity=1, value=100))
            c.execute(insert(InvTransaction).values(id=f"t-{iid}", account_id=iid, date="2026-09-01"))
            c.execute(insert(ManualPosition).values(account_id=iid, security_id="s1", shares=1))
            c.execute(insert(ManualContribution).values(account_id=iid, date="2026-09-01", amount=10))
            c.execute(insert(ManualState).values(account_id=iid, drift=0))
            c.execute(insert(CostOverride).values(account_id=iid, security_id="s1", cost_basis=50))
        db.set_setting(c, sk.sf_raw("brk"), "{}")
        db.set_setting(c, sk.SIMPLEFIN_HOLDINGS_SEEN, json.dumps({"brk": 3, "other": 1}))
        self.assertEqual(api.api_account_removal(c, {}, {}, "brk")["holdings"], 2)
        api.api_account_remove(c, {}, {}, "brk")
        for model in (Holding, InvSnapshot, HoldingSnapshot, InvTransaction, ManualPosition, ManualContribution, ManualState, CostOverride):
            self.assertEqual([r[0] for r in c.execute(select(model.account_id))], ["w2"], model.__name__)
        self.assertEqual([r[0] for r in c.execute(select(InvAccount.id))], ["w2"])
        self.assertIsNone(db.get_setting(c, sk.sf_raw("brk")))
        self.assertEqual(json.loads(db.get_setting(c, sk.SIMPLEFIN_HOLDINGS_SEEN) or "{}"), {"other": 1})
        self.assertEqual(deleted_accounts.inv_ids(c), {"sf:brk", "w1"})

    def test_refused_while_a_sync_runs(self):
        with _sync_lock:
            with self.assertRaises(ApiError) as e:
                api.api_account_remove(self.conn, {}, {}, "cc")
        self.assertEqual(e.exception.status, 409)
        self.assertEqual(self.count(Account, Account.id == "cc"), 1)
        self.assertFalse(_sync_lock.locked())


class StaysDeletedTests(LedgerCase):
    """A sync after deleting doesn't bring the account back; one after restoring does."""

    def simplefin_payload(self):
        return {"errors": [], "accounts": [{"org": {"name": "Chase"}, "id": "A1", "name": "Sapphire Reserve", "currency": "USD",
                                            "balance": "-50.00", "balance-date": ts(TODAY), "transactions": [
                                                {"id": "t1", "posted": ts(TODAY), "amount": "-10.00", "description": "CAFE"}]}]}

    def test_simplefin(self):
        c = self.conn
        simplefin.store_payload(c, self.simplefin_payload(), TODAY)
        db.set_setting(c, sk.SIMPLEFIN_BACKFILL, json.dumps({"seen": ["A1"], "done": ["A1"]}))
        api.api_account_remove(c, {}, {}, "A1")
        self.assertEqual(simplefin.store_payload(c, self.simplefin_payload(), TODAY), [])
        self.assertEqual((self.count(Account), self.count(Transaction)), (0, 0))
        self.assertEqual(api.api_account_restore(c, {}, {}, "A1"), {"ok": True, "name": "Sapphire Reserve"})
        self.assertEqual(json.loads(db.get_setting(c, sk.SIMPLEFIN_BACKFILL)), {"seen": ["A1"], "done": []})   # its history again
        self.assertEqual(simplefin.store_payload(c, self.simplefin_payload(), TODAY), ["A1|t1"])
        self.assertEqual(self.count(Account), 1)
        self.assertEqual(api.api_accounts_deleted(c, {}, {}), [])
        with self.assertRaises(ApiError) as e:
            api.api_account_restore(c, {}, {}, "A1")
        self.assertEqual(e.exception.status, 404)

    def count(self, model):
        return self.conn.execute(select(func.count()).select_from(model)).scalar()

    def plaid_replies(self, path, body):
        if path == "/accounts/get":
            return {"item": {"institution_name": "Chase"}, "accounts": [
                {"account_id": "p-cc", "name": "Freedom", "mask": "9999", "type": "credit", "subtype": "credit card",
                 "balances": {"current": 50.0}}]}
        if path == "/transactions/sync":
            return {"next_cursor": "c1", "has_more": False, "modified": [], "removed": [], "added": [
                {"transaction_id": "x1", "account_id": "p-cc", "date": "2026-09-20", "amount": 12.0, "name": "Cafe"}]}
        if path == "/liabilities/get":
            return {"liabilities": {"credit": [{"account_id": "p-cc", "last_statement_balance": 40.0,
                                                "last_statement_issue_date": "2026-09-05", "next_payment_due_date": "2026-10-02"}]}}
        raise AssertionError(path)

    def plaid_sync(self):
        with mock.patch.object(plaidbank, "call", side_effect=lambda _c, path, body: self.plaid_replies(path, body)):
            return plaidbank.sync_item(self.conn, "item", TODAY)

    def test_plaid_bank_account(self):
        c = self.conn
        c.execute(insert(PlaidItem).values(item_id="item", access_token="x", institution_name="Chase", products="transactions,liabilities"))
        self.plaid_sync()
        plaidbank.match(c, "p-cc", "new", TODAY)
        self.plaid_sync()
        self.assertEqual((self.count(Account), self.count(Transaction), self.count(CardStatement)), (1, 1, 1))
        api.api_account_remove(c, {}, {}, "pl:p-cc")
        self.plaid_sync()
        self.assertEqual((self.count(Account), self.count(Transaction), self.count(CardStatement)), (0, 0, 0))
        self.assertEqual(c.execute(select(PlaidAccount.ignored)).scalar(), 1)
        api.api_account_restore(c, {}, {}, "pl:p-cc")
        self.assertEqual(c.execute(select(Account.id)).scalar(), "pl:p-cc")   # back now; its history with the next sync
        self.assertIsNone(c.execute(select(PlaidItem.cursor)).scalar())
        self.plaid_sync()
        self.assertEqual((self.count(Account), self.count(Transaction), self.count(CardStatement)), (1, 1, 1))

    def test_choosing_a_deleted_plaid_account_again_restores_it(self):
        c = self.conn
        c.execute(insert(PlaidItem).values(item_id="item", access_token="x", products="transactions,liabilities"))
        self.plaid_sync()
        plaidbank.match(c, "p-cc", "new", TODAY)
        api.api_account_remove(c, {}, {}, "pl:p-cc")
        plaidbank.match(c, "p-cc", "new", TODAY)   # from the ignored accounts, under “New from Plaid”
        self.assertEqual(api.api_accounts_deleted(c, {}, {}), [])

    def test_plaid_investment_account(self):
        c = self.conn
        c.execute(insert(PlaidItem).values(item_id="inv", access_token="x", institution_name="Wealthfront", products="investments"))
        holdings = {"accounts": [{"account_id": "w1", "name": "Roth IRA", "type": "investment", "balances": {"current": 1500.0}}],
                    "securities": [{"security_id": "s1", "ticker_symbol": "VTI", "type": "etf"}],
                    "holdings": [{"account_id": "w1", "security_id": "s1", "quantity": 5, "institution_value": 1500}]}
        activity = {"total_investment_transactions": 1, "investment_transactions": [
            {"investment_transaction_id": "t1", "account_id": "w1", "security_id": "s1", "date": "2026-09-01", "type": "buy"}]}

        def sync():
            with mock.patch.object(plaid, "call", side_effect=lambda _c, path, body: holdings if "holdings" in path else activity):
                plaid.sync_item(c, "inv", TODAY)
        sync()
        self.assertEqual(c.execute(select(Account.id)).scalar(), "pl:w1")
        api.api_account_remove(c, {}, {}, "pl:w1")
        sync()
        self.assertEqual([self.count(m) for m in (Account, InvAccount, Holding, InvSnapshot, InvTransaction)], [0, 0, 0, 0, 0])
        api.api_account_restore(c, {}, {}, "pl:w1")
        sync()
        self.assertEqual([self.count(m) for m in (Account, InvAccount, Holding, InvSnapshot, InvTransaction)], [1, 1, 1, 1, 1])
