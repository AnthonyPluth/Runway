"""The app lock (runway/applock.py, server/api/lock.py, and the server refusing a locked session: server/handler.py):
WebAuthn checked against a software authenticator (both kinds of key), each way a registration or an unlock can be
wrong, and through a real server: turning it on, locking, the 423s (the API and the assistants' consent page) while
only the lock screen's calls go through, unlocking, an unlock running out (and calls from the background not keeping it
going), the lock ending with the sign-in, and the device's cache key share (#357)."""
import base64
import contextlib
import hashlib
import io
import json
import os
import secrets
import time
import unittest
from unittest import mock

import sentry_sdk
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import insert, select, update

from runway import applock, monitoring
from runway.server import mcp_access, mcp_http, mcp_server
from runway.storage import backup, db, secretbox
from runway.storage.models import AppLock, AuthSession, User
from tests.shared import ServerCase, fetch, own_database
from tests.test_monitoring import start as start_sentry
from tests.webauthn_support import OIDC_ENV, ORIGIN, RP_ID, Authenticator, b64


def stop_sentry():
    sentry_sdk.get_client().close()
    sentry_sdk.init(dsn=None)
    monitoring._enabled = False


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


class SignedIn(ServerCase):
    """A real server with sign-in on, a session made for each test, and the calls to turn the lock on and unlock."""
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


class ThroughTheServer(SignedIn):
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

    # A Lock now (another tab, or the app opening) landing after the server let a request through unlocked: whatever
    # that request writes leaves the lock locked, and it gets the 423 a locked session gets (#363).

    def let_through(self):
        """The server's check (applock.refuses) passing, as it did just before the lock landed."""
        return mock.patch.object(applock, "refuses", return_value=False)

    def test_changing_when_it_locks_does_not_unlock_a_lock_that_landed_in_between(self):
        a = Authenticator()
        self.turn_on(a, idle=60)
        self.call("POST", "/api/lock/engage")
        with self.let_through():
            status, out = self.call("POST", "/api/lock/settings", {"idle": 900})
        self.assertEqual((status, out), (423, {"error": applock.LOCKED, "locked": True}))
        row = self.lock_row()
        self.assertEqual((row["unlocked_until"], row["idle"]), (None, 60))     # locked, and nothing changed
        self.assertEqual(self.call("GET", "/api/state")[0], 423)
        self.assertEqual(self.unlock(a)[0], 200)
        self.assertEqual(self.call("POST", "/api/lock/settings", {"idle": 900})[1]["idle"], 900)   # unlocked: as before

    def test_turning_it_on_again_does_not_unlock_a_lock_that_landed_in_between(self):
        a = Authenticator()
        self.turn_on(a)
        first = self.lock_row()["id"]
        self.call("POST", "/api/lock/engage")
        _, ch = self.call("POST", "/api/lock/challenge", {"purpose": "register"})
        with self.let_through():
            status, out = self.call("POST", "/api/lock/register", Authenticator().create(ch["challenge"]))
        self.assertEqual((status, out), (423, {"error": applock.LOCKED, "locked": True}))
        row = self.lock_row()
        self.assertEqual((row["id"], row["credential_id"], row["unlocked_until"]), (first, b64(a.cred), None))
        self.assertEqual(self.unlock(a)[0], 200)                              # still the first passkey's to unlock

    def test_an_unlock_only_unlocks_the_row_its_passkey_signed_for(self):
        a = Authenticator()
        self.turn_on(a)
        self.call("POST", "/api/lock/engage")
        verify = applock.verify_assertion

        def replaced_meanwhile(body, lock, *args):   # turned on again, with another passkey, while this one was checked
            count = verify(body, lock, *args)
            with db.session() as conn:
                conn.execute(update(AppLock).where(AppLock.session == self.key).values(id="dev_other", credential_id="x"))
            return count
        with mock.patch.object(applock, "verify_assertion", side_effect=replaced_meanwhile):
            status, out = self.unlock(a)
        self.assertEqual((status, out["error"]), (400, applock.NOT_CHECKED))
        self.assertIsNone(self.lock_row()["unlocked_until"])
        self.assertEqual(self.call("GET", "/api/state")[0], 423)


