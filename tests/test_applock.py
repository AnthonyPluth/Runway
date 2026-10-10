"""The app lock (runway/applock.py, server/api/lock.py, and the server refusing a locked session: server/handler.py):
WebAuthn checked against a software authenticator (both kinds of key), each way a registration or an unlock can be
wrong, and through a real server: turning it on, locking, the 423s (the API and the assistants' consent page) while
only the lock screen's calls go through, unlocking, an unlock running out (and calls from the background not keeping it
going), and the lock ending with the sign-in."""
import hashlib
import os
import secrets
import time
import unittest
from unittest import mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import insert, select, update

from runway import applock
from runway.storage import db
from runway.storage.models import AppLock, AuthSession
from tests.shared import ServerCase, fetch
from tests.webauthn_support import OIDC_ENV, ORIGIN, RP_ID, Authenticator, b64


class Checks(unittest.TestCase):
    """verify_registration and verify_assertion on their own."""

    def setUp(self):
        self.challenge = secrets.token_bytes(32)

    def registered(self, a: Authenticator) -> dict:
        return applock.verify_registration(a.create(b64(self.challenge)), self.challenge, ORIGIN, RP_ID)

    def test_both_kinds_of_key_register_and_unlock(self):
        for alg in (applock.ES256, applock.RS256):
            for counter in (0, 5):
                a = Authenticator(alg, counter=counter)
                lock = self.registered(a)
                self.assertEqual((lock["alg"], lock["credential_id"], lock["sign_count"]), (alg, b64(a.cred), counter))
                count = applock.verify_assertion(a.get(b64(self.challenge)), lock, self.challenge, ORIGIN, RP_ID)
                self.assertEqual(count, 6 if counter else 0)

    def test_a_registration_that_is_wrong_is_refused(self):
        a = Authenticator()
        ch = b64(self.challenge)
        wrong = {
            "another challenge": a.create(b64(secrets.token_bytes(32))),
            "another address": a.create(ch, origin="https://evil.example"),
            "in a frame": a.create(ch, crossOrigin=True),
            "not verified": a.create(ch, flags=applock.UP | applock.AT),
            "not present": a.create(ch, flags=applock.UV | applock.AT),
            "no credential": a.create(ch, flags=applock.UP | applock.UV),
            "another credential": {**a.create(ch), "credential_id": b64(b"x" * 16)},
            "a get, not a create": {**a.create(ch), "client_data": b64(a._client("webauthn.get", ch, ORIGIN))},
            "a weak key": {**a.create(ch), "public_key": b64(rsa.generate_private_key(65537, 1024).public_key().public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)), "alg": applock.RS256},
            "a key of the other kind": {**a.create(ch), "alg": applock.RS256},
            "not a key": {**a.create(ch), "public_key": b64(b"nonsense")},
            "an unknown algorithm": {**a.create(ch), "alg": -8},
            "not base64url": {**a.create(ch), "client_data": "not/base64+"},
            "not JSON": {**a.create(ch), "client_data": b64(b"{")},
            "too short": {**a.create(ch), "authenticator_data": b64(b"short")},
            "missing": {},
            "alg as text": {**a.create(ch), "alg": "-7"},
        }
        for why, body in wrong.items():
            with self.subTest(why), self.assertRaises(applock.LockError):
                applock.verify_registration(body, self.challenge, ORIGIN, RP_ID)
        with self.assertRaises(applock.LockError):   # another relying party
            applock.verify_registration(Authenticator(rp_id="evil.example").create(ch), self.challenge, ORIGIN, RP_ID)

    def test_an_unlock_that_is_wrong_is_refused(self):
        a = Authenticator(counter=3)
        lock = self.registered(a)
        ch = b64(self.challenge)
        other = Authenticator()
        wrong = {
            "another challenge": a.get(b64(secrets.token_bytes(32))),
            "another address": a.get(ch, origin="https://evil.example"),
            "not verified": a.get(ch, flags=applock.UP),
            "a create, not a get": a.get(ch, kind="webauthn.create"),
            "another passkey": other.get(ch),
            "another passkey's signature, this one's id": {**other.get(ch), "credential_id": b64(a.cred)},
            "a signature that doesn't fit": {**a.get(ch), "signature": a.get(b64(b"other"))["signature"]},
        }
        for why, body in wrong.items():
            with self.subTest(why), self.assertRaises(applock.LockError):
                applock.verify_assertion(body, lock, self.challenge, ORIGIN, RP_ID)
        # The counter going backwards (or standing still) is a copied passkey.
        a.counter = 2
        with self.assertRaises(applock.LockError):
            applock.verify_assertion(a.get(ch), {**lock, "sign_count": 4}, self.challenge, ORIGIN, RP_ID)

    def test_challenges_are_single_use_per_session_and_purpose_and_expire(self):
        now = time.time()
        c = applock.challenge("s1", "unlock", now)
        with self.assertRaises(applock.LockError):
            applock._take_challenge("s2", "unlock", now)        # another session's
        with self.assertRaises(applock.LockError):
            applock._take_challenge("s1", "register", now)      # another purpose
        self.assertEqual(b64(applock._take_challenge("s1", "unlock", now)), c)
        with self.assertRaises(applock.LockError):
            applock._take_challenge("s1", "unlock", now)        # used up
        applock.challenge("s1", "unlock", now)
        with self.assertRaises(applock.LockError):
            applock._take_challenge("s1", "unlock", now + applock.CHALLENGE_TTL + 1)

    def test_the_relying_party_is_the_public_address(self):
        for url, want in (("https://runway.example", (ORIGIN, RP_ID)), ("https://Runway.Example:443/", (ORIGIN, RP_ID)),
                          ("https://runway.example:8443", ("https://runway.example:8443", RP_ID)),
                          ("http://localhost:8765", ("http://localhost:8765", "localhost"))):
            with mock.patch.dict(os.environ, {**OIDC_ENV, "RUNWAY_PUBLIC_URL": url}):
                self.assertEqual(applock.relying_party(), want, url)
        with mock.patch.dict(os.environ, {"OIDC_ISSUER": ""}):
            self.assertIsNone(applock.relying_party())          # no sign-in, no lock


