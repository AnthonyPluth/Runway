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
            mcp_access.remove_write_token(conn)

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
            self.assertEqual(mcp_access.status(conn), {"token": False, "token_created": None, "write_token": False, "write_token_created": None})
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
        for method, path in (("POST", "/api/mcp/accounts"), ("POST", "/api/mcp/sync")):
            with self.subTest(path=path):
                self.assertEqual(self.get(path, key, method)[0], 404)
        # the read key can never change churning (nor anything else): it's told so, not just refused
        status, said = self.get("/api/mcp/churning/cards", key, "POST")
        self.assertEqual(status, 403)
        self.assertIn("write key", said["error"])

    def test_every_readable_page_is_a_get_route(self):
        gets = {p for m, p, _ in server.ROUTES if m == "GET"}
        self.assertLessEqual(mcp_access.READABLE, gets)
        for path in mcp_access.READABLE:   # read-only: nothing that carries credentials or changes state
            self.assertNotRegex(path, r"plaid|settings|backup|token|key|sync|carta|simplefin")

    def post(self, path, key, body):
        r = urllib.request.Request(self.base + path, method="POST", data=json.dumps(body).encode(),
                                   headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(r, timeout=20) as resp:
                return resp.status, json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null")

    def test_write_key_lifecycle(self):
        with db.session() as conn:
            read, write = mcp_access.new_token(conn), mcp_access.new_write_token(conn)
            self.assertTrue(write.startswith("rww_"))
            self.assertEqual((mcp_access.access(conn, f"Bearer {read}"), mcp_access.access(conn, f"Bearer {write}")), ("read", "write"))
            self.assertIsNone(mcp_access.access(conn, "Bearer rww_nope"))
            self.assertIsNone(mcp_access.access(conn, None))
            self.assertFalse(mcp_access.check_token(conn, f"Bearer {write}"))     # check_token is the read key only
            self.assertNotIn(write, json.dumps([dict(r) for r in conn.execute("SELECT key, value FROM settings").fetchall()]))
            mcp_access.remove_write_token(conn)
            self.assertEqual(mcp_access.access(conn, f"Bearer {write}"), None)
            self.assertEqual(mcp_access.access(conn, f"Bearer {read}"), "read")
        made = self.get("/api/mcp-key/write", method="POST")[1]["token"]
        self.assertTrue(self.get("/api/mcp-key")[1]["write_token"])
        self.assertEqual(self.get("/api/mcp/accounts", made)[0], 200)             # the write key reads too
        self.assertEqual(self.get("/api/mcp-key/write/remove", method="POST")[0], 200)
        self.assertEqual(self.get("/api/mcp/accounts", made)[0], 401)

    def test_write_key_makes_the_listed_churning_changes_and_nothing_else(self):
        with db.session() as conn:
            key = mcp_access.new_write_token(conn)
        status, card = self.post("/api/mcp/churning/cards", key, {"owner": "Alex", "issuer": "chase", "product": "Sapphire Reserve", "opened_on": "2025-01-15",
                                                                    "annual_fee": 550})
        self.assertEqual(status, 200, card)
        cid = card["id"]
        self.assertEqual(self.post(f"/api/mcp/churning/cards/{cid}", key, {"notes": "from an assistant"})[0], 200)
        status, benefit = self.post(f"/api/mcp/churning/cards/{cid}/benefits", key, {"name": "Travel credit", "kind": "credit", "amount": 300, "period": "annual"})
        self.assertEqual(status, 200, benefit)
        bid = benefit["id"]
        self.assertEqual(self.post(f"/api/mcp/churning/benefits/{bid}/use", key, {"amount": 100})[0], 200)
        self.assertEqual(self.post(f"/api/mcp/churning/benefits/{bid}/unuse", key, {})[0], 200)
        status, task = self.post("/api/mcp/churning/tasks", key, {"card_id": cid, "due_on": "2027-01-01", "action": "Call retention"})
        self.assertEqual(status, 200, task)
        self.assertEqual(self.post(f"/api/mcp/churning/tasks/{task['id']}/snooze", key, {"days": 7})[0], 200)
        status, wish = self.post("/api/mcp/churning/wishlist", key, {"owner": "Alex", "kind": "card", "issuer": "amex", "product": "Gold"})
        self.assertEqual(status, 200, wish)
        self.assertEqual(self.post(f"/api/mcp/churning/wishlist/{wish['id']}", key, {"apply_url": "https://example.com/apply"})[0], 200)
        with db.session() as conn:
            self.assertEqual(conn.execute("SELECT notes FROM churn_cards WHERE id=?", (cid,)).fetchone()[0], "from an assistant")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM churn_benefit_uses").fetchone()[0], 0)   # used, then undone
        # never a delete, and nothing outside the list
        for path in (f"/api/mcp/churning/cards/{cid}/remove", f"/api/mcp/churning/benefits/{bid}/remove", f"/api/mcp/churning/tasks/{task['id']}/remove",
                     f"/api/mcp/churning/wishlist/{wish['id']}/remove", "/api/mcp/churning/currencies", "/api/mcp/churning/balances",
                     "/api/mcp/accounts/chk", "/api/mcp/transactions", "/api/mcp/mcp-key", "/api/mcp/mcp-key/write", "/api/mcp/settings", "/api/mcp/backup",
                     "/api/mcp/plaid/status", "/api/mcp/churning/cards/1/extra"):
            with self.subTest(path=path):
                self.assertEqual(self.post(path, key, {})[0], 404)
        self.assertEqual(self.get(f"/api/mcp/churning/cards/{cid}", key, "DELETE")[0], 404)
        self.assertEqual(self.post(f"/api/mcp/churning/cards/{cid}", "rww_wrong", {"notes": "x"})[0], 401)
        # a body that isn't an object is refused
        r = urllib.request.Request(self.base + f"/api/mcp/churning/cards/{cid}", method="POST", data=b"[1]", headers={"Authorization": f"Bearer {key}"})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(r, timeout=20)
        self.assertEqual(cm.exception.code, 400)
        with db.session() as conn:
            conn.execute("DELETE FROM churn_tasks WHERE card_id=?", (cid,))
            conn.execute("DELETE FROM churn_benefits WHERE card_id=?", (cid,))
            conn.execute("DELETE FROM churn_cards WHERE id=?", (cid,))
            conn.execute("DELETE FROM churn_wishlist WHERE id=?", (wish["id"],))   # the database can be shared with other tests (Postgres)

    def test_every_writable_path_is_a_post_route_and_none_removes_anything(self):
        posts = {p for m, p, _ in server.ROUTES if m == "POST"}
        self.assertLessEqual(set(mcp_access.WRITABLE), posts)
        for path in mcp_access.WRITABLE:
            self.assertTrue(path.startswith("/api/churning/"), path)
            self.assertNotRegex(path, r"remove|delete|currenc|balance|score|bank")

    def test_settings_routes_make_and_remove_the_key(self):
        self.assertEqual(self.get("/api/mcp-key")[1], {"token": False, "token_created": None, "write_token": False, "write_token_created": None})
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

    def __call__(self, path, params, body=None):
        self.calls.append((path, params) if body is None else (path, params, body))
        if body is not None:
            return {"ok": True}
        if path not in self.pages:
            raise mcp_server.ToolError("Not found")
        return self.pages[path]


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

    def test_changing_tools_are_only_offered_when_switched_on(self):
        import unittest.mock as mock
        read_only = {t["name"] for t in ask("tools/list")["result"]["tools"]}
        self.assertEqual(read_only, {t["name"] for t in mcp_server.TOOLS})
        self.assertTrue(read_only.isdisjoint({t["name"] for t in mcp_server.WRITE_TOOLS}))
        fake = Fake()
        off = ask("tools/call", {"name": "mark_benefit_used", "arguments": {"benefit_id": 1}}, fake)
        self.assertTrue(off["result"]["isError"])
        self.assertEqual(fake.calls, [])                                          # nothing was sent to Runway
        with mock.patch.dict(os.environ, {"RUNWAY_MCP_ALLOW_WRITES": "1"}):
            tools = {t["name"]: t for t in ask("tools/list")["result"]["tools"]}
            self.assertEqual(set(tools), {t["name"] for t in mcp_server.ALL_TOOLS})
            for t in mcp_server.WRITE_TOOLS:
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
        with mock.patch.dict(os.environ, {"RUNWAY_MCP_ALLOW_WRITES": "1"}):
            for name, args, want in calls:
                with self.subTest(tool=name):
                    fake.calls.clear()
                    r = ask("tools/call", {"name": name, "arguments": args}, fake)
                    self.assertNotIn("isError", r["result"], r)
                    self.assertEqual(fake.calls, [want])
            self.assertTrue(ask("tools/call", {"name": "update_card", "arguments": {"card_id": "x", "fields": {}}}, fake)["result"]["isError"])
        from runway.mcp_access import WRITABLE
        for _name, _args, want in calls:   # every path a tool posts to is one the write key may reach
            self.assertTrue(any(mcp_access_match(p, "/api/" + want[0]) for p in WRITABLE), want[0])

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
