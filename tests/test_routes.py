"""Every API route answers a plain request without a server error.

Each route is called once with no parameters, an empty body and a made-up (or a real) id. A route may refuse (4xx)
what it's sent (and a route that talks to Plaid or another service answers 502 when that isn't set up), but a 500
means a handler crashed on input it should have checked."""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runway import db, server  # noqa: E402

# Routes that would reach out to another service even with an empty request; they're covered by their own tests.
NETWORK = {"/api/push/test", "/api/investments/live", "/api/assets/{id}/refresh", "/api/carta/sync"}


class RouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["RUNWAY_DATA"] = cls.tmp.name
        db.init()
        today = date.today()
        with db.session() as conn:   # on Postgres the database is shared with later tests, so note what's there already
            cls.had_settings = {r["key"] for r in conn.execute("SELECT key FROM settings").fetchall()}
            cls.had_snapshots = {r["date"] for r in conn.execute("SELECT date FROM networth_snapshots").fetchall()}
        with db.session() as conn:   # a little data, so the pages have something to add up
            conn.execute("INSERT INTO accounts(id, name, kind, balance, balance_date) VALUES (?,?,?,?,?)",
                         ("chk", "Checking", "checking", 2500.0, today.isoformat()))
            conn.execute("INSERT INTO accounts(id, name, kind, balance, balance_date) VALUES (?,?,?,?,?)",
                         ("card", "Card", "credit", -300.0, today.isoformat()))
            for i, (acct, days, amount, desc, cat) in enumerate([
                ("chk", 3, 2000.0, "ACME PAYROLL", "Paycheck"), ("chk", 5, -1200.0, "RENT", "Rent"),
                ("card", 2, -45.5, "GROCER", "Groceries"), ("card", 40, -12.0, "COFFEE", None),
            ]):
                conn.execute("INSERT INTO transactions(id, account_id, posted, amount, description, payee, category, pending) "
                             "VALUES (?,?,?,?,?,?,?,?)",
                             (f"{acct}|{i}", acct, (today - timedelta(days=days)).isoformat(), amount, desc, desc.title(), cat, 0))
        cls.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        with db.session() as conn:   # leave the database as it was found
            conn.execute("DELETE FROM transactions WHERE account_id IN ('chk', 'card')")
            conn.execute("DELETE FROM accounts WHERE id IN ('chk', 'card')")
            for r in conn.execute("SELECT key FROM settings").fetchall():
                if r["key"] not in cls.had_settings:
                    conn.execute("DELETE FROM settings WHERE key=?", (r["key"],))
            for r in conn.execute("SELECT date FROM networth_snapshots").fetchall():
                if r["date"] not in cls.had_snapshots:
                    conn.execute("DELETE FROM networth_snapshots WHERE date=?", (r["date"],))
        cls.tmp.cleanup()
        os.environ.pop("RUNWAY_DATA", None)

    def req(self, method, path, body=None):
        r = urllib.request.Request(self.base + path, method=method, headers={"X-Runway": "1", "Content-Type": "application/json"},
                                   data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(r, timeout=20) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_no_route_fails_with_a_server_error(self):
        for method, pattern, _ in server.ROUTES:
            if pattern in NETWORK:
                continue
            for rid in ("1", "chk|0", "nope"):
                path = pattern.replace("{id}", urllib.request.quote(rid, safe=""))
                with self.subTest(method=method, path=path):
                    status, body = self.req(method, path, None if method == "GET" else {})
                    self.assertNotEqual(status, 500, f"{method} {path}: {body[:300]!r}")
                if "{id}" not in pattern:
                    break

    def test_list_filters(self):
        month = date.today().strftime("%Y-%m")
        for path in (f"/api/transactions?q=gro&month={month}&limit=5", "/api/transactions?review=1&limit=abc",
                     f"/api/budget?month={month}", "/api/budget?month=bad", "/api/reports/spending?months=-1",
                     "/api/reports/merchant?name=Grocer&months=12", "/api/cashflow?months=x"):
            with self.subTest(path=path):
                status, body = self.req("GET", path)
                self.assertNotEqual(status, 500, f"{path}: {body[:300]!r}")


if __name__ == "__main__":
    unittest.main()
