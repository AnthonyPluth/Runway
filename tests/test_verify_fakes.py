"""The stand-in services make verify and tools/control run beside each demo server (runway/verify.py's start_fakes,
tests/fakes/): what the demo server is told about them and nothing more, what they leave on disk and where, that their
made-up data is the demo's, and the whole of it through a real demo server."""
import ipaddress
import json
import os
import re
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from datetime import UTC, date, datetime, timedelta
from unittest import mock

from cryptography import x509
from sqlalchemy import select

from runway import verify
from runway.domain import demo
from runway.providers import prices, simplefin
from runway.storage import db
from runway.storage import settings_keys as sk
from runway.storage.models import Price
from tests.fakes import certs, prices_server, simplefin_server
from tests.shared import TODAY, DbCase

BASE = {"PATH": "/usr/bin:/bin", "HOME": "/home/me", "SSL_CERT_FILE": "/etc/ssl/system.pem"}
FAKES = {"simplefin": {"name": simplefin_server.NAME, "address": "127.0.0.1", "port": 40001, "ca": "/run/data/fakes/ca.pem",
                       "bundle": "/run/data/fakes/ca-bundle.pem", "pid": 11},
         "prices": {"address": "127.0.0.1", "port": 40002, "url": "http://127.0.0.1:40002/chart", "pid": 12}}


