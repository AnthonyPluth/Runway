import io
import json
import os
import threading
import time
import unittest
import urllib.request
from unittest import mock


import sentry_sdk

from runway import categorize, db, monitoring, server, simplefin
from runway.server import sync
from runway.server.handler import _traced, trace_name

from tests.shared import own_database

DSN = "https://publickey@o123.ingest.us.sentry.io/456"
SIMPLEFIN = "https://user:secretpass@beta-bridge.simplefin.org/simplefin"


class Capture(sentry_sdk.transport.Transport):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.events = []
        self.items: list[tuple[str, object]] = []   # everything else: transactions, check-ins, logs, metrics

    def capture_envelope(self, envelope):
        self.events += [i.payload.json for i in envelope.items if i.type == "event"]
        self.items += [(i.type, i.payload.json) for i in envelope.items if i.type != "event"]

    def of(self, kind):
        return [p for t, p in self.items if t == kind]


ALL_ON = {"SENTRY_DSN": DSN, "SENTRY_TRACES_SAMPLE_RATE": "1", "SENTRY_LOGS": "1", "SENTRY_METRICS": "1", "SENTRY_CRONS": "1"}
# Errors only: everything that's on by default with a DSN, turned off.
ERRORS_ONLY = {"SENTRY_DSN": DSN, **{f"SENTRY_{k}_SAMPLE_RATE": "0" for k in ("TRACES", "PROFILE_SESSION", "REPLAY", "REPLAY_ON_ERROR")},
               **{f"SENTRY_{k}": "0" for k in ("LOGS", "METRICS", "CRONS", "FEEDBACK", "AI_CONTENT")}}


def start(env=None) -> Capture:
    """Reporting on (with everything in ALL_ON unless `env` says otherwise), sending to a Capture instead of Sentry."""
    with mock.patch.dict(os.environ, env or ALL_ON), mock.patch("builtins.print"):
        assert monitoring.init()
    transport = Capture()
    sentry_sdk.get_client().transport = transport
    return transport