class ThroughTheServer(ServerCase):
    env = OIDC_ENV

    def setUp(self):
        self.token = secrets.token_urlsafe(32)
        self.key = hashlib.sha256(self.token.encode()).hexdigest()
        with db.session() as conn:
            conn.execute(insert(AuthSession).values(token_hash=self.key, sub="u1", email="me@example.com", name="Me",
                                                    created=time.time(), expires=time.time() + 86400))

    def call(self, method, path, body=None, token=None, hidden=False):
        h = {"Cookie": f"runway_session={token or self.token}", **({"X-Runway-Hidden": "1"} if hidden else {})}
        return self.req(method, path, body, h)

    def lock_row(self):
        with db.session() as conn:
            return conn.execute(select(AppLock).where(AppLock.session == self.key)).fetchone()

    def turn_on(self, a: Authenticator, idle=60) -> dict:
        status, ch = self.call("POST", "/api/lock/challenge", {"purpose": "register"})
        self.assertEqual((status, ch["rp_id"], ch["credential_id"]), (200, RP_ID, None))
        status, out = self.call("POST", "/api/lock/register", a.create(ch["challenge"], idle=idle))
        self.assertEqual(status, 200, out)
        return out

    def unlock(self, a: Authenticator):
        status, ch = self.call("POST", "/api/lock/challenge", {"purpose": "unlock"})
        self.assertEqual((status, ch["credential_id"]), (200, b64(a.cred)))
        return self.call("POST", "/api/lock/unlock", a.get(ch["challenge"]))

    def test_turn_on_lock_unlock_and_turn_off(self):
        self.assertEqual(self.call("GET", "/api/lock")[1], {"available": True, "on": False, "locked": False, "idle": 60,
                                                             "credential_id": None, "device_id": None})
        a = Authenticator()
        on = self.turn_on(a, idle=300)
        self.assertEqual(on, {"available": True, "on": True, "locked": False, "idle": 300, "credential_id": b64(a.cred),
                              "device_id": self.lock_row()["id"]})
        self.assertRegex(on["device_id"], r"^dev_[\w-]{22}$")
        self.assertEqual(self.call("GET", "/api/state")[0], 200)              # unlocked: as before
        self.assertEqual(self.call("POST", "/api/lock/engage")[1]["locked"], True)
        status, out = self.call("GET", "/api/state")
        self.assertEqual((status, out["locked"]), (423, True))
        self.assertIn("locked", out["error"])
        for method, path, body in (("GET", "/api/overview", None), ("GET", "/api/transactions", None),
                                   ("POST", "/api/categories", {"name": "X"}), ("DELETE", "/api/lock", None),
                                   ("POST", "/api/lock/settings", {"idle": 0}), ("POST", "/api/lock/register", {}),
                                   ("GET", "/api/backup", None)):
            self.assertEqual(self.call(method, path, body)[0], 423, path)       # nothing, not even turning it off
        status, _, page = fetch(self.base, "GET", "/oauth/authorize?client_id=x", headers={"Cookie": f"runway_session={self.token}"})
        self.assertEqual(status, 423)                                         # no approving an assistant either
        self.assertIn(b"Runway is locked", page)
        status, _, page = fetch(self.base, "GET", "/", headers={"Cookie": f"runway_session={self.token}"})
        self.assertNotEqual(status, 423)                                      # the page itself (no data) loads, for the lock screen
        self.assertEqual(self.call("GET", "/api/lock")[0], 200)               # and the lock screen's calls go through
        status, out = self.unlock(a)
        self.assertEqual((status, out["locked"]), (200, False), out)
        self.assertEqual(self.call("GET", "/api/state")[0], 200)
        self.assertEqual(self.call("POST", "/api/lock/settings", {"idle": 0})[1]["idle"], 0)
        self.assertEqual(self.call("POST", "/api/lock/settings", {"idle": 7})[0], 400)
        self.assertEqual(self.call("DELETE", "/api/lock")[1]["on"], False)
        self.assertIsNone(self.lock_row())

    def test_a_failed_or_replayed_unlock_stays_locked(self):
        a = Authenticator()
        self.turn_on(a)
        self.call("POST", "/api/lock/engage")
        _, ch = self.call("POST", "/api/lock/challenge", {"purpose": "unlock"})
        good = a.get(ch["challenge"])
        status, out = self.call("POST", "/api/lock/unlock", Authenticator().get(ch["challenge"]))   # another device's passkey
        self.assertEqual(status, 400)
        self.assertIn("didn’t check out", out["error"])
        status, out = self.call("POST", "/api/lock/unlock", good)     # the challenge went with the failed try
        self.assertEqual((status, out["error"]), (400, applock.EXPIRED))
        self.assertEqual(self.call("GET", "/api/state")[0], 423)
        _, ch = self.call("POST", "/api/lock/challenge", {"purpose": "unlock"})
        self.assertEqual(self.call("POST", "/api/lock/unlock", a.get(ch["challenge"]))[0], 200)
        self.call("POST", "/api/lock/engage")
        self.assertEqual(self.call("POST", "/api/lock/unlock", good)[0], 400)   # an old answer doesn't unlock again
        self.assertEqual(self.call("GET", "/api/state")[0], 423)

    def test_an_unlock_runs_out_and_the_background_does_not_keep_it_going(self):
        self.turn_on(Authenticator(), idle=60)
        soon = time.time() + 30
        with db.session() as conn:
            conn.execute(update(AppLock).where(AppLock.session == self.key).values(unlocked_until=soon))
        self.assertEqual(self.call("GET", "/api/state", hidden=True)[0], 200)   # still unlocked...
        self.assertEqual(self.lock_row()["unlocked_until"], soon)                # ...but not moved on
        self.assertEqual(self.call("GET", "/api/state")[0], 200)
        self.assertGreater(self.lock_row()["unlocked_until"], time.time() + 60 + applock.GRACE - 10)   # on screen: moved on
        with db.session() as conn:
            conn.execute(update(AppLock).where(AppLock.session == self.key).values(unlocked_until=time.time() - 1))
        self.assertEqual(self.call("GET", "/api/state")[0], 423)

    def test_an_unlock_ends_after_twelve_hours_however_much_it_is_used(self):
        self.turn_on(Authenticator(), idle=900)
        with db.session() as conn:
            conn.execute(update(AppLock).where(AppLock.session == self.key)
                         .values(unlocked_at=time.time() - applock.MAX_UNLOCKED + 5, unlocked_until=time.time() + 5))
        self.call("GET", "/api/state")
        self.assertLessEqual(self.lock_row()["unlocked_until"], time.time() + 5)

    def test_it_is_this_session_s_alone_and_ends_with_it(self):
        self.turn_on(Authenticator())
        self.call("POST", "/api/lock/engage")
        other = secrets.token_urlsafe(32)
        with db.session() as conn:
            conn.execute(insert(AuthSession).values(token_hash=hashlib.sha256(other.encode()).hexdigest(), sub="u1",
                                                    email="me@example.com", name="Me", created=time.time(), expires=time.time() + 86400))
        self.assertEqual(self.call("GET", "/api/state", token=other)[0], 200)   # the same person's other device: no lock
        self.assertEqual(self.call("GET", "/api/lock", token=other)[1]["on"], False)
        self.assertEqual(self.call("DELETE", "/api/lock", token=other)[0], 200)   # (and can't turn this one off)
        self.assertIsNotNone(self.lock_row())
        # Signing out (allowed while locked: the way out when Face ID won't work) ends it with the session.
        with mock.patch("runway.oidc.provider_sign_out", return_value=None):
            status, _ = self.call("POST", "/auth/logout")
        self.assertEqual(status, 200)
        self.assertIsNone(self.lock_row())

    def test_a_session_that_runs_out_takes_its_lock_along(self):
        self.turn_on(Authenticator())
        with db.session() as conn:
            conn.execute(update(AuthSession).where(AuthSession.token_hash == self.key).values(expires=time.time() - 1))
        self.assertEqual(self.call("GET", "/api/state")[0], 401)
        self.assertIsNone(self.lock_row())

    def test_the_device_ends_when_its_person_s_access_does(self):
        # Taken off OIDC_ALLOWED_EMAILS: their sessions end at the next request, and each device's row with them (AGENTS.md,
        # access that outlives the person), so nothing kept for the device can be used again.
        self.turn_on(Authenticator())
        with mock.patch.dict(os.environ, {"OIDC_ALLOWED_EMAILS": "someone-else@example.com"}):
            self.assertEqual(self.call("GET", "/api/lock")[0], 401)
        self.assertIsNone(self.lock_row())

    def test_turning_it_on_again_is_a_new_device_row(self):
        self.turn_on(Authenticator())
        first = self.lock_row()["id"]
        a = Authenticator()
        self.turn_on(a)
        with db.session() as conn:
            rows = conn.execute(select(AppLock.id).where(AppLock.session == self.key)).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertNotEqual(rows[0]["id"], first)   # what was kept for the old passkey went with its row
        self.call("POST", "/api/lock/engage")
        status, out = self.unlock(a)
        self.assertEqual(status, 200, out)
        self.assertGreater(self.lock_row()["last_used"], time.time() - 5)

    def test_turning_on_needs_this_session_s_challenge(self):
        a = Authenticator()
        status, out = self.call("POST", "/api/lock/register", a.create(b64(secrets.token_bytes(32))))
        self.assertEqual((status, out["error"]), (400, applock.EXPIRED))
        self.assertEqual(self.call("POST", "/api/lock/challenge", {"purpose": "unlock"})[0], 400)   # nothing to unlock
        self.assertEqual(self.call("POST", "/api/lock/challenge", {"purpose": "other"})[0], 400)
        _, ch = self.call("POST", "/api/lock/challenge", {"purpose": "register"})
        self.assertEqual(self.call("POST", "/api/lock/register", a.create(ch["challenge"], idle=42))[0], 400)
        self.assertIsNone(self.lock_row())


class WithoutSignIn(ServerCase):
    unset = ("OIDC_ISSUER",)

    def test_there_is_no_lock_without_sign_in(self):
        self.assertEqual(self.req("GET", "/api/lock")[1], {"available": False, "on": False, "locked": False, "idle": 60,
                                                            "credential_id": None, "device_id": None})
        status, out = self.req("POST", "/api/lock/challenge", {"purpose": "register"})
        self.assertEqual((status, out["error"]), (400, applock.NO_SIGN_IN))
        self.assertEqual(self.req("POST", "/api/lock/engage")[0], 400)
        self.assertEqual(self.req("GET", "/api/state")[0], 200)


if __name__ == "__main__":
    unittest.main()
