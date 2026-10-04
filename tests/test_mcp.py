"""The MCP server: what an assistant connected with OAuth may reach (mcp_access, mcp_http.local_fetch), POST /mcp, the
Settings routes for it, and the tools and protocol (runway/mcp_server.py) with Runway stood in for by a fake. OAuth
itself is tested in tests/test_mcp_oauth.py and tests/test_mcp_oauth_http.py."""
import base64
import hashlib
import json
import unittest
import urllib.error
import urllib.request

from sqlalchemy import delete, func, insert, select, update

from runway import db, mcp_access, mcp_oauth, mcp_server, server
from runway.server import mcp_http
from runway.models import (Account, Category, ChurnBenefit, ChurnBenefitUse, ChurnCard, ChurnTask, ChurnWish, OAuthGrant, RetailItem,
                           RetailItemMemory, RetailOrder, Rule, Transaction, TxSplit)
from tests.shared import ServerCase, forget_oauth, tag

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


class RunwayServer(ServerCase):
    """A real Runway on a database of its own (tests/shared.py's own_database, for the whole class; on Postgres its own
    schema), and OAuth tokens for it. Nothing else writes to it, so the churning switch needn't be held; each test still
    removes what it made and turns the switches off, so the tests don't depend on each other's order."""
    unset = ("RUNWAY_PUBLIC_URL",)      # (own_database's environment goes back after the server has stopped)

    def setUp(self):
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
        tx, item, acct = "mcp-" + self.tag, None, "mcp-acct-" + self.tag
        with db.session() as conn:
            conn.execute(insert(Account).values(id=acct, name="Checking", kind="checking"))
            conn.execute(insert(Transaction).values(id=tx, account_id=acct, posted="2026-09-20", amount=-12,
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
                conn.execute(delete(Account).where(Account.id == acct))


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
        return self.req("POST" if body is not None else "GET", path, body)

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