class KeyShare(SignedIn):
    """POST /api/lock/key-share: the device's share of its cache key (#357), once per passkey check, to this session
    unlocked, never logged or reported, and gone with the device's row however that ends."""

    def share(self, token=None):
        return self.call("POST", "/api/lock/key-share", {}, token=token)

    def stored(self):
        row = self.lock_row()
        return row["key_share"] if row else None

    def test_once_after_each_passkey_check_and_the_same_share_each_time(self):
        a = Authenticator()
        self.turn_on(a)                                                   # turning it on is a passkey check
        before = int(time.time())
        status, headers, raw = fetch(self.base, "POST", "/api/lock/key-share", b"{}",
                                     {"Cookie": f"runway_session={self.token}", "X-Runway": "1",
                                      "Content-Type": "application/json"})
        self.assertEqual(status, 200, raw)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertIsNone(headers["ETag"])
        out = json.loads(raw)
        self.assertEqual(set(out), {"share", "expires"})
        self.assertRegex(out["share"], r"^[A-Za-z0-9_-]{43}$")
        self.assertEqual(len(base64.urlsafe_b64decode(out["share"] + "=")), 32)
        self.assertEqual(applock.SHARE_WINDOW, 72 * 3600)
        self.assertTrue(before + applock.SHARE_WINDOW <= out["expires"] <= int(time.time()) + applock.SHARE_WINDOW)
        stored = self.stored()
        self.assertTrue(stored.startswith(secretbox.PREFIX))              # kept encrypted
        self.assertNotIn(out["share"], stored)
        self.assertEqual(secretbox.decrypt(stored), out["share"])
        status, again = self.share()                                      # once per check
        self.assertEqual((status, again["error"]), (403, applock.NOT_FRESH))
        self.call("POST", "/api/lock/engage")
        self.assertEqual(self.unlock(a)[0], 200)
        status, second = self.share()
        self.assertEqual(status, 200, second)
        self.assertEqual(second["share"], out["share"])                   # one share per device, not one per unlock
        self.assertEqual(self.stored(), stored)

    def test_not_without_a_passkey_check_just_before(self):
        self.turn_on(Authenticator())
        applock._challenges[(self.key, "share")] = (self.lock_row()["id"].encode(), time.time() - 1)   # too long ago
        status, out = self.share()
        self.assertEqual((status, out["error"]), (403, applock.NOT_FRESH))
        applock._checked(self.key, "dev_another", time.time())           # a check for another device row
        self.assertEqual(self.share()[0], 403)
        applock._checked(self.key + "x", self.lock_row()["id"], time.time())   # another session's check
        self.assertEqual(self.share()[0], 403)
        self.assertIsNone(self.stored())                                  # nothing made
        status, out = self.call("POST", "/api/lock/challenge", {"purpose": "share"})
        self.assertEqual(status, 400)                                     # no way to ask for a check without a passkey

    def test_locked_is_423_and_nothing_is_made(self):
        self.turn_on(Authenticator())
        self.call("POST", "/api/lock/engage")
        self.assertEqual(self.share(), (423, {"error": applock.LOCKED, "locked": True}))
        self.assertIsNone(self.stored())

    def test_a_lock_that_lands_in_between_is_423_and_nothing_is_made(self):
        self.turn_on(Authenticator())
        self.call("POST", "/api/lock/engage")
        with mock.patch.object(applock, "refuses", return_value=False):
            self.assertEqual(self.share(), (423, {"error": applock.LOCKED, "locked": True}))
        self.assertIsNone(self.stored())

    def test_not_with_the_lock_off_or_without_a_session(self):
        status, out = self.share()
        self.assertEqual((status, out["error"]), (400, applock.NOT_ON))
        self.assertEqual(self.share(token="not-a-session")[0], 401)

    def test_not_for_assistants_or_the_extension(self):
        self.turn_on(Authenticator())
        self.assertTrue(mcp_access.blocked("/api/lock/key-share"))
        anything = mcp_access.Access(frozenset({"read", "write", "churning:write", "categorize:write"}), None, None)
        with self.assertRaisesRegex(mcp_server.ToolError, "^" + mcp_http.OUT_OF_REACH):
            mcp_http.local_fetch("lock/key-share", {}, {}, anything, "POST")
        status, _ = self.req("POST", "/api/lock/key-share", {}, {"Authorization": "Bearer rwx_whatever"})
        self.assertEqual(status, 401)                                     # the extension's key isn't a sign-in
        status, _ = self.req("POST", "/api/ext/lock/key-share", {}, {"Authorization": "Bearer rwx_whatever"})
        self.assertEqual(status, 404)                                     # nor is it one of the extension's own
        self.assertIsNone(self.stored())
        self.assertEqual(self.share()[0], 200)                            # the check is still there for the device

    def test_asked_too_often_is_429(self):
        a = Authenticator()
        self.turn_on(a)
        with mock.patch.object(applock, "SHARE_LIMIT", 2):
            self.assertEqual(self.share()[0], 200)
            self.assertEqual(self.share()[0], 403)                        # (counted, though refused)
            self.assertEqual(self.unlock(a)[0], 200)
            status, out = self.share()
            self.assertEqual((status, out["error"]), (429, applock.TOO_MANY))
        with mock.patch.object(applock, "SHARE_LIMIT_WINDOW", 0):
            self.assertEqual(self.share()[0], 200)                        # the window over: the check still holds

    def test_never_logged_or_reported(self):
        transport = start_sentry()
        self.addCleanup(stop_sentry)
        out, err = io.StringIO(), io.StringIO()
        a = Authenticator()
        made = []

        def encrypt_fails(value):                                         # an error that quotes the share
            made.append(value)
            raise RuntimeError(f"couldn't keep {value}")
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.turn_on(a)
            status, got = self.share()
            self.assertEqual(status, 200, got)
            self.call("POST", "/api/lock/engage")
            self.unlock(a)
            self.assertEqual(self.share()[0], 200)
            with db.session() as conn:
                conn.execute(update(AppLock).where(AppLock.session == self.key).values(key_share=None))
            self.call("POST", "/api/lock/engage")
            self.unlock(a)
            with mock.patch.object(applock.secretbox, "encrypt", side_effect=encrypt_fails):
                status, failed = self.share()
            sentry_sdk.flush()
        self.assertEqual(status, 500)
        self.assertIn("reference", failed["error"])
        self.assertTrue(transport.events)                                 # the failure was reported...
        sent = json.dumps([transport.events, transport.items])
        self.assertIn("/api/lock/key-share", out.getvalue())              # ...and the request logged...
        for secret in (got["share"], made[0]):
            for where, text in (("stdout", out.getvalue()), ("stderr", err.getvalue()), ("Sentry", sent)):
                self.assertNotIn(secret, text, where)                     # ...without the share
        self.assertNotIn(secretbox.PREFIX, out.getvalue() + err.getvalue() + sent)

    # Every way the device's row ends takes the share along (AGENTS.md, access that outlives the person).

    def with_share(self) -> Authenticator:
        a = Authenticator()
        self.turn_on(a)
        self.assertEqual(self.share()[0], 200)
        self.assertIsNotNone(self.stored())
        return a

    def no_share_left(self):
        with db.session() as conn:
            self.assertEqual(conn.execute(select(AppLock.key_share).where(AppLock.key_share.is_not(None))
                                          .where(AppLock.session == self.key)).fetchall(), [])

    def test_gone_when_this_device_signs_out(self):
        self.with_share()
        with mock.patch("runway.oidc.provider_sign_out", return_value=None):
            self.assertEqual(self.call("POST", "/auth/logout")[0], 200)
        self.assertIsNone(self.lock_row())
        self.assertEqual(self.share()[0], 401)

    def test_gone_when_the_session_runs_out(self):
        self.with_share()
        with db.session() as conn:
            conn.execute(update(AuthSession).where(AuthSession.token_hash == self.key).values(expires=time.time() - 1))
        self.assertEqual(self.share()[0], 401)
        self.assertIsNone(self.lock_row())

    def test_gone_when_taken_off_the_allowed_emails(self):
        self.with_share()
        with mock.patch.dict(os.environ, {"OIDC_ALLOWED_EMAILS": "someone-else@example.com"}):
            self.assertEqual(self.share()[0], 401)
        self.assertIsNone(self.lock_row())

    def test_gone_when_a_group_sign_in_lapses(self):
        groups = {"OIDC_ALLOWED_EMAILS": "", "OIDC_ALLOWED_GROUPS": "family"}
        with db.session() as conn:   # signed in just now
            db.upsert(conn, User, {"sub": "u1", "email": "me@example.com", "last_seen": time.time()}, key=["sub"])
        with mock.patch.dict(os.environ, groups):
            self.with_share()
            with db.session() as conn:   # a group's sign-in isn't renewed: its session ends at its fixed end
                conn.execute(update(AuthSession).where(AuthSession.token_hash == self.key).values(expires=time.time() - 1))
            self.assertEqual(self.share()[0], 401)
        self.assertIsNone(self.lock_row())

    def test_not_issued_and_dropped_when_access_lapsed_before_the_session_ended(self):
        # A session that outlives its person's access (signed in before OIDC_ALLOWED_GROUPS took over from emails, say):
        # oidc.access_lapsed says so, and the share is dropped rather than handed out.
        a = self.with_share()
        with db.session() as conn:
            db.upsert(conn, User, {"sub": "u1", "email": "me@example.com", "last_seen": time.time() - 400 * 86400}, key=["sub"])
        with mock.patch.dict(os.environ, {"OIDC_ALLOWED_EMAILS": "", "OIDC_ALLOWED_GROUPS": "family"}):
            self.call("POST", "/api/lock/engage")
            self.assertEqual(self.unlock(a)[0], 200)
            status, out = self.share()
        self.assertEqual((status, out["error"]), (403, applock.LAPSED))
        self.assertIsNotNone(self.lock_row())
        self.no_share_left()

    def test_gone_when_turned_off(self):
        self.with_share()
        self.assertEqual(self.call("DELETE", "/api/lock")[1]["on"], False)
        self.assertIsNone(self.lock_row())
        self.assertEqual(self.share()[0], 400)

    def test_turning_it_on_again_is_a_new_share(self):
        self.with_share()
        first = secretbox.decrypt(self.stored())
        self.turn_on(Authenticator())
        self.assertIsNone(self.stored())                                  # the old row, and its share, are gone
        status, out = self.share()
        self.assertEqual(status, 200, out)
        self.assertNotEqual(out["share"], first)

    def test_a_share_this_key_cannot_read_is_replaced(self):
        a = self.with_share()
        first = secretbox.decrypt(self.stored())
        with db.session() as conn:   # kept under a key that's since gone
            conn.execute(update(AppLock).where(AppLock.session == self.key).values(key_share=secretbox.PREFIX + "gAAAAAbad"))
        self.call("POST", "/api/lock/engage")
        self.unlock(a)
        status, out = self.share()
        self.assertEqual(status, 200, out)
        self.assertNotEqual(out["share"], first)
        self.assertEqual(secretbox.decrypt(self.stored()), out["share"])

    def test_backups_never_carry_it_and_a_restore_brings_none_back(self):
        self.with_share()
        kept = self.stored()
        with db.session() as conn:
            data = json.loads(json.dumps(backup.export(conn)))
        self.assertNotIn("app_locks", data["tables"])
        self.assertNotIn(kept, json.dumps(data))
        self.assertNotIn(secretbox.decrypt(kept), json.dumps(data))
        with db.session() as conn:
            backup.restore(conn, data)                                    # this device is still signed in: kept as it was
        self.assertEqual(self.stored(), kept)
        self.assertEqual(self.call("DELETE", "/api/lock")[0], 200)        # the device ends...
        with db.session() as conn:
            backup.restore(conn, data)                                    # ...and a backup from before doesn't bring it back
        self.assertIsNone(self.lock_row())
        self.no_share_left()


