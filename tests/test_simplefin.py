"""SimpleFIN: storing what the bridge sends, syncing, and its HTTP client."""
import itertools
import json
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

from runway import simplefin, splits
from tests.shared import TODAY, LedgerCase, ts


class SimpleFinStoreTests(LedgerCase):
    def payload(self, txs, balance="-123.45"):
        return {"errors": [], "accounts": [{
            "org": {"name": "Chase"}, "id": "A1", "name": "Sapphire Reserve", "currency": "USD",
            "balance": balance, "available-balance": "0", "balance-date": ts(TODAY), "transactions": txs}]}

    def test_store_and_pending_carryover(self):
        p1 = self.payload([
            {"id": "t1", "posted": ts(date(2026, 9, 20)), "amount": "-10.00", "description": "SQ *CAFE"},
            {"id": "p1", "posted": 0, "transacted_at": ts(date(2026, 9, 22)), "amount": "-5.00", "description": "UBER *TRIP", "pending": True},
        ])
        new = simplefin.store_payload(self.conn, p1, date(2026, 9, 1))
        self.assertEqual(set(new), {"A1|t1", "A1|p1"})
        a = self.conn.execute("SELECT * FROM accounts").fetchone()
        self.assertEqual((a["kind"], a["balance"], a["org"]), ("credit", -123.45, "Chase"))
        self.conn.execute("UPDATE transactions SET category='Rideshare & Taxi' WHERE id='A1|p1'")
        # Pending posts with a new id: category carries over, not reported as new.
        p2 = self.payload([
            {"id": "t1", "posted": ts(date(2026, 9, 20)), "amount": "-10.00", "description": "SQ *CAFE"},
            {"id": "t2", "posted": ts(date(2026, 9, 23)), "amount": "-5.00", "description": "UBER *TRIP"},
        ])
        new2 = simplefin.store_payload(self.conn, p2, date(2026, 9, 1))
        self.assertEqual(new2, [])
        self.assertEqual(self.conn.execute("SELECT category FROM transactions WHERE id='A1|t2'").fetchone()[0], "Rideshare & Taxi")
        self.assertIsNone(self.conn.execute("SELECT 1 FROM transactions WHERE id='A1|p1'").fetchone())
        p3 = self.payload([{"id": "p9", "posted": 0, "transacted_at": ts(date(2026, 9, 23)), "amount": "-7.00",
                            "description": "LYFT *RIDE", "pending": True}])
        simplefin.store_payload(self.conn, p3, date(2026, 9, 1))
        self.conn.execute("UPDATE transactions SET category='Rideshare & Taxi' WHERE id='A1|p9'")
        p4 = self.payload([{"id": "p10", "posted": 0, "transacted_at": ts(date(2026, 9, 23)), "amount": "-7.00",
                            "description": "LYFT *RIDE", "pending": True}])
        self.assertEqual(simplefin.store_payload(self.conn, p4, date(2026, 9, 1)), [])
        self.assertEqual(self.conn.execute("SELECT category FROM transactions WHERE id='A1|p10'").fetchone()[0], "Rideshare & Taxi")

    def test_a_split_pending_charge_keeps_its_parts_when_it_posts_under_a_new_id(self):
        simplefin.store_payload(self.conn, self.payload([{"id": "p1", "posted": 0, "transacted_at": ts(date(2026, 9, 22)),
                                                          "amount": "-100.00", "description": "TARGET", "pending": True}]), date(2026, 9, 1))
        splits.set_splits(self.conn, "A1|p1", [{"amount": -60, "category": "Groceries"}, {"amount": -40, "category": "Shopping"}])
        new = simplefin.store_payload(self.conn, self.payload([{"id": "t1", "posted": ts(date(2026, 9, 23)), "amount": "-100.00",
                                                                "description": "TARGET"}]), date(2026, 9, 1))
        self.assertEqual(new, [])
        self.assertEqual([(p["amount"], p["category"]) for p in splits.get(self.conn, "A1|t1")], [(-60.0, "Groceries"), (-40.0, "Shopping")])
        self.assertEqual(self.conn.execute("SELECT is_split FROM transactions WHERE id='A1|t1'").fetchone()[0], 1)

    def test_a_hold_that_never_posts_is_cleared(self):
        old = self.payload([{"id": "h1", "posted": 0, "transacted_at": ts(date(2026, 8, 10)), "amount": "-300.00",
                             "description": "HOTEL HOLD", "pending": True}])
        simplefin.store_payload(self.conn, old, date(2026, 8, 1))
        # Weeks later the hold is gone from the bank; routine syncs only re-read the last 14 days.
        simplefin.store_payload(self.conn, self.payload([]), date(2026, 8, 20))   # a sync on Sep 3
        self.assertTrue(self.conn.execute("SELECT 1 FROM transactions WHERE id='A1|h1'").fetchone())   # 24 days: could still post
        simplefin.store_payload(self.conn, self.payload([]), date(2026, 9, 1))    # Sep 15: 36 days
        self.assertIsNone(self.conn.execute("SELECT 1 FROM transactions WHERE id='A1|h1'").fetchone())

    def test_sync_chunks_backfill(self):
        calls = []

        def fake_fetch(url, start, end):
            calls.append((start, end))
            return self.payload([{"id": f"t{len(calls)}", "posted": ts(start), "amount": "-1", "description": "X"}])

        r = simplefin.sync(self.conn, "https://u:p@h/simplefin", today=TODAY, fetch=fake_fetch)
        self.assertTrue(r["backfill"])
        self.assertEqual(calls[0][0], TODAY - timedelta(days=simplefin.BACKFILL_DAYS))
        self.assertEqual(calls[-1][1], TODAY)
        for s, e in calls:
            self.assertLessEqual((e - s).days + 1, simplefin.CHUNK_DAYS)
        for (_, e1), (s2, _) in itertools.pairwise(calls):
            self.assertEqual(s2, e1 + timedelta(days=1))
        calls.clear()
        r = simplefin.sync(self.conn, "https://u:p@h/simplefin", today=TODAY, fetch=fake_fetch)
        self.assertFalse(r["backfill"])
        self.assertEqual(calls, [(TODAY - timedelta(days=simplefin.REFRESH_DAYS), TODAY)])

    def test_backfill_that_stops_part_way_is_finished_later(self):
        calls, fail = [], {"at": 2}

        def fake_fetch(url, start, end):
            calls.append((start, end))
            if len(calls) == fail["at"]:
                raise simplefin.SimpleFinError("Couldn't reach SimpleFIN: timed out")
            return self.payload([{"id": f"t{len(calls)}", "posted": ts(start), "amount": "-1", "description": "X"}])

        with self.assertRaises(simplefin.SimpleFinError):
            simplefin.sync(self.conn, "https://u:p@h/simplefin", today=TODAY, fetch=fake_fetch)
        calls.clear(); fail["at"] = 0
        r = simplefin.sync(self.conn, "https://u:p@h/simplefin", today=TODAY, fetch=fake_fetch)
        self.assertTrue(r["backfill"])   # the middle is read, not just the last 14 days
        self.assertEqual(calls[0][0], TODAY - timedelta(days=simplefin.BACKFILL_DAYS))
        calls.clear()
        self.assertFalse(simplefin.sync(self.conn, "https://u:p@h/simplefin", today=TODAY, fetch=fake_fetch)["backfill"])

    def test_a_bank_added_later_gets_its_history(self):
        accts = ["A1"]

        def fake_fetch(url, start, end):
            return {"accounts": [{"id": a, "name": a, "balance": "1", "currency": "USD", "transactions": []} for a in accts]}

        simplefin.sync(self.conn, "https://u:p@h/simplefin", today=TODAY, fetch=fake_fetch)
        self.assertFalse(simplefin.sync(self.conn, "https://u:p@h/simplefin", today=TODAY, fetch=fake_fetch)["backfill"])
        accts.append("B2")
        simplefin.sync(self.conn, "https://u:p@h/simplefin", today=TODAY, fetch=fake_fetch)
        self.assertTrue(simplefin.sync(self.conn, "https://u:p@h/simplefin", today=TODAY, fetch=fake_fetch)["backfill"])
        self.assertFalse(simplefin.sync(self.conn, "https://u:p@h/simplefin", today=TODAY, fetch=fake_fetch)["backfill"])

    def test_simplefin_timeout_is_a_simplefin_error(self):
        from unittest import mock
        for exc in (TimeoutError("timed out"), ConnectionResetError()):
            with mock.patch("urllib.request.urlopen", side_effect=exc), self.assertRaises(simplefin.SimpleFinError):
                simplefin.fetch_accounts("https://u:p@h/simplefin", TODAY)

    def test_kind_guess(self):
        self.assertEqual(simplefin.guess_kind("Venture X"), "credit")
        self.assertEqual(simplefin.guess_kind("Mortgage 1588"), "loan")
        self.assertEqual(simplefin.guess_kind("Fidelity Joint"), "checking")


