"""What sign-in keeps in the database: unfinished sign-ins (auth_pending), sessions (auth_sessions, stored hashed) and
the people who've signed in (users). The HTTP flow is in test_server.py; these pin the rows."""
import os
import tempfile
import time
import unittest
import urllib.parse
from unittest import mock

from runway import db, oidc, secretbox

ISSUER = "https://id.example.com"


class OIDCStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        env = mock.patch.dict(os.environ, {"RUNWAY_DATA": self.tmp.name, "OIDC_ISSUER": ISSUER, "OIDC_CLIENT_ID": "runway",
                                           "OIDC_CLIENT_SECRET": "s", "OIDC_ALLOWED_EMAILS": "me@example.com",
                                           "RUNWAY_PUBLIC_URL": "https://runway.example.com"})
        env.start()
        self.addCleanup(env.stop)
        oidc._discovery[ISSUER] = (time.time(), {"issuer": ISSUER, "authorization_endpoint": ISSUER + "/authorize",
                                                 "token_endpoint": ISSUER + "/token", "end_session_endpoint": ISSUER + "/logout"})
        self.addCleanup(oidc._discovery.clear)
        path = os.path.join(self.tmp.name, "t.db")
        db.init(path)
        self.c = db.connect(path)

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

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