class MonitoringTests(unittest.TestCase):
    def tearDown(self):
        sentry_sdk.get_client().close()
        sentry_sdk.init(dsn=None)
        monitoring._enabled, monitoring._opts = False, {}

    def test_off_without_a_dsn(self):
        with mock.patch.dict(os.environ, {"SENTRY_DSN": ""}):
            self.assertFalse(monitoring.init())
            self.assertIsNone(monitoring.browser_config())
            self.assertNotIn("sentry.io", server.content_security_policy("n"))
            try:
                raise RuntimeError("boom")
            except RuntimeError:
                with mock.patch("traceback.print_exc"):
                    monitoring.report()   # just logged

    def test_what_a_service_said_is_kept_safe(self):
        # A bank's message (through SimpleFIN or Plaid) or an API's error is kept and shown: no account numbers in it.
        self.assertEqual(monitoring.public_text("Chase: account 123456789 needs a new login (HTTP 401)"),
                         "Chase: account [number] needs a new login (HTTP 401)")
        self.assertEqual(monitoring.public_text(f"refused at {SIMPLEFIN}/accounts?x=1"),
                         monitoring.scrub(f"refused at {SIMPLEFIN}/accounts?x=1"))
        self.assertIsNone(monitoring.public_text(None))

    def test_secrets_are_blanked(self):
        text = f"Couldn't reach {SIMPLEFIN}/accounts?start-date=1 with access-production-1234abcd-9f00-4c1e"
        out = monitoring.scrub(text)
        for secret in ("secretpass", "user:", "start-date", "1234abcd"):
            self.assertNotIn(secret, out)
        self.assertIn("beta-bridge.simplefin.org/simplefin/accounts", out)

    def test_a_report_carries_the_error_and_nothing_private(self):
        with mock.patch.dict(os.environ, {"SENTRY_DSN": DSN, "RUNWAY_VERSION": "v9.9.9"}):
            self.assertTrue(monitoring.init())
        transport = Capture()
        sentry_sdk.get_client().transport = transport
        balance = sum([1000, 234.56])   # a local variable: its value must not be sent  # noqa: F841
        try:
            raise ValueError(f"SimpleFIN said no: {SIMPLEFIN}?token=abc")
        except ValueError:
            with mock.patch("traceback.print_exc"):
                monitoring.report(ref="abcd1234")
        sentry_sdk.flush()
        self.assertEqual(len(transport.events), 1)
        ev = transport.events[0]
        self.assertEqual((ev["release"], ev["tags"]["ref"]), ("v9.9.9", "abcd1234"))
        exc = ev["exception"]["values"][0]
        self.assertEqual(exc["type"], "ValueError")
        self.assertNotIn("secretpass", exc["value"])
        self.assertNotIn("token=abc", exc["value"])
        self.assertTrue(all("vars" not in f for f in exc["stacktrace"]["frames"]))
        self.assertNotIn("1234.56", str(ev))

    def test_database_errors_lose_the_row_they_were_writing(self):
        import sqlalchemy as sa
        engine = sa.create_engine("sqlite://")
        with engine.begin() as c:
            c.execute(sa.text("CREATE TABLE tx (id INTEGER PRIMARY KEY, payee TEXT, amount REAL)"))
            c.execute(sa.text("INSERT INTO tx VALUES (1, 'WHOLE FOODS', 87.12)"))
        transport = start({"SENTRY_DSN": DSN})
        try:
            with engine.begin() as c:
                c.execute(sa.text("INSERT INTO tx VALUES (:id, :payee, :amount)"), {"id": 1, "payee": "WHOLE FOODS", "amount": 87.12})
        except sa.exc.IntegrityError as e:
            self.assertIn("WHOLE FOODS", str(e))   # what SQLAlchemy says, and the local log keeps
            with mock.patch("traceback.print_exception"):
                monitoring.report(e)
        sentry_sdk.flush()
        value = transport.events[0]["exception"]["values"][-1]["value"]
        self.assertNotIn("WHOLE FOODS", value)
        self.assertNotIn("87.12", value)
        self.assertIn("[SQL: INSERT INTO tx VALUES", value)   # the query itself helps, and holds no data
        self.assertIn("[parameters: [Filtered]]", value)
        # Postgres's own details name the values too.
        pg = ("(psycopg.errors.NotNullViolation) null value in column \"category\" violates not-null constraint\n"
              "DETAIL:  Failing row contains (tx-9, 2026-09-01, -87.12, WHOLE FOODS, null).\n"
              "[SQL: INSERT INTO transactions ...]\n[parameters: {'id': 'tx-9', 'amount': -87.12}]\n"
              "(Background on this error at: https://sqlalche.me/e/20/gkpj)")
        dup = "DETAIL:  Key (plaid_account_id)=(p-csp) already exists."
        out = monitoring.scrub(pg) + monitoring.scrub(dup)
        for private in ("WHOLE FOODS", "87.12", "tx-9", "p-csp"):
            self.assertNotIn(private, out)
        self.assertIn("Failing row contains ([Filtered])", out)
        self.assertIn("Key (plaid_account_id)=([Filtered]) already exists", out)
        self.assertIn("(Background on this error at: https://sqlalche.me/e/20/gkpj)", out)
        # Text that repeats a marker is scrubbed in linear time (a regex could take minutes on it).
        import time
        for marker in ("[parameters: ", "Failing row contains (", "Key (", "Key ()=("):
            started = time.monotonic()
            monitoring.scrub(marker * 50_000)
            self.assertLess(time.monotonic() - started, 1, marker)

    def test_request_details_are_trimmed(self):
        ev = monitoring._before_send({"request": {"method": "POST", "url": "https://runway.example/api/sync?x=1",
                                                  "data": {"amount": 5}, "cookies": {"runway_session": "s"},
                                                  "headers": {"Authorization": "Bearer t"}},
                                      "user": {"email": "a@b.c"}, "extra": {"body": "x"}}, {})
        self.assertEqual(ev, {"request": {"method": "POST", "url": "https://runway.example/api/sync"}, "message": None})
        crumb = monitoring._before_breadcrumb({"category": "httplib", "data": {"url": SIMPLEFIN + "/accounts?a=1",
                                               "http.query": "a=1"}}, {})
        self.assertEqual(crumb["data"], {"url": "https://beta-bridge.simplefin.org/simplefin/accounts"})
        crumb = monitoring._before_breadcrumb({"category": "query", "message": "SELECT 1 WHERE x=?", "data": {"params": [5]}}, {})
        self.assertNotIn("data", crumb)

    def test_the_web_app_may_report_to_sentry_only(self):
        with mock.patch.dict(os.environ, {"SENTRY_DSN": DSN, "SENTRY_BROWSER_DSN": ""}):
            self.assertEqual(monitoring.browser_config()["dsn"], DSN)
            self.assertIn("connect-src 'self' https://production.plaid.com https://o123.ingest.us.sentry.io;",
                          server.content_security_policy("n").replace(server.PLAID_API, "https://production.plaid.com"))
        with mock.patch.dict(os.environ, {"SENTRY_DSN": DSN, "RUNWAY_SENTRY_BROWSER": "0"}):
            self.assertIsNone(monitoring.browser_config())
        with mock.patch.dict(os.environ, {"SENTRY_DSN": "http://k@evil.example/1"}):   # not https: never allowed
            self.assertIsNone(monitoring.browser_config())
            self.assertIsNone(monitoring.browser_origin())


    def test_the_web_app_is_told_which_features_are_on(self):
        with mock.patch.dict(os.environ, ERRORS_ONLY):
            cfg = monitoring.browser_config()
            self.assertEqual((cfg["traces"], cfg["profiles"], cfg["replays"], cfg["logs"], cfg["feedback"]), (0, 0, 0, False, False))
            self.assertFalse(monitoring.browser_profiling())
        with mock.patch.dict(os.environ, {"SENTRY_DSN": DSN, "SENTRY_TRACES_SAMPLE_RATE": "0.5",
                                          "SENTRY_PROFILE_SESSION_SAMPLE_RATE": "1", "SENTRY_REPLAY_SAMPLE_RATE": "2",
                                          "SENTRY_REPLAY_ON_ERROR_SAMPLE_RATE": "x", "SENTRY_LOGS": "1", "SENTRY_FEEDBACK": "true"}):
            cfg = monitoring.browser_config()
            self.assertEqual((cfg["traces"], cfg["profiles"], cfg["replays"], cfg["replays_on_error"], cfg["logs"], cfg["feedback"]),
                             (0.5, 1.0, 1.0, 0.0, True, True))
            self.assertTrue(monitoring.browser_profiling())
        with mock.patch.dict(os.environ, {**ERRORS_ONLY, "SENTRY_PROFILE_SESSION_SAMPLE_RATE": "1"}):
            self.assertEqual(monitoring.browser_config()["profiles"], 0)   # profiling needs tracing

    def test_everything_is_on_with_just_a_dsn(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("SENTRY_")}
        with mock.patch.dict(os.environ, {**env, "SENTRY_DSN": DSN, "SENTRY_TRACES_SAMPLE_RATE": ""}, clear=True):
            cfg = monitoring.browser_config()
            self.assertEqual((cfg["traces"], cfg["profiles"], cfg["replays"], cfg["replays_on_error"], cfg["logs"], cfg["feedback"]),
                             (1.0, 1.0, 1.0, 1.0, True, True))   # an empty rate (compose's default) is an unset one
            self.assertTrue(monitoring.browser_profiling())
        transport = start({**env, "SENTRY_DSN": DSN})
        self.assertEqual(monitoring._opts, {"traces": 1.0, "profiles": 1.0, "logs": True, "metrics": True, "crons": True,
                                            "ai_content": True})
        opts = sentry_sdk.get_client().options
        self.assertEqual((opts["traces_sample_rate"], opts["profile_session_sample_rate"]), (1.0, 1.0))
        with mock.patch("builtins.print"):
            monitoring.log("hello")
        sentry_sdk.flush()
        self.assertTrue(transport.of("log"))
        # Each can still be turned off.
        with mock.patch.dict(os.environ, {**ERRORS_ONLY, "SENTRY_LOGS": "false"}):
            cfg = monitoring.browser_config()
            self.assertEqual((cfg["traces"], cfg["replays"], cfg["logs"], cfg["feedback"]), (0, 0, False, False))

    def test_off_features_send_nothing(self):
        transport = start(ERRORS_ONLY)
        with monitoring.request("GET", "/api/state", {}) as tx, mock.patch("builtins.print"):
            monitoring.log("hello")
            monitoring.metric("count", "runway.test", 1)
            self.assertIsNone(monitoring.cron_start("x", "0 7 * * *"))
        self.assertIsNone(tx)
        self.assertEqual(monitoring.trace_meta(), "")
        sentry_sdk.flush()
        self.assertEqual([t for t, _ in transport.items if t != "sessions"], [])

    def test_requests_are_named_by_route(self):
        cases = {"/api/transactions/chk%7C0/category": "/api/transactions/{id}/category", "/api/state": "/api/state",
                 "/api/merchants/starbucks/logo": "/api/merchants/{id}/logo", "/oauth/token": "/oauth/token",
                 "/oauth/authorize": "/oauth/authorize", "/.well-known/oauth-protected-resource/mcp": "/.well-known/oauth-protected-resource/mcp",
                 "/oauth/whatever-1234": "/oauth/*", "/.well-known/security.txt": "/.well-known/*",
                 "/api/sync": "/api/sync", "/api/ext/ping": "/api/ext/ping", "/api/nope/secret-name": "/api/*",
                 "/": "/", "/plaid/oauth": "/", "/auth/callback": "/auth/callback", "/auth/whatever": "/"}
        for path, name in cases.items():
            self.assertEqual(trace_name(path), name, path)
        for path in ("/healthz", "/api/investments/stream", "/assets/index-abc.js", "/sw.js", "/logo.svg"):
            self.assertFalse(_traced(path), path)
        for path in ("/", "/api/state", "/mcp", "/plaid/oauth", "/oauth/token", "/.well-known/oauth-authorization-server"):
            self.assertTrue(_traced(path), path)

    def test_a_request_is_traced_without_its_query_or_values(self):
        own_database(self)
        transport = start()
        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        trace_id = "abcdef0123456789abcdef0123456789"
        req = urllib.request.Request(f"http://127.0.0.1:{httpd.server_port}/api/transactions?q=rent-money&limit=5",
                                     headers={"sentry-trace": f"{trace_id}-1234567890abcdef-1"})
        with mock.patch("builtins.print"):
            with urllib.request.urlopen(req, timeout=20) as r:
                self.assertEqual(r.status, 200)
            urllib.request.urlopen(f"http://127.0.0.1:{httpd.server_port}/healthz", timeout=20).close()
        # The server finishes a request's transaction just after it has sent the response, so on a busy machine the
        # transaction can still be on its way: wait for it (briefly) rather than read the transport too soon.
        deadline = time.monotonic() + 10
        while True:
            sentry_sdk.flush()
            txs = transport.of("transaction")
            if txs or time.monotonic() > deadline:
                break
            time.sleep(0.05)
        self.assertEqual([t["transaction"] for t in txs], ["GET /api/transactions"])   # /healthz isn't traced
        tx = txs[0]
        self.assertEqual(tx["contexts"]["trace"]["trace_id"], trace_id)   # continues the web app's trace
        self.assertEqual(tx["contexts"]["trace"]["data"]["http.response.status_code"], 200)
        self.assertTrue(any(sp["op"] == "db" for sp in tx["spans"]))   # database queries, as spans
        self.assertNotIn("rent-money", json.dumps(tx))
        logs = json.dumps(transport.of("log"))
        self.assertIn("GET /api/transactions 200", logs)
        self.assertNotIn("127.0.0.1", logs)   # the access log line in Sentry has no address

    def test_spans_lose_queries_and_credentials(self):
        tx = monitoring._before_send_transaction({
            "request": {"url": "https://runway.test/api/x?q=1", "headers": {"a": "b"}}, "user": {"id": "u"},
            "spans": [{"op": "http.client", "description": f"GET {SIMPLEFIN}/accounts?start-date=1",
                       "data": {"url": SIMPLEFIN + "/accounts", "http.query": "start-date=1", "db.params": [5]}}],
        }, {})
        text = json.dumps(tx)
        for secret in ("secretpass", "start-date", "headers", '"user"', "db.params"):
            self.assertNotIn(secret, text)
        self.assertEqual(tx["spans"][0]["data"]["url"], "https://beta-bridge.simplefin.org/simplefin/accounts")

    def test_background_work_is_traced_and_timed(self):
        transport = start()
        with monitoring.task("bank sync"), monitoring.span("db", "SELECT 1"):
            pass
        with self.assertRaises(RuntimeError), monitoring.task("bank sync"):
            raise RuntimeError("no")
        sentry_sdk.flush()
        self.assertEqual([t["transaction"] for t in transport.of("transaction")][:1], ["bank sync"])
        metrics = json.dumps(transport.items)
        self.assertIn("runway.task.duration", metrics)
        self.assertIn('"error"', metrics)

    def test_profiling_runs_with_traces_only(self):
        start({**ALL_ON, "SENTRY_PROFILE_SESSION_SAMPLE_RATE": "0.25"})
        opts = sentry_sdk.get_client().options
        self.assertEqual((opts["profile_session_sample_rate"], opts["profile_lifecycle"]), (0.25, "trace"))
        start({**ERRORS_ONLY, "SENTRY_PROFILE_SESSION_SAMPLE_RATE": "1"})   # no tracing: nothing to profile
        self.assertEqual(sentry_sdk.get_client().options["profile_session_sample_rate"], 0)
        self.assertEqual(sentry_sdk.get_client().options["trace_propagation_targets"], [])   # no headers to banks

    def test_a_bank_sync_checks_in_and_one_that_cant_start_doesnt(self):
        own_database(self, TZ="America/Chicago")
        transport = start({**ALL_ON, "TZ": "America/Chicago"})
        with db.session() as conn:
            db.set_setting(conn, "simplefin_access_url", SIMPLEFIN)
        with mock.patch("runway.simplefin.sync", return_value={"new": [], "errors": []}), mock.patch("builtins.print"):
            sync.run_sync()   # the Sync button's, or the daily one: either counts
            self.assertTrue(sync._sync_lock.acquire(blocking=False))
            try:   # another sync is running: this one doesn't start, so it's neither a success nor a failure
                with self.assertRaises(sync.ApiError):
                    sync.run_sync()
            finally:
                sync._sync_lock.release()
        with mock.patch("runway.simplefin.sync", side_effect=simplefin.SimpleFinError("bank said no")), \
                mock.patch("builtins.print"), self.assertRaises(sync.ApiError):
            sync.run_sync()
        # With automatic syncing off, a manual sync checks in without a schedule: it doesn't create a daily monitor.
        with mock.patch.object(sync, "AUTO_SYNC", False), mock.patch("builtins.print"), \
                mock.patch("runway.simplefin.sync", return_value={"new": [], "errors": []}):
            sync.run_sync()
        with db.session() as conn:   # nothing connected: nothing to check in
            db.set_setting(conn, "simplefin_access_url", None)
        with self.assertRaises(sync.ApiError):
            sync.run_sync()
        sentry_sdk.flush()
        checkins = transport.of("check_in")
        self.assertEqual([c["status"] for c in checkins], ["in_progress", "ok", "in_progress", "error", "in_progress", "ok"])
        self.assertTrue(all("monitor_config" not in c for c in checkins[4:]))
        self.assertEqual({c["monitor_slug"] for c in checkins}, {"runway-bank-sync"})
        self.assertEqual(checkins[0]["monitor_config"]["schedule"], {"type": "crontab", "value": f"0 {sync.DAILY_SYNC_HOUR} * * *"})
        self.assertEqual(checkins[0]["monitor_config"]["timezone"], "America/Chicago")
        self.assertEqual(checkins[0]["check_in_id"], checkins[1]["check_in_id"])

    def test_the_monitor_uses_runways_own_time_zone(self):
        for tz, zone in (("America/Chicago", "America/Chicago"), (":Europe/Berlin", "Europe/Berlin"), ("UTC", "UTC"),
                         ("EST5EDT", None), ("CST6CDT,M3.2.0,M11.1.0", None), ("/usr/share/zoneinfo/Asia/Tokyo", None)):
            with mock.patch.dict(os.environ, {"TZ": tz}):
                self.assertEqual(monitoring.local_timezone(), zone, tz)
        env = {k: v for k, v in os.environ.items() if k != "TZ"}
        with mock.patch.dict(os.environ, env, clear=True):   # no TZ: the system's zone
            with mock.patch("os.path.realpath", return_value="/usr/share/zoneinfo/America/Denver"):
                self.assertEqual(monitoring.local_timezone(), "America/Denver")
            with mock.patch("os.path.realpath", return_value="/etc/localtime"), \
                    mock.patch("builtins.open", mock.mock_open(read_data="Australia/Perth\n")):
                self.assertEqual(monitoring.local_timezone(), "Australia/Perth")
        # A zone that can't be told: the check-in doesn't create the monitor (its schedule would be off by hours).
        transport = start({**ALL_ON, "TZ": "EST5EDT"})
        with mock.patch.dict(os.environ, {"TZ": "EST5EDT"}):
            monitoring.cron_finish(monitoring.cron_start("runway-bank-sync", "0 7 * * *"), True)
        sentry_sdk.flush()
        self.assertTrue(all("monitor_config" not in c for c in transport.of("check_in")))
        self.assertEqual(len(transport.of("check_in")), 2)

    def test_logs_are_scrubbed_and_can_say_less_than_the_console(self):
        transport = start()
        with mock.patch("builtins.print") as printed:
            monitoring.log(f"Couldn't reach {SIMPLEFIN}", "warning")
            monitoring.log("bad value 1234.56", "warning", remote="bad value")
        self.assertIn("secretpass", str(printed.call_args_list[0]))   # the local log is unchanged
        sentry_sdk.flush()
        sent = json.dumps(transport.of("log"))
        self.assertIn("beta-bridge.simplefin.org", sent)
        self.assertNotIn("secretpass", sent)
        self.assertNotIn("1234.56", sent)

    def _categorize_once(self, env=None):
        """Ask the categorizer about one merchant (OpenRouter mocked); returns what reached Sentry, as spans' attributes."""
        transport = start(env)
        reply = {"id": "gen-1", "model": "anthropic/claude-haiku-4.5",
                 "choices": [{"finish_reason": "stop", "message": {"content": '[{"i": 0, "category": "Groceries", "confidence": 0.9}]'}}],
                 "usage": {"prompt_tokens": 120, "completion_tokens": 8, "total_tokens": 128}}
        resp = mock.MagicMock()
        resp.__enter__.return_value = io.BytesIO(json.dumps(reply).encode())
        own_database(self)
        with db.session() as conn:
            db.set_setting(conn, "openrouter_api_key", "sk-or-key")
            db.set_setting(conn, "llm_model", "anthropic/claude-haiku-4.5")
            conn.commit()
            group = [{"posted": "2026-09-01", "amount": -87.12, "kind": "credit", "payee": "Whole Foods", "description": "WHOLE FOODS #123"}]
            with monitoring.task("sync"), mock.patch("urllib.request.urlopen", return_value=resp):
                self.assertEqual(categorize.ask_model(conn, [group])[0][0], "Groceries")
        sentry_sdk.flush()
        spans = {sp["name"]: {k: v["value"] for k, v in sp["attributes"].items()} | {"trace_id": sp["trace_id"]}
                 for batch in transport.of("span") for sp in batch["items"]}
        return transport, spans

    def test_the_categorizer_is_an_agent_without_its_prompt(self):
        transport, spans = self._categorize_once({**ALL_ON, "SENTRY_AI_CONTENT": "0"})
        agent, chat = spans["invoke_agent Transaction categorizer"], spans["chat anthropic/claude-haiku-4.5"]
        self.assertEqual((agent["sentry.op"], agent["gen_ai.agent.name"], agent["gen_ai.pipeline.name"]),
                         ("gen_ai.invoke_agent", "Transaction categorizer", "sync"))
        self.assertEqual((chat["sentry.op"], chat["gen_ai.agent.name"], chat["gen_ai.provider.name"]),
                         ("gen_ai.chat", "Transaction categorizer", "openrouter"))
        self.assertEqual((chat["gen_ai.usage.input_tokens"], chat["gen_ai.usage.output_tokens"], chat["gen_ai.response.model"]),
                         (120, 8, "anthropic/claude-haiku-4.5"))
        self.assertTrue(chat["gen_ai.conversation.id"].startswith("transaction-categorizer-"))   # the run is one conversation
        self.assertEqual(chat["trace_id"], transport.of("transaction")[0]["contexts"]["trace"]["trace_id"])
        everything = json.dumps(transport.items)
        for private in ("Whole Foods", "WHOLE FOODS", "87.12", "sk-or-key", "gen_ai.input.messages", "gen_ai.output.messages"):
            self.assertNotIn(private, everything)
        self.assertIn("runway.ai.tokens", everything)

    def test_only_web_addresses_are_opened_for_the_ai(self):
        with mock.patch.object(categorize, "OPENROUTER_URL", "file:///etc/passwd"), \
                mock.patch("urllib.request.urlopen") as urlopen, self.assertRaisesRegex(RuntimeError, "http"):
            categorize.call_llm("k", "m", "p")
        urlopen.assert_not_called()

    def test_the_prompt_and_reply_unless_turned_off(self):
        _, spans = self._categorize_once()
        chat = spans["chat anthropic/claude-haiku-4.5"]
        sent = json.loads(chat["gen_ai.input.messages"])
        self.assertEqual(sent[0]["role"], "user")
        self.assertIn("WHOLE FOODS", sent[0]["parts"][0]["content"])
        self.assertIn("Groceries", chat["gen_ai.output.messages"])
        self.assertNotIn("sk-or-key", json.dumps(spans))

    def test_mcp_calls_name_the_tool_not_its_arguments(self):
        transport = start()
        msg = {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "transactions", "arguments": {"q": "my-landlord"}}}
        with monitoring.task("mcp"), monitoring.mcp_call(msg) as sp:
            monitoring.mcp_result(sp, {"result": {"isError": True}})
        sentry_sdk.flush()
        span = transport.of("transaction")[0]["spans"][0]
        self.assertEqual((span["op"], span["description"]), ("mcp.server", "tools/call transactions"))
        self.assertEqual((span["data"]["mcp.tool.name"], span["data"]["mcp.tool.result.is_error"]), ("transactions", True))
        self.assertNotIn("my-landlord", json.dumps(span))

    def test_the_page_carries_its_trace_to_the_browser(self):
        start()
        with monitoring.request("GET", "/", {}) as tx:
            meta = monitoring.trace_meta()
        self.assertIn(f'<meta name="sentry-trace" content="{tx.trace_id}-', meta)
        self.assertIn('<meta name="baggage"', meta)


if __name__ == "__main__":
    unittest.main()
