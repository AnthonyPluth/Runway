import os
import tempfile
import threading
import time
import unittest
from datetime import date
from http.server import HTTPServer
from unittest import mock

from runway import db, notify, webpush
from tests.test_webpush import PushService, decrypt, receiver

TODAY = date(2026, 9, 23)


class NotifyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
        cls.srv = HTTPServer(("127.0.0.1", 0), PushService)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        path = os.path.join(self.tmp.name, "t.db")
        db.init(path)
        self.c = db.connect(path)
        self.c.execute("INSERT INTO accounts(id, name, kind, balance, balance_date) VALUES ('chk','Checking','checking',700,'2026-09-23')")
        # Card closes on the 1st, due on the 25th (two days away), $400 statement unpaid.
        self.c.execute("INSERT INTO accounts(id, name, kind, balance, balance_date, pay_from, plaid_account_id) "
                       "VALUES ('cc','Visa','credit',-400,'2026-09-23','chk','p-cc')")
        self.c.execute("INSERT INTO card_statements(plaid_account_id, item_id, last_statement_balance, last_statement_date, next_due_date) "
                       "VALUES ('p-cc','item',400,'2026-09-01','2026-09-25')")
        self.c.execute("INSERT INTO transactions(id, account_id, posted, amount, description, payee, category) "
                       "VALUES ('cc|1','cc','2026-08-20',-400,'STORE','Store','Shopping')")
        self.c.execute("INSERT INTO transactions(id, account_id, posted, amount, description, payee, category) "
                       "VALUES ('chk|1','chk','2026-09-22',-812.5,'BEST BUY','Best Buy','Shopping')")
        self.ua, self.p256dh, _ = receiver()
        env = mock.patch.dict(os.environ, {"RUNWAY_PUSH_HOSTS": "127.0.0.1"})   # the test's own push service
        env.start()
        self.addCleanup(env.stop)
        self.c.execute("INSERT INTO push_subscriptions(endpoint, p256dh, auth, device, created) VALUES (?,?,?,?,0)",
                       (f"http://127.0.0.1:{self.srv.server_port}/p/1", self.p256dh,
                        webpush.b64u(b"0123456789abcdef"), "Test phone"))
        PushService.received.clear()

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def titles(self):
        return [decrypt(b, self.ua, b"0123456789abcdef")["title"] for _p, _h, b in PushService.received]

    def test_alerts_sent_once(self):
        notify.run(self.c, TODAY)
        t = self.titles()
        self.assertIn("Visa payment due Friday", t)
        self.assertIn("$812.50 at Best Buy", t)
        self.assertTrue(any(x.startswith("Checking gets low") or x.startswith("Checking goes negative") for x in t), t)
        n = len(PushService.received)
        notify.run(self.c, TODAY)                      # nothing new: nothing sent
        self.assertEqual(len(PushService.received), n)

    def test_preferences(self):
        notify.save_prefs(self.c, {"card_due": False, "big_charge_over": 1000, "low_balance": False})
        notify.run(self.c, TODAY)
        self.assertEqual(self.titles(), [])
        with self.assertRaises(ValueError):
            notify.save_prefs(self.c, {"big_charge_over": "lots"})

    def test_nothing_sent_or_remembered_without_devices(self):
        self.c.execute("DELETE FROM push_subscriptions")
        notify.run(self.c, TODAY)
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM notify_log").fetchone()[0], 0)

    def test_dead_devices_are_removed(self):
        PushService.status = 410
        try:
            notify.send_all(self.c, {"title": "x"})
        finally:
            PushService.status = 201
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM push_subscriptions").fetchone()[0], 0)

    def test_subscribe_checks_the_key(self):
        with self.assertRaises(ValueError):
            notify.subscribe(self.c, {"endpoint": "https://fcm.googleapis.com/fcm/send/1", "keys": {"p256dh": webpush.b64u(b"\x04" + bytes(64)), "auth": "x"}}, "d", None)
        notify.subscribe(self.c, {"endpoint": "https://fcm.googleapis.com/fcm/send/1", "keys": {"p256dh": self.p256dh, "auth": "YWJj"}}, "iPhone · app", "u1")
        self.assertEqual(self.c.execute("SELECT device FROM push_subscriptions WHERE endpoint='https://fcm.googleapis.com/fcm/send/1'").fetchone()[0], "iPhone · app")
        # subscribing again (new keys) updates the device, but keeps who it belongs to and when it was added
        created = self.c.execute("SELECT created FROM push_subscriptions WHERE endpoint='https://fcm.googleapis.com/fcm/send/1'").fetchone()[0]
        _ua2, p256dh2, _ = receiver()
        notify.subscribe(self.c, {"endpoint": "https://fcm.googleapis.com/fcm/send/1", "keys": {"p256dh": p256dh2, "auth": "ZGVm"}}, "", "u2")
        row = self.c.execute("SELECT p256dh, auth, device, user_sub, created FROM push_subscriptions "
                             "WHERE endpoint='https://fcm.googleapis.com/fcm/send/1'").fetchone()
        self.assertEqual(tuple(row), (p256dh2, "ZGVm", "This device", "u1", created))
        self.assertEqual([s["device"] for s in notify.subscriptions(self.c)], ["Test phone", "This device"])   # oldest first
        notify.unsubscribe(self.c, "https://fcm.googleapis.com/fcm/send/1")
        self.assertEqual([s["device"] for s in notify.subscriptions(self.c)], ["Test phone"])

    def test_delivery_is_recorded_on_the_device(self):
        notify.send_all(self.c, {"title": "x"})
        s = notify.subscriptions(self.c)[0]
        self.assertIsNotNone(s["last_ok"])
        self.assertIsNone(s["last_error"])
        PushService.status = 500
        try:
            r = notify.send_all(self.c, {"title": "y"})
        finally:
            PushService.status = 201
        self.assertEqual(r["sent"], 0)
        self.assertEqual(len(r["failed"]), 1)
        self.assertTrue(r["failed"][0].startswith("Test phone: "))
        s2 = notify.subscriptions(self.c)[0]
        self.assertEqual(s2["last_ok"], s["last_ok"])
        self.assertTrue(s2["last_error"])

    def test_subject(self):
        with mock.patch.dict(os.environ, {"RUNWAY_PUBLIC_URL": ""}):
            self.assertEqual(notify.subject(self.c), "mailto:runway@example.com")
            self.c.execute("INSERT INTO users(sub, email, last_seen) VALUES ('a', 'old@example.com', 1), ('b', NULL, 3), "
                           "('c', 'new@example.com', 2)")
            self.assertEqual(notify.subject(self.c), "mailto:new@example.com")

    def test_review_big_charge_and_sync_failed_alerts(self):
        from runway import settings_keys as sk
        self.c.execute("INSERT INTO accounts(id, name, kind, balance, owner) VALUES ('inv','Brokerage','investment',0,NULL)")
        self.c.execute("INSERT INTO accounts(id, name, kind, balance, hidden) VALUES ('old','Old','checking',0,1)")
        self.c.execute("UPDATE accounts SET owner='Sam' WHERE id='chk'")
        for tid, acct, amount, cat, review in (("inv|1", "inv", -900, None, 1), ("old|1", "old", -900, None, 1),
                                               ("chk|2", "chk", -600, "Transfer", 1), ("chk|3", "chk", -20, None, 1)):
            self.c.execute("INSERT INTO transactions(id, account_id, posted, amount, description, payee, category, needs_review) "
                           "VALUES (?,?,'2026-09-22',?,'X','X',?,?)", (tid, acct, amount, cat, review))
        self.c.execute("INSERT INTO sync_log(ok, message) VALUES (1, 'fine'), (0, 'The bank said no')")
        db.set_setting(self.c, sk.SIMPLEFIN_ACCESS_URL, "https://x:y@bank.example/simplefin")
        p = {**notify.DEFAULTS, "review": True, "card_due": False, "low_balance": False, "missed": False}
        got = {a["key"]: a for a in notify.alerts(self.c, TODAY, p)}
        self.assertEqual(sorted(got), ["big:chk|1", "review:2026-09-23", "syncfail:2026-09-23"])
        self.assertEqual((got["big:chk|1"]["title"], got["big:chk|1"]["body"]), ("$812.50 at Best Buy", "On Checking (Sam)."))
        self.assertEqual(got["review:2026-09-23"]["title"], "3 transactions to review")   # not the brokerage's
        self.assertEqual(got["syncfail:2026-09-23"]["body"], "The bank said no")
        self.c.execute("INSERT INTO sync_log(ok, message) VALUES (1, 'fine again')")
        self.assertNotIn("syncfail:2026-09-23", {a["key"] for a in notify.alerts(self.c, TODAY, p)})

    def test_old_log_entries_are_forgotten(self):
        self.c.execute("INSERT INTO notify_log(key, sent, title) VALUES ('ancient', 1, 'x'), ('recent', ?, 'y')", (time.time(),))
        notify.run(self.c, TODAY)
        keys = {r[0] for r in self.c.execute("SELECT key FROM notify_log")}
        self.assertNotIn("ancient", keys)
        self.assertIn("recent", keys)
        self.assertIn("card:cc:2026-09-25", keys)
        self.assertEqual(self.c.execute("SELECT title FROM notify_log WHERE key='card:cc:2026-09-25'").fetchone()[0],
                         "Visa payment due Friday")


if __name__ == "__main__":
    unittest.main()
