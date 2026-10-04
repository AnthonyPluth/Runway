"""The MCP server: what an assistant connected with OAuth may reach (mcp_access, mcp_http.local_fetch), POST /mcp, the
Settings routes for it, and the tools and protocol (runway/mcp_server.py) with Runway stood in for by a fake. OAuth
itself is tested in tests/test_mcp_oauth.py and tests/test_mcp_oauth_http.py."""
import base64
import hashlib
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from sqlalchemy import delete, func, insert, select, update

from runway import db, mcp_access, mcp_oauth, mcp_server, server
from runway.server import mcp_http
from runway.models import (Category, ChurnBenefit, ChurnBenefitUse, ChurnCard, ChurnTask, ChurnWish, OAuthGrant, RetailItem,
                           RetailItemMemory, RetailOrder, Rule, Transaction, TxSplit)
from tests.shared import forget_oauth, hold_mcp_switch, tag

VERIFIER = "v" * 50
CALLBACK = "http://127.0.0.1:1/cb"
READ = mcp_access.Access(frozenset({"read"}), None, None)
WRITE = mcp_access.Access(frozenset({"read", "churning:write"}), None, None)
CATEGORIZE = mcp_access.Access(frozenset({"read", "categorize:write"}), None, None)
ANY = mcp_access.Access(frozenset({"read", "write"}), None, None)

# Every change "write" allows (mcp_access.writable_routes over ROUTES). A route added to server/routes.py is allowed
# unless it's BLOCKED, and fails this list until someone has looked at whether an assistant should have it.
ALLOWED = [
    "DELETE /api/overrides", "DELETE /api/recurring/{id}", "DELETE /api/rules/{id}", "DELETE /api/transactions/{id}",
    "POST /api/accounts/{id}", "POST /api/accounts/{id}/remove", "POST /api/accounts/{id}/restore", "POST /api/accounts/{id}/statements",
    "POST /api/accounts/{id}/statements/{id}/remove", "POST /api/ai/apply", "POST /api/ai/suggest", "POST /api/assets",
    "POST /api/assets/{id}", "POST /api/assets/{id}/refresh", "POST /api/assets/{id}/remove", "POST /api/budget", "POST /api/categories",
    "POST /api/categories/look", "POST /api/categories/move", "POST /api/categories/pay-with", "POST /api/categories/remove",
    "POST /api/categories/rename", "POST /api/churning/balances", "POST /api/churning/bank", "POST /api/churning/bank/{id}",
    "POST /api/churning/bank/{id}/remove", "POST /api/churning/benefits/{id}", "POST /api/churning/benefits/{id}/remove",
    "POST /api/churning/benefits/{id}/unuse", "POST /api/churning/benefits/{id}/use", "POST /api/churning/cards",
    "POST /api/churning/cards/{id}", "POST /api/churning/cards/{id}/benefits", "POST /api/churning/cards/{id}/plan/done",
    "POST /api/churning/cards/{id}/plan/undo", "POST /api/churning/cards/{id}/rates", "POST /api/churning/cards/{id}/remove",
    "POST /api/churning/currencies", "POST /api/churning/currencies/{id}/remove", "POST /api/churning/found/{id}/dismiss",
    "POST /api/churning/found/{id}/undismiss", "POST /api/churning/scores", "POST /api/churning/suggest", "POST /api/churning/tasks",
    "POST /api/churning/tasks/{id}", "POST /api/churning/tasks/{id}/remove", "POST /api/churning/tasks/{id}/snooze",
    "POST /api/churning/wishlist", "POST /api/churning/wishlist/{id}", "POST /api/churning/wishlist/{id}/applied",
    "POST /api/churning/wishlist/{id}/remove", "POST /api/equity/companies", "POST /api/equity/companies/{id}",
    "POST /api/equity/companies/{id}/grants", "POST /api/equity/companies/{id}/remove", "POST /api/equity/grants/{id}",
    "POST /api/equity/grants/{id}/remove", "POST /api/investments/cost", "POST /api/investments/plan", "POST /api/overrides",
    "POST /api/recategorize", "POST /api/recurring", "POST /api/recurring/dismiss", "POST /api/recurring/suggestions/dismiss",
    "POST /api/recurring/suggestions/restore", "POST /api/recurring/{id}", "POST /api/recurring/{id}/amount",
    "POST /api/recurring/{id}/match", "POST /api/retail/charges/{id}/apply", "POST /api/retail/charges/{id}/link",
    "POST /api/retail/charges/{id}/restore", "POST /api/retail/charges/{id}/unlink", "POST /api/retail/items/{id}",
    "POST /api/retail/items/{id}/restore", "POST /api/retail/match", "POST /api/retail/orders/{id}/suggest", "POST /api/rules",
    "POST /api/rules/preview", "POST /api/rules/{id}", "POST /api/rules/{id}/apply", "POST /api/tracked/{id}", "POST /api/transactions",
    "POST /api/transactions/bulk", "POST /api/transactions/import", "POST /api/transactions/{id}", "POST /api/transactions/{id}/accept",
    "POST /api/transactions/{id}/category", "POST /api/transactions/{id}/name", "POST /api/transactions/{id}/recurring",
    "POST /api/transactions/{id}/split",
]


class RunwayServer(unittest.TestCase):
    """A real Runway on a temporary database, and OAuth tokens for it. On Postgres the database is shared with test
    modules running alongside (tests/shared.py): each test removes only what it made, and holds the churning switch."""
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["RUNWAY_DATA"] = cls.tmp.name
        os.environ.pop("RUNWAY_PUBLIC_URL", None)
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

    def setUp(self):
        hold_mcp_switch(self)
        self.tag = tag()
        self.owner = "Alex " + self.tag                                           # the churning cards this test makes
        self.clients: list[str] = []
        self.addCleanup(self.forget)

    def forget(self):
        with db.session() as conn:
            forget_oauth(conn, self.clients)
            conn.execute(delete(ChurnCard).where(ChurnCard.owner == self.owner))
            mcp_access.set_allow_writes(conn, False)
            mcp_access.set_allow_categorize(conn, False)
            mcp_access.set_allow_all(conn, False)

    def cards(self):
        with db.session() as conn:
            return conn.execute(select(func.count()).select_from(ChurnCard)
                                .where(ChurnCard.owner == self.owner)).fetchone()[0]

    def make_token(self, *scopes, name="Claude", who=None):
        """An access token for this Runway's /mcp, as if an assistant had connected and you had approved `scopes`."""
        with db.session() as conn:
            c = mcp_oauth.register(conn, {"client_name": name, "redirect_uris": [CALLBACK]})
            self.clients.append(c["client_id"])
            challenge = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b"=").decode()
            params = {"client_id": c["client_id"], "redirect_uri": CALLBACK, "code_challenge": challenge, "resource": self.base + "/mcp"}
            code = mcp_oauth.approve(conn, params, frozenset(scopes or ("read",)), who and "sub-" + who, who)
            out = mcp_oauth.token(conn, mcp_oauth.get_client(conn, c["client_id"]),
                                  {"grant_type": "authorization_code", "code": code, "redirect_uri": CALLBACK, "code_verifier": VERIFIER},
                                  self.base)
        return out["access_token"]


