"""The MCP server: its read-only key and what it opens (mcp_access, GET /api/mcp/...), and the stdio server itself
(runway/mcp_server.py) with Runway stood in for by a fake."""
import io
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from runway import db, mcp_access, mcp_server, server


class KeyAndPagesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["RUNWAY_DATA"] = cls.tmp.name
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

    def tearDown(self):
        with db.session() as conn:
            mcp_access.remove_token(conn)

    def get(self, path, key=None, method="GET"):
        headers = {"X-Runway": "1", **({"Authorization": f"Bearer {key}"} if key else {})}
        r = urllib.request.Request(self.base + path, method=method, headers=headers, data=b"{}" if method == "POST" else None)
        try:
            with urllib.request.urlopen(r, timeout=20) as resp:
                return resp.status, json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null")

    def make_key(self):
        with db.session() as conn:
            return mcp_access.new_token(conn)

    def test_key_lifecycle(self):
        with db.session() as conn:
            self.assertEqual(mcp_access.status(conn), {"token": False, "token_created": None})
            self.assertFalse(mcp_access.check_token(conn, "Bearer rwm_x"))
            key = mcp_access.new_token(conn)
            self.assertTrue(key.startswith("rwm_"))
            self.assertTrue(mcp_access.status(conn)["token"])
            self.assertTrue(mcp_access.check_token(conn, f"Bearer {key}"))
            self.assertFalse(mcp_access.check_token(conn, f"Bearer {key}x"))
            self.assertFalse(mcp_access.check_token(conn, key))                     # not a bearer header
            self.assertFalse(mcp_access.check_token(conn, None))
            self.assertNotIn(key, json.dumps([dict(r) for r in conn.execute("SELECT key, value FROM settings").fetchall()]))   # only a hash is kept
            replaced = mcp_access.new_token(conn)
            self.assertFalse(mcp_access.check_token(conn, f"Bearer {key}"))
            self.assertTrue(mcp_access.check_token(conn, f"Bearer {replaced}"))
            mcp_access.remove_token(conn)
            self.assertFalse(mcp_access.check_token(conn, f"Bearer {replaced}"))

    def test_other_keys_and_no_key_are_refused(self):
        self.assertEqual(self.get("/api/mcp/accounts")[0], 401)                    # no key made yet
        key = self.make_key()
        self.assertEqual(self.get("/api/mcp/accounts")[0], 401)
        self.assertEqual(self.get("/api/mcp/accounts", "rwm_wrong")[0], 401)
        self.assertEqual(self.get("/api/mcp/accounts", key)[0], 200)

    def test_key_reads_the_listed_pages(self):
        key = self.make_key()
        status, accounts = self.get("/api/mcp/accounts", key)
        self.assertEqual(status, 200)
        self.assertIsInstance(accounts, list)
        self.assertEqual(self.get("/api/mcp/transactions?limit=5", key)[0], 200)
        self.assertEqual(self.get("/api/mcp/churning", key)[0], 200)
        self.assertEqual(self.get("/api/mcp/reports/spending?months=3", key)[0], 200)
        self.assertEqual(self.get("/api/mcp/budget?month=bad", key)[0], 400)      # a handler's own refusal comes through

    def test_key_opens_nothing_else(self):
        key = self.make_key()
        for path in ("/api/mcp/state", "/api/mcp/plaid/status", "/api/mcp/backup", "/api/mcp/retail", "/api/mcp/push",
                     "/api/mcp/mcp-key", "/api/mcp/settings", "/api/mcp/", "/api/mcp/../accounts", "/api/mcp/accounts/x"):
            with self.subTest(path=path):
                self.assertEqual(self.get(path, key)[0], 404)
        for method, path in (("POST", "/api/mcp/accounts"), ("POST", "/api/mcp/churning/cards"), ("POST", "/api/mcp/sync")):
            with self.subTest(path=path):
                self.assertEqual(self.get(path, key, method)[0], 404)

    def test_every_readable_page_is_a_get_route(self):
        gets = {p for m, p, _ in server.ROUTES if m == "GET"}
        self.assertLessEqual(mcp_access.READABLE, gets)
        for path in mcp_access.READABLE:   # read-only: nothing that carries credentials or changes state
            self.assertNotRegex(path, r"plaid|settings|backup|token|key|sync|carta|simplefin")

    def test_settings_routes_make_and_remove_the_key(self):
        self.assertEqual(self.get("/api/mcp-key")[1], {"token": False, "token_created": None})
        status, made = self.get("/api/mcp-key", method="POST")
        self.assertEqual(status, 200)
        self.assertEqual(self.get("/api/mcp/accounts", made["token"])[0], 200)
        self.assertTrue(self.get("/api/mcp-key")[1]["token"])
        self.assertEqual(self.get("/api/mcp-key/remove", method="POST")[0], 200)
        self.assertEqual(self.get("/api/mcp/accounts", made["token"])[0], 401)


