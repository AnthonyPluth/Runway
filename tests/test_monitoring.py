import contextlib
import io
import unittest
from unittest import mock


from runway.domain import categorize
from runway import monitoring
from runway.server.handler import trace_name

SIMPLEFIN = "https://user:secretpass@beta-bridge.simplefin.org/simplefin"


class MonitoringTests(unittest.TestCase):
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

    def test_a_logged_error_carries_the_error_and_nothing_private(self):
        err, said = io.StringIO(), f"SimpleFIN said no: {SIMPLEFIN}?token=abc"   # (the traceback quotes the raise line)
        try:
            raise ValueError(said)
        except ValueError:
            with contextlib.redirect_stderr(err):
                monitoring.report()
        logged = err.getvalue()
        self.assertIn("ValueError: SimpleFIN said no: https://[Filtered]@beta-bridge.simplefin.org/simplefin", logged)
        self.assertNotIn("secretpass", logged)
        self.assertNotIn("token=abc", logged)

    def test_database_errors_lose_the_row_they_were_writing(self):
        import sqlalchemy as sa
        engine = sa.create_engine("sqlite://")
        with engine.begin() as c:
            c.exec_driver_sql("CREATE TABLE tx (id INTEGER PRIMARY KEY, payee TEXT, amount REAL)")
            c.exec_driver_sql("INSERT INTO tx VALUES (1, 'WHOLE FOODS', 87.12)")
        err, row = io.StringIO(), {"id": 1, "payee": "WHOLE FOODS", "amount": 87.12}   # (the traceback quotes the line)
        try:
            with engine.begin() as c:
                # raw SQL: a throwaway table on a plain engine, with the bound parameters the log must drop
                c.execute(sa.text("INSERT INTO tx VALUES (:id, :payee, :amount)"), row)
        except sa.exc.IntegrityError as e:
            self.assertIn("WHOLE FOODS", str(e))   # what SQLAlchemy says
            with contextlib.redirect_stderr(err):
                monitoring.report(e)
        logged = err.getvalue()
        self.assertNotIn("WHOLE FOODS", logged)
        self.assertNotIn("87.12", logged)
        self.assertIn("[SQL: INSERT INTO tx VALUES", logged)   # the query itself helps, and holds no data
        self.assertIn("[parameters: [Filtered]]", logged)
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

    def test_a_log_line_goes_to_stdout_or_stderr(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            monitoring.log("hello")
            monitoring.log("oops", stderr=True)
        self.assertEqual((out.getvalue(), err.getvalue()), ("hello\n", "oops\n"))

    def test_requests_are_named_by_route(self):
        cases = {"/api/transactions/chk%7C0/category": "/api/transactions/{id}/category", "/api/state": "/api/state",
                 "/api/merchants/starbucks/logo": "/api/merchants/{id}/logo", "/oauth/token": "/oauth/token",
                 "/oauth/authorize": "/oauth/authorize", "/.well-known/oauth-protected-resource/mcp": "/.well-known/oauth-protected-resource/mcp",
                 "/oauth/whatever-1234": "/oauth/*", "/.well-known/security.txt": "/.well-known/*",
                 "/api/sync": "/api/sync", "/api/ext/ping": "/api/ext/ping", "/api/nope/secret-name": "/api/*",
                 "/": "/", "/plaid/oauth": "/", "/auth/callback": "/auth/callback", "/auth/whatever": "/"}
        for path, name in cases.items():
            self.assertEqual(trace_name(path), name, path)

    def test_only_web_addresses_are_opened_for_the_ai(self):
        with mock.patch.object(categorize, "OPENROUTER_URL", "file:///etc/passwd"), \
                mock.patch("runway.tls.urlopen") as urlopen, self.assertRaisesRegex(RuntimeError, "http"):
            categorize.call_llm("k", "m", "p")
        urlopen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
