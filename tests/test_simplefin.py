"""SimpleFIN: storing what the bridge sends, syncing, and its HTTP client."""
import itertools
import json
import os
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest import mock
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

from sqlalchemy import select, update

from runway import tls
from runway.providers import simplefin
from runway.domain import splits
from runway.storage.models import Account, Transaction
from tests.fakes import certs, simplefin_server
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
        a = self.conn.execute(select(Account)).fetchone()
        self.assertEqual((a["kind"], a["balance"], a["org"]), ("credit", -123.45, "Chase"))
        self.conn.execute(update(Transaction).where(Transaction.id == "A1|p1").values(category="Rideshare & Taxi"))
        p2 = self.payload([
            {"id": "t1", "posted": ts(date(2026, 9, 20)), "amount": "-10.00", "description": "SQ *CAFE"},
            {"id": "t2", "posted": ts(date(2026, 9, 23)), "amount": "-5.00", "description": "UBER *TRIP"},
        ])
        new2 = simplefin.store_payload(self.conn, p2, date(2026, 9, 1))
        self.assertEqual(new2, [])
        self.assertEqual(self.conn.execute(select(Transaction.category)
                                           .where(Transaction.id == "A1|t2")).fetchone()[0], "Rideshare & Taxi")
        self.assertIsNone(self.conn.execute(select(Transaction.id).where(Transaction.id == "A1|p1")).fetchone())
        p3 = self.payload([{"id": "p9", "posted": 0, "transacted_at": ts(date(2026, 9, 23)), "amount": "-7.00",
                            "description": "LYFT *RIDE", "pending": True}])
        simplefin.store_payload(self.conn, p3, date(2026, 9, 1))
        self.conn.execute(update(Transaction).where(Transaction.id == "A1|p9").values(category="Rideshare & Taxi"))
        p4 = self.payload([{"id": "p10", "posted": 0, "transacted_at": ts(date(2026, 9, 23)), "amount": "-7.00",
                            "description": "LYFT *RIDE", "pending": True}])
        self.assertEqual(simplefin.store_payload(self.conn, p4, date(2026, 9, 1)), [])
        self.assertEqual(self.conn.execute(select(Transaction.category)
                                           .where(Transaction.id == "A1|p10")).fetchone()[0], "Rideshare & Taxi")

    def test_a_pending_charge_you_renamed_keeps_its_name_when_it_posts(self):
        def pending(tid, desc, amount):
            return {"id": tid, "posted": 0, "transacted_at": ts(date(2026, 9, 22)), "amount": amount, "description": desc, "pending": True}
        simplefin.store_payload(self.conn, self.payload([pending("p1", "DIRECT DEBIT TARGET DEBIT CACH TRAN (Cash)", "-35.91"),
                                                         pending("p2", "SQ *CAFE", "-4.00")]), date(2026, 9, 1))
        self.assertEqual(self.conn.execute(select(Transaction.payee).where(Transaction.id == "A1|p1")).scalar(), "Target")
        self.conn.execute(update(Transaction).where(Transaction.id == "A1|p1").values(payee="Birthday Gifts"))
        new = simplefin.store_payload(self.conn, self.payload([
            {"id": "t1", "posted": ts(date(2026, 9, 23)), "amount": "-35.91", "description": "DIRECT DEBIT TARGET DEBIT CACH TRAN (Cash)"},
            {"id": "t2", "posted": ts(date(2026, 9, 23)), "amount": "-4.00", "description": "SQ *CAFE"}]), date(2026, 9, 1))
        self.assertEqual(sorted(new), ["A1|t1", "A1|t2"])
        self.assertEqual(dict(self.conn.execute(select(Transaction.id, Transaction.payee)).fetchall()),
                         {"A1|t1": "Birthday Gifts", "A1|t2": "Cafe"})

    def test_a_split_pending_charge_keeps_its_parts_when_it_posts_under_a_new_id(self):
        simplefin.store_payload(self.conn, self.payload([{"id": "p1", "posted": 0, "transacted_at": ts(date(2026, 9, 22)),
                                                          "amount": "-100.00", "description": "TARGET", "pending": True}]), date(2026, 9, 1))
        splits.set_splits(self.conn, "A1|p1", [{"amount": -60, "category": "Groceries"}, {"amount": -40, "category": "Shopping"}])
        new = simplefin.store_payload(self.conn, self.payload([{"id": "t1", "posted": ts(date(2026, 9, 23)), "amount": "-100.00",
                                                                "description": "TARGET"}]), date(2026, 9, 1))
        self.assertEqual(new, [])
        self.assertEqual([(p["amount"], p["category"]) for p in splits.get(self.conn, "A1|t1")], [(-60.0, "Groceries"), (-40.0, "Shopping")])
        self.assertEqual(self.conn.execute(select(Transaction.is_split)
                                           .where(Transaction.id == "A1|t1")).fetchone()[0], 1)

    def test_a_pending_charge_keeps_its_recurring_link_when_it_posts(self):
        """Linked to a recurring item by you, marked as not one (recurring_id 0), or linked on its own: the posted
        charge, under a new id, keeps it, whether you'd categorized it or not."""
        def pending(tid, desc, amount):
            return {"id": tid, "posted": 0, "transacted_at": ts(date(2026, 9, 22)), "amount": amount, "description": desc, "pending": True}
        simplefin.store_payload(self.conn, self.payload([pending("p1", "STREAMCO", "-15.49"), pending("p2", "GYMCO", "-40.00"),
                                                         pending("p3", "PHONECO", "-60.00"), pending("p4", "SQ *CAFE", "-4.00")]),
                                date(2026, 9, 1), TODAY)
        marks = {"A1|p1": (7, "you", "Subscriptions"), "A1|p2": (0, "you", None), "A1|p3": (3, "auto", "Phone"),
                 "A1|p4": (5, "you", None)}
        for tid, (rid, by, cat) in marks.items():
            self.conn.execute(update(Transaction).where(Transaction.id == tid)
                              .values(recurring_id=rid, recurring_linked_by=by, category=cat, notes=f"note {rid}"))

        def posted(tid, desc, amount):
            return {"id": tid, "posted": ts(date(2026, 9, 23)), "amount": amount, "description": desc}
        new = simplefin.store_payload(self.conn, self.payload([posted("t1", "STREAMCO", "-15.49"), posted("t2", "GYMCO", "-40.00"),
                                                               posted("t3", "PHONECO", "-60.00"), posted("t4", "SQ *CAFE", "-4.00")]),
                                      date(2026, 9, 1), TODAY)
        self.assertEqual(sorted(new), ["A1|t2", "A1|t4"])
        got = {r["id"]: (r["recurring_id"], r["recurring_linked_by"], r["category"], r["notes"]) for r in self.conn.execute(
            select(Transaction.id, Transaction.recurring_id, Transaction.recurring_linked_by, Transaction.category, Transaction.notes))}
        self.assertEqual(got, {"A1|t1": (7, "you", "Subscriptions", "note 7"), "A1|t2": (0, "you", None, "note 0"),
                               "A1|t3": (3, "auto", "Phone", "note 3"), "A1|t4": (5, "you", None, "note 5")})

    def test_a_missing_date_is_the_syncs_today(self):
        acct = self.payload([{"id": "t1", "posted": 0, "amount": "-1.00", "description": "X"}])
        acct["accounts"][0].pop("balance-date")
        simplefin.store_payload(self.conn, acct, date(2026, 9, 1), date(2026, 9, 10))
        self.assertEqual(self.conn.execute(select(Account.balance_date)).scalar(), "2026-09-10")
        self.assertEqual(self.conn.execute(select(Transaction.posted)).scalar(), "2026-09-10")

    def test_amounts_and_balances_that_arent_numbers_arent_saved(self):
        """"nan", "inf" or a number too big to be real is left out (no balance: 0; no amount: 0), never saved as is."""
        for bad in ("nan", "inf", "-Infinity", "1e300", "lots"):
            with self.subTest(bad=bad):
                self.conn.execute(Transaction.__table__.delete())
                simplefin.store_payload(self.conn, self.payload([{"id": "t1", "posted": ts(date(2026, 9, 20)), "amount": bad,
                                                                  "description": "X"}], balance=bad), date(2026, 9, 1), TODAY)
                a = self.conn.execute(select(Account.balance, Account.available)).fetchone()
                self.assertEqual((a["balance"], a["available"]), (0.0, 0.0))
                self.assertEqual(self.conn.execute(select(Transaction.amount)).scalar(), 0.0)

    def test_a_hold_that_never_posts_is_cleared(self):
        old = self.payload([{"id": "h1", "posted": 0, "transacted_at": ts(date(2026, 8, 10)), "amount": "-300.00",
                             "description": "HOTEL HOLD", "pending": True}])
        simplefin.store_payload(self.conn, old, date(2026, 8, 1))
        simplefin.store_payload(self.conn, self.payload([]), date(2026, 8, 20))
        self.assertTrue(self.conn.execute(select(Transaction.id).where(Transaction.id == "A1|h1")).fetchone())
        simplefin.store_payload(self.conn, self.payload([]), date(2026, 9, 1))
        self.assertIsNone(self.conn.execute(select(Transaction.id).where(Transaction.id == "A1|h1")).fetchone())

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
        self.assertTrue(r["backfill"])
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
            failing = mock.Mock(open=mock.Mock(side_effect=exc))
            with mock.patch.object(simplefin, "_opener", return_value=failing), self.assertRaises(simplefin.SimpleFinError):
                simplefin.fetch_accounts("https://u:p@h/simplefin", TODAY)

    def test_kind_guess(self):
        self.assertEqual(simplefin.guess_kind("Venture X"), "credit")
        self.assertEqual(simplefin.guess_kind("Mortgage 4100"), "loan")
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