class MockBridge(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        import base64
        auth = self.headers.get("Authorization", "")
        MockBridge.user_agent = self.headers.get("User-Agent")
        if auth != "Basic " + base64.b64encode(b"user:p@ss").decode():
            body = b"<html><body>Forbidden: bad credentials</body></html>"
            self.send_response(403); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
        MockBridge.last_path = self.path
        body = json.dumps({"errors": [], "accounts": []}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)


class SimpleFinHttpTests(unittest.TestCase):
    def test_fetch_with_basic_auth(self):
        srv = HTTPServer(("127.0.0.1", 0), MockBridge)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            url = f"http://user:p%40ss@127.0.0.1:{srv.server_port}/simplefin"
            # the mock bridge is plain http on this machine, which the real opener refuses (see test_hardening)
            plain = mock.patch.object(simplefin, "_opener", lambda: urllib.request.build_opener(simplefin._NoRedirects()))
            plain.start()
            self.addCleanup(plain.stop)
            out = simplefin.fetch_accounts(url, date(2026, 9, 1), date(2026, 9, 23))
            self.assertEqual(out["accounts"], [])
            self.assertTrue(MockBridge.last_path.startswith("/simplefin/accounts?start-date="))
            self.assertIn("pending=1", MockBridge.last_path)
            self.assertTrue(MockBridge.user_agent.startswith("Runway/"))
            with self.assertRaises(simplefin.SimpleFinError) as cm:
                simplefin.fetch_accounts(f"http://user:wrong@127.0.0.1:{srv.server_port}/simplefin", date(2026, 9, 1))
            self.assertIn('server said: "Forbidden: bad credentials"', str(cm.exception))
        finally:
            srv.shutdown()

    def test_bad_setup_token(self):
        with self.assertRaises(simplefin.SimpleFinError):
            simplefin.claim_setup_token("not a token!!")


if __name__ == "__main__":
    unittest.main()
