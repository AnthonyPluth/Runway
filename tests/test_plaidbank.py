"""Banks and cards through Plaid: matching accounts, choosing the provider per account, transactions without
duplicates, and card statements from the bank in the forecast."""
import json
import os
import tempfile
import threading
import unittest
from datetime import date
from http.server import BaseHTTPRequestHandler, HTTPServer

import sqlalchemy.exc

from runway import db, forecast, plaid, plaidbank, simplefin

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
                 "last_payment_date": "2026-08-30", "is_overdue": False}]}})
        self.reply(404, {"error_code": "NOT_FOUND"})


def tx(tid, acct, day, amount, name, pending=False, pending_id=None):
    return {"transaction_id": tid, "account_id": acct, "date": day, "amount": amount, "name": name, "merchant_name": name,
            "pending": pending, "pending_transaction_id": pending_id}


class PlaidBankTests(unittest.TestCase):
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
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "t.db")
        db.init(self.path)
        self.c = db.connect(self.path)
        MockBank.products = ["transactions", "liabilities"]
        MockBank.pages, MockBank.calls, MockBank.reject_redirect = [], [], False
        MockBank.drop, MockBank.fail, MockBank.during_sync = set(), {}, None
        db.set_setting(self.c, "plaid_client_id", "cid"); db.set_setting(self.c, "plaid_secret", "sec")
        # What SimpleFIN already brought in: checking and a card, with some history.
        self.c.execute("INSERT INTO accounts(id, name, kind, balance) VALUES ('sf-chk', 'Chase Checking', 'checking', 2500)")
        self.c.execute("INSERT INTO accounts(id, name, kind, balance, pay_from) "
                       "VALUES ('sf-csp', 'CSP ...1234', 'credit', -812.34, 'sf-chk')")
        self.c.executemany("INSERT INTO transactions(id, account_id, posted, amount, description, payee, category) VALUES (?,?,?,?,?,?,?)", [
            ("sf-chk|1", "sf-chk", "2026-08-01", -40.0, "OLD GROCERY", "Old Grocery", "Groceries"),
            ("sf-chk|2", "sf-chk", "2026-09-20", -12.5, "COFFEE", "Coffee", "Coffee & Snacks"),
        ])
        self.c.commit()

    def tearDown(self):
        self.c.close(); self.tmp.cleanup()

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
        item = self.c.execute("SELECT products FROM plaid_items").fetchone()
        self.assertEqual(item["products"], "liabilities,transactions")

    def test_accounts_matched_by_mask_or_balance_the_rest_wait_for_you(self):
        r = self.link()
        got = {a["id"]: a["plaid_account_id"] for a in db.rows(self.c.execute("SELECT id, plaid_account_id FROM accounts"))}
        self.assertEqual(got["sf-csp"], "p-csp")   # "1234" in the name
        self.assertEqual(got["sf-chk"], "p-chk")   # the only balance of 2,500
        self.assertEqual(sorted(r["matched"]), ["CSP ...1234", "Chase Checking"])
        # Freedom matches nothing; you add it as its own account.
        plaidbank.match(self.c, "p-new", "new", TODAY)
        new = self.c.execute("SELECT * FROM accounts WHERE id='pl:p-new'").fetchone()
        self.assertEqual((new["kind"], new["provider"], new["owed_positive"], new["balance"]), ("credit", "plaid", 1, 50.0))
        # Nothing changes for matched accounts until you pick Plaid for them.
        self.assertEqual(self.c.execute("SELECT provider FROM accounts WHERE id='sf-chk'").fetchone()[0], "simplefin")

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
        c.execute("DELETE FROM accounts")
        for aid, name, bal in [("csr", "CSR", -1203.10), ("csp", "CSP (Sara)", -455.00), ("citi", "Citi Double Cash", -88.20),
                               ("boa", "BofA Premium Rewards", -310.00)]:
            c.execute("INSERT INTO accounts(id, name, kind, balance) VALUES (?,?,'credit',?)", (aid, name, bal))
        def item(item_id, inst, accts):
            c.execute("INSERT INTO plaid_items(item_id, access_token, institution_name, products) VALUES (?,?,?, 'liabilities')",
                      (item_id, "t-" + item_id, inst))
            for pid, name, official, mask, cur in accts:
                c.execute("INSERT INTO plaid_accounts(plaid_account_id, item_id, name, official_name, mask, type, subtype, current) "
                          "VALUES (?,?,?,?,?,'credit','credit card',?)", (pid, item_id, name, official, mask, cur))
        # Balances differ a little from SimpleFIN's (pending charges), and none of the names carry the last 4 digits.
        item("chase", "Chase", [("p1", "Sapphire Preferred", "Chase Sapphire Preferred", "4417", 461.12),
                                ("p2", "Sapphire Reserve", "Chase Sapphire Reserve", "9921", 1203.10)])
        item("citi", "Citi", [("p3", "Citi Double Cash® Card", None, "0042", 91.00)])
        item("boa", "Bank of America", [("p4", "Premium Rewards Visa Signature", None, "7788", 300.00)])
        for i in ("chase", "citi", "boa"):
            plaidbank.auto_match(c, i)
        got = {r["id"]: r["plaid_account_id"] for r in c.execute("SELECT id, plaid_account_id FROM accounts")}
        self.assertEqual(got, {"csr": "p2", "csp": "p1", "citi": "p3", "boa": "p4"})

    def test_no_match_across_institutions_or_when_unsure(self):
        c = self.c
        c.execute("DELETE FROM accounts")
        c.execute("INSERT INTO accounts(id, name, kind, balance) VALUES ('citi', 'Citi Double Cash', 'credit', -50)")
        c.execute("INSERT INTO accounts(id, name, kind, balance) VALUES ('c1', 'Card one', 'credit', -20)")
        c.execute("INSERT INTO accounts(id, name, kind, balance) VALUES ('c2', 'Card two', 'credit', -20)")
        c.execute("INSERT INTO plaid_items(item_id, access_token, institution_name, products) VALUES ('ch','t','Chase','liabilities')")
        c.execute("INSERT INTO plaid_accounts(plaid_account_id, item_id, name, type, subtype, current) "
                  "VALUES ('px','ch','Freedom Unlimited','credit','credit card',50)")
        c.execute("INSERT INTO plaid_accounts(plaid_account_id, item_id, name, type, subtype, current) "
                  "VALUES ('py','ch','Card','credit','credit card',20)")
        self.assertEqual(plaidbank.auto_match(c, "ch"), [])   # same balance as the Citi card, but Chase; two equal "Card"s

    def test_switching_to_plaid_keeps_history_and_skips_duplicates(self):
        self.link()
        plaidbank.set_provider(self.c, "sf-chk", "plaid", TODAY)
        self.assertIsNone(self.c.execute("SELECT cursor FROM plaid_items").fetchone()[0])   # re-read from the start
        MockBank.pages = [{"added": [
            tx("a0", "p-chk", "2026-08-01", 40.0, "OLD GROCERY"),       # long before the switch: SimpleFIN has it
            tx("a1", "p-chk", "2026-09-21", 12.5, "COFFEE"),            # the same coffee, a day later at Plaid
            tx("a2", "p-chk", "2026-09-22", 3000.0 * -1, "PAYROLL"),     # new: money in
            tx("a3", "p-chk", "2026-09-23", 20.0, "LUNCH", pending=True),
            tx("c1", "p-csp", "2026-09-22", 99.0, "CARD CHARGE"),       # the card still uses SimpleFIN
        ]}]
        r = plaidbank.sync_item(self.c, "item-b", TODAY)
        rows = {t["id"]: t for t in db.rows(self.c.execute("SELECT * FROM transactions WHERE account_id='sf-chk'"))}
        self.assertEqual(sorted(rows), ["sf-chk|1", "sf-chk|2", "sf-chk|pl:a2", "sf-chk|pl:a3"])
        self.assertEqual(rows["sf-chk|pl:a2"]["amount"], 3000.0)
        self.assertEqual(sorted(r["new"]), ["sf-chk|pl:a2", "sf-chk|pl:a3"])
        self.assertFalse(self.c.execute("SELECT 1 FROM transactions WHERE account_id='sf-csp'").fetchone())
        self.assertEqual(self.c.execute("SELECT balance FROM accounts WHERE id='sf-chk'").fetchone()[0], 2500.0)
        # The lunch posts: it replaces the pending one and keeps the category you gave it.
        self.c.execute("UPDATE transactions SET category='Restaurants', category_source='manual' WHERE id='sf-chk|pl:a3'")
        MockBank.pages = [{"added": [tx("a4", "p-chk", "2026-09-24", 21.0, "LUNCH", pending_id="a3")], "removed": [{"transaction_id": "a3"}]}]
        r = plaidbank.sync_item(self.c, "item-b", TODAY)
        self.assertEqual(r["new"], [])
        lunch = self.c.execute("SELECT * FROM transactions WHERE id='sf-chk|pl:a4'").fetchone()
        self.assertEqual((lunch["amount"], lunch["category"], lunch["pending"]), (-21.0, "Restaurants", 0))
        self.assertFalse(self.c.execute("SELECT 1 FROM transactions WHERE id='sf-chk|pl:a3'").fetchone())

    def test_merchants_and_their_logos_are_noted(self):
        self.link()
        plaidbank.set_provider(self.c, "sf-chk", "plaid", TODAY)
        joe = {**tx("m1", "p-chk", "2026-09-23", 4.5, "SQ *JOES"), "merchant_name": "Joe's Coffee",
               "merchant_entity_id": "ent-joe", "logo_url": "https://plaid-merchant-logos.plaid.com/joes.png",
               "website": "joescoffee.com"}
        MockBank.pages = [{"added": [joe, tx("m2", "p-chk", "2026-09-23", 9.0, "NO LOGO SHOP")]}]
        plaidbank.sync_item(self.c, "item-b", TODAY)
        ids = dict(self.c.execute("SELECT id, merchant_id FROM transactions WHERE id LIKE '%pl:m%'").fetchall())
        self.assertEqual(ids, {"sf-chk|pl:m1": "ent-joe", "sf-chk|pl:m2": None})
        m = self.c.execute("SELECT name, website, logo_url FROM merchants").fetchone()
        self.assertEqual(tuple(m), ("Joe's Coffee", "joescoffee.com", "https://plaid-merchant-logos.plaid.com/joes.png"))

    def test_simplefin_leaves_plaid_accounts_alone(self):
        self.link()
        plaidbank.set_provider(self.c, "sf-chk", "plaid", TODAY)
        payload = {"accounts": [{"id": "sf-chk", "name": "Chase Checking", "balance": "1.00", "currency": "USD",
                                 "transactions": [{"id": "9", "posted": 1790000000, "amount": "-5.00", "description": "SF ONLY"}]}]}
        self.assertEqual(simplefin.store_payload(self.c, payload, date(2026, 9, 1)), [])
        self.assertEqual(self.c.execute("SELECT balance FROM accounts WHERE id='sf-chk'").fetchone()[0], 2500.0)

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
        self.c.execute("INSERT INTO overrides(key, amount) VALUES (?, ?)", ("stmt:sf-csp:2026-09-05", 600))
        c = next(x for x in forecast.build(self.c, TODAY, 30)["cards"] if x["id"] == "sf-csp")
        self.assertEqual((c["statement_balance"], c["statement_set"], c["statement_reported"]), (600, True, 640.5))

    def test_statements_asked_for_even_if_not_listed_when_linked(self):
        MockBank.products = ["transactions"]        # Liabilities was optional and didn't show up on the Item
        self.link()
        self.assertTrue(any(p == "/liabilities/get" for p, _ in MockBank.calls))
        self.assertIn("liabilities", self.c.execute("SELECT products FROM plaid_items").fetchone()[0])
        self.assertTrue(self.c.execute("SELECT 1 FROM card_statements WHERE plaid_account_id='p-csp'").fetchone())

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
        a = self.c.execute("SELECT provider, plaid_account_id FROM accounts WHERE id='sf-chk'").fetchone()
        self.assertEqual((a["provider"], a["plaid_account_id"]), ("simplefin", None))
        self.assertFalse(self.c.execute("SELECT 1 FROM card_statements").fetchone())


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
        item = self.c.execute("SELECT access_token, products FROM plaid_items WHERE item_id=?", (item_id,)).fetchone()
        self.assertTrue(item["access_token"])
        self.assertEqual(item["products"], "transactions")
        self.assertTrue(plaidbank.is_bank_item(item))
        # With /item/get answering, its products win.
        MockBank.drop = set()
        plaid.exchange(self.c, "public-1", {"name": "Chase"}, "investments")
        self.assertEqual(self.c.execute("SELECT products FROM plaid_items").fetchone()[0], "liabilities,transactions")

    def test_one_failing_connection_doesnt_stop_the_others(self):
        self.link()
        self.c.execute("INSERT INTO plaid_items(item_id, access_token, institution_name, products) "
                       "VALUES ('item-a', 'access-a', 'Ally', 'transactions')")
        MockBank.drop = {"/accounts/get"}
        out = plaidbank.sync_all(self.c, TODAY)
        self.assertEqual((out["items"], len(out["errors"])), (0, 2))
        self.assertTrue(self.c.execute("SELECT error FROM plaid_items WHERE item_id='item-a'").fetchone()[0])

    def test_refresh_asks_each_transactions_connection(self):
        self.link()
        self.c.execute("INSERT INTO plaid_items(item_id, access_token, institution_name, products) "
                       "VALUES ('item-c', 'access-c', 'Amex', 'liabilities')")   # statements only: nothing to refresh
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
        self.c.execute("UPDATE transactions SET category='Shopping' WHERE id='pl:p-new|pl:f1'")
        # It turns out to be a card SimpleFIN already has.
        self.c.execute("INSERT INTO accounts(id, name, kind, balance) VALUES ('sf-free', 'Freedom', 'credit', -50)")
        self.c.execute("INSERT INTO transactions(id, account_id, posted, amount, description) "
                       "VALUES ('sf-free|1', 'sf-free', '2026-09-21', -25.0, 'BOOKS')")
        plaidbank.match(self.c, "p-new", "sf-free", TODAY)
        own = self.c.execute("SELECT hidden, plaid_account_id FROM accounts WHERE id='pl:p-new'").fetchone()
        self.assertEqual((own["hidden"], own["plaid_account_id"]), (1, None))
        self.assertEqual(self.c.execute("SELECT category FROM transactions WHERE id='sf-free|1'").fetchone()[0], "Shopping")
        # Syncing no longer updates the copy.
        MockBank.pages = [{"added": [tx("f2", "p-new", "2026-09-22", 5.0, "MORE BOOKS")]}]
        plaidbank.sync_item(self.c, "item-b", TODAY)
        self.assertFalse(self.c.execute("SELECT 1 FROM transactions WHERE id LIKE '%pl:f2'").fetchone())
        # Adding it as its own again brings the copy back.
        plaidbank.match(self.c, "p-new", "new", TODAY)
        own = self.c.execute("SELECT hidden, plaid_account_id FROM accounts WHERE id='pl:p-new'").fetchone()
        self.assertEqual((own["hidden"], own["plaid_account_id"]), (0, "p-new"))
        self.assertIsNone(self.c.execute("SELECT plaid_account_id FROM accounts WHERE id='sf-free'").fetchone()[0])

    def test_an_account_you_dont_use_isnt_synced(self):
        self.link()
        plaidbank.match(self.c, "p-new", "new", TODAY)
        plaidbank.match(self.c, "p-new", "ignore", TODAY)
        self.assertEqual(self.c.execute("SELECT hidden FROM accounts WHERE id='pl:p-new'").fetchone()[0], 1)
        MockBank.pages = [{"added": [tx("i1", "p-new", "2026-09-22", 5.0, "IGNORED")]}]
        plaidbank.sync_item(self.c, "item-b", TODAY)
        self.assertFalse(self.c.execute("SELECT 1 FROM transactions WHERE id LIKE '%pl:i1'").fetchone())

    def test_an_account_already_linked_cant_be_linked_again(self):
        self.link()   # sf-csp is linked to p-csp
        with self.assertRaises(ValueError):
            plaidbank.match(self.c, "p-new", "sf-csp", TODAY)
        self.assertEqual(self.c.execute("SELECT plaid_account_id FROM accounts WHERE id='sf-csp'").fetchone()[0], "p-csp")
        self.assertEqual(self.c.execute("SELECT ignored FROM plaid_accounts WHERE plaid_account_id='p-new'").fetchone()[0], 0)
        plaidbank.match(self.c, "p-csp", "sf-csp", TODAY)   # the same link again is fine
        plaidbank.match(self.c, "p-csp", "", TODAY)         # unlinked: free for another
        plaidbank.match(self.c, "p-new", "sf-csp", TODAY)
        self.assertEqual(self.c.execute("SELECT plaid_account_id FROM accounts WHERE id='sf-csp'").fetchone()[0], "p-new")

    def test_one_plaid_account_is_one_of_your_accounts(self):
        self.link()
        with self.assertRaises(sqlalchemy.exc.IntegrityError):   # the unique index, on either database
            self.c.execute("UPDATE accounts SET plaid_account_id='p-csp' WHERE id='sf-chk'")

    def test_no_write_lock_is_held_while_plaid_answers(self):
        if os.environ.get("DATABASE_URL"):
            self.skipTest("SQLite's database-wide write lock")
        import sqlite3
        self.link()
        plaidbank.set_provider(self.c, "sf-chk", "plaid", TODAY)
        self.c.commit()
        blocked = []

        def write_elsewhere():   # what an edit in the app, or the extension, does meanwhile
            other = sqlite3.connect(self.path, timeout=0.2)
            try:
                other.execute("UPDATE settings SET value=value WHERE key='plaid_client_id'")
                other.commit()
            except sqlite3.OperationalError as e:
                blocked.append(str(e))
            finally:
                other.close()
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
        self.assertEqual(self.c.execute("SELECT error FROM plaid_items").fetchone()[0], "INTERNAL_SERVER_ERROR")
        self.assertTrue(self.c.execute("SELECT 1 FROM transactions WHERE id='sf-chk|pl:s1'").fetchone())

    def test_holds_dropped_while_reading_everything_again_are_removed(self):
        self.link()
        plaidbank.set_provider(self.c, "sf-chk", "plaid", TODAY)
        MockBank.pages = [{"added": [tx("h1", "p-chk", "2026-09-22", 60.0, "HOTEL HOLD", pending=True),
                                     tx("h2", "p-chk", "2026-09-22", 9.0, "LUNCH", pending=True)]}]
        plaidbank.sync_item(self.c, "item-b", TODAY)
        plaidbank._reread(self.c, "item-b")   # e.g. another account switched to Plaid
        MockBank.pages = [{"added": [tx("h2", "p-chk", "2026-09-22", 9.0, "LUNCH", pending=True)]}]
        plaidbank.sync_item(self.c, "item-b", TODAY)
        ids = {r[0] for r in self.c.execute("SELECT id FROM transactions WHERE id LIKE '%|pl:%'")}
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


if __name__ == "__main__":
    unittest.main()