class LockLandsAfterTheRead(unittest.TestCase):
    """set_idle and refuses read the row, then write it: a Lock now committed between the two stays locked."""

    def setUp(self):
        own_database(self)
        self.key = "k" * 64
        now = time.time()
        with db.session() as conn:
            conn.execute(insert(AuthSession).values(token_hash=self.key, sub="u1", email="me@example.com", name="Me",
                                                    created=now, expires=now + 86400))
            conn.execute(insert(AppLock).values(id="dev_1", session=self.key, credential_id="c", public_key="p", alg=-7,
                                                sign_count=0, idle=60, unlocked_at=now - 60, unlocked_until=now + 30,
                                                created=now, last_used=now))

    def engaging_after_the_read(self, conn, then=applock.engage):
        """`conn`, with Lock now (or `then`) landing just after the first thing read from it."""
        key, first = self.key, []

        class Conn:
            def execute(self, stmt):
                out = conn.execute(stmt)
                if first:
                    return out
                first.append(True)
                row = out.fetchone()
                then(conn, key)
                return mock.Mock(fetchone=lambda: row)
        return Conn()

    def lock_row(self):
        with db.session() as conn:
            return conn.execute(select(AppLock).where(AppLock.session == self.key)).fetchone()

    def test_set_idle(self):
        with db.session() as conn, self.assertRaises(applock.Locked):
            applock.set_idle(self.engaging_after_the_read(conn), self.key, 900, time.time())
        row = self.lock_row()
        self.assertEqual((row["unlocked_until"], row["idle"]), (None, 60))

    def test_an_unlock_moved_on_by_a_call(self):
        with db.session() as conn:
            refused = applock.refuses(self.engaging_after_the_read(conn), self.key, "GET", "/api/state", False, time.time())
        self.assertTrue(refused)                     # refused like any call on a locked session
        self.assertIsNone(self.lock_row()["unlocked_until"])

    def test_the_key_share(self):
        applock._checked(self.key, "dev_1", time.time())
        with mock.patch.dict(os.environ, OIDC_ENV), db.session() as conn, self.assertRaises(applock.Locked):
            applock.key_share(self.engaging_after_the_read(conn), self.key, None, time.time())
        row = self.lock_row()
        self.assertEqual((row["unlocked_until"], row["key_share"]), (None, None))   # locked, and no share made for it

    def test_the_key_share_while_it_is_turned_off(self):
        applock._checked(self.key, "dev_1", time.time())
        with mock.patch.dict(os.environ, OIDC_ENV), db.session() as conn, self.assertRaises(applock.LockError) as cm:
            applock.key_share(self.engaging_after_the_read(conn, then=applock.turn_off), self.key, None, time.time())
        self.assertEqual(str(cm.exception), applock.NOT_ON)
        self.assertIsNone(self.lock_row())

    def test_an_unlock_moved_on_while_it_is_turned_off(self):
        with db.session() as conn:               # gone rather than locked: nothing to refuse for
            c = self.engaging_after_the_read(conn, then=applock.turn_off)
            self.assertFalse(applock.refuses(c, self.key, "GET", "/api/state", False, time.time()))
        self.assertIsNone(self.lock_row())


class WithoutSignIn(ServerCase):
    unset = ("OIDC_ISSUER",)

    def test_there_is_no_lock_without_sign_in(self):
        self.assertEqual(self.req("GET", "/api/lock")[1], {"available": False, "on": False, "locked": False, "idle": 60,
                                                            "credential_id": None, "device_id": None})
        status, out = self.req("POST", "/api/lock/challenge", {"purpose": "register"})
        self.assertEqual((status, out["error"]), (400, applock.NO_SIGN_IN))
        self.assertEqual(self.req("POST", "/api/lock/engage")[0], 400)
        status, out = self.req("POST", "/api/lock/key-share", {})
        self.assertEqual((status, out["error"]), (400, applock.NO_SIGN_IN))
        self.assertEqual(self.req("GET", "/api/state")[0], 200)


if __name__ == "__main__":
    unittest.main()
