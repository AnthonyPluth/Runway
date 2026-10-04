"""Every API route answers a plain request without a server error.

Each route is called once with no parameters, an empty body and a made-up (or a real) id. A route may refuse (4xx)
what it's sent (and a route that talks to Plaid or another service answers 502 when that isn't set up), but a 500
means a handler crashed on input it should have checked."""
import json
import unittest
import urllib.error
import urllib.request
from datetime import date, timedelta

from sqlalchemy import insert

from runway import db, server
from runway.models import Account, Transaction
from tests.shared import ServerCase, fetch, freeze_today, hold_mcp_switch

# Routes that would reach out to another service even with an empty request; they're covered by their own tests.
NETWORK = {"/api/push/test", "/api/investments/live", "/api/assets/{id}/refresh", "/api/carta/sync"}


class RouteTests(ServerCase):
    @classmethod
    def setUpClass(cls):
        hold_mcp_switch(cls)   # it posts to the assistants' switch (tests/shared.py)
        super().setUpClass()
        today = freeze_today(cls)
        with db.session() as conn:   # a little data, so the pages have something to add up
            conn.execute(insert(Account).values(id="chk", name="Checking", kind="checking", balance=2500.0,
                                                balance_date=today.isoformat()))
            conn.execute(insert(Account).values(id="card", name="Card", kind="credit", balance=-300.0,
                                                balance_date=today.isoformat()))
            for i, (acct, days, amount, desc, cat) in enumerate([
                ("chk", 3, 2000.0, "ACME PAYROLL", "Paycheck"), ("chk", 5, -1200.0, "RENT", "Rent"),
                ("card", 2, -45.5, "GROCER", "Groceries"), ("card", 40, -12.0, "COFFEE", None),
            ]):
                conn.execute(insert(Transaction).values(id=f"{acct}|{i}", account_id=acct,
                                                        posted=(today - timedelta(days=days)).isoformat(),
                                                        amount=amount, description=desc, payee=desc.title(),
                                                        category=cat, pending=0))

    def raw(self, method, path, body=None):
        status, _, data = fetch(self.base, method, path, json.dumps(body).encode() if body is not None else None,
                                {"X-Runway": "1", "Content-Type": "application/json"})
        return status, data

    def test_no_route_fails_with_a_server_error(self):
        for method, pattern, *_ in server.ROUTES:
            if pattern in NETWORK:
                continue
            for rid in ("1", "chk|0", "nope"):
                path = pattern.replace("{id}", urllib.request.quote(rid, safe=""))
                with self.subTest(method=method, path=path):
                    status, body = self.raw(method, path, None if method == "GET" else {})
                    self.assertNotEqual(status, 500, f"{method} {path}: {body[:300]!r}")
                if "{id}" not in pattern:
                    break

    def test_list_filters(self):
        month = date.today().strftime("%Y-%m")
        for path in (f"/api/transactions?q=gro&month={month}&limit=5", "/api/transactions?review=1&limit=abc",
                     f"/api/budget?month={month}", "/api/budget?month=bad", "/api/reports/spending?months=-1",
                     "/api/reports/merchant?name=Grocer&months=12", "/api/cashflow?months=x"):
            with self.subTest(path=path):
                status, body = self.raw("GET", path)
                self.assertNotEqual(status, 500, f"{path}: {body[:300]!r}")


class TableTests(unittest.TestCase):
    """routes.TABLE: ROUTES split up once, found as a scan of ROUTES in order would find them."""

    @staticmethod
    def scan(method, path):
        """The first route in ROUTES whose method (any, for None) and pattern match: how the server used to look."""
        parts = path.strip("/").split("/")
        for m, pattern, *_ in server.ROUTES:
            want = pattern.strip("/").split("/")
            if (method is None or m == method) and len(want) == len(parts) \
                    and all(w == "{id}" or w == p for w, p in zip(want, parts, strict=True)):
                return pattern
        return None

    def test_every_route_is_found_as_a_scan_would(self):
        for method, pattern, *_ in server.ROUTES:
            for rid in ("7", "chk%7C0", "bulk", "deleted", "suggestions"):
                path = pattern.replace("{id}", rid)
                for m in (method, None):
                    with self.subTest(method=m, path=path):
                        found = server.routes.match(m, path)
                        self.assertEqual(found.route.pattern if found else None, self.scan(m, path))

    def test_ids_and_order(self):
        found = server.routes.match("POST", "/api/accounts/chk%7C0/statements/2026-09-01/remove")
        self.assertEqual((found.route.pattern, found.params), ("/api/accounts/{id}/statements/{id}/remove", ["chk|0", "2026-09-01"]))
        self.assertEqual(server.routes.match("POST", "/api/transactions/bulk").route.pattern, "/api/transactions/bulk")
        self.assertEqual(server.routes.match("GET", "/api/accounts/deleted").route.pattern, "/api/accounts/deleted")
        self.assertEqual(server.routes.match("GET", "/api/accounts/").route.pattern, "/api/accounts")
        self.assertIsNone(server.routes.match("DELETE", "/api/accounts"))
        self.assertIsNone(server.routes.match("GET", "/api/nothing/here"))


if __name__ == "__main__":
    unittest.main()
