"""The MCP server over POST /mcp, and its tools and protocol (runway/mcp_server.py) with Runway stood in for by a fake."""
import json
import os
import unittest

from sqlalchemy import delete, func, insert, select

from runway import db, mcp_access, mcp_server, server
from runway.server import mcp_http
from runway.models import (Account, Transaction)
from tests.shared import fetch
from tests.test_mcp import RunwayServer


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
        return fetch(self.base, method, "/mcp", raw if raw is not None else (json.dumps(msg).encode() if method == "POST" else None), h)

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
    return server.routes.Table([("GET", pattern, None)]).match("GET", path) is not None


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
