"""What sign-in keeps in the database: unfinished sign-ins (auth_pending), sessions (auth_sessions, stored hashed) and
the people who've signed in (users). The HTTP flow is in test_server.py; these pin the rows."""
import os
import time
import unittest
import urllib.parse
from unittest import mock

from runway import oidc, secretbox
from tests.shared import DbCase

ISSUER = "https://id.example.com"


class OIDCStoreTests(DbCase):
    def setUp(self):
        super().setUp()
        env = mock.patch.dict(os.environ, {"OIDC_ISSUER": ISSUER, "OIDC_CLIENT_ID": "runway",
                                           "OIDC_CLIENT_SECRET": "s", "OIDC_ALLOWED_EMAILS": "me@example.com",
                                           "RUNWAY_PUBLIC_URL": "https://runway.example.com"})
        env.start()
        self.addCleanup(env.stop)
        oidc._discovery[ISSUER] = (time.time(), {"issuer": ISSUER, "authorization_endpoint": ISSUER + "/authorize",
                                                 "token_endpoint": ISSUER + "/token", "end_session_endpoint": ISSUER + "/logout"})
        self.addCleanup(oidc._discovery.clear)

    def pending(self):
        return [tuple(r) for r in self.c.execute("SELECT state, next FROM auth_pending ORDER BY created")]

    def test_start_login_saves_the_pending_sign_in(self):
        url, state = oidc.start_login(self.c, "/#budget")
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        self.assertEqual(q["state"], state)
        row = self.c.execute("SELECT * FROM auth_pending WHERE state=?", (state,)).fetchone()
        self.assertEqual((row["nonce"], row["next"]), (q["nonce"], "/#budget"))
        self.assertAlmostEqual(row["created"], time.time(), delta=5)

    def test_old_and_surplus_pending_sign_ins_are_dropped(self):
        now = time.time()
        self.c.execute("INSERT INTO auth_pending(state, nonce, verifier, next, created) VALUES ('stale','n','v','/',?)",
                       (now - oidc.LOGIN_TTL - 5,))
        for i in range(5):
            self.c.execute("INSERT INTO auth_pending(state, nonce, verifier, next, created) VALUES (?,'n','v','/',?)",
                           (f"s{i}", now - 100 + i))
        with mock.patch.object(oidc, "MAX_PENDING", 3):
            _, state = oidc.start_login(self.c)
        self.assertEqual([s for s, _ in self.pending()], ["s3", "s4", state])   # the newest ones stay

    def test_finish_login_refuses_expired_or_unknown_state(self):
        self.c.execute("INSERT INTO auth_pending(state, nonce, verifier, next, created) VALUES ('old','n','v','/',?)",
                       (time.time() - oidc.LOGIN_TTL - 1,))
        with self.assertRaises(oidc.OIDCError) as e:
            oidc.finish_login(self.c, {"state": "old", "code": "c"}, "old")
        self.assertIn("expired", str(e.exception))
        self.assertEqual(self.pending(), [])                                  # used up either way
        with self.assertRaises(oidc.OIDCError):
            oidc.finish_login(self.c, {"state": "nope", "code": "c"}, "nope")
        with self.assertRaises(oidc.OIDCError):                              # the cookie must match the state
            oidc.finish_login(self.c, {"state": "x", "code": "c"}, "y")

    def add_session(self, token, email="me@example.com", expires=None, id_token=None, sub="u1"):
        now = time.time()
        self.c.execute("INSERT INTO auth_sessions(token_hash, sub, email, name, created, expires, id_token) VALUES (?,?,?,?,?,?,?)",
                       (oidc._hash(token), sub, email, "Me", now, expires if expires is not None else now + 3600,
                        secretbox.encrypt(id_token)))

    def hashes(self):
        return {r[0] for r in self.c.execute("SELECT token_hash FROM auth_sessions")}

    def test_session_user(self):
        self.add_session("good")
        self.add_session("old", expires=time.time() - 1)
        self.add_session("gone", email="removed@example.com")
        self.assertEqual(oidc.session_user(self.c, "good"), {"sub": "u1", "email": "me@example.com", "name": "Me"})
        self.assertIsNone(oidc.session_user(self.c, "good-ish"))
        self.assertIsNone(oidc.session_user(self.c, None))
        self.assertIsNone(oidc.session_user(self.c, "old"))                   # expired: removed
        self.assertIsNone(oidc.session_user(self.c, "gone"))                  # taken off the allow-list: removed
        self.assertEqual(self.hashes(), {oidc._hash("good")})

    def test_logout_ends_the_session_and_expired_ones(self):
        self.add_session("mine", id_token="idt-1")
        self.add_session("other")
        self.add_session("expired", expires=time.time() - 1)
        url = oidc.logout(self.c, "mine")
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        self.assertTrue(url.startswith(ISSUER + "/logout?"))
        self.assertEqual(q["id_token_hint"], "idt-1")
        self.assertEqual(self.hashes(), {oidc._hash("other")})
        self.assertNotIn("id_token_hint", oidc.logout(self.c, None))
        self.assertEqual(self.hashes(), {oidc._hash("other")})

    def expires(self, token):
        return self.c.execute("SELECT expires FROM auth_sessions WHERE token_hash=?", (oidc._hash(token),)).fetchone()[0]

    def test_a_session_in_use_is_extended_once_a_day(self):
        now, day = time.time(), 86400
        self.add_session("fresh", expires=now + 14 * day - 60)            # signed in a minute ago: nothing to do
        self.assertIsNone(oidc.renew_session(self.c, "fresh"))
        self.add_session("used", expires=now + 3 * day)                   # 11 days in: back to 14 days from now
        max_age = oidc.renew_session(self.c, "used")
        self.assertAlmostEqual(max_age, 14 * day, delta=5)
        self.assertAlmostEqual(self.expires("used"), now + 14 * day, delta=5)
        self.assertIsNone(oidc.renew_session(self.c, "used"))             # and not again until tomorrow
        self.assertEqual(oidc.session_user(self.c, "used")["email"], "me@example.com")   # the same token still works
        self.add_session("over", expires=now - 1)
        for token in ("over", "unknown", None):
            self.assertIsNone(oidc.renew_session(self.c, token))

    def test_a_session_ends_90_days_after_signing_in_however_much_its_used(self):
        now, day = time.time(), 86400
        self.add_session("old", expires=now + 3 * day)
        self.c.execute("UPDATE auth_sessions SET created=? WHERE token_hash=?", (now - 85 * day, oidc._hash("old")))
        max_age = oidc.renew_session(self.c, "old")                      # only as far as day 90
        self.assertAlmostEqual(max_age, 5 * day, delta=5)
        self.assertAlmostEqual(self.expires("old"), now + 5 * day, delta=5)
        self.c.execute("UPDATE auth_sessions SET created=?, expires=? WHERE token_hash=?",
                       (now - 89 * day, now + day, oidc._hash("old")))
        self.assertIsNone(oidc.renew_session(self.c, "old"))             # at the limit: it ends when it ends
        self.assertEqual(self.expires("old"), now + day)

    def test_sessions_let_in_by_group_are_not_extended(self):
        now, day = time.time(), 86400
        self.add_session("partner", email="partner@example.com", expires=now + 3 * day)
        with mock.patch.dict(os.environ, {"OIDC_ALLOWED_GROUPS": "finance"}):
            self.assertIsNotNone(oidc.session_user(self.c, "partner"))
            self.assertIsNone(oidc.renew_session(self.c, "partner"))     # leaving the group still ends it on time
        with mock.patch.dict(os.environ, {"OIDC_ALLOW_ANY_USER": "1"}):
            self.assertIsNotNone(oidc.renew_session(self.c, "partner"))

    def test_logout_ends_a_renewed_session(self):
        self.add_session("mine", expires=time.time() + 86400)
        self.assertIsNotNone(oidc.renew_session(self.c, "mine"))
        oidc.logout(self.c, "mine")
        self.assertIsNone(oidc.session_user(self.c, "mine"))
        self.assertIsNone(oidc.renew_session(self.c, "mine"))

    def test_choosing_another_account(self):
        def prompt(**kw):
            url, _ = oidc.start_login(self.c, "/", **kw)
            return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query)).get("prompt")
        self.assertIsNone(prompt())
        self.assertEqual(prompt(choose_account=True), "select_account")   # not advertised: the standard value
        d = oidc._discovery[ISSUER][1]
        for supported, want in ((["none", "login", "consent", "select_account"], "select_account"),
                                (["none", "login", "consent"], "login"), (["none"], None)):
            d["prompt_values_supported"] = supported
            self.assertEqual(prompt(choose_account=True), want, supported)

    def test_the_refusal_names_who_but_not_the_settings(self):
        with self.assertRaises(oidc.NotAllowed) as e:
            oidc.authorize({"sub": "u9", "email": "Stranger@example.com", "email_verified": True})
        self.assertEqual(e.exception.who, "stranger@example.com")
        self.assertNotIn("OIDC_", str(e.exception))
        with self.assertRaises(oidc.NotAllowed) as e:
            oidc.authorize({"sub": "u9"})
        self.assertEqual(e.exception.who, "u9")

    def test_provider_sign_out(self):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(oidc.provider_sign_out()).query))
        self.assertEqual(q, {"client_id": "runway", "post_logout_redirect_uri": "https://runway.example.com/auth/signed-out"})
        del oidc._discovery[ISSUER][1]["end_session_endpoint"]
        self.assertIsNone(oidc.provider_sign_out())
        self.assertEqual(oidc.logout(self.c, None), "/auth/signed-out")

    def users(self):
        return [tuple(r) for r in self.c.execute("SELECT sub, email, name, first_name, last_seen FROM users ORDER BY sub")]

    def test_remember_user_adds_then_updates(self):
        oidc.remember_user(self.c, None, "x@example.com", "X")              # no sub: nothing to remember
        oidc.remember_user(self.c, "u1", "me@example.com", "Anthony Example", None, 100.0)
        self.assertEqual(self.users(), [("u1", "me@example.com", "Anthony Example", "Anthony", 100.0)])
        oidc.remember_user(self.c, "u1", "new@example.com", "Tony E", "Ant", 200.0)
        self.assertEqual(self.users(), [("u1", "new@example.com", "Tony E", "Ant", 200.0)])

    def test_backfill_users_from_sessions(self):
        for token, sub, created in (("a", "u1", 10.0), ("b", "u1", 30.0), ("c", "u2", 20.0), ("d", None, 40.0)):
            self.c.execute("INSERT INTO auth_sessions(token_hash, sub, email, name, created, expires) VALUES (?,?,?,?,?,?)",
                           (token, sub, f"{sub}@example.com", f"Name {sub}", created, 9e9))
        oidc.remember_user(self.c, "u2", "kept@example.com", "Kept", None, 5.0)    # already known: left alone
        oidc.backfill_users(self.c)
        self.assertEqual(self.users(), [("u1", "u1@example.com", "Name u1", "Name", 30.0),
                                        ("u2", "kept@example.com", "Kept", "Kept", 5.0)])


if __name__ == "__main__":
    unittest.main()