class StandInBridgeTests(LedgerCase):
    """End to end against the stand-in bridge (tests/fakes/simplefin_server.py) over real TLS, as a demo server under
    make verify reaches it: its made-up name opened at its loopback port (simplefin.STAND_IN_ENV), reached directly, and
    its throwaway authority trusted. Nothing in the client is patched but which certificates it trusts."""

    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.fake = simplefin_server.start(tmp.name, TODAY)
        self.addCleanup(self.fake.stop)
        self.trusted = certs.trusting(self.fake.ca)
        reached = f"127.0.0.1,localhost,{simplefin_server.NAME}"   # directly, as a demo server reaches them (verify.fakes_env)
        for patch in (mock.patch.dict(os.environ, {simplefin.STAND_IN_ENV: self.fake.stand_in, "NO_PROXY": reached, "no_proxy": reached}),
                      mock.patch.object(tls, "ssl_context", lambda: self.trusted),
                      mock.patch.object(simplefin, "time", mock.Mock(sleep=lambda _s: None))):   # the pause between a backfill's windows
            patch.start()
            self.addCleanup(patch.stop)

    def sync(self, url):
        return simplefin.sync(self.conn, url, today=TODAY)

    def no_such_name(self):
        """Looking up a .invalid name finds nothing, as it must anywhere (without asking this machine's resolver)."""
        real = socket.getaddrinfo

        def lookup(host, *args, **kw):
            if str(host).endswith(".invalid"):
                raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
            return real(host, *args, **kw)
        patch = mock.patch.object(socket, "getaddrinfo", lookup)
        patch.start()
        self.addCleanup(patch.stop)

    def test_claim_sync_and_a_401_over_real_tls(self):
        url = simplefin.claim_setup_token(simplefin_server.setup_token())
        self.assertEqual(url, simplefin_server.access_url(simplefin_server.CLAIMED_LOGIN))
        simplefin.check_address(url)   # as api_connect checks it before saving
        first = self.sync(url)
        self.assertTrue(first["backfill"])
        self.assertEqual(sorted(first["new"]), ["demo-card|fake-coffee", "demo-card|fake-groceries", "demo-card|fake-tacos",
                                                "demo-vacation|fake-transfer"])
        accounts = {r["id"]: (r["name"], r["org"], r["balance"]) for r in self.conn.execute(select(Account))}
        self.assertEqual(accounts["demo-card"], ("Rewards Visa", "Sample Card Co", -1342.18))
        self.assertEqual(accounts["demo-vacation"], ("Vacation Savings", "Sample Credit Union", 2310.40))
        self.assertEqual(len(accounts), 6)
        held = self.conn.execute(select(Transaction.pending, Transaction.amount, Transaction.posted)
                                 .where(Transaction.id == "demo-card|fake-tacos")).fetchone()
        self.assertEqual(tuple(held), (1, -31.40, TODAY.isoformat()))
        self.assertEqual(self.sync(url)["new"], ["demo-card|fake-tacos"])   # the hold comes again; the rest is here already
        asked = [r for r in self.fake.bridge.requests if r["path"].startswith("/simplefin/accounts")]
        self.assertTrue(asked)
        self.assertEqual({(r["login"], r["status"]) for r in asked}, {("claimed", 200)})   # the claimed login's basic auth, every time
        self.conn.commit()

        self.fake.bridge.set_mode("401")
        before = self.conn.execute(select(Transaction.id).order_by(Transaction.id)).fetchall()
        with self.assertRaises(simplefin.SimpleFinError) as cm:
            self.sync(url)
        self.assertEqual(str(cm.exception), 'SimpleFIN request failed (HTTP 401; server said: "Unauthorized").')
        self.conn.rollback()
        self.assertEqual(self.conn.execute(select(Transaction.id).order_by(Transaction.id)).fetchall(), before)
        with self.assertRaises(simplefin.SimpleFinError) as cm:   # a token can't be claimed from a bridge that refuses either
            simplefin.claim_setup_token(simplefin_server.setup_token())
        self.assertIn("HTTP 401", str(cm.exception))

    def test_the_other_ways_the_bridge_fails_or_falls_short(self):
        url = simplefin_server.access_url()   # the demo's own login: its five accounts
        self.fake.bridge.set_mode("403")
        with self.assertRaises(simplefin.SimpleFinError) as cm:
            self.sync(url)
        self.assertIn("rejected the access credentials (HTTP 403", str(cm.exception))
        self.assertIn("Reconnect in Settings.", str(cm.exception))
        self.conn.rollback()
        self.fake.bridge.set_mode("500")
        with self.assertRaises(simplefin.SimpleFinError) as cm:
            self.sync(url)
        self.assertIn("HTTP 500", str(cm.exception))
        self.conn.rollback()
        self.fake.bridge.set_mode("partial")
        got = self.sync(url)
        self.assertEqual(set(got["errors"]), {"Connection to Sample Card Co may need attention"})
        self.assertEqual(sorted(r[0] for r in self.conn.execute(select(Account.id))), ["demo-checking", "demo-mortgage", "demo-savings"])
        self.fake.bridge.set_mode("ok")
        self.assertEqual(sorted(self.sync(url)["new"]), ["demo-card|fake-coffee", "demo-card|fake-groceries", "demo-card|fake-tacos"])
        with self.assertRaises(simplefin.SimpleFinError) as cm:   # a login the bridge doesn't know, and a token it doesn't
            simplefin.fetch_accounts(f"https://demo:wrong@{simplefin_server.NAME}/simplefin", TODAY)
        self.assertIn("Forbidden: bad credentials", str(cm.exception))
        with self.assertRaises(simplefin.SimpleFinError) as cm:
            simplefin.claim_setup_token(simplefin_server.setup_token("another-token"))
        self.assertIn("A setup token works only once", str(cm.exception))

    def test_the_slow_mode_answers_after_its_wait(self):
        self.fake.bridge.slow = 0.3
        self.fake.bridge.set_mode("slow")
        started = time.monotonic()
        simplefin.fetch_accounts(simplefin_server.access_url(), TODAY)
        self.assertGreaterEqual(time.monotonic() - started, 0.3)

    def test_its_certificate_is_checked_like_any_bridges(self):
        url = simplefin_server.access_url()
        with mock.patch.object(tls, "ssl_context", certs.trusting), self.assertRaises(simplefin.SimpleFinError) as cm:
            simplefin.fetch_accounts(url, TODAY)   # the system's certificates don't include the throwaway authority
        self.assertIn("CERTIFICATE_VERIFY_FAILED", str(cm.exception))
        self.assertEqual([r for r in self.fake.bridge.requests if r["path"].startswith("/simplefin")], [])   # nothing was sent

    def test_only_its_own_name_is_opened_on_this_machine(self):
        """The stand-in opens one made-up name at one loopback port: this machine's address, another made-up name and
        plain http are refused as they always are."""
        self.no_such_name()
        port = self.fake.port
        for url in (f"https://demo:demo@127.0.0.1:{port}/simplefin", f"https://demo:demo@localhost:{port}/simplefin"):
            with self.subTest(url=url):
                with self.assertRaises(simplefin.SimpleFinError) as cm:
                    simplefin.check_address(url)
                self.assertIn("private network address", str(cm.exception))
                with self.assertRaises(simplefin.SimpleFinError) as cm:
                    simplefin.fetch_accounts(url, TODAY)
                self.assertIn("private network address", str(cm.exception))
        with self.assertRaises(simplefin.SimpleFinError) as cm:
            simplefin.check_address("https://demo:demo@another-bank.invalid/simplefin")
        self.assertIn("can't find another-bank.invalid", str(cm.exception))
        with self.assertRaises(simplefin.SimpleFinError) as cm:   # the name at another port is not the stand-in
            simplefin.check_address(f"https://demo:demo@{simplefin_server.NAME}:{port}/simplefin")
        self.assertIn("can't find", str(cm.exception))
        with self.assertRaises(simplefin.SimpleFinError) as cm:
            simplefin.fetch_accounts(f"http://demo:demo@{simplefin_server.NAME}/simplefin", TODAY)
        self.assertIn("must start with https://", str(cm.exception))
        self.assertEqual(self.fake.bridge.requests, [])

    def test_without_the_stand_in_its_name_goes_nowhere(self):
        self.no_such_name()
        with mock.patch.dict(os.environ):
            del os.environ[simplefin.STAND_IN_ENV]
            with self.assertRaises(simplefin.SimpleFinError) as cm:
                simplefin.check_address(simplefin_server.access_url())
            self.assertIn(f"can't find {simplefin_server.NAME}", str(cm.exception))
            with self.assertRaises(simplefin.SimpleFinError):
                simplefin.fetch_accounts(simplefin_server.access_url(), TODAY)
        self.assertEqual(self.fake.bridge.requests, [])