def stop_fakes() -> None:
    """Ends the stand-ins verify.start_fakes started in this process."""
    while verify.FAKE_PROCESSES:
        proc = verify.FAKE_PROCESSES.pop()
        proc.terminate()
        proc.wait(30)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def running(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


class FakesEnvTests(unittest.TestCase):
    def test_the_only_addition_to_no_proxy_is_the_bridges_made_up_name_which_is_on_loopback(self):
        server = verify.server_env("/run/data", 8123, "/lib/libfaketime.so.1", signed_in=False, base=BASE)
        env = verify.fakes_env(server, FAKES)
        for key in ("NO_PROXY", "no_proxy"):
            before, after = server[key].split(","), env[key].split(",")
            self.assertEqual(before, verify.NO_PROXY_FOR.split(","))
            self.assertEqual(after[:len(before)], before)
            self.assertEqual(after[len(before):], [simplefin_server.NAME])
        # That name is a reserved one, opened only where the stand-in listens: a port of the loopback address.
        self.assertTrue(simplefin_server.NAME.endswith(".invalid"))
        name, address, port = simplefin.stand_in(env[verify.STAND_IN_ENV])
        self.assertEqual((name, port), (simplefin_server.NAME, 40001))
        self.assertTrue(ipaddress.ip_address(address).is_loopback)
        self.assertEqual(verify.STAND_IN_ENV, simplefin.STAND_IN_ENV)

    def test_everything_else_still_goes_to_the_dead_proxy(self):
        server = verify.server_env("/run/data", 8123, "/lib/libfaketime.so.1", signed_in=False, base=BASE)
        env = verify.fakes_env(server, FAKES)
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            self.assertEqual(env[key], verify.NO_OUTSIDE)
        changed = {k for k in env if env[k] != server.get(k)}
        self.assertEqual(changed, {"NO_PROXY", "no_proxy", "SSL_CERT_FILE", verify.STAND_IN_ENV, "RUNWAY_PRICES_URL"})
        self.assertEqual(env["SSL_CERT_FILE"], "/run/data/fakes/ca-bundle.pem")
        prices_at = urllib.parse.urlsplit(env["RUNWAY_PRICES_URL"])
        self.assertTrue(ipaddress.ip_address(prices_at.hostname).is_loopback)
        self.assertIn(prices_at.hostname, env["NO_PROXY"].split(","))   # already reached directly: no addition for it
        self.assertEqual(server, verify.server_env("/run/data", 8123, "/lib/libfaketime.so.1", signed_in=False, base=BASE))   # (not changed in place)

    def test_the_bundle_is_the_systems_certificates_and_the_throwaway_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            system, authority = os.path.join(tmp, "system.pem"), os.path.join(tmp, "ca.pem")
            with open(system, "w", encoding="utf-8") as f:
                f.write("# Főtanúsítvány\n-----BEGIN CERTIFICATE-----\nSYSTEM\n-----END CERTIFICATE-----\n")
            with open(authority, "w") as f:
                f.write("-----BEGIN CERTIFICATE-----\nTHROWAWAY\n-----END CERTIFICATE-----\n")
            bundle = certs.bundle(tmp, authority, system)
            self.assertEqual(bundle, os.path.join(tmp, "ca-bundle.pem"))
            with open(bundle, encoding="utf-8") as f:
                self.assertEqual(f.read(), "# Főtanúsítvány\n-----BEGIN CERTIFICATE-----\nSYSTEM\n-----END CERTIFICATE-----\n"
                                           "-----BEGIN CERTIFICATE-----\nTHROWAWAY\n-----END CERTIFICATE-----\n")
            self.assertEqual(os.stat(bundle).st_mode & 0o777, 0o600)
            os.remove(bundle)
            with mock.patch.dict(os.environ, {"SSL_CERT_FILE": system}):   # by default: the file ssl reads the system's from
                with open(certs.bundle(tmp, authority), encoding="utf-8") as f:
                    self.assertEqual(f.read().count("BEGIN CERTIFICATE"), 2)

    def test_only_run_py_verify_and_tools_control_turn_them_on(self):
        self.assertNotIn(verify.FAKES_ENV, verify.clean_env("/run/data", {**BASE, verify.FAKES_ENV: "1"}))   # never inherited
        with open(os.path.join(verify.ROOT, "frontend", "verify", "control.mjs"), encoding="utf-8") as f:
            self.assertIn(f'export const FAKES_ENV = "{verify.FAKES_ENV}"', f.read())


class ThrowawayAuthorityTests(unittest.TestCase):
    def test_it_signs_for_the_one_name_and_only_its_certificate_is_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            made = certs.make(tmp, "sample-bank.invalid", (TODAY,))
            self.assertEqual(sorted(os.listdir(tmp)), ["ca.pem", "server.key", "server.pem"])
            for name in os.listdir(tmp):
                self.assertEqual(os.stat(os.path.join(tmp, name)).st_mode & 0o777, 0o600)
            with open(made.ca, "rb") as f:
                text = f.read()
            self.assertNotIn(b"PRIVATE KEY", text)   # the authority's key was never written
            ca = x509.load_pem_x509_certificate(text)
            limits = ca.extensions.get_extension_for_class(x509.NameConstraints)
            self.assertTrue(limits.critical)
            self.assertEqual(limits.value.permitted_subtrees, [x509.DNSName("sample-bank.invalid")])
            basic = ca.extensions.get_extension_for_class(x509.BasicConstraints)
            self.assertEqual((basic.critical, basic.value.ca, basic.value.path_length), (True, True, 0))
            # Valid now and on the day it was given (a demo server checks it on the frozen day), a day either side, no longer.
            now, given = datetime.now(UTC), datetime(TODAY.year, TODAY.month, TODAY.day, 12, tzinfo=UTC)
            self.assertLessEqual(ca.not_valid_before_utc, min(now, given) - timedelta(hours=23))
            self.assertGreaterEqual(ca.not_valid_after_utc, max(now, given) + timedelta(hours=23))
            self.assertLessEqual(ca.not_valid_after_utc - ca.not_valid_before_utc, abs(now - given) + timedelta(days=2, minutes=5))
            made.forget()
            self.assertEqual(os.listdir(tmp), ["ca.pem"])
            made.forget()   # twice is fine
        with tempfile.TemporaryDirectory() as tmp:   # given no day: two days, around now
            with open(certs.make(tmp, "sample-bank.invalid").ca, "rb") as f:
                ca = x509.load_pem_x509_certificate(f.read())
            self.assertLessEqual(ca.not_valid_after_utc - ca.not_valid_before_utc, timedelta(days=2, minutes=5))

    def test_a_running_bridge_leaves_only_the_authoritys_certificate(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = simplefin_server.start(tmp, TODAY)
            try:
                self.assertEqual(sorted(os.listdir(tmp)), ["ca-bundle.pem", "ca.pem"])
                self.assertEqual((fake.ca, fake.bundle), (os.path.join(tmp, "ca.pem"), os.path.join(tmp, "ca-bundle.pem")))
                # Its certificate is for its made-up name, from that authority: a client that trusts the authority and
                # asks for that name connects; one that asks for another name is refused.
                trusted = certs.trusting(fake.ca)
                with trusted.wrap_socket(socket.create_connection((fake.address, fake.port)), server_hostname=fake.name) as s:
                    self.assertEqual(s.version() in ("TLSv1.3", "TLSv1.2"), True)
                with self.assertRaises(ssl.SSLCertVerificationError):
                    trusted.wrap_socket(socket.create_connection((fake.address, fake.port)), server_hostname="other.invalid")
            finally:
                fake.stop()

    def test_no_certificate_or_key_is_in_the_repository_and_the_fakes_arent_in_the_image(self):
        tracked = subprocess.run(["git", "ls-files"], cwd=verify.ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(tracked.returncode, 0, tracked.stderr)   # the tests run in a checkout
        kept = [f for f in tracked.stdout.splitlines() if f.lower().endswith((".pem", ".key", ".crt", ".cer", ".p12", ".pfx", ".der"))]
        self.assertEqual(kept, [])
        with open(os.path.join(verify.ROOT, ".dockerignore")) as f:
            self.assertIn("tests", f.read().split())
        with open(os.path.join(verify.ROOT, "Dockerfile")) as f:
            self.assertEqual([line for line in f if line.startswith(("COPY", "ADD")) and "tests" in line], [])


class StartFakesTests(unittest.TestCase):
    def test_both_start_and_all_they_leave_is_under_the_servers_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = os.path.join(tmp, "data")
            os.mkdir(data)
            system = os.path.join(tmp, "system.pem")
            with open(system, "w") as f:
                f.write("-----BEGIN CERTIFICATE-----\nSYSTEM\n-----END CERTIFICATE-----\n")
            scratch = os.path.join(tmp, "tmp")   # the stand-ins' own temporary folder (kept_env keeps TMPDIR), to see they use none
            os.mkdir(scratch)
            with mock.patch.dict(os.environ, {"SSL_CERT_FILE": system, "TMPDIR": scratch}):
                fakes = verify.start_fakes(data, os.getpid())
            pids = [fakes["simplefin"]["pid"], fakes["prices"]["pid"]]
            try:
                self.assertEqual(sorted(os.listdir(data)), [verify.FAKES_DIR, verify.FAKES_FILE])
                self.assertEqual(sorted(os.listdir(os.path.join(data, verify.FAKES_DIR))), ["ca-bundle.pem", "ca.pem"])   # no key
                self.assertEqual(fakes["simplefin"]["ca"], os.path.join(data, verify.FAKES_DIR, "ca.pem"))
                self.assertEqual(fakes["simplefin"]["bundle"], os.path.join(data, verify.FAKES_DIR, "ca-bundle.pem"))
                for name in ("ca-bundle.pem", "ca.pem"):
                    with open(os.path.join(data, verify.FAKES_DIR, name)) as f:
                        self.assertNotIn("PRIVATE KEY", f.read())
                with open(fakes["simplefin"]["bundle"]) as f:   # the system's certificates, then the authority's
                    both = f.read()
                self.assertEqual((both.count("SYSTEM"), both.count("BEGIN CERTIFICATE")), (1, 2))
                with open(os.path.join(data, verify.FAKES_FILE)) as f:
                    self.assertEqual(json.load(f), fakes)
                self.assertEqual((fakes["simplefin"]["name"], fakes["simplefin"]["address"], fakes["prices"]["address"]),
                                 (simplefin_server.NAME, "127.0.0.1", "127.0.0.1"))
                self.assertEqual(os.listdir(scratch), [])   # nothing beside the server's own folder
                self.assertEqual(sorted(os.listdir(tmp)), ["data", "system.pem", "tmp"])
                # Each got kept_env: the dead proxy, and none of this process's other settings.
                for pid in pids:
                    with open(f"/proc/{pid}/environ", "rb") as f:
                        seen = dict(kv.split("=", 1) for kv in f.read().decode(errors="replace").split("\0") if "=" in kv)
                    self.assertEqual(seen["HTTPS_PROXY"], verify.NO_OUTSIDE)
                    self.assertNotIn("RUNWAY_DATA", seen)
                # The demo server's bundle trusts the bridge, at its name; the price service answers.
                trusted = certs.trusting(fakes["simplefin"]["ca"])
                env = {simplefin.STAND_IN_ENV: f"{simplefin_server.NAME}=127.0.0.1:{fakes['simplefin']['port']}",
                       "NO_PROXY": f"127.0.0.1,{simplefin_server.NAME}", "no_proxy": f"127.0.0.1,{simplefin_server.NAME}",
                       "RUNWAY_PRICES_URL": fakes["prices"]["url"]}
                from runway import tls
                with mock.patch.dict(os.environ, env), mock.patch.object(tls, "ssl_context", lambda: trusted):
                    day = verify.FROZEN_DAY
                    got = simplefin.fetch_accounts(simplefin_server.access_url(), date.fromisoformat(day) - timedelta(days=3))
                    self.assertEqual([a["id"] for a in got["accounts"]], [a[0] for a in simplefin_server.ACCOUNTS])
                    self.assertEqual(prices._quote("DEMOTM")["price"], 284.95)
            finally:
                stop_fakes()
            self.assertEqual([p for p in pids if running(p)], [])

    def test_a_stand_in_that_doesnt_start_says_so(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(verify, "ROOT", tmp), self.assertRaises(verify.FakesFailed) as cm:
            verify.start_fakes(tmp, os.getpid())
        self.assertIn("tests/fakes/simplefin_server.py isn't here", str(cm.exception))
        with tempfile.TemporaryDirectory() as tmp, open(os.path.join(tmp, "errors"), "w") as errors:
            # A --dir that isn't there: it ends before it listens (what it says goes where this process's errors go).
            keep = os.dup(2)
            os.dup2(errors.fileno(), 2)
            try:
                with self.assertRaises(verify.FakesFailed) as cm:
                    verify._start_fake("simplefin_server", ["--dir", os.path.join(tmp, "missing")], os.getpid(), 30)
            finally:
                os.dup2(keep, 2)
                os.close(keep)
            self.assertIn("ended before it was listening", str(cm.exception))
            with open(errors.name) as said:
                self.assertIn("FileNotFoundError", said.read())
        self.assertEqual(verify.FAKE_PROCESSES, [])


class MadeUpDataTests(DbCase):
    """The stand-ins' data is the demo's: a sync or a price refresh against them changes nothing the demo already shows."""

    def test_the_bridges_accounts_are_the_demos_and_its_login_the_one_the_demo_saves(self):
        self.assertEqual([(i, n, o, b) for i, n, o, b in simplefin_server.ACCOUNTS], [(i, n, o, b) for i, n, o, _kind, b in demo.ACCOUNTS])
        demo.seed(self.c, TODAY)
        self.assertEqual(db.get_setting(self.c, sk.SIMPLEFIN_ACCESS_URL), simplefin_server.access_url())
        everyday = {payee.upper() for payee, *_ in demo.EVERYDAY} | {"TRANSFER FROM CHECKING"}
        self.assertLessEqual({t[4] for t in simplefin_server.TRANSACTIONS}, everyday)   # payees the demo already categorized
        added = {a[0] for a in simplefin_server.ADDED_ACCOUNTS}
        self.assertLessEqual({t[0] for t in simplefin_server.TRANSACTIONS}, {a[0] for a in simplefin_server.ACCOUNTS} | added)
        self.assertEqual(added & {a[0] for a in demo.ACCOUNTS}, set())
        self.assertIn(simplefin_server.PARTIAL_WITHOUT, {a[2] for a in simplefin_server.ACCOUNTS})

    def test_the_price_services_closes_are_the_ones_the_demo_seeds(self):
        self.assertEqual({t: (name, close_before, close) for t, (name, _kind, close_before, close) in prices_server.HOLDINGS.items()},
                         {t: (name, before, close) for t, name, _kind, _shares, _paid, before, close in demo.HOLDINGS})
        self.assertEqual({t: kind.lower() for t, (_n, kind, *_rest) in prices_server.HOLDINGS.items()},
                         {t: kind for t, _name, kind, *_rest in demo.HOLDINGS})
        demo.seed_investments(self.c, TODAY)
        seeded = {(r["ticker"], r["date"]): r["close"] for r in self.c.execute(select(Price.ticker, Price.date, Price.close))}
        fake = prices_server.start(TODAY)
        self.addCleanup(fake.stop)
        with mock.patch.dict(os.environ, {"RUNWAY_PRICES_URL": fake.url, "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}):
            fetched = {(t, d): c for t in prices_server.HOLDINGS for d, c, _adj in prices.fetch(t, TODAY - timedelta(days=400), TODAY)[0]}
            self.assertEqual(fetched, seeded)
            rows, _splits, info = prices.fetch("DEMOSI", TODAY - timedelta(days=2), TODAY)
            self.assertEqual(([d for d, *_ in rows], info), ([(TODAY - timedelta(days=k)).isoformat() for k in (2, 1, 0)],
                                                              {"type": "EQUITY", "name": "Sample Industries Inc"}))
            quote = prices._quote("DEMOIN")
            self.assertEqual((quote["price"], quote["prev_close"], quote["type"]), (67.55, 68.20, "ETF"))
            with self.assertRaises(urllib.error.HTTPError) as cm:   # the S&P 500 fund: no data, so the market reads as closed
                prices._quote(prices.BENCHMARK)
            self.assertEqual(cm.exception.code, 404)
            cm.exception.close()
        self.assertEqual(fake.requests[-2:], ["DEMOIN", prices.BENCHMARK])

    def test_the_modes_are_the_ones_the_flows_and_tools_control_know(self):
        with open(os.path.join(verify.ROOT, "frontend", "verify", "fakes.mjs"), encoding="utf-8") as f:
            listed = re.search(r"export const BANK_MODES = \[([^\]]*)\]", f.read())
        assert listed
        self.assertEqual(tuple(re.findall(r'"([^"]+)"', listed.group(1))), simplefin_server.MODES)
        # The setup token tools/control's help and the flows give is the one the bridge knows.
        token = simplefin_server.setup_token()
        for path in ("frontend/verify/control.mjs", "frontend/verify/flows/simplefin-connect.json", "frontend/verify/flows/simplefin-connect-phone.json",
                     ".claude/skills/verify-runway/references/journeys.md"):
            with open(os.path.join(verify.ROOT, path), encoding="utf-8") as f:
                self.assertIn(token, f.read(), path)


class ServeWithFakesTests(unittest.TestCase):
    """`python -m runway.verify serve` with the stand-ins, as verify.mjs and tools/control run it: a real demo server under
    the frozen clock, driven through its API."""

    @classmethod
    def setUpClass(cls):
        cls.lib = verify.find_libfaketime()
        if not cls.lib:
            raise unittest.SkipTest("libfaketime isn't installed")
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        seed = os.path.join(cls.tmp.name, "seed")
        os.mkdir(seed)
        seeded = verify.seed_demo(seed, cls.lib, ("investments",))
        assert seeded.returncode == 0, seeded.stderr
        cls.data = os.path.join(cls.tmp.name, "server")
        cls.port = free_port()
        # (From this process's environment, with a credential in it that must not reach the demo server or the stand-ins.)
        env = {**os.environ, verify.FAKES_ENV: "1", "SOME_SERVICE_TOKEN": "real"}
        cls.server = subprocess.Popen([sys.executable, "-m", "runway.verify", "serve", "--parent", str(os.getpid()), "--seed", seed,
                                       "--data", cls.data, "--port", str(cls.port)], cwd=verify.ROOT, env=env)
        cls.addClassCleanup(cls.stop_server)
        cls.direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        end = time.monotonic() + 120
        while True:
            try:
                cls.call("/api/state")
                break
            except OSError:
                if cls.server.poll() is not None or time.monotonic() > end:
                    raise AssertionError(f"the demo server didn't start:\n{cls.log()}") from None
                time.sleep(0.2)
        with open(os.path.join(cls.data, verify.FAKES_FILE)) as f:
            cls.fakes = json.load(f)

    @classmethod
    def stop_server(cls):
        if cls.server.poll() is None:
            cls.server.terminate()
        cls.server.wait(30)

    @classmethod
    def log(cls) -> str:
        try:
            with open(os.path.join(cls.data, "server.log")) as f:
                return f.read()[-3000:]
        except OSError:
            return ""

    @classmethod
    def call(cls, path, body=None):
        req = urllib.request.Request(f"http://127.0.0.1:{cls.port}{path}", data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "X-Runway": "1"}, method="GET" if body is None else "POST")
        try:
            with cls.direct.open(req, timeout=120) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read())

    def bank(self, mode: str) -> None:
        """POST /__fake/mode, over TLS at the bridge's made-up name (as frontend/verify/fakes.mjs asks it)."""
        import http.client
        bridge = self.fakes["simplefin"]
        conn = http.client.HTTPSConnection(bridge["address"], bridge["port"], timeout=30, context=certs.trusting(bridge["ca"]))
        conn._context.check_hostname = True   # type: ignore[attr-defined]
        try:
            conn.sock = conn._context.wrap_socket(socket.create_connection((bridge["address"], bridge["port"]), 30),  # type: ignore[attr-defined]
                                                  server_hostname=bridge["name"])
            conn.request("POST", "/__fake/mode", body=mode, headers={"Host": bridge["name"]})
            answer = conn.getresponse()
            self.assertEqual((answer.status, json.loads(answer.read())), (200, {"mode": mode}))
        finally:
            conn.close()

    def test_the_demo_server_syncs_with_the_stand_ins_and_nothing_else(self):
        with open(f"/proc/{self.server.pid}/environ", "rb") as f:
            env = dict(kv.split("=", 1) for kv in f.read().decode(errors="replace").split("\0") if "=" in kv)
        self.assertEqual(env["NO_PROXY"], f"{verify.NO_PROXY_FOR},{simplefin_server.NAME}")
        self.assertEqual(env["no_proxy"], env["NO_PROXY"])
        self.assertEqual(env["HTTPS_PROXY"], verify.NO_OUTSIDE)
        self.assertNotIn("SOME_SERVICE_TOKEN", env)
        self.assertNotIn(verify.FAKES_ENV, env)
        # The throwaway authority is in this server's own folder (the run's temporary directory), and nowhere else.
        folder = os.path.join(self.data, verify.FAKES_DIR)
        self.assertEqual(env["SSL_CERT_FILE"], os.path.join(folder, "ca-bundle.pem"))
        self.assertEqual(os.path.dirname(self.fakes["simplefin"]["ca"]), folder)
        self.assertEqual(sorted(os.listdir(folder)), ["ca-bundle.pem", "ca.pem"])
        self.assertTrue(os.path.realpath(folder).startswith(os.path.realpath(self.tmp.name) + os.sep))

        status, first = self.call("/api/sync", {})
        self.assertEqual((status, first["new"], first["bank_messages"]), (200, 3, []), self.log())
        self.assertEqual(first["categorized"]["history"], 3)   # at payees the demo already has
        status, tx = self.call("/api/transactions?limit=400")
        ids = {t["id"] for t in tx["items"]}
        self.assertLessEqual({"demo-card|fake-coffee", "demo-card|fake-groceries", "demo-card|fake-tacos"}, ids)
        # The claim, through the app's own Connect: the new login brings the account the demo didn't have.
        self.assertEqual(self.call("/api/connect", {"token": simplefin_server.setup_token()}), (200, {"ok": True}))
        self.assertEqual(self.call("/api/sync", {})[0], 200)
        status, accounts = self.call("/api/accounts")
        self.assertIn("Vacation Savings", json.dumps(accounts))
        # A price refresh brings back the demo's own prices (the benchmark has none: the market reads as closed).
        status, inv = self.call("/api/investments/sync", {})
        self.assertEqual((status, inv["prices"]), (200, {"fetched": ["DEMOIN", "DEMOSI", "DEMOTM"], "failed": [prices.BENCHMARK]}), self.log())
        status, live = self.call("/api/investments/live")
        self.assertEqual((live["market"], live["quotes"]["DEMOTM"]["price"]), ("closed", 284.95))

        self.bank("403")
        status, failed = self.call("/api/sync", {})
        self.assertEqual(status, 502)
        self.assertIn("Reconnect in Settings.", failed["error"])
        self.assertEqual(self.call("/api/state")[1]["last_log"]["ok"], 0)
        self.bank("ok")
        self.assertEqual(self.call("/api/sync", {})[0], 200)

    def test_zz_the_stand_ins_end_with_the_demo_server(self):
        pids = [self.fakes["simplefin"]["pid"], self.fakes["prices"]["pid"]]
        self.assertTrue(all(running(p) for p in pids))
        self.server.kill()   # however it ends
        self.server.wait(30)
        end = time.monotonic() + 15
        while any(running(p) for p in pids) and time.monotonic() < end:
            time.sleep(0.1)
        self.assertEqual([p for p in pids if running(p)], [])


if __name__ == "__main__":
    unittest.main()