class PagesTests(RunwayServer):
    """What mcp_http.local_fetch (the tools' way into Runway) reaches, as an assistant."""

    def test_reads_the_listed_pages(self):
        self.assertIsInstance(mcp_http.local_fetch("accounts", {}, None, READ), list)
        self.assertIn("items", mcp_http.local_fetch("transactions", {"limit": 5}, None, READ))
        self.assertIn("cards", mcp_http.local_fetch("churning", {}, None, READ))
        mcp_http.local_fetch("reports/spending", {"months": 3}, None, READ)
        with self.assertRaisesRegex(mcp_server.ToolError, "Month"):              # a page's own refusal comes through
            mcp_http.local_fetch("budget", {"month": "bad"}, None, READ)
        nothing = mcp_access.Access(frozenset(), None, None)
        with self.assertRaises(mcp_server.ToolError):                            # no read scope, no reading
            mcp_http.local_fetch("accounts", {}, None, nothing)

    def test_reads_orders_with_their_items_and_categories(self):
        with db.session() as conn:
            conn.execute(insert(RetailOrder).values(id="costco|9", retailer="costco", order_number="9",
                                                    channel="store", placed="2026-09-26", total=10, details=1))
            conn.execute(insert(RetailItem).values(order_id="costco|9", title="BANANAS", amount=5, quantity=1,
                                                   category="Groceries", category_source="ai"))
        try:
            self.assertIn("costco|9", json.dumps(mcp_http.local_fetch("retail", {}, None, READ)))
            order = mcp_http.local_fetch("retail/orders/costco%7C9", {}, None, READ)
            self.assertEqual([(i["title"], i["category"], i["category_source"]) for i in order["items"]], [("BANANAS", "Groceries", "ai")])
            with self.assertRaises(mcp_server.ToolError):                        # the page's own refusal
                mcp_http.local_fetch("retail/orders/nope", {}, None, READ)
            for path in ("retail/orders/costco%7C9/suggest", "retail/items/1", "retail/token", "retail/extension.zip"):
                with self.subTest(path=path), self.assertRaises(mcp_server.ToolError):
                    mcp_http.local_fetch(path, {}, None, READ)                    # only those two pages
        finally:
            with db.session() as conn:
                conn.execute(delete(RetailItem).where(RetailItem.order_id == "costco|9"))
                conn.execute(delete(RetailOrder).where(RetailOrder.id == "costco|9"))

    def test_nothing_else_is_reachable(self):
        for path in ("state", "settings", "plaid/status", "backup", "retail/token", "push", "mcp-settings", "", "../accounts",
                     "accounts/x", "access/x"):
            with self.subTest(path=path), self.assertRaises(mcp_server.ToolError):
                mcp_http.local_fetch(path, {}, None, WRITE)
        with db.session() as conn:
            mcp_access.set_allow_writes(conn, True)
        for path in ("accounts", "sync", "mcp-settings/writes", "settings", "backup", "plaid/status", "churning/cards/1/extra"):
            with self.subTest(path=path), self.assertRaisesRegex(mcp_server.ToolError, "Not found"):
                mcp_http.local_fetch(path, {}, {}, WRITE)                         # posting: only the listed changes
        for path in ("transactions", "churning/currencies", "churning/balances", "churning/cards/1/remove", "churning/benefits/1/remove",
                     "churning/tasks/1/remove", "churning/wishlist/1/remove"):
            with self.subTest(path=path), self.assertRaisesRegex(mcp_server.ToolError, mcp_http.CANT_CHANGE):
                mcp_http.local_fetch(path, {}, {}, WRITE)                         # what only "write" allows
        with self.assertRaises(urllib.error.HTTPError) as e:                     # the old key's pages are gone
            urllib.request.urlopen(self.base + "/api/mcp/accounts", timeout=20)
        self.assertEqual(e.exception.code, 404)

    def test_every_readable_page_is_a_get_route(self):
        gets = {p for m, p, _ in server.ROUTES if m == "GET"}
        self.assertLessEqual(mcp_access.READABLE, gets)
        for path in mcp_access.READABLE:   # read-only: nothing that carries credentials or changes state
            self.assertNotRegex(path, r"plaid|settings|backup|token|key|sync|carta|simplefin|mcp")

    def test_changes_need_the_scope_and_the_switch_every_time(self):
        with db.session() as conn:
            self.assertFalse(mcp_access.allow_writes(conn))                       # off by default
        card = {"owner": self.owner, "issuer": "chase", "product": "X", "opened_on": "2025-01-15"}
        with self.assertRaisesRegex(mcp_server.ToolError, "switched off"):
            mcp_http.local_fetch("churning/cards", {}, card, WRITE)
        self.assertEqual(mcp_http.local_fetch("access", {}, None, WRITE), {"writes": False, "why": mcp_http.WRITES_OFF})
        with db.session() as conn:
            mcp_access.set_allow_writes(conn, True)
        self.assertEqual(mcp_http.local_fetch("access", {}, None, WRITE), {"writes": True})
        self.assertEqual(mcp_http.local_fetch("access", {}, None, READ), {"writes": False, "why": mcp_http.READ_ONLY})
        with self.assertRaisesRegex(mcp_server.ToolError, "reconnect"):
            mcp_http.local_fetch("churning/cards", {}, card, READ)
        self.assertEqual(self.cards(), 0)                                         # nothing was made
        with db.session() as conn:
            mcp_access.set_allow_writes(conn, False)
        with self.assertRaisesRegex(mcp_server.ToolError, "switched off"):       # off again applies to the very next change
            mcp_http.local_fetch("churning/cards", {}, card, WRITE)

    def test_with_scope_and_switch_the_listed_churning_changes_work(self):
        with db.session() as conn:
            mcp_access.set_allow_writes(conn, True)

        def post(path, body):
            return mcp_http.local_fetch(path, {}, body, WRITE)
        card = post("churning/cards", {"owner": self.owner, "issuer": "chase", "product": "Sapphire Reserve", "opened_on": "2025-01-15", "annual_fee": 550})
        cid = card["id"]
        post(f"churning/cards/{cid}", {"notes": "from an assistant"})
        benefit = post(f"churning/cards/{cid}/benefits", {"name": "Travel credit", "kind": "credit", "amount": 300, "period": "annual"})
        bid = benefit["id"]
        post(f"churning/benefits/{bid}/use", {"amount": 100})
        post(f"churning/benefits/{bid}/unuse", {})
        task = post("churning/tasks", {"card_id": cid, "due_on": "2027-01-01", "action": "Call retention"})
        post(f"churning/tasks/{task['id']}/snooze", {"days": 7})
        wish = post("churning/wishlist", {"owner": "Alex", "kind": "card", "issuer": "amex", "product": "Gold"})
        post(f"churning/wishlist/{wish['id']}", {"apply_url": "https://example.com/apply"})
        with db.session() as conn:
            self.assertEqual(conn.execute(select(ChurnCard.notes)
                                          .where(ChurnCard.id == cid)).fetchone()[0], "from an assistant")
            self.assertEqual(conn.execute(select(func.count()).select_from(ChurnBenefitUse)).fetchone()[0], 0)   # used, then undone
            conn.execute(delete(ChurnTask).where(ChurnTask.card_id == cid))
            conn.execute(delete(ChurnBenefit).where(ChurnBenefit.card_id == cid))
            conn.execute(delete(ChurnCard).where(ChurnCard.id == cid))
            conn.execute(delete(ChurnWish).where(ChurnWish.id == wish["id"]))   # the database can be shared with other tests (Postgres)

    def test_every_writable_path_is_a_post_route_and_none_removes_anything(self):
        posts = {p for m, p, _ in server.ROUTES if m == "POST"}
        self.assertLessEqual(set(mcp_access.WRITABLE), posts)
        for path in mcp_access.WRITABLE:
            self.assertTrue(path.startswith("/api/churning/"), path)
            self.assertNotRegex(path, r"remove|delete|currenc|balance|score|bank")
        self.assertLessEqual(set(mcp_access.CATEGORIZABLE), posts)
        for path in mcp_access.CATEGORIZABLE:
            self.assertRegex(path, r"^/api/(transactions/\{id\}/(category|accept)|retail/items/\{id\})$")
        self.assertEqual({s: paths for s, (paths, _key) in mcp_access.CHANGES.items()},
                         {"churning:write": mcp_access.WRITABLE, "categorize:write": mcp_access.CATEGORIZABLE})

    def test_categorizing_needs_its_own_scope_and_switch(self):
        tx, item = "mcp-" + self.tag, None
        with db.session() as conn:
            conn.execute(insert(Transaction).values(id=tx, account_id="mcp-acct", posted="2026-09-20", amount=-12,
                                                    payee="Corner Market", category="Shopping", needs_review=1))
            conn.execute(insert(RetailOrder).values(id="costco|" + self.tag, retailer="costco", order_number=self.tag,
                                                    channel="store", placed="2026-09-26", total=10, details=1))
            conn.execute(insert(RetailItem).values(order_id="costco|" + self.tag, title="APPLES " + self.tag, amount=5,
                                                   quantity=1, category="Shopping", category_source="ai"))
            item = conn.execute(select(RetailItem.id).where(RetailItem.order_id == "costco|" + self.tag)).fetchone()[0]

        def row():
            with db.session() as conn:
                return tuple(conn.execute(select(Transaction.category, Transaction.category_source,
                                                 Transaction.needs_review)
                                          .where(Transaction.id == tx)).fetchone())
        try:
            path, body = "transactions/" + tx + "/category", {"category": "Groceries", "remember": False}
            with self.assertRaisesRegex(mcp_server.ToolError, "switched off"):       # scope, switch off
                mcp_http.local_fetch(path, {}, body, CATEGORIZE)
            self.assertEqual(mcp_http.local_fetch("access", {"scope": "categorize:write"}, None, CATEGORIZE),
                             {"writes": False, "why": mcp_http.CATEGORIZE_OFF})
            with db.session() as conn:
                mcp_access.set_allow_writes(conn, True)                              # the churning switch doesn't do it
            with self.assertRaisesRegex(mcp_server.ToolError, "switched off"):
                mcp_http.local_fetch(path, {}, body, CATEGORIZE)
            with db.session() as conn:
                mcp_access.set_allow_categorize(conn, True)
            for access in (READ, WRITE):                                              # nor does churning:write
                with self.subTest(scopes=access.scopes), self.assertRaisesRegex(mcp_server.ToolError, "reconnect"):
                    mcp_http.local_fetch(path, {}, body, access)
            self.assertEqual(mcp_http.local_fetch("access", {"scope": "categorize:write"}, None, WRITE),
                             {"writes": False, "why": mcp_http.CANT_CATEGORIZE})
            with self.assertRaisesRegex(mcp_server.ToolError, "reconnect"):          # and categorize:write doesn't change churning
                mcp_http.local_fetch("churning/cards", {}, {"owner": self.owner, "issuer": "chase", "product": "X"}, CATEGORIZE)
            self.assertEqual(row(), ("Shopping", None, 1))                            # nothing changed so far
            self.assertEqual(mcp_http.local_fetch("access", {"scope": "categorize:write"}, None, CATEGORIZE), {"writes": True})

            got = mcp_http.local_fetch(path, {}, body, CATEGORIZE)
            self.assertEqual(got["was"][0]["category"], "Shopping")
            self.assertEqual(row(), ("Groceries", "manual", 0))
            with self.assertRaisesRegex(mcp_server.ToolError, "Unknown category"):   # only categories that exist
                mcp_http.local_fetch(path, {}, {"category": "Made Up " + self.tag}, CATEGORIZE)
            with db.session() as conn:
                conn.execute(update(Transaction).where(Transaction.id == tx)
                             .values(category="Restaurants", category_source="ai", needs_review=1))
            mcp_http.local_fetch("transactions/" + tx + "/accept", {}, {}, CATEGORIZE)
            self.assertEqual(row(), ("Restaurants", "manual", 0))

            from runway import splits                                                 # a split one: refused, its parts kept
            with db.session() as conn:
                splits.set_splits(conn, tx, [{"amount": -7, "category": "Groceries"}, {"amount": -5, "category": "Shopping"}])
                before = [(p["amount"], p["category"]) for p in splits.of(conn, [tx])[tx]]
            with self.assertRaisesRegex(mcp_server.ToolError, "split"):
                mcp_http.local_fetch(path, {}, body, CATEGORIZE)
            with db.session() as conn:
                self.assertEqual([(p["amount"], p["category"]) for p in splits.of(conn, [tx])[tx]], before)
                self.assertEqual(len(before), 2)
                splits.set_splits(conn, tx, [])

            mcp_http.local_fetch(f"retail/items/{item}", {}, {"category": "Groceries", "remember": False}, CATEGORIZE)
            with db.session() as conn:
                self.assertEqual(tuple(conn.execute(select(RetailItem.category, RetailItem.category_source)
                                                    .where(RetailItem.id == item)).fetchone()),
                                 ("Groceries", "manual"))
                self.assertIsNone(conn.execute(select(RetailItemMemory.key)
                                               .where(RetailItemMemory.key.like(f"%{self.tag}%"))).fetchone())
            for other in (f"transactions/{tx}/split", f"transactions/{tx}/recurring", "transactions/bulk", "recategorize",
                         "categories", "categories/rename", "categories/remove", f"retail/orders/costco%7C{self.tag}/suggest"):
                with self.subTest(path=other), self.assertRaisesRegex(mcp_server.ToolError, "Change anything"):
                    mcp_http.local_fetch(other, {}, {}, CATEGORIZE)                    # only the listed changes ("write" has the rest)
            with db.session() as conn:
                mcp_access.set_allow_categorize(conn, False)
            with self.assertRaisesRegex(mcp_server.ToolError, "switched off"):       # off again applies to the very next change
                mcp_http.local_fetch(path, {}, body, CATEGORIZE)
        finally:
            with db.session() as conn:
                conn.execute(delete(TxSplit).where(TxSplit.tx_id == tx))
                conn.execute(delete(Transaction).where(Transaction.id == tx))
                conn.execute(delete(RetailItem).where(RetailItem.order_id == "costco|" + self.tag))
                conn.execute(delete(RetailOrder).where(RetailOrder.id == "costco|" + self.tag))


