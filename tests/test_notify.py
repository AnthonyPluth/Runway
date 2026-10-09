import os
import threading
import time
import unittest
from http.server import HTTPServer
from unittest import mock

from sqlalchemy import delete, func, insert, select, update

from runway.storage import db
from runway.domain import notify
from runway import oidc
from runway.providers import webpush
from runway.storage.models import Account, CardStatement, NotifyLog, PushSubscription, SyncLog, Transaction, User
from tests.test_webpush import PushService, decrypt, receiver
from tests.shared import DbCase, TODAY



class NotifyTests(DbCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
        cls.srv = HTTPServer(("127.0.0.1", 0), PushService)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        super().setUp()
        self.c.execute(insert(Account).values(id="chk", name="Checking", kind="checking", balance=700,
                                              balance_date="2026-09-23"))
        self.c.execute(insert(Account).values(id="cc", name="Visa", kind="credit", balance=-400,
                                              balance_date="2026-09-23", pay_from="chk", plaid_account_id="p-cc"))
        self.c.execute(insert(CardStatement).values(plaid_account_id="p-cc", item_id="item",
                                                    last_statement_balance=400, last_statement_date="2026-09-01",
                                                    next_due_date="2026-09-25"))
        self.c.execute(insert(Transaction).values(id="cc|1", account_id="cc", posted="2026-08-20", amount=-400,
                                                  description="STORE", payee="Store", category="Shopping"))
        self.c.execute(insert(Transaction).values(id="chk|1", account_id="chk", posted="2026-09-22", amount=-812.5,
                                                  description="BEST BUY", payee="Best Buy", category="Shopping"))
        self.ua, self.p256dh, _ = receiver()
        env = mock.patch.dict(os.environ, {"RUNWAY_PUSH_HOSTS": "127.0.0.1"})
        env.start()
        self.addCleanup(env.stop)
        self.c.execute(insert(PushSubscription).values(endpoint=f"http://127.0.0.1:{self.srv.server_port}/p/1",
                                                       p256dh=self.p256dh, auth=webpush.b64u(b"0123456789abcdef"),
                                                       device="Test phone", created=0))
        PushService.received.clear()

    def titles(self):
        return [decrypt(b, self.ua, b"0123456789abcdef")["title"] for _p, _h, b in PushService.received]

    def test_alerts_sent_once(self):
        notify.run(self.c, TODAY)
        t = self.titles()
        self.assertIn("Visa payment due Friday", t)
        self.assertIn("$812.50 at Best Buy", t)
        self.assertTrue(any(x.startswith("Checking gets low") or x.startswith("Checking goes negative") for x in t), t)
        n = len(PushService.received)
        notify.run(self.c, TODAY)
        self.assertEqual(len(PushService.received), n)

    def test_preferences(self):
        notify.save_prefs(self.c, {"card_due": False, "big_charge_over": 1000, "low_balance": False})
        notify.run(self.c, TODAY)
        self.assertEqual(self.titles(), [])
        for bad in ("lots", "nan", "inf", "1e13", None, ""):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "^Enter a number$"):
                notify.save_prefs(self.c, {"big_charge_over": bad})
        p = notify.save_prefs(self.c, {"review": "true", "missed": "false", "sync_failed": "0", "churn_fee": 1,
                                       "card_due_days": "30", "low_balance_below": "-5"})
        self.assertEqual({k: p[k] for k in ("review", "missed", "sync_failed", "churn_fee", "card_due_days", "low_balance_below")},
                         {"review": True, "missed": False, "sync_failed": False, "churn_fee": True, "card_due_days": 14,
                          "low_balance_below": 0})

    def test_nothing_sent_or_remembered_without_devices(self):
        self.c.execute(delete(PushSubscription))
        notify.run(self.c, TODAY)
        self.assertEqual(self.c.execute(select(func.count()).select_from(NotifyLog)).fetchone()[0], 0)

    def test_dead_devices_are_removed(self):
        PushService.status = 410
        try:
            notify.send_all(self.c, {"title": "x"})
        finally:
            PushService.status = 201
        self.assertEqual(self.c.execute(select(func.count()).select_from(PushSubscription)).fetchone()[0], 0)

    def test_subscribe_checks_the_key(self):
        with self.assertRaises(ValueError):
            notify.subscribe(self.c, {"endpoint": "https://fcm.googleapis.com/fcm/send/1", "keys": {"p256dh": webpush.b64u(b"\x04" + bytes(64)), "auth": "x"}}, "d", None)
        notify.subscribe(self.c, {"endpoint": "https://fcm.googleapis.com/fcm/send/1", "keys": {"p256dh": self.p256dh, "auth": "YWJj"}}, "iPhone · app", "u1")
        self.assertEqual(self.c.execute(select(PushSubscription.device)
                                        .where(PushSubscription.endpoint == "https://fcm.googleapis.com/fcm/send/1")).fetchone()[0], "iPhone · app")
        created = self.c.execute(select(PushSubscription.created)
                                 .where(PushSubscription.endpoint == "https://fcm.googleapis.com/fcm/send/1")).fetchone()[0]
        notify.subscribe(self.c, {"endpoint": "https://fcm.googleapis.com/fcm/send/1", "keys": {"p256dh": self.p256dh, "auth": "YWJj"}}, "", "u2")
        row = self.c.execute(select(PushSubscription.p256dh, PushSubscription.auth, PushSubscription.device,
                                    PushSubscription.user_sub, PushSubscription.created)
                             .where(PushSubscription.endpoint == "https://fcm.googleapis.com/fcm/send/1")).fetchone()
        self.assertEqual(tuple(row), (self.p256dh, "YWJj", "This device", "u2", created))
        _ua2, p256dh2, _ = receiver()
        with self.assertRaises(ValueError):
            notify.subscribe(self.c, {"endpoint": "https://fcm.googleapis.com/fcm/send/1", "keys": {"p256dh": p256dh2, "auth": "ZGVm"}}, "x", "u1")
        notify.subscribe(self.c, {"endpoint": "https://fcm.googleapis.com/fcm/send/1", "keys": {"p256dh": p256dh2, "auth": "ZGVm"}}, "iPhone · app", "u2")
        self.assertEqual(self.c.execute(select(PushSubscription.p256dh, PushSubscription.user_sub)
                                        .where(PushSubscription.endpoint == "https://fcm.googleapis.com/fcm/send/1")).fetchone()[:],
                         (p256dh2, "u2"))
        old = "https://fcm.googleapis.com/fcm/send/old"
        notify.subscribe(self.c, {"endpoint": old, "keys": {"p256dh": self.p256dh, "auth": "YWJj"}}, "Old phone", None)
        with self.assertRaises(ValueError):
            notify.subscribe(self.c, {"endpoint": old, "keys": {"p256dh": p256dh2, "auth": "ZGVm"}}, "x", "u1")
        notify.subscribe(self.c, {"endpoint": old, "keys": {"p256dh": self.p256dh, "auth": "YWJj"}}, "Old phone", "u1")
        self.assertEqual(self.c.execute(select(PushSubscription.user_sub)
                                        .where(PushSubscription.endpoint == old)).fetchone()[0], "u1")
        notify.unsubscribe(self.c, old)
        self.assertEqual([s["device"] for s in notify.subscriptions(self.c)], ["Test phone", "iPhone · app"])
        notify.unsubscribe(self.c, "https://fcm.googleapis.com/fcm/send/1")
        self.assertEqual([s["device"] for s in notify.subscriptions(self.c)], ["Test phone"])

    def test_devices_end_with_the_person(self):
        oidc.remember_user(self.c, "u1", "a@example.com", "A")
        oidc.remember_user(self.c, "u2", "b@example.com", "B")
        for who, n in (("u1", 2), ("u2", 3)):
            self.c.execute(insert(PushSubscription).values(endpoint=f"http://127.0.0.1:{self.srv.server_port}/p/{n}",
                                                           p256dh=self.p256dh, auth=webpush.b64u(b"0123456789abcdef"),
                                                           device=f"{who}'s phone", user_sub=who, created=n))
        with mock.patch.dict(os.environ, {"OIDC_ISSUER": "https://id.example.com", "OIDC_ALLOWED_EMAILS": "a@example.com"}):
            self.assertEqual(notify.lapsed(self.c, {"user_sub": "u2"}), "user_removed")
            self.assertIsNone(notify.lapsed(self.c, {"user_sub": "u1"}))
            self.assertIsNone(notify.lapsed(self.c, {"user_sub": None}))
            self.assertEqual([s["device"] for s in notify.subscriptions(self.c)], ["Test phone", "u1's phone"])
            self.assertEqual(notify.send_all(self.c, {"title": "x"})["sent"], 2)
        self.assertEqual(self.c.execute(select(func.count()).select_from(PushSubscription)).fetchone()[0], 2)

    def test_someone_let_in_by_group_who_hasnt_signed_in_lately_is_skipped_not_dropped(self):
        oidc.remember_user(self.c, "u1", "a@example.com", "A", when=time.time() - 40 * 86400)
        self.c.execute(insert(PushSubscription).values(endpoint=f"http://127.0.0.1:{self.srv.server_port}/p/2",
                                                       p256dh=self.p256dh, auth=webpush.b64u(b"0123456789abcdef"),
                                                       device="A's phone", user_sub="u1", created=2))
        with mock.patch.dict(os.environ, {"OIDC_ISSUER": "https://id.example.com", "OIDC_ALLOWED_GROUPS": "family"}):
            self.assertEqual(notify.lapsed(self.c, {"user_sub": "u1"}), "sign_in_lapsed")
            self.assertEqual([s["device"] for s in notify.subscriptions(self.c)], ["Test phone", "A's phone"])
            self.assertEqual(notify.send_all(self.c, {"title": "x"})["sent"], 1)
            oidc.remember_user(self.c, "u1", "a@example.com", "A")
            self.assertIsNone(notify.lapsed(self.c, {"user_sub": "u1"}))
            self.assertEqual(notify.send_all(self.c, {"title": "y"})["sent"], 2)

    def add_device(self, who, n):
        self.c.execute(insert(PushSubscription).values(endpoint=self.endpoint(n), p256dh=self.p256dh,
                                                       auth=webpush.b64u(b"0123456789abcdef"), device=f"{who}'s phone",
                                                       user_sub=who, created=n))

    def endpoint(self, n):
        return f"http://127.0.0.1:{self.srv.server_port}/p/{n}"

    def test_each_person_has_their_own_notifications(self):
        from runway.server.api import notifications as api
        from runway.server.common import ApiError, _current
        oidc.remember_user(self.c, "u1", "a@example.com", "A")
        oidc.remember_user(self.c, "u2", "b@example.com", "B")
        self.add_device("u1", 2)
        self.add_device("u2", 3)
        self.addCleanup(lambda: setattr(_current, "user", None))
        with mock.patch.dict(os.environ, {"OIDC_ISSUER": "https://id.example.com", "OIDC_ALLOWED_EMAILS": "a@example.com,b@example.com"}):
            _current.user = {"sub": "u2", "email": "b@example.com"}
            api.api_push_prefs(self.c, {}, {"card_due": False, "low_balance": False})
            mine = api.api_push(self.c, {}, {})
            self.assertFalse(mine["prefs"]["card_due"])
            self.assertEqual([(d["device"], d["unclaimed"]) for d in mine["devices"]], [("Test phone", True), ("u2's phone", False)])
            _current.user = {"sub": "u1", "email": "a@example.com"}
            self.assertTrue(api.api_push(self.c, {}, {})["prefs"]["card_due"])
            self.assertEqual([d["device"] for d in api.api_push(self.c, {}, {})["devices"]], ["Test phone", "u1's phone"])
            api.api_push_unsubscribe(self.c, {}, {"endpoint": self.endpoint(3)})
            with self.assertRaises(ApiError):
                api.api_push_test(self.c, {}, {"endpoint": self.endpoint(3)})
            with self.assertRaises(ApiError) as e:
                api.api_push_test(self.c, {}, {"endpoint": self.endpoint(1)})
            self.assertIn("before sign-in", str(e.exception))
            self.assertEqual(self.c.execute(select(func.count()).select_from(PushSubscription)).fetchone()[0], 3)
            self.assertEqual(PushService.received, [])

            notify.run(self.c, TODAY)
            to = {p: [decrypt(b, self.ua, b"0123456789abcdef")["title"] for q, _h, b in PushService.received if q == p]
                  for p in ("/p/1", "/p/2", "/p/3")}
            self.assertEqual(to["/p/1"], [])
            self.assertIn("Visa payment due Friday", to["/p/2"])
            self.assertIn("$812.50 at Best Buy", to["/p/2"])
            self.assertEqual(to["/p/3"], ["$812.50 at Best Buy"])
            self.assertEqual([r["title"] for r in api.api_push(self.c, {}, {})["recent"]].count("$812.50 at Best Buy"), 1)
            _current.user = {"sub": "u2", "email": "b@example.com"}
            self.assertEqual([r["title"] for r in api.api_push(self.c, {}, {})["recent"]], ["$812.50 at Best Buy"])
            n = len(PushService.received)
            notify.run(self.c, TODAY)
            self.assertEqual(len(PushService.received), n)
            api.api_push_unsubscribe(self.c, {}, {"endpoint": self.endpoint(3)})
            api.api_push_unsubscribe(self.c, {}, {"endpoint": self.endpoint(1)})
            self.assertEqual([r[0] for r in self.c.execute(select(PushSubscription.device))], ["u1's phone"])

    def test_alerts_sent_before_they_were_each_person_s_count_as_sent(self):
        notify.run(self.c, TODAY)
        oidc.remember_user(self.c, "u1", "a@example.com", "A")
        self.c.execute(update(PushSubscription).values(user_sub="u1"))
        n = len(PushService.received)
        with mock.patch.dict(os.environ, {"OIDC_ISSUER": "https://id.example.com", "OIDC_ALLOWED_EMAILS": "a@example.com"}):
            notify.run(self.c, TODAY)
            self.assertEqual(len(notify.recent(self.c, "u1")), min(n, 8))
        self.assertEqual(len(PushService.received), n)

    def test_without_sign_in_every_device_is_yours(self):
        from runway.server.api import notifications as api
        self.add_device("u1", 2)
        self.assertEqual([d["device"] for d in api.api_push(self.c, {}, {})["devices"]], ["Test phone", "u1's phone"])
        notify.run(self.c, TODAY)
        self.assertEqual({p for p, _h, _b in PushService.received}, {"/p/1", "/p/2"})

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
            self.c.execute(insert(User), [{"sub": "a", "email": "old@example.com", "last_seen": 1},
                                          {"sub": "b", "email": None, "last_seen": 3},
                                          {"sub": "c", "email": "new@example.com", "last_seen": 2}])
            self.assertEqual(notify.subject(self.c), "mailto:new@example.com")

    def test_review_big_charge_and_sync_failed_alerts(self):
        from runway.storage import settings_keys as sk
        self.c.execute(insert(Account).values(id="inv", name="Brokerage", kind="investment", balance=0, owner=None))
        self.c.execute(insert(Account).values(id="old", name="Old", kind="checking", balance=0, hidden=1))
        self.c.execute(update(Account).where(Account.id == "chk").values(owner="Sam"))
        for tid, acct, amount, cat, review in (("inv|1", "inv", -900, None, 1), ("old|1", "old", -900, None, 1),
                                               ("chk|2", "chk", -600, "Transfer", 1), ("chk|3", "chk", -20, None, 1)):
            self.c.execute(insert(Transaction).values(id=tid, account_id=acct, posted="2026-09-22", amount=amount,
                                                      description="X", payee="X", category=cat, needs_review=review))
        self.c.execute(insert(SyncLog), [{"ok": 1, "message": "fine"}, {"ok": 0, "message": "The bank said no"}])
        db.set_setting(self.c, sk.SIMPLEFIN_ACCESS_URL, "https://x:y@bank.example/simplefin")
        p = {**notify.DEFAULTS, "review": True, "card_due": False, "low_balance": False, "missed": False}
        got = {a["key"]: a for a in notify.alerts(self.c, TODAY, p)}
        self.assertEqual(sorted(got), ["big:chk|1", "review:2026-09-23", "syncfail:2026-09-23"])
        self.assertEqual((got["big:chk|1"]["title"], got["big:chk|1"]["body"]), ("$812.50 at Best Buy", "On Checking (Sam)."))
        self.assertEqual(got["review:2026-09-23"]["title"], "3 transactions to review")
        self.assertNotIn("The bank said no", got["syncfail:2026-09-23"]["body"])
        self.assertIn("Settings", got["syncfail:2026-09-23"]["body"])
        self.c.execute(insert(SyncLog).values(ok=1, message="fine again"))
        self.assertNotIn("syncfail:2026-09-23", {a["key"] for a in notify.alerts(self.c, TODAY, p)})

    def test_sync_failed_alert_with_plaid_only(self):
        from runway.storage.models import PlaidItem
        self.c.execute(insert(SyncLog).values(ok=0, message="Plaid: ITEM_LOGIN_REQUIRED"))
        p = {**notify.DEFAULTS, "card_due": False, "low_balance": False, "missed": False, "big_charge": False}
        keys = lambda: {a["key"] for a in notify.alerts(self.c, TODAY, p)}
        self.assertNotIn("syncfail:2026-09-23", keys())
        db.set_setting(self.c, "plaid_client_id", "cid"); db.set_setting(self.c, "plaid_secret", "sec")
        self.c.execute(insert(PlaidItem).values(item_id="it", access_token="x", products="transactions"))
        self.assertIn("syncfail:2026-09-23", keys())

    def test_a_card_payment_alert_says_which_account_pays(self):
        p = {**notify.DEFAULTS, "card_due": True, "review": False, "low_balance": False, "missed": False}
        got = {a["key"]: a for a in notify.alerts(self.c, TODAY, p)}
        self.assertEqual(got["card:cc:2026-09-25"]["body"], "$400.00 comes out of Checking.")
        from runway.storage import settings_keys as sk
        db.set_setting(self.c, sk.card_pay_mode("cc"), "fixed")
        db.set_setting(self.c, sk.card_pay_amount("cc"), "150")
        got = {a["key"]: a for a in notify.alerts(self.c, TODAY, p)}
        self.assertEqual(got["card:cc:2026-09-25"]["body"], "$150.00 comes out of Checking.")

    def test_old_log_entries_are_forgotten(self):
        self.c.execute(insert(NotifyLog), [{"key": "ancient", "sent": 1, "title": "x"},
                                           {"key": "recent", "sent": time.time(), "title": "y"}])
        notify.run(self.c, TODAY)
        keys = {r[0] for r in self.c.execute(select(NotifyLog.key))}
        self.assertNotIn("ancient", keys)
        self.assertIn("recent", keys)
        self.assertIn("card:cc:2026-09-25", keys)
        self.assertEqual(self.c.execute(select(NotifyLog.title)
                                        .where(NotifyLog.key == "card:cc:2026-09-25")).fetchone()[0],
                         "Visa payment due Friday")

    # The browser extension's import waiting for a sign-in to a store

    def log(self):
        return {r["key"]: r["sent"] for r in self.c.execute(select(NotifyLog.key, NotifyLog.sent)).fetchall()}

    def test_extension_sign_in_is_said_once_until_the_import_carries_on(self):
        now = time.time()
        with mock.patch("time.time", return_value=now):
            self.assertEqual(notify.ext_signin(self.c, "amazon", "Amazon", None), {"sent": 1, "why": None})
            msg = decrypt(PushService.received[-1][2], self.ua, b"0123456789abcdef")
            # Only the store's name: no amounts, merchants or accounts on a lock screen.
            self.assertEqual(msg, {"title": "Sign in to Amazon for Runway",
                                   "body": "The browser extension's import is waiting for you to sign in to Amazon in the tab "
                                           "it opened. It carries on once you have.",
                                   "url": "/#setup/connections", "tag": "extsignin:amazon"})
            # The daily import asks again the next day: nothing new while it waits, and another store is its own.
            self.assertEqual(notify.ext_signin(self.c, "amazon", "Amazon", None), {"sent": 0, "why": "already"})
            self.assertEqual(notify.ext_signin(self.c, "carta", "Carta", None)["sent"], 1)
            self.assertEqual(len(PushService.received), 2)
        with mock.patch("time.time", return_value=now + notify.EXT_SIGNIN_REMIND - 60):
            self.assertEqual(notify.ext_signin(self.c, "amazon", "Amazon", None)["why"], "already")
        with mock.patch("time.time", return_value=now + notify.EXT_SIGNIN_REMIND + 60):   # still waiting: a reminder
            self.assertEqual(notify.ext_signin(self.c, "amazon", "Amazon", None)["sent"], 1)
        later = now + notify.EXT_SIGNIN_REMIND + 60
        # The import got through: a new sign-out is told about, though not within EXT_SIGNIN_EVERY of the last one.
        notify.ext_resumed(self.c, "amazon", None)
        self.assertEqual(set(self.log()) & {"extsignin:amazon", "extsignin:amazon:resumed"}, {"extsignin:amazon:resumed"})
        with mock.patch("time.time", return_value=later + notify.EXT_SIGNIN_EVERY - 60):
            self.assertEqual(notify.ext_signin(self.c, "amazon", "Amazon", None)["why"], "already")
        with mock.patch("time.time", return_value=later + notify.EXT_SIGNIN_EVERY + 60):
            self.assertEqual(notify.ext_signin(self.c, "amazon", "Amazon", None)["sent"], 1)
        self.assertEqual(set(self.log()) & {"extsignin:amazon", "extsignin:amazon:resumed"}, {"extsignin:amazon"})
        notify.ext_resumed(self.c, "target", None)   # nothing was waiting: nothing changes
        self.assertNotIn("extsignin:target:resumed", self.log())
        self.assertEqual(len(PushService.received), 4)
        self.assertIn("Sign in to Amazon for Runway", [r["title"] for r in notify.recent(self.c, None)])

    def test_an_extension_sign_in_nobody_got_isnt_remembered(self):
        notify.ext_signin(self.c, "costco", "Costco", None)
        notify.ext_resumed(self.c, "costco", None)
        before = self.log()
        PushService.status = 500
        try:
            with mock.patch("time.time", return_value=time.time() + notify.EXT_SIGNIN_EVERY + 60):
                self.assertEqual(notify.ext_signin(self.c, "costco", "Costco", None), {"sent": 0, "why": "failed"})
        finally:
            PushService.status = 201
        self.assertEqual(self.log(), before)   # as it was: the time limit still runs from the one that was delivered
        self.assertIsNotNone(self.c.execute(select(PushSubscription.last_error)).fetchone()[0])
        with mock.patch("time.time", return_value=time.time() + notify.EXT_SIGNIN_EVERY + 60):
            self.assertEqual(notify.ext_signin(self.c, "costco", "Costco", None)["sent"], 1)   # tried again, and said
        self.c.execute(delete(PushSubscription))
        self.assertEqual(notify.ext_signin(self.c, "target", "Target", None), {"sent": 0, "why": "no_devices"})
        self.assertNotIn("extsignin:target", self.log())

    def test_extension_sign_in_can_be_turned_off(self):
        notify.save_prefs(self.c, {"ext_signin": "false"})
        self.assertEqual(notify.ext_signin(self.c, "amazon", "Amazon", None), {"sent": 0, "why": "off"})
        self.assertEqual((PushService.received, self.log()), ([], {}))

    def test_extension_sign_in_goes_to_the_keys_maker_while_they_may_sign_in(self):
        oidc.remember_user(self.c, "u1", "a@example.com", "A")
        oidc.remember_user(self.c, "u2", "b@example.com", "B")
        self.add_device("u1", 2)
        self.add_device("u2", 3)
        with mock.patch.dict(os.environ, {"OIDC_ISSUER": "https://id.example.com", "OIDC_ALLOWED_EMAILS": "a@example.com,b@example.com"}):
            # a key made before keys had owners: nobody's to tell (not the devices turned on before sign-in)
            self.assertEqual(notify.ext_signin(self.c, "amazon", "Amazon", None), {"sent": 0, "why": "no_devices"})
            self.assertEqual(notify.ext_signin(self.c, "amazon", "Amazon", "u1"), {"sent": 1, "why": None})
            self.assertEqual([p for p, _h, _b in PushService.received], ["/p/2"])
            self.assertIn(notify._log_key("u1", "extsignin:amazon"), self.log())
        with mock.patch.dict(os.environ, {"OIDC_ISSUER": "https://id.example.com", "OIDC_ALLOWED_EMAILS": "b@example.com"}):
            # taken off the sign-in list: their devices are dropped, and nothing goes anywhere else
            self.assertEqual(notify.ext_signin(self.c, "target", "Target", "u1"), {"sent": 0, "why": "no_devices"})
        self.assertEqual(len(PushService.received), 1)
        self.assertNotIn(self.endpoint(2), [s["endpoint"] for s in notify.subscriptions(self.c)])


if __name__ == "__main__":
    unittest.main()
