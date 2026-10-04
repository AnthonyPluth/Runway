"""What a request is told when its handler fails, the same for the web app (/api/...) and the assistants (/mcp), which
both go through routes.dispatch: a value it can't use is a 400 saying which; the database busy with a sync is a 503;
anything else is a bug, a 500 with only a reference, logged and reported to Sentry without what the error said."""
import contextlib
import io
import json
import os
import urllib.error
import urllib.request
from unittest import mock

import psycopg.errors
import sentry_sdk
from sqlalchemy.exc import OperationalError

from runway import categories, mcp_server, monitoring
from runway.server import mcp_http, routes
from runway.server.common import ApiError
from tests.test_mcp import READ, RunwayServer
from tests.test_monitoring import DSN, Capture

# What a failing handler's error says: a merchant and an amount, as an exception's text can quote what it was reading.
PRIVATE = "Acme Coffee 12.34"
FAILURES = (KeyError(PRIVATE), TypeError(f"unsupported operand type(s) for +: 'NoneType' and '{PRIVATE}'"),
            ValueError(f"invalid literal for int() with base 10: '{PRIVATE}'"), AttributeError(PRIVATE))
DEADLOCK = OperationalError("UPDATE transactions SET category=?", {}, psycopg.errors.DeadlockDetected("deadlock detected"))


