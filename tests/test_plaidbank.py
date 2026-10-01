"""Banks and cards through Plaid: matching accounts, choosing the provider per account, transactions without
duplicates, and card statements from the bank in the forecast."""
import json
import os
import threading
import unittest
from datetime import date
from http.server import BaseHTTPRequestHandler, HTTPServer

import sqlalchemy.exc
from sqlalchemy import delete, insert, select, update

from runway import db, forecast, plaid, plaidbank, simplefin
from runway.models import (Account, CardStatement, Holding, InvAccount, InvSnapshot, InvTransaction, LoanTerms,
                           Merchant, Override, PlaidAccount, PlaidItem, Security, Transaction)
from tests.shared import DbCase

TODAY = date(2026, 9, 23)


class MockBank(BaseHTTPRequestHandler):
    """A Plaid bank connection: a checking account and two cards (one has "1234" in its SimpleFIN name)."""
    products = ["transactions", "liabilities"]
    reject_redirect = False
    pages: list = []          # /transactions/sync responses, served in order
    calls: list = []
    drop: set = set()         # paths whose connection is dropped without a reply
    fail: dict = {}           # path -> (status, body): Plaid refuses
    during_sync = None        # run while Plaid answers /transactions/sync
    accounts: list | None = None   # /accounts/get's accounts instead of the usual three
    loans: dict = {}          # /liabilities/get's mortgage and student loans

    def log_message(self, *a):
        pass

    def reply(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        MockBank.calls.append((self.path, req))
        if self.path in MockBank.drop:
            self.close_connection = True
            return
        if self.path in MockBank.fail:
            return self.reply(*MockBank.fail[self.path])
        if self.path == "/transactions/sync" and MockBank.during_sync:
            MockBank.during_sync()
        if self.path == "/link/token/create":
            if req.get("redirect_uri") and MockBank.reject_redirect:
                return self.reply(400, {"error_code": "INVALID_FIELD",
                                        "error_message": "OAuth redirect URI must be configured in the developer dashboard"})
            if "transactions" in req.get("products", []) and "transactions" not in MockBank.products:
                return self.reply(400, {"error_code": "INVALID_PRODUCT", "error_message": "transactions is not enabled"})
            return self.reply(200, {"link_token": "link-1"})
        if self.path == "/item/public_token/exchange":
            return self.reply(200, {"access_token": "access-b", "item_id": "item-b"})
        if self.path in ("/item/remove", "/transactions/refresh"):
            return self.reply(200, {})
        if self.path == "/item/get":
            return self.reply(200, {"item": {"item_id": "item-b", "products": MockBank.products, "billed_products": MockBank.products}})
        if self.path == "/accounts/get" and MockBank.accounts is not None:
            return self.reply(200, {"item": {"institution_name": "Chase"}, "accounts": MockBank.accounts})
        if self.path == "/accounts/get":
            return self.reply(200, {"item": {"institution_name": "Chase"}, "accounts": [
                {"account_id": "p-chk", "name": "Checking", "mask": "0001", "type": "depository", "subtype": "checking",
                 "balances": {"current": 2500.0, "available": 2400.0}},
                {"account_id": "p-csp", "name": "Sapphire Preferred", "mask": "1234", "type": "credit", "subtype": "credit card",
                 "balances": {"current": 812.34}},
                {"account_id": "p-new", "name": "Freedom", "mask": "9999", "type": "credit", "subtype": "credit card",
                 "balances": {"current": 50.0}},
            ]})
        if self.path == "/transactions/sync":
            page = MockBank.pages.pop(0) if MockBank.pages else {"added": [], "modified": [], "removed": [], "has_more": False}
            return self.reply(200, {"next_cursor": f"c{len(MockBank.calls)}", "has_more": False, "added": [], "modified": [],
                                    "removed": [], **page})
        if self.path == "/liabilities/get":
            return self.reply(200, {"liabilities": {"credit": [
                {"account_id": "p-csp", "last_statement_balance": 640.5, "last_statement_issue_date": "2026-09-05",
                 "next_payment_due_date": "2026-10-02", "minimum_payment_amount": 35, "last_payment_amount": 700,
                 "last_payment_date": "2026-08-30", "is_overdue": False,
                 "aprs": [{"apr_type": "cash_apr", "apr_percentage": 29.99, "balance_subject_to_apr": None},
                          {"apr_type": "purchase_apr", "apr_percentage": 24.99, "balance_subject_to_apr": 640.5}]}],
                **MockBank.loans}})
        self.reply(404, {"error_code": "NOT_FOUND"})


def tx(tid, acct, day, amount, name, pending=False, pending_id=None):
    return {"transaction_id": tid, "account_id": acct, "date": day, "amount": amount, "name": name, "merchant_name": name,
            "pending": pending, "pending_transaction_id": pending_id}


class PlaidBankTests(DbCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), MockBank)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        os.environ["RUNWAY_PLAID_URL"] = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        os.environ.pop("RUNWAY_PLAID_URL", None)

    def setUp(self):
        super().setUp()
        MockBank.products = ["transactions", "liabilities"]
        MockBank.pages, MockBank.calls, MockBank.reject_redirect = [], [], False
        MockBank.drop, MockBank.fail, MockBank.during_sync = set(), {}, None
        MockBank.accounts, MockBank.loans = None, {}
        db.set_setting(self.c, "plaid_client_id", "cid"); db.set_setting(self.c, "plaid_secret", "sec")
        # What SimpleFIN already brought in: checking and a card, with some history.
        self.c.execute(insert(Account).values(id="sf-chk", name="Chase Checking", kind="checking", balance=2500))
        self.c.execute(insert(Account).values(id="sf-csp", name="CSP ...1234", kind="credit", balance=-812.34,
                                              pay_from="sf-chk"))
        self.c.execute(insert(Transaction), [{"id": "sf-chk|1", "account_id": "sf-chk", "posted": "2026-08-01",
                                              "amount": -40.0, "description": "OLD GROCERY", "payee": "Old Grocery",
                                              "category": "Groceries"},
                                             {"id": "sf-chk|2", "account_id": "sf-chk", "posted": "2026-09-20",
                                              "amount": -12.5, "description": "COFFEE", "payee": "Coffee",
                                              "category": "Coffee & Snacks"}])
        self.c.commit()

    def link(self):
        plaid.link_token(self.c, None, "bank")
        item_id = plaid.exchange(self.c, "public-1", {"name": "Chase"})
        return plaidbank.sync_item(self.c, item_id, TODAY)

    def test_link_asks_for_transactions_and_statements(self):
        self.link()
        body = next(b for p, b in MockBank.calls if p == "/link/token/create")
        self.assertEqual(body["products"], ["transactions"])
        self.assertEqual(body["optional_products"], ["liabilities"])
        self.assertEqual(body["transactions"]["days_requested"], 730)
        item = self.c.execute(select(PlaidItem.products)).fetchone()
        self.assertEqual(item["products"], "liabilities,transactions")

    def test_accounts_matched_by_mask_or_balance_the_rest_wait_for_you(self):
        r = self.link()
        got = {a["id"]: a["plaid_account_id"]
               for a in db.rows(self.c.execute(select(Account.id, Account.plaid_account_id)))}
        self.assertEqual(got["sf-csp"], "p-csp")   # "1234" in the name
        self.assertEqual(got["sf-chk"], "p-chk")   # the only balance of 2,500
        self.assertEqual(sorted(r["matched"]), ["CSP ...1234", "Chase Checking"])
        # Freedom matches nothing; you add it as its own account.
        plaidbank.match(self.c, "p-new", "new", TODAY)
        new = self.c.execute(select(Account).where(Account.id == "pl:p-new")).fetchone()
        self.assertEqual((new["kind"], new["provider"], new["owed_positive"], new["balance"]), ("credit", "plaid", 1, 50.0))
        # Nothing changes for matched accounts until you pick Plaid for them.
        self.assertEqual(self.c.execute(select(Account.provider)
                                        .where(Account.id == "sf-chk")).fetchone()[0], "simplefin")

    def test_redirect_for_banks_that_sign_in_on_their_own_site(self):
        os.environ["RUNWAY_PUBLIC_URL"] = "https://runway.example.com"
        try:
            plaid.link_token(self.c, None, "bank")
            self.assertEqual(MockBank.calls[-1][1]["redirect_uri"], "https://runway.example.com/plaid/oauth")
            # Not registered in the Plaid Dashboard yet: Link still opens, with pop-ups only.
            MockBank.reject_redirect = True
            self.assertEqual(plaid.link_token(self.c, None, "bank"), "link-1")
            self.assertNotIn("redirect_uri", MockBank.calls[-1][1])
        finally:
            os.environ.pop("RUNWAY_PUBLIC_URL", None)
        self.assertIsNone(plaid.redirect_uri(self.c))   # plain http: no redirect

    def test_resume_after_the_bank_sends_you_back(self):
        from runway import server
        with self.assertRaises(server.ApiError):
            server.api_plaid_oauth_resume(self.c, {}, {})
        r = server.api_plaid_link_token(self.c, {}, {"kind": "bank"})
        self.assertEqual(server.api_plaid_oauth_resume(self.c, {}, {}), {"link_token": r["link_token"], "kind": "bank", "item_id": None})

    def test_matching_by_initials_words_and_institution(self):
        c = self.c
        c.execute(delete(Account))
        for aid, name, bal in [("csr", "CSR", -1203.10), ("csp", "CSP (Sara)", -455.00), ("citi", "Citi Double Cash", -88.20),
                               ("boa", "BofA Premium Rewards", -310.00)]:
            c.execute(insert(Account).values(id=aid, name=name, kind="credit", balance=bal))
        def item(item_id, inst, accts):
            c.execute(insert(PlaidItem).values(item_id=item_id, access_token="t-" + item_id, institution_name=inst,
                                               products="liabilities"))
            for pid, name, official, mask, cur in accts:
                c.execute(insert(PlaidAccount).values(plaid_account_id=pid, item_id=item_id, name=name,
                                                      official_name=official, mask=mask, type="credit",
                                                      subtype="credit card", current=cur))
        # Balances differ a little from SimpleFIN's (pending charges), and none of the names carry the last 4 digits.
        item("chase", "Chase", [("p1", "Sapphire Preferred", "Chase Sapphire Preferred", "4417", 461.12),
                                ("p2", "Sapphire Reserve", "Chase Sapphire Reserve", "9921", 1203.10)])
        item("citi", "Citi", [("p3", "Citi Double Cash® Card", None, "0042", 91.00)])
        item("boa", "Bank of America", [("p4", "Premium Rewards Visa Signature", None, "7788", 300.00)])
        for i in ("chase", "citi", "boa"):
            plaidbank.auto_match(c, i)
        got = {r["id"]: r["plaid_account_id"] for r in c.execute(select(Account.id, Account.plaid_account_id))}
        self.assertEqual(got, {"csr": "p2", "csp": "p1", "citi": "p3", "boa": "p4"})

    def test_no_match_across_institutions_or_when_unsure(self):
        c = self.c
        c.execute(delete(Account))
        c.execute(insert(Account).values(id="citi", name="Citi Double Cash", kind="credit", balance=-50))
        c.execute(insert(Account).values(id="c1", name="Card one", kind="credit", balance=-20))
        c.execute(insert(Account).values(id="c2", name="Card two", kind="credit", balance=-20))
        c.execute(insert(PlaidItem).values(item_id="ch", access_token="t", institution_name="Chase",
                                           products="liabilities"))
        c.execute(insert(PlaidAccount).values(plaid_account_id="px", item_id="ch", name="Freedom Unlimited",
                                              type="credit", subtype="credit card", current=50))
        c.execute(insert(PlaidAccount).values(plaid_account_id="py", item_id="ch", name="Card", type="credit",
                                              subtype="credit card", current=20))
        self.assertEqual(plaidbank.auto_match(c, "ch"), [])   # same balance as the Citi card, but Chase; two equal "Card"s

    def test_switching_to_plaid_keeps_history_and_skips_duplicates(self):
        self.link()
        plaidbank.set_provider(self.c, "sf-chk", "plaid", TODAY)
        self.assertIsNone(self.c.execute(select(PlaidItem.cursor)).fetchone()[0])   # re-read from the start
        MockBank.pages = [{"added": [
            tx("a0", "p-chk", "2026-08-01", 40.0, "OLD GROCERY"),       # long before the switch: SimpleFIN has it
            tx("a1", "p-chk", "2026-09-21", 12.5, "COFFEE"),            # the same coffee, a day later at Plaid
            tx("a2", "p-chk", "2026-09-22", 3000.0 * -1, "PAYROLL"),     # new: money in
            tx("a3", "p-chk", "2026-09-23", 20.0, "LUNCH", pending=True),
            tx("c1", "p-csp", "2026-09-22", 99.0, "CARD CHARGE"),       # the card still uses SimpleFIN
        ]}]
        r = plaidbank.sync_item(self.c, "item-b", TODAY)
        rows = {t["id"]: t for t in db.rows(self.c.execute(select(Transaction)
                                                           .where(Transaction.account_id == "sf-chk")))}
        self.assertEqual(sorted(rows), ["sf-chk|1", "sf-chk|2", "sf-chk|pl:a2", "sf-chk|pl:a3"])
        self.assertEqual(rows["sf-chk|pl:a2"]["amount"], 3000.0)
        self.assertEqual(sorted(r["new"]), ["sf-chk|pl:a2", "sf-chk|pl:a3"])
        self.assertFalse(self.c.execute(select(Transaction.id).where(Transaction.account_id == "sf-csp")).fetchone())
        self.assertEqual(self.c.execute(select(Account.balance).where(Account.id == "sf-chk")).fetchone()[0], 2500.0)
        # The lunch posts: it replaces the pending one and keeps the category you gave it.
        self.c.execute(update(Transaction).where(Transaction.id == "sf-chk|pl:a3")
                       .values(category="Restaurants", category_source="manual", recurring_id=7, recurring_linked_by="you"))
        MockBank.pages = [{"added": [tx("a4", "p-chk", "2026-09-24", 21.0, "LUNCH", pending_id="a3")], "removed": [{"transaction_id": "a3"}]}]
        r = plaidbank.sync_item(self.c, "item-b", TODAY)
        self.assertEqual(r["new"], [])
        lunch = self.c.execute(select(Transaction).where(Transaction.id == "sf-chk|pl:a4")).fetchone()
        self.assertEqual((lunch["amount"], lunch["category"], lunch["pending"]), (-21.0, "Restaurants", 0))
        self.assertEqual((lunch["recurring_id"], lunch["recurring_linked_by"]), (7, "you"))   # and the link you made
        self.assertFalse(self.c.execute(select(Transaction.id).where(Transaction.id == "sf-chk|pl:a3")).fetchone())

    def test_merchants_and_their_logos_are_noted(self):
        self.link()
        plaidbank.set_provider(self.c, "sf-chk", "plaid", TODAY)
        joe = {**tx("m1", "p-chk", "2026-09-23", 4.5, "SQ *JOES"), "merchant_name": "Joe's Coffee",
               "merchant_entity_id": "ent-joe", "logo_url": "https://plaid-merchant-logos.plaid.com/joes.png",
               "website": "joescoffee.com"}
        MockBank.pages = [{"added": [joe, tx("m2", "p-chk", "2026-09-23", 9.0, "NO LOGO SHOP")]}]
        plaidbank.sync_item(self.c, "item-b", TODAY)
        ids = dict(self.c.execute(select(Transaction.id, Transaction.merchant_id)
                                  .where(Transaction.id.like("%pl:m%"))).fetchall())
        self.assertEqual(ids, {"sf-chk|pl:m1": "ent-joe", "sf-chk|pl:m2": None})
        m = self.c.execute(select(Merchant.name, Merchant.website, Merchant.logo_url)).fetchone()
        self.assertEqual(tuple(m), ("Joe's Coffee", "joescoffee.com", "https://plaid-merchant-logos.plaid.com/joes.png"))

    def test_simplefin_leaves_plaid_accounts_alone(self):
        self.link()
        plaidbank.set_provider(self.c, "sf-chk", "plaid", TODAY)
        payload = {"accounts": [{"id": "sf-chk", "name": "Chase Checking", "balance": "1.00", "currency": "USD",
                                 "transactions": [{"id": "9", "posted": 1790000000, "amount": "-5.00", "description": "SF ONLY"}]}]}
        self.assertEqual(simplefin.store_payload(self.c, payload, date(2026, 9, 1)), [])
        self.assertEqual(self.c.execute(select(Account.balance).where(Account.id == "sf-chk")).fetchone()[0], 2500.0)

    def test_switching_back_to_simplefin_matches_up_the_overlap(self):
        self.link()
        plaidbank.set_provider(self.c, "sf-chk", "plaid", date(2026, 9, 1))
        MockBank.pages = [{"added": [tx("b1", "p-chk", "2026-09-10", 30.0, "GAS")]}]
        plaidbank.sync_item(self.c, "item-b", TODAY)
        plaidbank.set_provider(self.c, "sf-chk", "simplefin", TODAY)
        ts = int(__import__("datetime").datetime(2026, 9, 11, 12).timestamp())
        payload = {"accounts": [{"id": "sf-chk", "name": "Chase Checking", "balance": "2400.00", "currency": "USD",
                                 "transactions": [{"id": "g", "posted": ts, "amount": "-30.00", "description": "GAS"},
                                                  {"id": "h", "posted": ts, "amount": "-8.00", "description": "SNACK"}]}]}
        new = simplefin.store_payload(self.c, payload, date(2026, 9, 9))
        self.assertEqual(new, ["sf-chk|h"])   # the gas is already here from Plaid

    def test_bank_statement_drives_the_card_forecast(self):
        self.link()
        fc = forecast.build(self.c, TODAY, 30)
        c = next(x for x in fc["cards"] if x["id"] == "sf-csp")
        self.assertEqual((c["last_close"], c["statement_balance"], c["statement_set"], c["due_date"], c["minimum_payment"]),
                         ("2026-09-05", 640.5, False, "2026-10-02", 35))
        ev = next(e for e in fc["events"] if e["kind"] == "card" and not e["estimated"])
        self.assertEqual((ev["date"], ev["amount"]), ("2026-10-02", -640.5))
        # A statement you enter yourself still wins.
        self.c.execute(insert(Override).values(key="stmt:sf-csp:2026-09-05", amount=600))
        c = next(x for x in forecast.build(self.c, TODAY, 30)["cards"] if x["id"] == "sf-csp")
        self.assertEqual((c["statement_balance"], c["statement_set"], c["statement_reported"]), (600, True, 640.5))

    def test_statements_asked_for_even_if_not_listed_when_linked(self):
        MockBank.products = ["transactions"]        # Liabilities was optional and didn't show up on the Item
        self.link()
        self.assertTrue(any(p == "/liabilities/get" for p, _ in MockBank.calls))
        self.assertIn("liabilities", self.c.execute(select(PlaidItem.products)).fetchone()[0])
        self.assertTrue(self.c.execute(select(CardStatement.plaid_account_id)
                                       .where(CardStatement.plaid_account_id == "p-csp")).fetchone())
        # the purchase APR, not the cash advance one
        self.assertEqual(self.c.execute(select(CardStatement.purchase_apr)).scalar(), 24.99)

    def test_a_cards_purchase_apr_is_kept_and_other_aprs_are_not(self):
        item = {"item_id": "it1"}
        card = {"account_id": "pc", "last_statement_balance": 100, "last_statement_issue_date": "2026-09-05"}
        apr = lambda: self.c.execute(select(CardStatement.purchase_apr).where(CardStatement.plaid_account_id == "pc")).scalar()
        plaidbank.store_statements(self.c, item, {"liabilities": {"credit": [{**card, "aprs": [
            {"apr_type": "balance_transfer_apr", "apr_percentage": 0.0}, {"apr_type": "special", "apr_percentage": 0.0},
            {"apr_type": "purchase_apr", "apr_percentage": "21.24"}]}]}})
        self.assertEqual(apr(), 21.24)
        # only a cash APR, or none at all: no purchase APR (and an old one doesn't linger)
        plaidbank.store_statements(self.c, item, {"liabilities": {"credit": [{**card, "aprs": [
            {"apr_type": "cash_apr", "apr_percentage": 29.99}]}]}})
        self.assertIsNone(apr())
        plaidbank.store_statements(self.c, item, {"liabilities": {"credit": [card]}})
        self.assertIsNone(apr())

    def test_loan_terms_from_liabilities(self):
        loan = lambda pid, subtype, owed: {"account_id": pid, "name": pid, "type": "loan", "subtype": subtype,
                                           "balances": {"current": owed}}
        MockBank.accounts = [loan("p-mtg", "mortgage", 250000.0), loan("p-stu", "student", 12000.0),
                             loan("p-car", "auto", 9000.0)]
        MockBank.loans = {
            "mortgage": [{"account_id": "p-mtg", "interest_rate": {"percentage": 6.25, "type": "fixed"},
                          "next_monthly_payment": 2140.5, "last_payment_amount": 2100, "maturity_date": "2052-05-01",
                          "origination_principal_amount": 300000}],
            "student": [{"account_id": "p-stu", "interest_rate_percentage": 4.5, "minimum_payment_amount": None,
                         "last_payment_amount": 180.25, "expected_payoff_date": "2032-06-15"},
                        {"account_id": "p-gone", "interest_rate_percentage": None, "minimum_payment_amount": None,
                         "expected_payoff_date": None}]}
        MockBank.products = ["transactions"]        # Liabilities wasn't listed on the Item, and there's no card
        out = self.link()
        self.assertTrue(any(p == "/liabilities/get" for p, _ in MockBank.calls))
        self.assertEqual(out["loans"], 3)
        got = {r["plaid_account_id"]: tuple(r)[1:6] for r in self.c.execute(
            select(LoanTerms.plaid_account_id, LoanTerms.item_id, LoanTerms.kind, LoanTerms.interest_rate,
                   LoanTerms.monthly_payment, LoanTerms.maturity_date))}
        self.assertEqual(got, {"p-mtg": ("item-b", "mortgage", 6.25, 2140.5, "2052-05-01"),
                               "p-stu": ("item-b", "student", 4.5, 180.25, "2032-06-15"),   # the last payment, with no minimum
                               "p-gone": ("item-b", "student", None, None, None)})   # auto loans aren't in Liabilities
        # The next sync updates them in place.
        MockBank.loans["mortgage"][0]["next_monthly_payment"] = 2150
        plaidbank.sync_item(self.c, "item-b", TODAY)
        self.assertEqual(self.c.execute(select(LoanTerms.monthly_payment).where(LoanTerms.plaid_account_id == "p-mtg")).scalar(), 2150)
        plaidbank.forget_item(self.c, "item-b")
        self.assertIsNone(self.c.execute(select(LoanTerms.plaid_account_id)).fetchone())

    def test_liabilities_not_asked_for_without_a_card_mortgage_or_student_loan(self):
        MockBank.accounts = [{"account_id": "p-car", "name": "Auto", "type": "loan", "subtype": "auto", "balances": {"current": 9000.0}},
                             {"account_id": "p-chk", "name": "Checking", "type": "depository", "subtype": "checking",
                              "balances": {"current": 10.0}}]
        MockBank.products = ["transactions"]
        self.link()
        self.assertFalse(any(p == "/liabilities/get" for p, _ in MockBank.calls))

    def test_a_mortgage_at_a_bank_without_liabilities_leaves_a_note_not_an_error(self):
        MockBank.accounts = [{"account_id": "p-mtg", "name": "Mortgage", "type": "loan", "subtype": "mortgage",
                              "balances": {"current": 250000.0}}]
        MockBank.products = ["transactions"]
        MockBank.fail = {"/liabilities/get": (400, {"error_code": "PRODUCTS_NOT_SUPPORTED", "error_message": "no"})}
        out = self.link()
        self.assertNotIn("error", out)
        self.assertEqual(db.get_setting(self.c, "plaid_stmt_note:item-b"), "PRODUCTS_NOT_SUPPORTED")
        self.assertIsNone(self.c.execute(select(LoanTerms.plaid_account_id)).fetchone())

    def test_statements_only_when_transactions_isnt_enabled(self):
        MockBank.products = ["liabilities"]
        with self.assertRaises(plaid.PlaidError):
            plaid.link_token(self.c, None, "bank")
        plaid.link_token(self.c, None, "cards")
        item_id = plaid.exchange(self.c, "public-1", {"name": "Chase"})
        r = plaidbank.sync_item(self.c, item_id, TODAY)
        self.assertEqual((r["statements"], r["new"]), (1, []))
        self.assertFalse(any(p == "/transactions/sync" for p, _ in MockBank.calls))
        with self.assertRaises(ValueError):
            plaidbank.set_provider(self.c, "sf-chk", "plaid", TODAY)

    def test_removing_the_connection_goes_back_to_simplefin(self):
        self.link()
        plaidbank.set_provider(self.c, "sf-chk", "plaid", TODAY)
        plaid.remove_item(self.c, "item-b")
        a = self.c.execute(select(Account.provider, Account.plaid_account_id).where(Account.id == "sf-chk")).fetchone()
        self.assertEqual((a["provider"], a["plaid_account_id"]), ("simplefin", None))
        self.assertFalse(self.c.execute(select(CardStatement.plaid_account_id)).fetchone())


    def test_a_dropped_connection_or_timeout_is_a_plaid_error(self):
        from unittest import mock
        MockBank.drop = {"/accounts/get"}
        with self.assertRaises(plaid.PlaidError):
            plaid.call(self.c, "/accounts/get", {})
        for exc in (TimeoutError("timed out"), ConnectionResetError()):
            with mock.patch("urllib.request.urlopen", side_effect=exc), self.assertRaises(plaid.PlaidError):
                plaid.call(self.c, "/accounts/get", {})

    def test_a_link_is_never_lost_after_the_token_exchange(self):
        # Plaid made the connection (and bills for it), then /item/get never answered: the token is still kept, and the
        # connection is the kind you linked, so the bank sync picks it up.
        MockBank.drop = {"/item/get"}
        item_id = plaid.exchange(self.c, "public-1", {"name": "Chase"}, "bank")
        item = self.c.execute(select(PlaidItem.access_token, PlaidItem.products)
                              .where(PlaidItem.item_id == item_id)).fetchone()
        self.assertTrue(item["access_token"])
        self.assertEqual(item["products"], "transactions")
        self.assertTrue(plaidbank.is_bank_item(item))
        # With /item/get answering, its products win.
        MockBank.drop = set()
        plaid.exchange(self.c, "public-1", {"name": "Chase"}, "investments")
        self.assertEqual(self.c.execute(select(PlaidItem.products)).fetchone()[0], "liabilities,transactions")

    def test_one_failing_connection_doesnt_stop_the_others(self):
        self.link()
        self.c.execute(insert(PlaidItem).values(item_id="item-a", access_token="access-a", institution_name="Ally",
                                                products="transactions"))
        MockBank.drop = {"/accounts/get"}
        out = plaidbank.sync_all(self.c, TODAY)
        self.assertEqual((out["items"], len(out["errors"])), (0, 2))
        self.assertTrue(self.c.execute(select(PlaidItem.error).where(PlaidItem.item_id == "item-a")).fetchone()[0])

    def test_refresh_asks_each_transactions_connection(self):
        self.link()
        self.c.execute(insert(PlaidItem).values(item_id="item-c", access_token="access-c", institution_name="Amex",
                                                products="liabilities"))   # statements only: nothing to refresh
        MockBank.calls = []
        self.assertEqual(plaidbank.refresh_all(self.c), [])
        self.assertEqual([p for p, _ in MockBank.calls], ["/transactions/refresh"])
        MockBank.fail = {"/transactions/refresh": (400, {"error_code": "PRODUCTS_NOT_SUPPORTED", "error_message": "no"})}
        self.assertEqual(len(plaidbank.refresh_all(self.c)), 1)

    def test_matching_an_account_added_as_its_own_retires_the_copy(self):
        self.link()
        plaidbank.match(self.c, "p-new", "new", TODAY)
        MockBank.pages = [{"added": [tx("f1", "p-new", "2026-09-20", 25.0, "BOOKS")]}]
        plaidbank.sync_item(self.c, "item-b", TODAY)
        self.c.execute(update(Transaction).where(Transaction.id == "pl:p-new|pl:f1").values(category="Shopping"))
        # It turns out to be a card SimpleFIN already has.
        self.c.execute(insert(Account).values(id="sf-free", name="Freedom", kind="credit", balance=-50))
        self.c.execute(insert(Transaction).values(id="sf-free|1", account_id="sf-free", posted="2026-09-21",
                                                  amount=-25.0, description="BOOKS"))
        plaidbank.match(self.c, "p-new", "sf-free", TODAY)
        own = self.c.execute(select(Account.hidden, Account.plaid_account_id)
                             .where(Account.id == "pl:p-new")).fetchone()
        self.assertEqual((own["hidden"], own["plaid_account_id"]), (1, None))
        self.assertEqual(self.c.execute(select(Transaction.category)
                                        .where(Transaction.id == "sf-free|1")).fetchone()[0], "Shopping")
        # Syncing no longer updates the copy.
        MockBank.pages = [{"added": [tx("f2", "p-new", "2026-09-22", 5.0, "MORE BOOKS")]}]
        plaidbank.sync_item(self.c, "item-b", TODAY)
        self.assertFalse(self.c.execute(select(Transaction.id).where(Transaction.id.like("%pl:f2"))).fetchone())
        # Adding it as its own again brings the copy back.
        plaidbank.match(self.c, "p-new", "new", TODAY)
        own = self.c.execute(select(Account.hidden, Account.plaid_account_id)
                             .where(Account.id == "pl:p-new")).fetchone()
        self.assertEqual((own["hidden"], own["plaid_account_id"]), (0, "p-new"))
        self.assertIsNone(self.c.execute(select(Account.plaid_account_id).where(Account.id == "sf-free")).fetchone()[0])

    def test_an_account_you_dont_use_isnt_synced(self):
        self.link()
        plaidbank.match(self.c, "p-new", "new", TODAY)
        plaidbank.match(self.c, "p-new", "ignore", TODAY)
        self.assertEqual(self.c.execute(select(Account.hidden).where(Account.id == "pl:p-new")).fetchone()[0], 1)
        MockBank.pages = [{"added": [tx("i1", "p-new", "2026-09-22", 5.0, "IGNORED")]}]
        plaidbank.sync_item(self.c, "item-b", TODAY)
        self.assertFalse(self.c.execute(select(Transaction.id).where(Transaction.id.like("%pl:i1"))).fetchone())

    def test_an_account_already_linked_cant_be_linked_again(self):
        self.link()   # sf-csp is linked to p-csp
        with self.assertRaises(ValueError):
            plaidbank.match(self.c, "p-new", "sf-csp", TODAY)
        self.assertEqual(self.c.execute(select(Account.plaid_account_id)
                                        .where(Account.id == "sf-csp")).fetchone()[0], "p-csp")
        self.assertEqual(self.c.execute(select(PlaidAccount.ignored)
                                        .where(PlaidAccount.plaid_account_id == "p-new")).fetchone()[0], 0)
        plaidbank.match(self.c, "p-csp", "sf-csp", TODAY)   # the same link again is fine
        plaidbank.match(self.c, "p-csp", "", TODAY)         # unlinked: free for another
        plaidbank.match(self.c, "p-new", "sf-csp", TODAY)
        self.assertEqual(self.c.execute(select(Account.plaid_account_id)
                                        .where(Account.id == "sf-csp")).fetchone()[0], "p-new")

    def test_one_plaid_account_is_one_of_your_accounts(self):
        self.link()
        with self.assertRaises(sqlalchemy.exc.IntegrityError):   # the unique index, on either database
            self.c.execute(update(Account).where(Account.id == "sf-chk").values(plaid_account_id="p-csp"))

    def test_no_write_lock_is_held_while_plaid_answers(self):
        if os.environ.get("DATABASE_URL"):
            self.skipTest("SQLite's database-wide write lock")
        import sqlite3
        self.link()
        plaidbank.set_provider(self.c, "sf-chk", "plaid", TODAY)
        self.c.commit()
        blocked = []

        def write_elsewhere():   # what an edit in the app, or the extension, does meanwhile
            dbapi_conn = sqlite3.connect(self.path, timeout=0.2)
            try:
                dbapi_conn.execute("UPDATE settings SET value=value WHERE key='plaid_client_id'")
                dbapi_conn.commit()
            except sqlite3.OperationalError as e:
                blocked.append(str(e))
            finally:
                dbapi_conn.close()
        MockBank.during_sync = write_elsewhere
        MockBank.pages = [{"added": [tx("w1", "p-chk", "2026-09-23", 5.0, "SNACK")]}]
        plaidbank.sync_item(self.c, "item-b", TODAY)
        self.assertEqual(blocked, [])

    def test_a_statement_failure_keeps_the_new_transactions(self):
        self.link()
        plaidbank.set_provider(self.c, "sf-chk", "plaid", TODAY)
        MockBank.fail = {"/liabilities/get": (500, {"error_code": "INTERNAL_SERVER_ERROR", "error_message": "oops"})}
        MockBank.pages = [{"added": [tx("s1", "p-chk", "2026-09-23", 7.0, "TACOS")]}]
        out = plaidbank.sync_all(self.c, TODAY)
        self.assertEqual(out["new"], ["sf-chk|pl:s1"])
        self.assertEqual(len(out["errors"]), 1)
        self.assertEqual(self.c.execute(select(PlaidItem.error)).fetchone()[0], "INTERNAL_SERVER_ERROR")
        self.assertTrue(self.c.execute(select(Transaction.id).where(Transaction.id == "sf-chk|pl:s1")).fetchone())

    def test_holds_dropped_while_reading_everything_again_are_removed(self):
        self.link()
        plaidbank.set_provider(self.c, "sf-chk", "plaid", TODAY)
        MockBank.pages = [{"added": [tx("h1", "p-chk", "2026-09-22", 60.0, "HOTEL HOLD", pending=True),
                                     tx("h2", "p-chk", "2026-09-22", 9.0, "LUNCH", pending=True)]}]
        plaidbank.sync_item(self.c, "item-b", TODAY)
        plaidbank._reread(self.c, "item-b")   # e.g. another account switched to Plaid
        MockBank.pages = [{"added": [tx("h2", "p-chk", "2026-09-22", 9.0, "LUNCH", pending=True)]}]
        plaidbank.sync_item(self.c, "item-b", TODAY)
        ids = {r[0] for r in self.c.execute(select(Transaction.id).where(Transaction.id.like("%|pl:%")))}
        self.assertEqual(ids, {"sf-chk|pl:h2"})

    def test_syncing_a_connection_waits_for_a_running_sync(self):
        from runway import server
        self.link()
        self.assertTrue(server._sync_lock.acquire(blocking=False))
        try:
            with self.assertRaises(server.ApiError) as e:
                server.api_plaid_item_sync(self.c, {}, {}, "item-b")
            self.assertEqual(e.exception.status, 409)
        finally:
            server._sync_lock.release()
        self.assertTrue(server.api_plaid_item_sync(self.c, {}, {}, "item-b")["ok"])

    # ---------------------------------------------------------------------------------------- pinned behavior
    # What Settings → Connections shows, and the less common sync paths, exactly as they are.

    def test_status_shows_each_connection_and_its_accounts(self):
        from runway import server
        self.link()
        plaidbank.match(self.c, "p-new", "new", TODAY)
        self.c.execute(update(Account).where(Account.id == "sf-csp").values(owner="Sara", display_name="Sapphire"))
        self.c.execute(insert(PlaidItem).values(item_id="inv", access_token="tok", institution_name="Wealthfront",
                                                products="investments", env="sandbox"))
        self.c.execute(insert(InvAccount), [{"id": "w1", "item_id": "inv", "name": "Roth IRA", "official_name": "Roth",
                                             "subtype": "roth", "mask": "3639", "balance": 1000},
                                            {"id": "w2", "item_id": "inv", "name": "Individual", "official_name": None,
                                             "subtype": "brokerage", "mask": None, "balance": 5}])
        self.c.execute(insert(InvAccount).values(id="sfi", item_id="sf", name="Old", source="simplefin"))
        self.c.execute(insert(Account).values(id="sf-wf", name="Wealthfront Roth", org="Wealthfront",
                                              kind="investment", balance=990))
        self.c.execute(insert(Account).values(id="sf-v", name="Vanguard", org="Vanguard", kind="investment", balance=1))
        self.c.execute(update(InvAccount).where(InvAccount.id == "w1").values(account_id="sf-wf"))
        s = server.api_plaid_status(self.c, {}, {})
        self.assertEqual([it["item_id"] for it in s["items"]], ["item-b", "inv"])
        bank, inv = s["items"]
        self.assertEqual(list(bank), ["item_id", "institution_name", "env", "created_at", "last_sync", "error", "products",
                                      "bank", "duplicates", "accounts"])
        self.assertEqual((bank["bank"], bank["products"], bank["duplicates"]), (True, ["liabilities", "transactions"], []))
        self.assertEqual(bank["accounts"], [   # by type, then name
            {"id": "p-new", "name": "Freedom", "official_name": None, "subtype": "credit card", "type": "credit", "mask": "9999",
             "balance": 50.0, "ignored": 0, "account_id": "pl:p-new", "account_name": "Freedom ••9999", "provider": "plaid",
             "last_statement_date": None, "last_statement_balance": None, "next_due_date": None},
            {"id": "p-csp", "name": "Sapphire Preferred", "official_name": None, "subtype": "credit card", "type": "credit",
             "mask": "1234", "balance": 812.34, "ignored": 0, "account_id": "sf-csp", "account_name": "Sapphire (Sara)",
             "provider": "simplefin", "last_statement_date": "2026-09-05", "last_statement_balance": 640.5, "next_due_date": "2026-10-02"},
            {"id": "p-chk", "name": "Checking", "official_name": None, "subtype": "checking", "type": "depository", "mask": "0001",
             "balance": 2500.0, "ignored": 0, "account_id": "sf-chk", "account_name": "Chase Checking", "provider": "simplefin",
             "last_statement_date": None, "last_statement_balance": None, "next_due_date": None}])
        self.assertEqual((inv["bank"], inv["products"], inv["env"]), (False, ["investments"], "sandbox"))
        self.assertEqual(inv["accounts"], [
            {"id": "w2", "name": "Individual", "official_name": None, "subtype": "brokerage", "mask": None, "balance": 5.0,
             "hidden": 0, "account_id": None},
            {"id": "w1", "name": "Roth IRA", "official_name": "Roth", "subtype": "roth", "mask": "3639", "balance": 1000.0,
             "hidden": 0, "account_id": "sf-wf"}])
        self.assertEqual(inv["candidates"], [{"id": "sf-wf", "name": "Wealthfront Roth", "display_name": None, "org": "Wealthfront",
                                              "balance": 990.0, "linked_to": "w1"}])
        self.assertEqual((s["inv_accounts"], s["configured"], s["env"], s["client_id"]), (3, True, "production", "cid"))

    def test_status_counts_what_waits_for_you(self):
        self.link()   # Freedom matches nothing
        self.c.execute(insert(PlaidItem).values(item_id="inv", access_token="tok", products="investments"))
        self.c.execute(insert(InvAccount), [{"id": "w1", "item_id": "inv", "name": "Roth IRA"},
                                            {"id": "w2", "item_id": "inv", "name": "Other"}])
        self.c.execute(update(InvAccount).where(InvAccount.id == "w2").values(account_id="x"))
        self.assertEqual(plaid.undecided_count(self.c), 2)
        plaidbank.match(self.c, "p-new", "ignore", TODAY)
        self.assertEqual(plaid.undecided_count(self.c), 1)

    def test_inv_account_hidden_and_match_goes_to_the_right_kind(self):
        from runway import server
        self.link()
        self.c.execute(insert(PlaidItem).values(item_id="inv", access_token="tok", institution_name="Wealthfront"))
        self.c.execute(insert(InvAccount).values(id="w1", item_id="inv", name="Roth IRA", mask="3639", balance=7))
        self.assertEqual(server.api_inv_account(self.c, {}, {"hidden": True}, "w1"), {"ok": True})
        self.assertEqual(self.c.execute(select(InvAccount.hidden).where(InvAccount.id == "w1")).fetchone()[0], 1)
        server.api_inv_account(self.c, {}, {}, "w1")
        self.assertEqual(self.c.execute(select(InvAccount.hidden).where(InvAccount.id == "w1")).fetchone()[0], 0)
        self.assertEqual(server.api_plaid_match(self.c, {}, {"plaid_account_id": "w1", "target": "new"}),
                         {"ok": True, "account_id": "pl:w1"})
        a = dict(self.c.execute(select(Account.name, Account.org, Account.kind, Account.balance, Account.provider,
                                       Account.hidden)
                                .where(Account.id == "pl:w1")).fetchone())
        self.assertEqual(a, {"name": "Roth IRA ••3639", "org": "Wealthfront", "kind": "investment", "balance": 7.0,
                             "provider": "plaid", "hidden": 0})
        self.assertEqual(server.api_plaid_match(self.c, {}, {"plaid_account_id": "p-new", "target": "ignore"}), {"ok": True})
        with self.assertRaises(server.ApiError):
            server.api_plaid_match(self.c, {}, {"plaid_account_id": "nope", "target": "new"})

    def test_bank_sync_names_the_connection_and_drops_removed_transactions(self):
        plaid.link_token(self.c, None, "bank")
        item_id = plaid.exchange(self.c, "public-1", None)
        self.assertIsNone(self.c.execute(select(PlaidItem.institution_name)).fetchone()[0])
        plaidbank.sync_item(self.c, item_id, TODAY)
        self.assertEqual(self.c.execute(select(PlaidItem.institution_name)).fetchone()[0], "Chase")
        plaidbank.set_provider(self.c, "sf-chk", "plaid", TODAY)
        MockBank.pages = [{"added": [tx("r1", "p-chk", "2026-09-22", 5.0, "A"), tx("r2", "p-chk", "2026-09-22", 6.0, "B")]}]
        plaidbank.sync_item(self.c, item_id, TODAY)
        MockBank.pages = [{"modified": [tx("r2", "p-chk", "2026-09-23", 7.0, "B2")], "removed": [{"transaction_id": "r1"}]}]
        self.assertEqual(plaidbank.sync_item(self.c, item_id, TODAY)["new"], [])
        got = [tuple(r) for r in self.c.execute(select(Transaction.id, Transaction.posted, Transaction.amount,
                                                       Transaction.description)
                                                .where(Transaction.id.like("%|pl:%")))]
        self.assertEqual(got, [("sf-chk|pl:r2", "2026-09-23", -7.0, "B2")])
        self.assertEqual(plaidbank.statement(self.c, "sf-csp", TODAY)["last_statement_balance"], 640.5)
        self.assertIsNone(plaidbank.statement(self.c, "sf-csp", date(2026, 9, 1)))
        self.assertIsNone(plaidbank.statement(self.c, "sf-chk", TODAY))

    def test_statement_notes_and_errors_on_the_connection(self):
        self.link()
        MockBank.fail = {"/liabilities/get": (400, {"error_code": "PRODUCTS_NOT_SUPPORTED", "error_message": "no"})}
        out = plaidbank.sync_item(self.c, "item-b", TODAY)
        self.assertNotIn("error", out)
        self.assertEqual(db.get_setting(self.c, "plaid_stmt_note:item-b"), "PRODUCTS_NOT_SUPPORTED")
        self.assertIsNone(self.c.execute(select(PlaidItem.error)).fetchone()[0])
        MockBank.fail = {"/accounts/get": (400, {"error_code": "ITEM_LOGIN_REQUIRED", "error_message": "log in"})}
        with self.assertRaises(plaid.PlaidError):
            plaidbank.sync_item(self.c, "item-b", TODAY)
        self.assertEqual(self.c.execute(select(PlaidItem.error)).fetchone()[0], "ITEM_LOGIN_REQUIRED")
        with self.assertRaises(plaid.PlaidError):
            plaidbank.sync_item(self.c, "nope", TODAY)

    def test_investment_sync_stores_holdings_snapshots_and_activity(self):
        from unittest import mock
        self.c.execute(insert(PlaidItem).values(item_id="inv", access_token="tok", products="investments"))
        self.c.execute(insert(Security).values(id="s1", ticker="VTI", name="Old name", close_price=200,
                                               close_as_of="2026-09-01", sector="Mixed"))
        holdings = {"item": {"institution_name": "Wealthfront"}, "accounts": [
            {"account_id": "w1", "name": "Roth IRA", "type": "investment", "subtype": "roth", "mask": "3639",
             "balances": {"current": 1500.0}},
            {"account_id": "w2", "name": "Cash", "type": "investment", "subtype": "cash management",
             "balances": {"current": 42.0, "iso_currency_code": "USD"}}],
            "securities": [{"security_id": "s1", "ticker_symbol": "VTI", "name": "Vanguard Total", "type": "etf",
                            "close_price": None, "sector": None},
                           {"security_id": "s2", "name": "Cash", "type": "cash", "close_price": 1}],
            "holdings": [{"account_id": "w1", "security_id": "s1", "quantity": 5, "institution_price": 300,
                          "institution_value": 1500, "cost_basis": 1000}]}
        pages = [{"total_investment_transactions": 3, "investment_transactions": [
                    {"investment_transaction_id": "t1", "account_id": "w1", "security_id": "s1", "date": "2026-09-01",
                     "name": "BUY", "type": "buy", "subtype": "buy", "quantity": 5, "amount": 1000, "price": 200},
                    {"investment_transaction_id": "t2", "account_id": "w2", "date": "2026-09-02", "name": "DEP",
                     "type": "cash", "subtype": "deposit", "amount": -42}]},
                 {"total_investment_transactions": 3, "securities": [{"security_id": "s3", "ticker_symbol": "X", "type": "equity"}],
                  "investment_transactions": [{"investment_transaction_id": "t3", "account_id": "w1", "security_id": "s3",
                                               "date": "2026-09-03", "type": "fee", "fees": 1.5}]}]
        seen = []

        def call(_conn, path, body):
            seen.append((path, body.get("options")))
            return holdings if path == "/investments/holdings/get" else pages[body["options"]["offset"] // 2]
        with mock.patch.object(plaid, "call", side_effect=call):
            out = plaid.sync_item(self.c, "inv", TODAY)
        self.assertEqual(out, {"accounts": 2, "holdings": 1, "transactions": 3})
        self.assertEqual([o for _, o in seen], [None, {"count": 500, "offset": 0}, {"count": 500, "offset": 2}])
        item = self.c.execute(select(PlaidItem.institution_name, PlaidItem.error, PlaidItem.last_sync)
                              .where(PlaidItem.item_id == "inv")).fetchone()
        self.assertEqual(item["institution_name"], "Wealthfront")
        self.assertTrue(item["last_sync"])
        secs = {r["id"]: dict(r) for r in self.c.execute(select(Security))}
        self.assertEqual((secs["s1"]["name"], secs["s1"]["close_price"], secs["s1"]["close_as_of"], secs["s1"]["sector"]),
                         ("Vanguard Total", 200.0, "2026-09-01", "Mixed"))
        self.assertEqual((secs["s2"]["is_cash"], secs["s2"]["currency"], secs["s3"]["is_cash"]), (1, "USD", 0))
        snaps = [tuple(r) for r in self.c.execute(select(InvSnapshot.date, InvSnapshot.account_id, InvSnapshot.value)
                                                  .order_by(InvSnapshot.account_id))]
        self.assertEqual(snaps, [("2026-09-23", "w1", 1500.0), ("2026-09-23", "w2", 42.0)])   # w2: no holdings, its balance
        txs = [tuple(r) for r in self.c.execute(select(InvTransaction.id, InvTransaction.quantity,
                                                       InvTransaction.amount, InvTransaction.fees,
                                                       InvTransaction.currency)
                                                .order_by(InvTransaction.id))]
        self.assertEqual(txs, [("t1", 5.0, 1000.0, 0.0, "USD"), ("t2", 0.0, -42.0, 0.0, "USD"), ("t3", 0.0, 0.0, 1.5, "USD")])
        # Nothing of yours at Wealthfront: both are accounts of their own.
        accts = [tuple(r) for r in self.c.execute(select(Account.id, Account.name, Account.kind, Account.balance,
                                                         Account.provider)
                                                  .where(Account.id.like("pl:%")).order_by(Account.id))]
        self.assertEqual(accts, [("pl:w1", "Roth IRA ••3639", "investment", 1500.0, "plaid"),
                                 ("pl:w2", "Cash", "investment", 42.0, "plaid")])
        # Holdings are a full snapshot; a second sync on the same day replaces the snapshot value.
        holdings["holdings"] = []
        holdings["accounts"][0]["balances"]["current"] = 1600.0
        pages[:] = [{"total_investment_transactions": 0, "investment_transactions": []}]
        with mock.patch.object(plaid, "call", side_effect=call):
            self.assertEqual(plaid.sync_item(self.c, "inv", TODAY), {"accounts": 2, "holdings": 0, "transactions": 0})
        self.assertFalse(self.c.execute(select(Holding.account_id)).fetchone())
        self.assertEqual(self.c.execute(select(InvSnapshot.value)
                                        .where(InvSnapshot.account_id == "w1")).fetchone()[0], 1600.0)
        self.assertEqual(self.c.execute(select(Account.balance).where(Account.id == "pl:w1")).fetchone()[0], 1600.0)
        # A failure is kept on the connection.
        with mock.patch.object(plaid, "call", side_effect=plaid.PlaidError("login", "ITEM_LOGIN_REQUIRED")), \
                self.assertRaises(plaid.PlaidError):
            plaid.sync_item(self.c, "inv", TODAY)
        self.assertEqual(self.c.execute(select(PlaidItem.error)
                                        .where(PlaidItem.item_id == "inv")).fetchone()[0], "ITEM_LOGIN_REQUIRED")
        with mock.patch.object(plaid, "call", side_effect=call):
            self.assertEqual(plaid.sync_all(self.c), {"items": 1, "errors": []})
        with self.assertRaises(plaid.PlaidError):
            plaid.sync_item(self.c, "nope", TODAY)

    def test_simplefin_copies_of_a_plaid_brokerage_are_hidden_once(self):
        self.c.execute(insert(PlaidItem), [{"item_id": "et", "access_token": "tok",
                                            "institution_name": "E*TRADE from Morgan Stanley",
                                            "products": "investments"},
                                           {"item_id": "bk", "access_token": "tok", "institution_name": "Chase",
                                            "products": "transactions"},
                                           {"item_id": "x", "access_token": "tok", "institution_name": "Ally",
                                            "products": None}])
        self.c.execute(insert(InvAccount), [{"id": "s1", "item_id": "sf", "name": "E*Trade Brokerage",
                                             "source": "simplefin", "institution": "E*Trade"},
                                            {"id": "s2", "item_id": "sf", "name": "Chase Invest",
                                             "source": "simplefin", "institution": "Chase"},
                                            {"id": "s3", "item_id": "sf", "name": "Ally Invest", "source": "simplefin",
                                             "institution": "Ally Invest"}])
        # Chase is a bank connection; Ally's (no products listed) counts as investments.
        self.assertEqual(plaid.hide_all_duplicates(self.c), ["E*Trade Brokerage", "Ally Invest"])
        self.assertEqual([tuple(r) for r in self.c.execute(select(InvAccount.id, InvAccount.hidden)
                                                           .order_by(InvAccount.id))],
                         [("s1", 1), ("s2", 0), ("s3", 1)])
        self.c.execute(update(InvAccount).values(hidden=0))
        self.assertEqual(plaid.hide_all_duplicates(self.c), [])   # once only
        self.assertEqual(plaid.hide_simplefin_duplicates(self.c, "nope"), [])

    def test_removing_an_investment_connection_removes_its_data(self):
        self.c.execute(insert(PlaidItem), [{"item_id": "inv", "access_token": "tok", "products": "investments"},
                                           {"item_id": "keep", "access_token": "tok", "products": "investments"}])
        self.c.execute(insert(InvAccount), [{"id": "w1", "item_id": "inv", "name": "Roth", "account_id": "pl:w1"},
                                            {"id": "k1", "item_id": "keep", "name": "Other", "account_id": None}])
        self.c.execute(insert(Account).values(id="pl:w1", name="Roth", kind="investment"))
        for aid in ("w1", "k1"):
            self.c.execute(insert(Holding).values(account_id=aid, security_id="s", value=1))
            self.c.execute(insert(InvTransaction).values(id="t" + aid, account_id=aid, date="2026-09-01"))
            self.c.execute(insert(InvSnapshot).values(date="2026-09-01", account_id=aid, value=1))
        plaid.remove_item(self.c, "inv")
        for col in (Holding.account_id, InvTransaction.account_id, InvSnapshot.account_id, InvAccount.id, PlaidItem.item_id):
            self.assertEqual([r[0] for r in self.c.execute(select(col))], ["keep" if col.class_ is PlaidItem else "k1"])
        self.assertFalse(self.c.execute(select(Account.id).where(Account.id == "pl:w1")).fetchone())
        with self.assertRaises(plaid.PlaidError):
            plaid.remove_item(self.c, "inv")
        with self.assertRaises(plaid.PlaidError):
            plaid.link_token(self.c, "inv")


if __name__ == "__main__":
    unittest.main()
