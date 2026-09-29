import os
import tempfile
import threading
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


if __name__ == "__main__":
    unittest.main()