CHURNING = {
    "today": "2026-09-30", "upcoming": [{"owner": "Alex", "title": "Fee"}, {"owner": "Sam", "title": "Bonus"}],
    "five24": {"Alex": {"count": 3}, "Sam": {"count": 5}},
    "cards": [
        {"id": 1, "owner": "Alex", "product": "Venture X", "status": "open", "annual_fee": 395, "benefits": [
            {"name": "Lyft", "kind": "credit", "amount": 10, "remaining": 10, "used": 0, "active": 1, "expiring": True, "days_left": 5},
            {"name": "Travel", "kind": "credit", "amount": 300, "remaining": 150, "used": 150, "active": 1, "expiring": False, "days_left": 90},
            {"name": "Dining", "kind": "credit", "amount": 100, "remaining": 0, "used": 100, "active": 1, "expiring": False, "days_left": 30},
            {"name": "Lounge", "kind": "access", "amount": None, "used_count": 1, "active": 1, "expiring": False, "days_left": None},
            {"name": "Old", "kind": "credit", "amount": 5, "remaining": 5, "active": 0}]},
        {"id": 2, "owner": "Sam", "product": "Ink", "status": "open", "benefits": [{"name": "Hotel", "kind": "access", "active": 1, "used_count": 0}]},
        {"id": 3, "owner": "Alex", "product": "Old card", "status": "closed", "benefits": [{"name": "Gone", "kind": "access", "active": 1}]},
    ],
}