class AnythingTests(RunwayServer):
    """The "write" scope and "Let assistants change anything": what mcp_http.local_fetch lets through, and what it never does."""

    def all(self, on):
        with db.session() as conn:
            mcp_access.set_allow_all(conn, on)

    def category(self, name):
        with db.session() as conn:
            return conn.execute(select(Category.name).where(Category.name == name)).fetchone() is not None

    def test_it_needs_its_own_scope_and_switch_every_time(self):
        name = "Assistant " + self.tag
        with db.session() as conn:
            self.assertFalse(mcp_access.allow_all(conn))                          # off by default
            mcp_access.set_allow_writes(conn, True)                               # the other switches don't open it
            mcp_access.set_allow_categorize(conn, True)
        for path, body in (("categories", {"name": name}), ("rules", None)):
            with self.subTest(path=path), self.assertRaisesRegex(mcp_server.ToolError, "switched off"):
                mcp_http.local_fetch(path, {}, body, ANY)
        self.assertEqual(mcp_http.local_fetch("access", {"scope": "write"}, None, ANY), {"writes": False, "why": mcp_http.ALL_OFF})
        self.all(True)
        for access in (READ, WRITE, CATEGORIZE):                                  # nor do the other scopes
            with self.subTest(scopes=access.scopes), self.assertRaisesRegex(mcp_server.ToolError, "reconnect"):
                mcp_http.local_fetch("categories", {}, {"name": name}, access)
            with self.subTest(scopes=access.scopes), self.assertRaises(mcp_server.ToolError):
                mcp_http.local_fetch("rules", {}, None, access)                   # a page only "write" opens
        self.assertFalse(self.category(name))
        self.assertEqual(mcp_http.local_fetch("access", {"scope": "write"}, None, ANY), {"writes": True})
        try:
            mcp_http.local_fetch("categories", {}, {"name": name}, ANY)
            mcp_http.local_fetch("categories/rename", {}, {"name": name, "new_name": name + " 2"}, ANY)
            self.assertTrue(self.category(name + " 2"))
            self.all(False)                                                       # off again applies to the very next change
            with self.assertRaisesRegex(mcp_server.ToolError, "switched off"):
                mcp_http.local_fetch("categories/remove", {}, {"name": name + " 2"}, ANY)
            self.assertTrue(self.category(name + " 2"))
            self.all(True)
            mcp_http.local_fetch("categories/remove", {}, {"name": name + " 2"}, ANY)
            self.assertFalse(self.category(name + " 2"))
        finally:
            with db.session() as conn:
                conn.execute(delete(Category).where(Category.name.in_([name, name + " 2"])))

    def test_it_brings_churning_and_categorizing_with_it(self):
        self.all(True)
        for scope in ("churning:write", "categorize:write"):
            self.assertEqual(mcp_http.local_fetch("access", {"scope": scope}, None, ANY), {"writes": True})
        mcp_http.local_fetch("churning/cards", {}, {"owner": self.owner, "issuer": "chase", "product": "X", "opened_on": "2025-01-15"}, ANY)
        self.assertEqual(self.cards(), 1)
        self.all(False)
        self.assertEqual(mcp_http.local_fetch("access", {"scope": "churning:write"}, None, ANY), {"writes": False, "why": mcp_http.WRITES_OFF})
        with db.session() as conn:
            mcp_access.set_allow_writes(conn, True)                               # its own switch still works for it
        self.assertEqual(mcp_http.local_fetch("access", {"scope": "churning:write"}, None, ANY), {"writes": True})

    def test_a_delete_is_routed_as_one(self):
        self.all(True)
        rid = mcp_http.local_fetch("rules", {}, {"match": "assistant " + self.tag, "rename": "Assistant"}, ANY)["id"]

        def there():
            with db.session() as conn:
                return conn.execute(select(Rule.id).where(Rule.id == rid)).fetchone() is not None
        try:
            self.assertIn(rid, [r["id"] for r in mcp_http.local_fetch("rules", {}, None, ANY)])
            with self.assertRaisesRegex(mcp_server.ToolError, "Not found"):
                mcp_http.local_fetch(f"rules/{rid}/apply", {}, {}, ANY, "DELETE")      # only routes that are DELETEs
            for method in ("PUT", "PATCH", "HEAD"):
                with self.subTest(method=method), self.assertRaisesRegex(mcp_server.ToolError, "Not found"):
                    mcp_http.local_fetch(f"rules/{rid}", {}, {}, ANY, method)
            self.assertTrue(there())
            mcp_http.local_fetch(f"rules/{rid}", {}, {}, ANY, "DELETE")
            self.assertFalse(there())
        finally:
            with db.session() as conn:
                conn.execute(delete(Rule).where(Rule.id == rid))

    def test_what_is_blocked_is_never_reached(self):
        self.all(True)
        token = self.make_token("read", "write")
        with db.session() as conn:
            grant = conn.execute(select(OAuthGrant.id).where(OAuthGrant.client_id == self.clients[-1])).scalar()
            key = db.get_setting(conn, "openrouter_api_key")
        blocked = [(m, p) for m, p, _fn in server.ROUTES if mcp_access.blocked(p)]
        self.assertGreater(len(blocked), 30)
        for m, p in blocked:
            path = p[len("/api/"):].replace("{id}", str(grant))
            body = None if m == "GET" else {"allow": False, "openrouter_api_key": "sk-changed", "clear": True}
            with self.subTest(route=f"{m} {p}"), self.assertRaisesRegex(mcp_server.ToolError, "^" + mcp_http.OUT_OF_REACH):
                mcp_http.local_fetch(path, {}, body, ANY, m)
        with db.session() as conn:                                               # nothing ran: the switch is on, the key kept
            self.assertTrue(mcp_access.allow_all(conn))
            self.assertEqual(db.get_setting(conn, "openrouter_api_key"), key)
            self.assertIsNone(conn.execute(select(OAuthGrant.revoked).where(OAuthGrant.id == grant)).scalar())
        self.assertTrue(token)

    def test_nothing_outside_the_routes_and_no_other_page(self):
        self.all(True)
        for path in ("backup", "restore", "sync", "investments/sync", "ext/ping", "auth/login", "../oauth/token", "../mcp",
                     "access/x", "endpoints/x", "", "mcp-settings%2Fall", "transactions?x=1"):
            for method, body in (("GET", None), ("POST", {}), ("DELETE", {})):
                with self.subTest(path=path, method=method), self.assertRaisesRegex(mcp_server.ToolError, "Not found"):
                    mcp_http.local_fetch(path, {}, body, ANY, method)
        for path in ("recurring/suggestions/dismissed", "investments/live", "plaid/oauth_resume", "accounts/x/logo-options"):
            with self.subTest(path=path), self.assertRaisesRegex(mcp_server.ToolError, "Not found"):
                mcp_http.local_fetch(path, {}, None, ANY)                          # GET: only the pages listed
        with self.assertRaisesRegex(mcp_server.ToolError, "JSON object"):
            mcp_http.local_fetch("categories", {}, ["x"], ANY)                   # type: ignore[arg-type]

    def test_an_accounts_bank_connection_is_not_changed(self):
        self.all(True)
        with self.assertRaisesRegex(mcp_server.ToolError, "bank connection"):
            mcp_http.local_fetch("accounts/nope", {}, {"display_name": "X", "provider": "plaid"}, ANY)
        with self.assertRaisesRegex(mcp_server.ToolError, "Account not found"):   # without it, the page's own answer
            mcp_http.local_fetch("accounts/nope", {}, {"display_name": "X"}, ANY)

    def test_the_endpoints_page(self):
        with self.assertRaisesRegex(mcp_server.ToolError, "switched off"):
            mcp_http.local_fetch("endpoints", {}, None, ANY)
        self.all(True)
        with self.assertRaisesRegex(mcp_server.ToolError, "reconnect"):
            mcp_http.local_fetch("endpoints", {}, None, WRITE)
        got = mcp_http.local_fetch("endpoints", {}, None, ANY)
        self.assertIn("DELETE /api/transactions/{id} (destructive)", got["transactions"])
        self.assertIn("POST /api/transactions/import", got["transactions"])
        self.assertIn("GET /api/rules", got["rules"])
        flat = [e for es in got.values() for e in es]
        self.assertFalse([e for e in flat if mcp_access.blocked(e.split()[1])])
        self.assertEqual({e.split()[0] + " " + e.split()[1] for e in flat if not e.startswith("GET")}, set(ALLOWED))