class ErrorTests(RunwayServer):
    def setUp(self):
        super().setUp()
        self.stderr = io.StringIO()   # what's logged (monitoring.report writes the traceback there)
        redirect = contextlib.redirect_stderr(self.stderr)
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)

    def api(self, path):
        r = urllib.request.Request(self.base + path, headers={"X-Runway": "1"})
        try:
            with urllib.request.urlopen(r, timeout=20) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read())

    def mcp_tool(self, key, name, args=None):
        r = urllib.request.Request(self.base + "/mcp", method="POST", headers={"Content-Type": "application/json",
                                                                                "Authorization": f"Bearer {key}"},
                                   data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                                    "params": {"name": name, "arguments": args or {}}}).encode())
        with urllib.request.urlopen(r, timeout=20) as resp:
            return json.loads(resp.read())["result"]

    def assertReported(self, ref: str, kind: str):
        logged = self.stderr.getvalue()
        self.assertIn(f"{kind}: [Filtered]", logged)        # the error's type and where it was raised...
        self.assertIn("api_categories", logged)
        for private in ("Acme", "12.34"):                   # ... never what it said
            self.assertNotIn(private, logged)

    def test_a_value_that_cant_be_read_is_a_400_saying_which(self):
        self.assertEqual(self.api("/api/transactions?limit=lots"), (400, {"error": "The limit must be a whole number"}))
        with self.assertRaisesRegex(mcp_server.ToolError, "^The limit must be a whole number$"):
            mcp_http.local_fetch("transactions", {"limit": "lots"}, None, READ)

    def test_a_bug_in_a_handler_is_a_500_and_reported_on_the_api(self):
        for e in FAILURES:
            with self.subTest(error=type(e).__name__), mock.patch.object(categories, "all_categories", side_effect=e), \
                    mock.patch.object(monitoring, "log") as log:
                status, reply = self.api("/api/categories")
                self.assertEqual(status, 500)
                ref = reply["error"].split("reference ")[1].split(";")[0]
                self.assertEqual(reply["error"], f"Something went wrong on Runway's side (reference {ref}; the details are in its log).")
                log.assert_any_call(f"[error {ref}] GET /api/categories", "error", ref=ref)   # the route, not the address
                self.assertReported(ref, type(e).__name__)

    def test_a_bug_in_a_handler_is_a_500_and_reported_on_mcp(self):
        key = self.make_token()
        for e in FAILURES:
            with self.subTest(error=type(e).__name__), mock.patch.object(categories, "all_categories", side_effect=e):
                with self.assertRaisesRegex(mcp_server.ToolError, r"^Something went wrong on Runway's side \(reference \w+;"):
                    mcp_http.local_fetch("categories", {}, None, READ)
                result = self.mcp_tool(key, "list_categories")   # and through POST /mcp, as an assistant sees it
                self.assertTrue(result["isError"])
                self.assertIn("Something went wrong on Runway's side (reference ", result["content"][0]["text"])
                self.assertNotIn("Acme", result["content"][0]["text"])
                self.assertReported("", type(e).__name__)

    def test_a_busy_database_is_a_503_on_both(self):
        with mock.patch.object(categories, "all_categories", side_effect=DEADLOCK):
            self.assertEqual(self.api("/api/categories"), (503, {"error": routes.BUSY}))
            with self.assertRaisesRegex(mcp_server.ToolError, f"^{routes.BUSY}$"):
                mcp_http.local_fetch("categories", {}, None, READ)
        self.assertEqual(self.stderr.getvalue(), "")   # not a bug: nothing reported

    def test_a_handlers_own_refusal_is_its_status_and_message(self):
        with mock.patch.object(categories, "all_categories", side_effect=ApiError("Not today", 409)):
            self.assertEqual(self.api("/api/categories"), (409, {"error": "Not today"}))
            with self.assertRaisesRegex(mcp_server.ToolError, "^Not today$"):
                mcp_http.local_fetch("categories", {}, None, READ)

    def test_what_sentry_is_sent(self):
        with mock.patch.dict(os.environ, {"SENTRY_DSN": DSN}), mock.patch("builtins.print"):
            self.assertTrue(monitoring.init())
        transport = Capture()
        sentry_sdk.get_client().transport = transport
        self.addCleanup(self.sentry_off)
        found = routes.match("GET", "/api/categories")
        with mock.patch.object(categories, "all_categories", side_effect=FAILURES[2]), self.assertRaises(ApiError) as cm:
            routes.dispatch(found, {}, {})
        sentry_sdk.flush()
        self.assertEqual(cm.exception.status, 500)
        (event,) = transport.events
        exc = event["exception"]["values"][-1]
        self.assertEqual((exc["type"], exc["value"]), ("ValueError", "[Filtered]"))
        self.assertIn(event["tags"]["ref"], str(cm.exception))
        self.assertTrue(any(f.get("function") == "api_categories" for f in exc["stacktrace"]["frames"]))
        self.assertNotIn("Acme", json.dumps(event))
        self.assertNotIn("12.34", json.dumps(event))

    @staticmethod
    def sentry_off():
        sentry_sdk.get_client().close()
        sentry_sdk.init(dsn=None)
        monitoring._enabled = False


class DatabaseErrorTests(RunwayServer):
    def test_a_database_error_keeps_its_sql_and_loses_its_values(self):
        # A database error's text names the row it was writing; reported, its SQL stays (it helps) and the values go.
        orig = psycopg.errors.UndefinedColumn(f'column "payee" does not exist: {PRIVATE}')
        err = OperationalError("UPDATE transactions SET payee=? WHERE id=?", {"payee": PRIVATE, "id": "tx-1"}, orig)

        def fails(_conn):
            raise err from orig   # as SQLAlchemy raises the driver's error
        out = io.StringIO()
        with contextlib.redirect_stderr(out), mock.patch.object(categories, "all_categories", side_effect=fails), \
                self.assertRaises(ApiError) as cm:
            routes.dispatch(routes.match("GET", "/api/categories"), {}, {})
        self.assertEqual(cm.exception.status, 500)
        logged = out.getvalue()
        self.assertIn("[SQL: UPDATE transactions SET payee=? WHERE id=?]", logged)
        self.assertIn("psycopg.errors.UndefinedColumn: [Filtered]", logged)   # the driver's own text, which can quote values
        self.assertNotIn("Acme", logged)
        self.assertNotIn("tx-1", logged)