class Fake:
    """Stands in for Runway: remembers what was asked and answers from a table."""
    def __init__(self, pages=None):
        self.pages, self.calls = pages or {"churning": CHURNING}, []

    def __call__(self, path, params):
        self.calls.append((path, params))
        if path not in self.pages:
            raise mcp_server.ToolError("Not found")
        return self.pages[path]


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
        self.assertIsNone(mcp_server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
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

    def test_every_tool_reads_a_page_the_key_opens(self):
        for t in mcp_server.TOOLS:
            with self.subTest(tool=t["name"]):
                fake = Fake({p: {"items": [], "cards": [], "upcoming": [], "five24": {}, "today": "2026-09-30"}
                             for p in ("overview", "accounts", "transactions", "budget", "categories", "cashflow", "month_pace",
                                       "reports/spending", "reports/income", "reports/merchants", "networth", "recurring",
                                       "investments", "equity", "churning", "churning/best")})
                r = ask("tools/call", {"name": t["name"], "arguments": {"amount": 20}}, fake)
                self.assertNotIn("isError", r["result"], r)
                for path, _ in fake.calls:
                    self.assertIn("/api/" + path, mcp_access.READABLE)

    def test_arguments_become_the_query(self):
        fake = Fake({"transactions": {"items": [{"id": "a", "posted": "2026-09-01", "amount": -5, "payee": "Cafe", "logo": "x" * 100, "splits": []}], "total": 9}})
        r = ask("tools/call", {"name": "list_transactions", "arguments": {"month": "2026-09", "search": "cafe", "limit": 500}}, fake)
        self.assertEqual(fake.calls[0], ("transactions", {"month": "2026-09", "account": None, "category": None, "q": "cafe", "limit": 200, "offset": None}))
        self.assertEqual(text(r), {"transactions": [{"id": "a", "posted": "2026-09-01", "amount": -5, "payee": "Cafe"}], "total": 9})   # trimmed

    def test_churning_tools(self):
        cards = text(ask("tools/call", {"name": "churning_cards", "arguments": {}}))
        self.assertEqual([c["product"] for c in cards["cards"]], ["Venture X", "Ink"])       # open only
        self.assertEqual(len(text(ask("tools/call", {"name": "churning_cards", "arguments": {"include_closed": True}}))["cards"]), 3)
        self.assertEqual([c["product"] for c in text(ask("tools/call", {"name": "churning_cards", "arguments": {"owner": "Sam"}}))["cards"]], ["Ink"])
        self.assertEqual([u["title"] for u in text(ask("tools/call", {"name": "churning_upcoming", "arguments": {"owner": "Sam"}}))["upcoming"]], ["Bonus"])
        self.assertEqual(list(text(ask("tools/call", {"name": "churning_five24", "arguments": {"owner": "Alex"}}))["five24"]), ["Alex"])
        b = text(ask("tools/call", {"name": "churning_benefits", "arguments": {}}))
        self.assertEqual([r["name"] for r in b["expiring"]], ["Lyft"])
        self.assertEqual(sorted(r["name"] for r in b["available"]), ["Hotel", "Travel"])
        self.assertEqual(sorted(r["name"] for r in b["used"]), ["Dining", "Lounge"])           # not the inactive one, nor a closed card's
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

    def test_serve_reads_lines_and_answers_them(self):
        out = io.StringIO()
        lines = [json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}), "", "not json",
                 json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}), json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"})]
        mcp_server.serve(io.StringIO("\n".join(lines) + "\n"), out, Fake())
        replies = [json.loads(x) for x in out.getvalue().splitlines()]
        self.assertEqual([r.get("id") for r in replies], [1, None, 2])
        self.assertEqual(replies[1]["error"]["code"], -32700)

    def test_talking_to_a_real_runway(self):
        # http_fetch against a server that checks the key, and a missing key
        tmp = tempfile.TemporaryDirectory()
        os.environ["RUNWAY_DATA"] = tmp.name
        try:
            db.init()
            with db.session() as conn:
                key = mcp_access.new_token(conn)
            httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            old = {k: os.environ.get(k) for k in ("RUNWAY_URL", "RUNWAY_MCP_KEY")}
            try:
                os.environ["RUNWAY_URL"] = f"http://127.0.0.1:{httpd.server_port}"
                os.environ["RUNWAY_MCP_KEY"] = key
                self.assertEqual(mcp_server.http_fetch("accounts", {}), [])   # a fresh database: no accounts
                os.environ["RUNWAY_MCP_KEY"] = "rwm_wrong"
                with self.assertRaisesRegex(mcp_server.ToolError, "doesn't know this key"):
                    mcp_server.http_fetch("accounts", {})
                os.environ["RUNWAY_MCP_KEY"] = ""
                with self.assertRaisesRegex(mcp_server.ToolError, "RUNWAY_MCP_KEY"):
                    mcp_server.http_fetch("accounts", {})
                os.environ["RUNWAY_MCP_KEY"] = key
                os.environ["RUNWAY_URL"] = "http://127.0.0.1:1"
                with self.assertRaisesRegex(mcp_server.ToolError, "Can't reach Runway"):
                    mcp_server.http_fetch("accounts", {})
            finally:
                httpd.shutdown()
                httpd.server_close()
                for k, v in old.items():
                    os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
                with db.session() as conn:   # the database can be shared with other tests (Postgres): leave no key behind
                    mcp_access.remove_token(conn)
        finally:
            tmp.cleanup()
            os.environ.pop("RUNWAY_DATA", None)


if __name__ == "__main__":
    unittest.main()