class AllowlistTests(unittest.TestCase):
    """What "write" opens, worked out from ROUTES, and what it never does."""

    def test_every_change_is_allowed_or_blocked_and_the_list_is_reviewed(self):
        allowed = {f"{m} {p}" for m, p in mcp_http.ANYTHING}
        self.assertEqual(sorted(allowed), ALLOWED)                               # a new route: look at it, then add it here
        for m, p, _fn in server.ROUTES:
            if m in ("POST", "DELETE"):
                with self.subTest(route=f"{m} {p}"):
                    self.assertNotEqual(f"{m} {p}" in allowed, mcp_access.blocked(p))
        for path in ALLOWED:
            self.assertNotRegex(path, r"mcp|settings|plaid|carta|finnhub|logodev|realie|token|push|connect|logo|backup")

    def test_the_blocked_set(self):
        for p in ("/api/mcp-settings", "/api/mcp-settings/all", "/api/mcp-settings/connections/{id}/revoke", "/api/settings",
                  "/api/connect", "/api/plaid/items/{id}/remove", "/api/carta/connect", "/api/finnhub/settings", "/api/logodev/fetch",
                  "/api/realie/settings", "/api/retail/token", "/api/retail/token/remove", "/api/retail/settings", "/api/push",
                  "/api/push/test", "/api/accounts/{id}/logo", "/api/merchants/logo", "/api/investments/logo", "/api/state"):
            self.assertTrue(mcp_access.blocked(p), p)
        for p in ("/api/settingsx", "/api/retail/tokens", "/api/accounts/{id}", "/api/pushed", "/api/retail/match"):
            self.assertFalse(mcp_access.blocked(p), p)
        gets = {p for m, p, _ in server.ROUTES if m == "GET"}
        for p in (*mcp_access.READABLE, *mcp_access.READABLE_PATTERNS, *mcp_access.WRITE_READABLE):
            self.assertIn(p, gets)
            self.assertFalse(mcp_access.blocked(p), p)

    def test_what_is_destructive(self):
        for m, p in (("DELETE", "/api/rules/{id}"), ("DELETE", "/api/overrides"), ("POST", "/api/categories/remove"),
                     ("POST", "/api/accounts/{id}/remove"), ("POST", "/api/recategorize"), ("POST", "/api/transactions/bulk"),
                     ("POST", "/api/rules/{id}/apply"), ("POST", "/api/ai/apply"), ("POST", "/api/recurring/dismiss")):
            self.assertTrue(mcp_access.destructive(m, p), p)
        for m, p in (("POST", "/api/transactions"), ("POST", "/api/transactions/import"), ("POST", "/api/rules/{id}"),
                     ("POST", "/api/accounts/{id}/restore"), ("POST", "/api/retail/items/{id}/restore")):
            self.assertFalse(mcp_access.destructive(m, p), p)