class StandInSettingTests(unittest.TestCase):
    def test_it_is_a_reserved_name_at_a_port_of_a_loopback_address_or_nothing(self):
        self.assertEqual(simplefin.stand_in("sample-bank.invalid=127.0.0.1:8443"), ("sample-bank.invalid", "127.0.0.1", 8443))
        self.assertEqual(simplefin.stand_in(" Sample-Bank.INVALID=::1:8443 "), ("sample-bank.invalid", "::1", 8443))
        for refused in ("", "sample-bank.invalid", "bridge.simplefin.org=127.0.0.1:8443", "simplefin.org.invalid.com=127.0.0.1:8443",
                        "invalid=127.0.0.1:8443", ".invalid=127.0.0.1:8443", "sample-bank.invalid=10.0.0.5:8443",
                        "sample-bank.invalid=192.168.1.1:443", "sample-bank.invalid=203.0.113.9:443", "sample-bank.invalid=localhost:8443",
                        "sample-bank.invalid=127.0.0.1", "sample-bank.invalid=127.0.0.1:0", "sample-bank.invalid=127.0.0.1:65536",
                        "sample-bank.invalid=127.0.0.1:-1", "sample-bank.invalid=127.0.0.1:port", "a@b.invalid=127.0.0.1:8443",
                        "a/b.invalid=127.0.0.1:8443", "a:1.invalid=127.0.0.1:8443", "*.invalid=127.0.0.1:8443"):
            with self.subTest(value=refused):
                self.assertIsNone(simplefin.stand_in(refused))

    def test_a_real_bridges_address_is_never_the_stand_in(self):
        with mock.patch.dict(os.environ, {simplefin.STAND_IN_ENV: "sample-bank.invalid=127.0.0.1:8443"}):
            self.assertEqual(simplefin._stand_in_for("sample-bank.invalid", None), ("127.0.0.1", 8443))
            self.assertEqual(simplefin._stand_in_for("SAMPLE-BANK.invalid", 443), ("127.0.0.1", 8443))
            for host, port in (("beta-bridge.simplefin.org", None), ("sample-bank.invalid.example.com", None), ("x.sample-bank.invalid", None),
                               ("sample-bank.invalid", 8443), ("127.0.0.1", 8443), (None, None)):
                self.assertIsNone(simplefin._stand_in_for(host, port))
        with mock.patch.dict(os.environ, {simplefin.STAND_IN_ENV: "beta-bridge.simplefin.org=127.0.0.1:8443"}):
            self.assertIsNone(simplefin._stand_in_for("beta-bridge.simplefin.org", None))   # not a reserved name: ignored


if __name__ == "__main__":
    unittest.main()