class SettingsTests(RunwayServer):
    def api(self, path, body=None):
        r = urllib.request.Request(self.base + path, method="POST" if body is not None else "GET",
                                   data=json.dumps(body).encode() if body is not None else None,
                                   headers={"X-Runway": "1", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(r, timeout=20) as resp:
                return resp.status, json.load(resp)
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.load(e)

    def test_the_address_and_the_switch(self):
        status, got = self.api("/api/mcp-settings")
        self.assertEqual(status, 200)
        self.assertEqual({k: v for k, v in got.items() if k != "connections"},
                         {"allow_writes": False, "allow_categorize": False, "allow_all": False, "oauth": True, "url": self.base + "/mcp",
                          "reason": None})
        self.assertIsInstance(got["connections"], list)                          # (listed in the next test)
        self.assertEqual(self.api("/api/mcp-settings/writes", {"allow": True})[1], {"allow_writes": True})
        self.assertTrue(self.api("/api/mcp-settings")[1]["allow_writes"])
        self.assertEqual(self.api("/api/mcp-settings/writes", {})[1], {"allow_writes": False})   # no value: off
        self.assertEqual(self.api("/api/mcp-settings/categorize", {"allow": True})[1], {"allow_categorize": True})
        self.assertEqual({k: v for k, v in self.api("/api/mcp-settings")[1].items() if k.startswith("allow")},
                         {"allow_writes": False, "allow_categorize": True, "allow_all": False})   # one switch doesn't flip another
        self.assertEqual(self.api("/api/mcp-settings/categorize", {})[1], {"allow_categorize": False})
        self.assertEqual(self.api("/api/mcp-settings/all", {"allow": True})[1], {"allow_all": True})
        self.assertEqual({k: v for k, v in self.api("/api/mcp-settings")[1].items() if k.startswith("allow")},
                         {"allow_writes": False, "allow_categorize": False, "allow_all": True})
        self.assertEqual(self.api("/api/mcp-settings/all", {"allow": "no"})[1], {"allow_all": False})

    def test_connections_are_listed_and_revoked(self):
        mine, other = "Claude Code " + self.tag, "Other " + self.tag   # other tests' connections may be listed too
        token = self.make_token("read", "churning:write", name=mine, who="me@example.com")
        self.make_token(name=other)

        def listed():
            return [r for r in self.api("/api/mcp-settings")[1]["connections"] if r["client"] in (mine, other)]
        rows = listed()
        self.assertEqual([(r["client"], r["who"], r["scope"]) for r in rows],
                         [(other, None, ["read"]), (mine, "me@example.com", ["read", "churning:write"])])   # newest first
        self.assertIsNone(rows[1]["last_used"])
        ping = urllib.request.Request(self.base + "/mcp", method="POST", data=b'{"jsonrpc": "2.0", "id": 1, "method": "ping"}',
                                      headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        with urllib.request.urlopen(ping, timeout=20) as r:
            self.assertEqual(r.status, 200)
        self.assertTrue(listed()[1]["last_used"])
        self.assertEqual(self.api(f"/api/mcp-settings/connections/{rows[1]['id']}/revoke", {}), (200, {"ok": True}))
        self.assertEqual([r["client"] for r in listed()], [other])
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(ping, timeout=20)
        self.assertEqual(e.exception.code, 401)
        with db.session() as conn:
            self.assertEqual(conn.execute(select(OAuthGrant.revoked_reason)
                                          .where(OAuthGrant.id == rows[1]["id"])).fetchone()[0],
                             "revoked_in_settings")
        self.assertEqual(self.api(f"/api/mcp-settings/connections/{rows[1]['id']}/revoke", {})[0], 404)
        self.assertEqual(self.api("/api/mcp-settings/connections/nope/revoke", {})[0], 404)

CHURNING = {
    "today": "2026-09-30", "upcoming": [{"owner": "Alex", "title": "Fee"}, {"owner": "Sam", "title": "Bonus"}],
    "five24": {"Alex": {"count": 3}, "Sam": {"count": 5}},
    "cards": [
        {"id": 1, "owner": "Alex", "product": "Venture X", "status": "open", "annual_fee": 395, "benefits": [
            {"name": "Lyft", "kind": "credit", "amount": 10, "remaining": 10, "used": 0, "active": 1, "expiring": True, "days_left": 5},
            {"name": "Travel", "kind": "credit", "amount": 300, "remaining": 150, "used": 150, "active": 1, "expiring": False, "days_left": 90},
            {"name": "Dining", "kind": "credit", "amount": 100, "remaining": 0, "used": 100, "active": 1, "expiring": False, "days_left": 30},
            {"id": 14, "name": "Lounge", "kind": "access", "amount": None, "guests": 2, "used_count": 1, "active": 1, "expiring": False,
             "days_left": None},
            {"name": "Free night", "kind": "other", "amount": None, "used_count": 1, "active": 1, "expiring": False, "days_left": 100},
            {"name": "Old", "kind": "credit", "amount": 5, "remaining": 5, "active": 0}]},
        {"id": 2, "owner": "Sam", "product": "Ink", "status": "open", "benefits": [{"name": "Hotel", "kind": "access", "active": 1, "used_count": 0}]},
        {"id": 3, "owner": "Alex", "product": "Old card", "status": "closed", "benefits": [{"name": "Gone", "kind": "access", "active": 1}]},
    ],
}


class StreamableHttpTests(RunwayServer):
    """POST /mcp: the server, served by Runway itself."""

    def rpc(self, msg, key="", headers=None, method="POST", raw=None):
        h = {"Content-Type": "application/json", **({"Authorization": f"Bearer {key}"} if key else {}), **(headers or {})}
        r = urllib.request.Request(self.base + "/mcp", method=method, headers=h,
                                   data=raw if raw is not None else (json.dumps(msg).encode() if method == "POST" else None))
        try:
            with urllib.request.urlopen(r, timeout=20) as resp:
                return resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.headers, e.read()

    def call(self, key, name, args=None, mid=1):
        status, _, body = self.rpc({"jsonrpc": "2.0", "id": mid, "method": "tools/call", "params": {"name": name, "arguments": args or {}}}, key)
        self.assertEqual(status, 200)
        return json.loads(body)["result"]

    def test_auth_failures_are_401_with_a_challenge(self):
        ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        key = self.make_token()
        for k in ("", "rwa_wrong", key + "x", "rwm_" + key[4:]):
            with self.subTest(key=k):
                status, headers, _ = self.rpc(ping, k)
                self.assertEqual(status, 401)
                self.assertTrue(headers["WWW-Authenticate"].startswith('Bearer realm="Runway", resource_metadata="'))
        status, _, _ = self.rpc(ping, headers={"Authorization": key})              # not a bearer header
        self.assertEqual(status, 401)
        self.assertEqual(self.rpc(ping, key)[0], 200)

    def test_initialize_and_tools_list(self):
        key = self.make_token("read", "churning:write")
        status, headers, body = self.rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}, key)
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("application/json"))
        reply = json.loads(body)
        self.assertEqual((reply["id"], reply["result"]["protocolVersion"], reply["result"]["serverInfo"]["name"]), (1, "2025-06-18", "runway"))
        names = [t["name"] for t in json.loads(self.rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, key)[2])["result"]["tools"]]
        self.assertEqual(names, [t["name"] for t in mcp_server.TOOLS])             # reads only: the switch is off

    def test_a_read_tool_runs_in_process(self):
        key = self.make_token()
        result = self.call(key, "list_accounts")
        self.assertNotIn("isError", result)
        self.assertIsInstance(json.loads(result["content"][0]["text"]), (list, dict))
        self.assertIn("isError", self.call(key, "no_such_tool"))

    def test_a_write_tool_needs_the_scope_and_the_switch(self):
        key, read_only = self.make_token("read", "churning:write"), self.make_token("read")
        fields = {"owner": self.owner, "issuer": "chase", "product": "X", "opened_on": "2025-01-15"}
        result = self.call(key, "add_card", {"fields": fields})
        self.assertTrue(result["isError"])
        self.assertIn("switched off", result["content"][0]["text"])
        self.assertEqual(self.cards(), 0)
        with db.session() as conn:
            mcp_access.set_allow_writes(conn, True)
        listed = json.loads(self.rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, key)[2])["result"]["tools"]
        self.assertIn("add_card", [t["name"] for t in listed])
        listed = json.loads(self.rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, read_only)[2])["result"]["tools"]
        self.assertNotIn("add_card", [t["name"] for t in listed])
        self.assertIn("reconnect", self.call(read_only, "add_card", {"fields": fields})["content"][0]["text"])
        self.assertNotIn("isError", self.call(key, "add_card", {"fields": fields}))
        self.assertEqual(self.cards(), 1)
        with db.session() as conn:
            mcp_access.set_allow_writes(conn, False)                           # and off again applies to the next change
        self.assertTrue(self.call(key, "add_card", {"fields": fields})["isError"])

    def test_a_statement_entered_over_mcp(self):
        from runway.models import Account
        key, acct = self.make_token("read", "write"), "cash-" + self.tag
        with db.session() as conn:
            conn.execute(insert(Account).values(id=acct, name="Wallet " + self.tag, kind="checking", balance=0))
        rows = [{"posted": "2026-09-01", "payee": "Farmers Market", "amount": -12.5}, {"posted": "2026-09-02", "payee": "Refund", "amount": 3},
                {"posted": "2026-09-01", "payee": "farmers  market", "amount": "-12.50"}]
        try:
            self.assertTrue(self.call(key, "add_transactions", {"account": acct, "transactions": rows})["isError"])   # switch off
            with db.session() as conn:
                mcp_access.set_allow_all(conn, True)
            _, _, body = self.rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, key)
            self.assertIn(mcp_server.ANYTHING_RULES, json.loads(body)["result"]["instructions"])
            got = json.loads(self.call(key, "add_transactions", {"account": acct, "transactions": rows})["content"][0]["text"])
            self.assertEqual((got["added"], got["skipped"], got["rows"][2]["status"]), (2, 1, "duplicate"))
            refused = self.call(key, "call_endpoint", {"method": "POST", "path": "/api/mcp-settings/all", "body": {"allow": False}})
            self.assertTrue(refused["isError"])
            self.assertIn("can't reach", refused["content"][0]["text"])
            with db.session() as conn:
                self.assertTrue(mcp_access.allow_all(conn))
                self.assertEqual(conn.execute(select(func.count()).select_from(Transaction).where(Transaction.account_id == acct)).scalar(), 2)
        finally:
            with db.session() as conn:
                conn.execute(delete(Transaction).where(Transaction.account_id == acct))
                conn.execute(delete(Account).where(Account.id == acct))

    def test_the_tools_reach_only_the_allowlists(self):
        key = self.make_token()
        result = self.call(key, "get_order", {"order_id": "../../settings"})
        self.assertTrue(result["isError"])

    def test_notifications_are_202_without_a_body(self):
        key = self.make_token()
        status, _, body = self.rpc({"jsonrpc": "2.0", "method": "notifications/initialized"}, key)
        self.assertEqual((status, body), (202, b""))

    def test_get_is_405(self):
        key = self.make_token()
        for method in ("GET", "DELETE"):
            status, headers, _ = self.rpc(None, key, method=method)
            self.assertEqual((status, headers["Allow"]), (405, "POST"))

    def test_a_foreign_origin_is_refused_even_with_a_token(self):
        key = self.make_token()
        ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        for origin in ("https://evil.example", "null", "http://127.0.0.1:1"):
            with self.subTest(origin=origin):
                self.assertEqual(self.rpc(ping, key, {"Origin": origin})[0], 403)
        self.assertEqual(self.rpc(ping, key, {"Origin": self.base})[0], 200)       # its own address is fine

    def test_bad_bodies(self):
        key = self.make_token()
        status, _, body = self.rpc(None, key, raw=b"{nope")
        self.assertEqual((status, json.loads(body)["error"]["code"]), (400, -32700))
        self.assertEqual(self.rpc([{"jsonrpc": "2.0", "id": 1, "method": "ping"}], key)[0], 400)   # no batches
        status, _, body = self.rpc({"jsonrpc": "2.0", "id": 3, "method": "nope"}, key)
        self.assertEqual((status, json.loads(body)["error"]["code"]), (200, -32601))


class Fake:
    """Stands in for Runway: remembers what was asked and answers from a table."""
    def __init__(self, pages=None):
        self.pages, self.calls = pages or {"churning": CHURNING}, []

    def __call__(self, path, params, body=None, method=None):
        self.calls.append((path, params) if body is None else (path, params, body) if method is None else (path, params, body, method))
        if body is not None:
            return {"ok": True}
        if path not in self.pages:
            raise mcp_server.ToolError("Not found")
        return self.pages[path]


class ScopedFake(Fake):
    """A Fake whose connection may make only the changes of the `allowed` scopes."""
    def __init__(self, allowed):
        super().__init__()
        self.allowed = set(allowed)

    def __call__(self, path, params, body=None, method=None):
        if path == "access":
            self.calls.append((path, params))
            scope = params.get("scope")
            return {"writes": True} if scope in self.allowed else {"writes": False, "why": f"No {scope}."}
        return super().__call__(path, params, body, method)


def mcp_access_match(pattern, path):
    return server.routes._match(pattern, path) is not None


def ask(method, params=None, fake=None, mid=1):
    return mcp_server.handle({"jsonrpc": "2.0", "id": mid, "method": method, **({"params": params} if params is not None else {})}, fake or Fake())


def text(reply):
    return json.loads(reply["result"]["content"][0]["text"])


class ProtocolTests(unittest.TestCase):
    def test_initialize_and_notifications(self):
        r = ask("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
        self.assertEqual(r["id"], 1)
        self.assertEqual(r["result"]["protocolVersion"], "2025-06-18")
        self.assertIn("tools", r["result"]["capabilities"])
        self.assertEqual(ask("initialize", {"protocolVersion": "1999-01-01"})["result"]["protocolVersion"], mcp_server.PROTOCOL_VERSIONS[0])
        self.assertIsNone(mcp_server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, Fake()))
        self.assertEqual(ask("ping")["result"], {})
        self.assertEqual(ask("nope")["error"]["code"], -32601)

    def test_every_tool_is_read_only_and_described(self):
        tools = ask("tools/list")["result"]["tools"]
        self.assertEqual(len({t["name"] for t in tools}), len(tools))
        for t in tools:
            with self.subTest(tool=t["name"]):
                self.assertTrue(t["description"])
                self.assertEqual(t["inputSchema"]["type"], "object")
                self.assertTrue(t["annotations"]["readOnlyHint"])
                self.assertNotRegex(t["name"], r"^(add|set|update|delete|remove|create|save|mark|sync)")

    def test_changing_tools_are_only_offered_when_switched_on(self):
        import unittest.mock as mock
        read_only = {t["name"] for t in ask("tools/list")["result"]["tools"]}
        self.assertEqual(read_only, {t["name"] for t in mcp_server.TOOLS})
        self.assertTrue(read_only.isdisjoint({t["name"] for t in mcp_server.WRITE_TOOLS}))
        fake = Fake()
        off = ask("tools/call", {"name": "mark_benefit_used", "arguments": {"benefit_id": 1}}, fake)
        self.assertTrue(off["result"]["isError"])
        self.assertEqual([c for c in fake.calls if len(c) == 3], [])              # no change was sent to Runway
        told = ask("tools/call", {"name": "add_card", "arguments": {"fields": {}}}, Fake({"access": {"writes": False, "why": "Reconnect."}}))
        self.assertEqual(told["result"]["content"][0]["text"], "Reconnect.")     # Runway says why
        on = Fake({"access": {"writes": True}})
        with mock.patch.dict(os.environ, {}):
            tools = {t["name"]: t for t in ask("tools/list", fake=on)["result"]["tools"]}
            self.assertEqual(set(tools), {t["name"] for t in mcp_server.ALL_TOOLS})
            for t in mcp_server.WRITE_TOOLS + mcp_server.CATEGORIZE_TOOLS:
                with self.subTest(tool=t["name"]):
                    a = tools[t["name"]]["annotations"]
                    self.assertEqual((a["readOnlyHint"], a["destructiveHint"]), (False, False))   # a change, never a delete
                    self.assertNotRegex(t["name"], r"^(remove|delete|drop)")
            for name in read_only:
                self.assertTrue(tools[name]["annotations"]["readOnlyHint"])

    def test_changing_tools_post_what_the_web_forms_send(self):
        import unittest.mock as mock
        fake = Fake()
        calls = [("mark_benefit_used", {"benefit_id": 4, "amount": 25}, ("churning/benefits/4/use", {}, {"amount": 25})),
                 ("undo_benefit_use", {"benefit_id": 4}, ("churning/benefits/4/unuse", {}, {})),
                 ("add_card", {"fields": {"product": "Gold"}}, ("churning/cards", {}, {"product": "Gold"})),
                 ("update_card", {"card_id": 2, "fields": {"notes": "x"}}, ("churning/cards/2", {}, {"notes": "x"})),
                 ("add_benefit", {"card_id": 2, "fields": {"name": "Lyft"}}, ("churning/cards/2/benefits", {}, {"name": "Lyft"})),
                 ("update_benefit", {"benefit_id": 4, "fields": {"amount": 10}}, ("churning/benefits/4", {}, {"amount": 10})),
                 ("complete_card_plan", {"card_id": 2, "on": "2026-10-01"}, ("churning/cards/2/plan/done", {}, {"on": "2026-10-01"})),
                 ("undo_card_plan", {"card_id": 2}, ("churning/cards/2/plan/undo", {}, {})),
                 ("add_task", {"fields": {"card_id": 2}}, ("churning/tasks", {}, {"card_id": 2})),
                 ("update_task", {"task_id": 9, "fields": {"done": 1}}, ("churning/tasks/9", {}, {"done": 1})),
                 ("snooze_task", {"task_id": 9, "days": 7}, ("churning/tasks/9/snooze", {}, {"days": 7})),
                 ("add_planned_item", {"fields": {"product": "Gold"}}, ("churning/wishlist", {}, {"product": "Gold"})),
                 ("update_planned_item", {"wish_id": 3, "fields": {"status": "dropped"}}, ("churning/wishlist/3", {}, {"status": "dropped"}))]
        self.assertEqual({c[0] for c in calls}, {t["name"] for t in mcp_server.WRITE_TOOLS})
        fake = Fake({"access": {"writes": True}})
        with mock.patch.dict(os.environ, {}):
            for name, args, want in calls:
                with self.subTest(tool=name):
                    fake.calls.clear()
                    r = ask("tools/call", {"name": name, "arguments": args}, fake)
                    self.assertNotIn("isError", r["result"], r)
                    self.assertEqual([c for c in fake.calls if len(c) == 3], [want])
            self.assertTrue(ask("tools/call", {"name": "update_card", "arguments": {"card_id": "x", "fields": {}}}, fake)["result"]["isError"])
        from runway.mcp_access import WRITABLE
        for _name, _args, want in calls:   # every path a tool posts to is one the write key may reach
            self.assertTrue(any(mcp_access_match(p, "/api/" + want[0]) for p in WRITABLE), want[0])

    def test_each_scope_offers_only_its_own_changing_tools(self):
        churning, categorize = ({t["name"] for t in ts} for ts in (mcp_server.WRITE_TOOLS, mcp_server.CATEGORIZE_TOOLS))
        read = {t["name"] for t in mcp_server.TOOLS}
        for allowed, want in (((), read), (("churning:write",), read | churning), (("categorize:write",), read | categorize),
                              (("churning:write", "categorize:write"), read | churning | categorize)):
            with self.subTest(allowed=allowed):
                fake = ScopedFake(allowed)
                self.assertEqual({t["name"] for t in ask("tools/list", fake=fake)["result"]["tools"]}, want)
                told = ask("initialize", {}, fake)["result"]["instructions"]
                self.assertEqual("churning tools" in told, "churning:write" in allowed)
                self.assertEqual("category" in told, "categorize:write" in allowed)
        fake = ScopedFake(("churning:write",))
        r = ask("tools/call", {"name": "set_transaction_category", "arguments": {"transaction_id": "a", "category": "Groceries"}}, fake)
        self.assertEqual(r["result"]["content"][0]["text"], "No categorize:write.")   # Runway says why
        self.assertEqual([c for c in fake.calls if len(c) == 3], [])

    def test_write_offers_every_tool_and_the_rest_offer_none_of_its_own(self):
        anything = {t["name"] for t in mcp_server.ANY_TOOLS}
        for allowed in ((), ("churning:write",), ("categorize:write",), ("churning:write", "categorize:write")):
            with self.subTest(allowed=allowed):
                fake = ScopedFake(allowed)
                self.assertTrue(anything.isdisjoint(t["name"] for t in ask("tools/list", fake=fake)["result"]["tools"]))
                r = ask("tools/call", {"name": "add_transaction", "arguments": {"fields": {}}}, fake)
                self.assertEqual(r["result"]["content"][0]["text"], "No write.")    # Runway says why
                self.assertEqual([c for c in fake.calls if len(c) > 2], [])
        fake = ScopedFake(("write",))
        self.assertEqual([t["name"] for t in ask("tools/list", fake=fake)["result"]["tools"]], [t["name"] for t in mcp_server.ALL_TOOLS])
        self.assertEqual([c for c in fake.calls if c[0] == "access"], [("access", {"scope": "write"})])   # one question is enough

    def test_write_tools_ask_first_and_say_when_they_destroy(self):
        tools = {t["name"]: t for t in ask("tools/list", fake=ScopedFake(("write",)))["result"]["tools"]}
        reads = {"list_rules", "preview_rule", "list_endpoints"}
        for t in mcp_server.ANY_TOOLS:
            with self.subTest(tool=t["name"]):
                listed = tools[t["name"]]
                a = listed["annotations"]
                if t["name"] in reads:
                    self.assertTrue(a["readOnlyHint"])
                    self.assertNotIn(mcp_server.ASK, listed["description"])
                    continue
                self.assertFalse(a["readOnlyHint"])
                if t["route"]:
                    want = mcp_access.destructive(*t["route"]) or t["name"] == "remove_budget"
                    self.assertEqual(a["destructiveHint"], want)
                    allowed = (t["route"] in mcp_http.ANYTHING or t["route"][1] in mcp_access.WRITE_READABLE)
                    self.assertTrue(allowed, t["route"])                          # every typed tool's route is one "write" opens
                self.assertTrue(listed["description"].endswith(mcp_server.DESTRUCTIVE if a["destructiveHint"] else mcp_server.ASK))
                self.assertIn(mcp_server.ASK, listed["description"])
        self.assertTrue(tools["call_endpoint"]["annotations"]["destructiveHint"])
        self.assertTrue(tools["add_transactions"]["annotations"]["idempotentHint"])
        self.assertFalse(tools["add_transactions"]["annotations"]["destructiveHint"])
        for t in mcp_server.WRITE_TOOLS + mcp_server.CATEGORIZE_TOOLS:            # the older ones ask first too
            self.assertTrue(t["description"].endswith(mcp_server.ASK), t["name"])
        self.assertEqual(len(tools), len(mcp_server.ALL_TOOLS))

    def test_the_instructions_say_to_ask_before_each_change(self):
        told = ask("initialize", {}, ScopedFake(("write",)))["result"]["instructions"]
        for words in ("explicit yes", "destructive", "never under one blanket yes", "add_transactions", "call_endpoint",
                      "Bank connections, API keys, notifications and these assistant settings are out of reach"):
            self.assertIn(words, told)
        self.assertNotIn("read-only", told)
        self.assertTrue(ask("initialize", {}, ScopedFake(()))["result"]["instructions"].endswith("Everything is read-only."))

    def test_write_tools_send_what_the_web_app_sends(self):
        rows = [{"posted": "2026-09-01", "payee": "Cafe", "amount": -4.5}]
        calls = [("add_transactions", {"account": "cash|1", "transactions": rows}, ("transactions/import", {}, {"account": "cash|1", "transactions": rows})),
                 ("add_transaction", {"fields": {"account": "a", "amount": 5}}, ("transactions", {}, {"account": "a", "amount": 5})),
                 ("update_transaction", {"transaction_id": "a|manual:x/y", "fields": {"notes": "n"}}, ("transactions/a%7Cmanual%3Ax%2Fy", {}, {"notes": "n"})),
                 ("delete_transaction", {"transaction_id": "a|manual:1"}, ("transactions/a%7Cmanual%3A1", {}, {}, "DELETE")),
                 ("remove_budget", {"category": "Dining"}, ("budget", {}, {"category": "Dining", "amount": 0})),
                 ("unlink_transaction_recurring", {"transaction_id": "t"}, ("transactions/t/recurring", {}, {})),
                 ("remove_statement", {"account_id": "card", "statement_date": "2026-09-01"}, ("accounts/card/statements/2026-09-01/remove", {}, {})),
                 ("delete_rule", {"rule_id": 7}, ("rules/7", {}, {}, "DELETE")),
                 ("clear_forecast_amount", {"key": "rec:1:2026-10-01"}, ("overrides", {}, {"key": "rec:1:2026-10-01"}, "DELETE")),
                 ("save_retirement_plan", {"plan": None}, ("investments/plan", {}, {"plan": None})),
                 ("call_endpoint", {"method": "post", "path": "/api/categories/look", "body": {"name": "Dining", "color": "red"}},
                  ("categories/look", {}, {"name": "Dining", "color": "red"})),
                 ("call_endpoint", {"method": "DELETE", "path": "api/rules/3"}, ("rules/3", {}, {}, "DELETE"))]
        fake = ScopedFake(("write",))
        for name, args, want in calls:
            with self.subTest(tool=name):
                fake.calls.clear()
                r = ask("tools/call", {"name": name, "arguments": args}, fake)
                self.assertNotIn("isError", r["result"], r)
                self.assertEqual([c for c in fake.calls if len(c) > 2], [want])
        fake.calls.clear()
        ask("tools/call", {"name": "call_endpoint", "arguments": {"method": "GET", "path": "/api/rules", "body": {"x": 1}, "query": {"a": 1}}}, fake)
        self.assertEqual(fake.calls[-1], ("rules", {"a": 1}))                     # a GET sends no body
        for args in ({"method": "PUT", "path": "/api/rules"}, {"method": "GET"}, {"method": "GET", "path": "/"},
                     {"method": "GET", "path": "rules", "query": ["x"]}):
            with self.subTest(args=args):
                fake.calls.clear()
                self.assertTrue(ask("tools/call", {"name": "call_endpoint", "arguments": args}, fake)["result"]["isError"])
                self.assertEqual([c for c in fake.calls if c[0] != "access"], [])
        for name, args in (("delete_transaction", {}), ("delete_rule", {"rule_id": "x"}), ("remove_statement", {"account_id": "card"})):
            with self.subTest(tool=name, args=args):
                fake.calls.clear()
                self.assertTrue(ask("tools/call", {"name": name, "arguments": args}, fake)["result"]["isError"])
                self.assertEqual([c for c in fake.calls if len(c) > 2], [])

    def test_categorizing_tools_post_what_the_web_app_sends(self):
        calls = [("set_transaction_category", {"transaction_id": "plaid/a b", "category": " Groceries "},
                  ("transactions/plaid%2Fa%20b/category", {}, {"category": "Groceries", "remember": False})),
                 ("set_transaction_category", {"transaction_id": "t1", "category": "Groceries", "remember": True},
                  ("transactions/t1/category", {}, {"category": "Groceries", "remember": True})),
                 ("accept_transaction_category", {"transaction_id": "t1"}, ("transactions/t1/accept", {}, {})),
                 ("set_order_item_category", {"item_id": 7, "category": "Groceries"},
                  ("retail/items/7", {}, {"category": "Groceries", "remember": False})),   # the web app remembers unless told not to
                 ("set_order_item_category", {"item_id": 7, "category": "Groceries", "remember": True},
                  ("retail/items/7", {}, {"category": "Groceries", "remember": True}))]
        self.assertEqual({c[0] for c in calls}, {t["name"] for t in mcp_server.CATEGORIZE_TOOLS})
        fake = ScopedFake(("categorize:write",))
        for name, args, want in calls:
            with self.subTest(tool=name, args=args):
                fake.calls.clear()
                r = ask("tools/call", {"name": name, "arguments": args}, fake)
                self.assertNotIn("isError", r["result"], r)
                self.assertEqual([c for c in fake.calls if len(c) == 3], [want])
                self.assertTrue(any(mcp_access_match(p, "/api/" + want[0]) for p in mcp_access.CATEGORIZABLE), want[0])
        for name, args in (("set_transaction_category", {"transaction_id": "t1"}), ("set_transaction_category", {"category": "Groceries"}),
                           ("set_transaction_category", {"transaction_id": "t1", "category": "  "}),
                           ("set_order_item_category", {"item_id": "x", "category": "Groceries"}), ("accept_transaction_category", {})):
            with self.subTest(tool=name, args=args):
                fake.calls.clear()
                self.assertTrue(ask("tools/call", {"name": name, "arguments": args}, fake)["result"]["isError"])
                self.assertEqual([c for c in fake.calls if len(c) == 3], [])

    def test_every_tool_reads_a_page_the_key_opens(self):
        for t in mcp_server.TOOLS:
            with self.subTest(tool=t["name"]):
                fake = Fake({p: {"items": [], "cards": [], "upcoming": [], "five24": {}, "today": "2026-09-30"}
                             for p in ("overview", "accounts", "transactions", "budget", "categories", "cashflow", "month_pace",
                                       "reports/spending", "reports/income", "reports/merchants", "reports/breakdown", "retail", "networth", "recurring",
                                       "investments", "equity", "churning", "churning/best", "retail/orders/x")})
                r = ask("tools/call", {"name": t["name"], "arguments": {"amount": 20, "order_id": "x"}}, fake)
                self.assertNotIn("isError", r["result"], r)
                for path, *_rest in fake.calls:
                    self.assertTrue("/api/" + path in mcp_access.READABLE or any(mcp_access_match(p, "/api/" + path) for p in mcp_access.READABLE_PATTERNS), path)

    def test_arguments_become_the_query(self):
        fake = Fake({"transactions": {"items": [{"id": "a", "posted": "2026-09-01", "amount": -5, "payee": "Cafe", "logo": "x" * 100, "splits": []}], "total": 9}})
        r = ask("tools/call", {"name": "list_transactions", "arguments": {"month": "2026-09", "search": "cafe", "limit": 500}}, fake)
        self.assertEqual(fake.calls[0], ("transactions", {"month": "2026-09", "account": None, "category": None, "q": "cafe", "limit": 200, "offset": None}))
        self.assertEqual(text(r), {"transactions": [{"id": "a", "posted": "2026-09-01", "amount": -5, "payee": "Cafe"}], "total": 9})   # trimmed

    def test_order_and_breakdown_tools(self):
        pages = {"retail": {"stores": {"costco": {"name": "Costco", "orders": 2, "matched": 1, "unmatched": 1, "last": "x", "junk": 1}},
                            "recent": [{"id": "costco|9", "retailer": "costco"}, {"id": "amazon|1", "retailer": "amazon"}], "token": True},
                 "retail/orders/costco%7C9": {"id": "costco|9", "raw": "{big}", "retailer": "costco", "charges": [{"id": "c"}],
                                              "items": [{"id": 1, "title": "BANANAS", "quantity": 1, "amount": 5, "category": "Groceries", "category_source": "ai", "junk": 1}]},
                 "reports/breakdown": {"categories": [{"category": "Groceries", "total": 5}]}}
        fake = Fake(pages)
        out = text(ask("tools/call", {"name": "list_orders", "arguments": {"retailer": "costco"}}, fake))
        self.assertEqual([o["id"] for o in out["orders"]], ["costco|9"])
        self.assertEqual(out["stores"]["costco"], {"name": "Costco", "orders": 2, "matched": 1, "unmatched": 1, "last": "x"})
        got = text(ask("tools/call", {"name": "get_order", "arguments": {"order_id": "costco|9"}}, fake))
        self.assertEqual(got["items"], [{"id": 1, "title": "BANANAS", "quantity": 1, "amount": 5, "category": "Groceries", "category_source": "ai"}])
        self.assertNotIn("raw", got)
        self.assertEqual(fake.calls[-1][0], "retail/orders/costco%7C9")                # the id is quoted into the path
        self.assertTrue(ask("tools/call", {"name": "get_order", "arguments": {}}, fake)["result"]["isError"])
        text(ask("tools/call", {"name": "spending_breakdown", "arguments": {"start": "2026-09-01", "end": "2026-10-01"}}, fake))
        self.assertEqual(fake.calls[-1], ("reports/breakdown", {"start": "2026-09-01", "end": "2026-10-01"}))

    def test_churning_tools(self):
        cards = text(ask("tools/call", {"name": "churning_cards", "arguments": {}}))
        self.assertEqual([c["product"] for c in cards["cards"]], ["Venture X", "Ink"])       # open only
        self.assertEqual(len(text(ask("tools/call", {"name": "churning_cards", "arguments": {"include_closed": True}}))["cards"]), 3)
        self.assertEqual([c["product"] for c in text(ask("tools/call", {"name": "churning_cards", "arguments": {"owner": "Sam"}}))["cards"]], ["Ink"])
        self.assertEqual([u["title"] for u in text(ask("tools/call", {"name": "churning_upcoming", "arguments": {"owner": "Sam"}}))["upcoming"]], ["Bonus"])
        self.assertEqual(list(text(ask("tools/call", {"name": "churning_five24", "arguments": {"owner": "Alex"}}))["five24"]), ["Alex"])
        b = text(ask("tools/call", {"name": "churning_benefits", "arguments": {}}))
        self.assertEqual([r["name"] for r in b["expiring"]], ["Lyft"])
        self.assertEqual(sorted(r["name"] for r in b["available"]), ["Travel"])
        self.assertEqual(sorted(r["name"] for r in b["used"]), ["Dining", "Free night"])       # not the inactive one, nor a closed card's
        self.assertEqual([(r["id"], r["name"], r["guests"]) for r in b["perks"]], [(14, "Lounge", 2), (None, "Hotel", None)])   # on all year
        only = text(ask("tools/call", {"name": "churning_benefits", "arguments": {"show": "expiring"}}))
        self.assertEqual(list(only), ["today", "expiring"])

    def test_failures_are_reported_to_the_assistant(self):
        for call in ({"name": "no_such_tool", "arguments": {}}, {"name": "get_equity", "arguments": {}},
                     {"name": "list_transactions", "arguments": {"limit": "many"}}):
            with self.subTest(call=call):
                r = ask("tools/call", call, Fake({}))
                self.assertTrue(r["result"]["isError"])
        self.assertEqual(ask("tools/call", {"arguments": {}})["error"]["code"], -32602)

    def test_a_big_reply_is_cut(self):
        fake = Fake({"equity": {"rows": ["x" * 1000] * 500}})
        self.assertLess(len(ask("tools/call", {"name": "get_equity", "arguments": {}}, fake)["result"]["content"][0]["text"]), mcp_server.MAX_TEXT + 200)



if __name__ == "__main__":
    unittest.main()
